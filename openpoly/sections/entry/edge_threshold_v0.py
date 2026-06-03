"""Edge-threshold entry — the entry decision section.

Picks a side from the analyzer's ``p_model`` (YES when ``p_model >= 0.5``, else
NO), reads the held side's live order book, and gates on edge + spread::

    held_price = best ask of the held side's token
    spread     = best ask - best bid
    edge       = (p_model if YES else 1 - p_model) - held_price

A position is sized by ``order_size_usd`` at ``held_price``. The section emits a
decision only — an ``OrderIntent``; the executor turns it into an actual fill
and is authoritative on the realized price. The book is read level-1 only
(best ask / best bid), matching the executor's crude micro-stakes fill model.

The section reads the live ``MarketStore`` singleton directly (same pattern as
the embedding section — no capability injection). When configured with a
``portfolio_provider``, it also reads the portfolio to enforce
``same_market_cooldown_minutes`` — a per-(market, side) re-entry cooldown that
prevents the "SL → news → re-enter NO → SL again" pattern seen in early paper
runs. The hard one-position-per-(market, side) invariant is still enforced by
the executor + the DB partial unique index; the cooldown is the *soft* gate
that also catches recently-closed positions.

When ``veto_enabled``, ``run()`` also does a late-buy veto — a CLOB
``/prices-history`` fetch — so the orchestrator offloads it to a worker thread.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.polymarket_api import recent_move
from openpoly.sections._base import SectionInput, SectionOutput
from openpoly.sections.analyzer.llm_v0 import AnalysisResult

if TYPE_CHECKING:
    from openpoly.portfolio import PortfolioStore


Side = Literal["yes", "no"]
PortfolioProvider = Callable[[], "PortfolioStore | None"]


@dataclass(frozen=True)
class OrderIntent:
    """An entry decision: buy ``qty`` of ``side`` at an estimated ``price``.

    ``price`` is the level-1 ask the section saw; the executor re-reads the live
    book at fill time and is authoritative on the actual fill price / qty.
    """

    market_id: str
    side: Side
    price: float
    qty: float


class EdgeThresholdConfig(BaseModel):
    min_edge: float = Field(default=0.05, ge=0.0, le=1.0)
    order_size_usd: float = Field(default=10.0, ge=1.0, le=100.0)
    max_spread: float = Field(default=0.05, ge=0.0, le=0.5)
    slippage_tolerance: float = Field(
        default=0.02,
        ge=0.0,
        le=0.2,
        description="Reserved — dormant under the level-1 fill model (v1).",
    )
    side_lock: bool = Field(default=False, description="Lock to YES only; never buy NO.")
    veto_enabled: bool = Field(
        default=False,
        description=(
            "Enable the late-buy veto. Off by default — run warn-only first "
            "and observe recent_move before enforcing."
        ),
    )
    veto_window_min: int = Field(
        default=60,
        ge=1,
        le=1440,
        description="Late-buy veto: price-move lookback window, in minutes.",
    )
    veto_move_threshold: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            "Late-buy veto: skip the entry if the held side's token has "
            "already moved up by at least this much over the window."
        ),
    )
    same_market_cooldown_minutes: int = Field(
        default=0,
        ge=0,
        le=1440,
        description=(
            "Skip the entry if a position on the same (market, side) was "
            "opened or closed within this many minutes. 0 disables the "
            "check. Targets the repeated-loss-on-same-market pattern. "
            "Superseded by ``same_market_lifetime_lockout`` when that is "
            "True."
        ),
    )
    same_market_lifetime_lockout: bool = Field(
        default=False,
        description=(
            "Strict mode: skip if ANY prior position exists on (market, "
            "side), regardless of when. One-shot-per-(market, side) "
            "across the lifetime of the strategy. When True, "
            "``same_market_cooldown_minutes`` is ignored."
        ),
    )
    heat_cap_usd: float = Field(
        default=0.0,
        ge=0.0,
        le=10_000.0,
        description=(
            "Skip the entry if the sum of (qty × avg_entry_price) across "
            "all currently-open positions is at or above this dollar "
            "amount. 0 disables the check. Caps total exposure during "
            "regimes where the analyzer's signal goes one-sided "
            "(cross-market correlated losses)."
        ),
    )

    # ---- A4 kill switch (entry-side circuit breakers) ----
    # All three default to 0 (disabled). Operator opts in via canvas config.
    # Trips are entry-only: open positions keep running their normal exit
    # logic (manual close + ExitMonitor still work). The brakes share the
    # same list_positions(500) read the cooldown gate already does.
    kill_max_consecutive_losses: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "Skip the entry if the most recent N closed positions are ALL "
            "losses (realized_pnl < 0). Catches regime change / strategy "
            "drift. 0 disables. Example: 5 stops new entries after 5 "
            "consecutive losers."
        ),
    )
    kill_daily_loss_usd: float = Field(
        default=0.0,
        ge=0.0,
        le=10_000.0,
        description=(
            "Skip the entry if the sum of realized_pnl across positions "
            "closed in the last 24h is ≤ -kill_daily_loss_usd. 0 disables. "
            "Bounds single-day damage."
        ),
    )
    kill_max_drawdown_usd: float = Field(
        default=0.0,
        ge=0.0,
        le=10_000.0,
        description=(
            "Skip the entry if the cumulative realized_pnl curve (all "
            "closed positions, chronological) has dropped this many "
            "dollars from its peak. 0 disables. Catches slow bleed across "
            "many small losses. Tighter than kill_daily_loss because it "
            "tracks ALL history, not just one day."
        ),
    )


class EdgeThresholdEntryV0:
    SECTION_TYPE = "entry"
    SECTION_VERSION = "0.3.0"
    REQUIRES = ["order_book", "market_data"]
    Config = EdgeThresholdConfig

    def __init__(
        self,
        config: EdgeThresholdConfig,
        portfolio_provider: PortfolioProvider | None = None,
    ) -> None:
        self.config = config
        # Lazy because the executor's portfolio is configured *after* the
        # orchestrator (and this section) is constructed. The provider is
        # called inside ``run()`` so it sees the live store.
        self._portfolio_provider = portfolio_provider

    def run(self, input: SectionInput) -> SectionOutput:
        res = input.payload
        if not isinstance(res, AnalysisResult):
            return SectionOutput(payload=None, verdict="skip", reason="no analysis upstream")

        side: Side = "yes" if res.p_model >= 0.5 else "no"
        if self.config.side_lock and side != "yes":
            return SectionOutput(payload=None, verdict="skip", reason="side_lock active")

        # Portfolio-aware gates — cheapest first. Only fetch the portfolio
        # when at least one gate is enabled (keeps the default config from
        # touching the DB at all, which contract tests rely on).
        needs_portfolio = (
            self.config.heat_cap_usd > 0
            or self.config.same_market_lifetime_lockout
            or self.config.same_market_cooldown_minutes > 0
            or self.config.kill_max_consecutive_losses > 0
            or self.config.kill_daily_loss_usd > 0
            or self.config.kill_max_drawdown_usd > 0
        )
        portfolio = (
            self._portfolio_provider() if needs_portfolio and self._portfolio_provider else None
        )
        if portfolio is not None:
            # heat_cap: portfolio-wide ceiling. One get_open_positions call,
            # only sums the currently-open set, returns fast.
            cap_usd = self.config.heat_cap_usd
            if cap_usd > 0:
                opens = portfolio.get_open_positions()
                open_cost = sum(h.qty * h.avg_entry_price for h in opens)
                if open_cost >= cap_usd:
                    return SectionOutput(
                        payload=None,
                        verdict="skip",
                        reason="heat_cap",
                        signals={
                            "side": side,
                            "open_cost": round(open_cost, 2),
                            "heat_cap_usd": cap_usd,
                            "open_position_count": len(opens),
                        },
                    )

            # A4 kill switch: portfolio-wide circuit breakers from realized
            # PnL history. Fires before per-market lockout because a tripped
            # brake is a system-level stop — no need to scan further.
            kill_skip = _kill_switch_check(portfolio, self.config, now=None)
            if kill_skip is not None:
                reason, signals = kill_skip
                return SectionOutput(
                    payload=None,
                    verdict="skip",
                    reason=reason,
                    signals={"side": side, **signals},
                )

            # Lockout / cooldown: per-(market, side) gate. Lifetime lockout
            # supersedes the time-window cooldown when enabled.
            if self.config.same_market_lifetime_lockout:
                if _market_side_has_history(portfolio, res.market_id, side):
                    return SectionOutput(
                        payload=None,
                        verdict="skip",
                        reason="same_market_lockout",
                        signals={"side": side},
                    )
            elif self.config.same_market_cooldown_minutes > 0:
                cooldown_min = self.config.same_market_cooldown_minutes
                if _in_cooldown(portfolio, res.market_id, side, cooldown_min):
                    return SectionOutput(
                        payload=None,
                        verdict="skip",
                        reason="same_market_cooldown",
                        signals={
                            "side": side,
                            "cooldown_minutes": cooldown_min,
                        },
                    )

        catalog = market_source_manager.store
        market = catalog.get(res.market_id)
        if market is None:
            return SectionOutput(payload=None, verdict="skip", reason="market not found")
        token_id = market.yes_token_id if side == "yes" else market.no_token_id
        if token_id is None:
            return SectionOutput(payload=None, verdict="skip", reason="no token for side")

        book = catalog.get_order_book(token_id)
        if book is None or not book.asks or not book.bids:
            return SectionOutput(payload=None, verdict="skip", reason="no order book")
        held_price = book.asks[0][0]
        if held_price <= 0.0:
            return SectionOutput(payload=None, verdict="skip", reason="invalid ask price")

        spread = held_price - book.bids[0][0]
        fair = res.p_model if side == "yes" else 1.0 - res.p_model
        edge = fair - held_price
        signals = {
            "side": side,
            "edge": round(edge, 4),
            "spread": round(spread, 4),
            "p_model": res.p_model,
            "held_price": held_price,
        }

        if edge < self.config.min_edge:
            return SectionOutput(
                payload=None,
                verdict="skip",
                reason="edge below min_edge",
                signals=signals,
            )
        if spread > self.config.max_spread:
            return SectionOutput(
                payload=None,
                verdict="skip",
                reason="spread above max_spread",
                signals=signals,
            )

        # Late-buy veto: if the held side's token has already run up over the
        # recent window, the fast-news alpha is gone. recent_move fails open
        # (None on missing data) so the veto never fires on a fetch failure.
        if self.config.veto_enabled:
            move = recent_move(token_id, window_min=self.config.veto_window_min)
            if move is not None:
                signals["recent_move"] = round(move, 4)
                if move >= self.config.veto_move_threshold:
                    return SectionOutput(
                        payload=None,
                        verdict="skip",
                        reason="late buy",
                        signals=signals,
                    )

        qty = self.config.order_size_usd / held_price
        intent = OrderIntent(market_id=res.market_id, side=side, price=held_price, qty=qty)
        return SectionOutput(payload=intent, verdict="ok", signals=signals)

    @staticmethod
    def CONTRACT_TEST() -> None:
        inst = EdgeThresholdEntryV0(EdgeThresholdConfig())

        out_none = inst.run(SectionInput(tick_type="event", payload=None))
        assert out_none.verdict == "skip"

        # A synthetic market_id is never in the live catalog, so this skips at
        # the market lookup — registry scan stays light, touches no DB.
        res = AnalysisResult(market_id="__contract_test__", p_model=0.6, confidence="medium")
        out_no_market = inst.run(SectionInput(tick_type="event", payload=res))
        assert out_no_market.verdict == "skip"


def _in_cooldown(
    portfolio: "PortfolioStore",
    market_id: str,
    side: Side,
    cooldown_minutes: int,
    now: float | None = None,
) -> bool:
    """True iff the most recent position on (market_id, side) was opened OR
    closed within ``cooldown_minutes``. Reads a bounded slice of the
    position table (newest 500); at paper-scale (~tens of positions/day) the
    most-recent-for-this-market is always inside this window."""
    cutoff_ts = (now if now is not None else time.time()) - cooldown_minutes * 60
    for pos in portfolio.list_positions(limit=500):
        if pos.market_id != market_id or pos.side != side:
            continue
        # Use the most recent stamp this position has (closed if closed, else
        # opened). closed_at can be None for open positions.
        ref_ts = pos.closed_at if pos.closed_at is not None else pos.opened_at
        if ref_ts > cutoff_ts:
            return True
        # list_positions is newest-first; the first matching row is the most
        # recent, so once we see a match older than the cutoff we can stop.
        return False
    return False


def _market_side_has_history(
    portfolio: "PortfolioStore",
    market_id: str,
    side: Side,
) -> bool:
    """True iff any prior position on (market_id, side) exists in the position
    table — open or closed, no time window. Backs the strict one-shot
    ``same_market_lifetime_lockout`` mode. Reads a bounded slice (newest 500)
    same as ``_in_cooldown``; lifetime-scale lookback at paper trade volume."""
    for pos in portfolio.list_positions(limit=500):
        if pos.market_id == market_id and pos.side == side:
            return True
    return False


def _kill_switch_check(
    portfolio: "PortfolioStore",
    config: "EdgeThresholdConfig",
    *,
    now: float | None = None,
) -> tuple[str, dict] | None:
    """A4 portfolio-wide circuit breakers — returns (reason, signals) on
    first trip, else None. Reads the same bounded position slice as
    ``_in_cooldown`` (newest 500); at paper / grain-scale trade volume that easily
    spans weeks of history. Each brake is independently opt-in via the
    matching kill_* config field; the first one tripped wins (consecutive
    → daily → drawdown), no full scan after a hit."""
    positions = portfolio.list_positions(limit=500)
    closed = [p for p in positions if p.closed_at is not None and p.realized_pnl is not None]
    if not closed:
        return None
    now_ts = now if now is not None else time.time()

    # 1. Consecutive losses: walk newest → first non-loss. closed is
    #    newest-first per list_positions contract, so the streak from index 0
    #    is the live tail.
    if config.kill_max_consecutive_losses > 0:
        streak = 0
        for p in closed:
            if p.realized_pnl < 0:
                streak += 1
            else:
                break
        if streak >= config.kill_max_consecutive_losses:
            return (
                "kill_consecutive_losses",
                {"streak": streak, "limit": config.kill_max_consecutive_losses},
            )

    # 2. Daily loss: sum realized PnL across positions closed in last 24h.
    if config.kill_daily_loss_usd > 0:
        cutoff = now_ts - 86400.0
        daily_pnl = sum(p.realized_pnl for p in closed if p.closed_at >= cutoff)
        if daily_pnl <= -config.kill_daily_loss_usd:
            return (
                "kill_daily_loss",
                {
                    "daily_pnl_usd": round(daily_pnl, 2),
                    "limit_usd": config.kill_daily_loss_usd,
                },
            )

    # 3. Peak-to-trough drawdown across all closed history. Walk chronological
    #    (reverse the newest-first slice), keep running cum + peak.
    if config.kill_max_drawdown_usd > 0:
        cum = 0.0
        peak = 0.0
        for p in reversed(closed):
            cum += p.realized_pnl
            if cum > peak:
                peak = cum
        drawdown = peak - cum
        if drawdown >= config.kill_max_drawdown_usd:
            return (
                "kill_drawdown",
                {
                    "drawdown_usd": round(drawdown, 2),
                    "peak_usd": round(peak, 2),
                    "limit_usd": config.kill_max_drawdown_usd,
                },
            )

    return None
