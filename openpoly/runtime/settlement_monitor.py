"""Settlement monitor — closes resolved-market positions at 0/1 final price.

Slice E. When a Polymarket market resolves, ``outcomePrices`` is stamped on
the Gamma market record. The exit-monitor doesn't catch this because the
resolved market typically drops out of the discovery catalog (Gamma's
``/events`` is filtered to ``closed=false``), so any open position on it
gets stuck at ``status=open``.

This monitor sweeps periodically (default every 5 min — settlement is slow,
no need to poll fast), groups open positions by ``condition_id``, fetches
those specific markets via the dedicated ``fetch_markets_by_condition_id``
path (which does NOT pass ``closed=false``), and for any resolved one calls
``PortfolioStore.close_position(reason="settlement", sell_price=0_or_1)``
directly — no broker tx, no CLOB call, no on-chain redemption.

CTF redemption (winning tokens → pUSD on the DepositWallet) is a separate
on-chain action that is **out of slice E scope** — slice C V2 pivot
corrigendum §C.SliceE.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Awaitable, Literal, Protocol

from openpoly.markets.models import normalize_gamma_market
from openpoly.markets.polymarket_api import fetch_markets_by_condition_id
from openpoly.portfolio import HeldPosition, PortfolioStore
from openpoly.runtime.section_log import SettlementDecision, settlement_log

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 300  # 5 min — settlement isn't latency-sensitive

State = Literal["stopped", "running"]


class _MarketFetcher(Protocol):
    """Async callable: (condition_ids: list[str]) -> list[raw_market_dict]."""

    def __call__(self, condition_ids: list[str]) -> Awaitable[list[dict]]: ...


def _settlement_price_for_side(outcome_prices: tuple[float, float], side: str) -> float | None:
    """Map Gamma's resolved ``outcomePrices`` to a 0/1 final price for the
    held side.

    Polymarket exposes outcomes as YES=index 0 / NO=index 1, with prices
    summing to 1 once resolved (``[1, 0]`` YES wins, ``[0, 1]`` NO wins).
    Returns None when the resolution is ambiguous (e.g. ``[0.5, 0.5]``
    after a disputed market goes to split) — caller skips and waits.
    """
    yes_price, no_price = outcome_prices
    # Only accept clean 0/1 outcomes; anything else (split / unresolved)
    # means downstream PnL math would be unreliable.
    if {round(yes_price, 4), round(no_price, 4)} != {0.0, 1.0}:
        return None
    if side == "yes":
        return float(yes_price)
    if side == "no":
        return float(no_price)
    return None


class SettlementMonitor:
    """Timer-driven loop that closes resolved-market positions at 0/1."""

    def __init__(
        self,
        *,
        fetcher: _MarketFetcher = fetch_markets_by_condition_id,
        tick_interval_seconds: int = DEFAULT_TICK_INTERVAL_SECONDS,
    ) -> None:
        self._fetcher = fetcher
        self._tick_interval = tick_interval_seconds
        self._portfolio: PortfolioStore | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._state: State = "stopped"

    @property
    def state(self) -> State:
        return self._state

    def configure(self, portfolio: PortfolioStore) -> None:
        """Inject PortfolioStore — FastAPI lifespan calls once DB is up."""
        self._portfolio = portfolio

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._state = "running"
        # Recreate the Event each start so it binds to the current loop —
        # this module singleton may be start()ed across distinct loops in tests.
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._task is None:
            self._state = "stopped"
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._state = "stopped"

    # ---------- loop ----------

    async def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick_once()
            except Exception:  # noqa: BLE001 — loop must survive any tick error
                logger.exception("settlement monitor: tick failed")
            await asyncio.sleep(0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_interval)

    # ---------- tick ----------

    async def _tick_once(self) -> None:
        if self._portfolio is None:
            return
        opens = self._portfolio.get_open_positions()
        if not opens:
            return

        # Group positions by condition_id so the same Gamma row resolves all
        # positions on that market in one pass (e.g. YES + NO opened in
        # different cycles still share one condition_id).
        by_cid: dict[str, list[HeldPosition]] = {}
        for held in opens:
            by_cid.setdefault(held.condition_id, []).append(held)

        try:
            raw_markets = await self._fetcher(list(by_cid))
        except Exception as exc:  # noqa: BLE001 — network errors must not crash loop
            logger.warning("settlement monitor: Gamma fetch failed: %s", exc)
            # Log one error entry per market we couldn't check, so the lag
            # surfaces in observability rather than disappearing silently.
            ts = time.time()
            for held_list in by_cid.values():
                for held in held_list:
                    self._log(
                        held,
                        ts,
                        verdict="error",
                        error=f"gamma_fetch_failed: {type(exc).__name__}",
                    )
            return

        # Index returned markets by condition_id; some requested may be absent
        # (Gamma occasionally returns a partial list) — those just get a skip.
        returned_cids: set[str] = set()
        for raw in raw_markets:
            cid = raw.get("conditionId") or raw.get("condition_id")
            if not cid:
                continue
            returned_cids.add(str(cid))
            self._process_market(raw, by_cid.get(str(cid), []))

        # Positions whose market Gamma did not return — likely transient;
        # log a skip so the gap is visible.
        ts = time.time()
        for cid, held_list in by_cid.items():
            if cid not in returned_cids:
                for held in held_list:
                    self._log(
                        held,
                        ts,
                        verdict="skip",
                        reason="market_not_returned_by_gamma",
                    )

    def _process_market(self, raw: dict, held_positions: list[HeldPosition]) -> None:
        ts = time.time()
        # Reuse the existing parser for consistency; outcome_prices is the
        # new (slice E) field we rely on here.
        market = normalize_gamma_market(raw, event=None)
        if market is None:
            for held in held_positions:
                self._log(
                    held,
                    ts,
                    verdict="error",
                    error="market_normalize_failed",
                )
            return

        if not market.closed:
            for held in held_positions:
                self._log(held, ts, verdict="skip", reason="still_trading")
            return

        if market.outcome_prices is None:
            for held in held_positions:
                self._log(
                    held,
                    ts,
                    verdict="skip",
                    reason="no_outcome_prices",
                )
            return

        for held in held_positions:
            final_price = _settlement_price_for_side(market.outcome_prices, held.side)
            if final_price is None:
                self._log(
                    held,
                    ts,
                    verdict="skip",
                    reason="ambiguous_outcome",
                )
                continue
            try:
                self._portfolio.close_position(  # type: ignore[union-attr]
                    held.position_id,
                    sell_price=final_price,
                    ts=ts,
                    close_reason="settlement",
                    trigger="settlement",
                    order_id=None,
                    tx_hash=None,
                )
            except Exception as exc:  # noqa: BLE001 — one bad close must not abort
                logger.exception(
                    "settlement monitor: close_position failed for %d",
                    held.position_id,
                )
                self._log(
                    held,
                    ts,
                    verdict="error",
                    final_price=final_price,
                    error=f"close_failed: {type(exc).__name__}: {str(exc)[:160]}",
                )
                continue
            realized = (final_price - held.avg_entry_price) * held.qty
            self._log(
                held,
                ts,
                verdict="ok",
                final_price=final_price,
                realized_pnl=realized,
                reason="settlement",
            )
            logger.info(
                "settled position %d: %s %s qty=%.4f @ %.4f realized=%+.4f",
                held.position_id,
                held.market_id,
                held.side,
                held.qty,
                final_price,
                realized,
            )

    def _log(
        self,
        held: HeldPosition,
        ts: float,
        *,
        verdict: str,
        final_price: float | None = None,
        realized_pnl: float | None = None,
        reason: str | None = None,
        error: str | None = None,
    ) -> None:
        settlement_log.append(
            SettlementDecision(
                ts=ts,
                position_id=held.position_id,
                market_id=held.market_id,
                side=held.side,
                verdict=verdict,  # type: ignore[arg-type]
                final_price=final_price,
                realized_pnl=realized_pnl,
                reason=reason,
                error=error,
            )
        )


# Module-level singleton — FastAPI lifespan calls configure() + start().
settlement_monitor = SettlementMonitor()
