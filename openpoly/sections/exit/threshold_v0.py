"""Threshold exit baseline — take-profit / stop-loss / peak-drawdown.

Atomized from a prior project v8 §10: Rule 6 (take_profit), Rule 2 (max_loss) and a
*lightweight* Rule 3 (peak_drawdown). openPoly tracks ``peak_price`` as a
scalar but deliberately drops ``peak_exit_cost`` (the cost-adjusted form) and
the V6 trailing_stop / LLM consult on top — see CLAUDE.md for the scoping.

The section is a pure function of its input ``MarkedPosition``: the runtime
injects the held side's current price *and* its tracked peak price into the
position before each call. Peak tracking itself lives in ``ExitMonitor`` —
the section never holds state across ticks.

Trigger precedence is ``stop_loss → peak_drawdown → take_profit`` (a prior project's
v6 ordering): the absolute-loss circuit fires first, then the trailing lock
on banked gains, with the absolute take-profit ceiling as the final fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from openpoly.sections._base import SectionInput, SectionOutput


Side = Literal["yes", "no"]
Trigger = Literal["take_profit", "stop_loss", "peak_drawdown"]


@dataclass(frozen=True)
class MarkedPosition:
    """An open position the exit section evaluates. ``avg_entry_price``,
    ``current_price`` and ``peak_price`` are all prices of the held ``side``
    (Polymarket token price in 0..1), so return math is side-agnostic. The
    monitor injects ``peak_price`` from its per-position max — see
    ``ExitMonitor`` — and the section uses it for peak-drawdown only.
    """

    market_id: str
    side: Side
    avg_entry_price: float
    qty: float
    current_price: float
    peak_price: float


@dataclass(frozen=True)
class CloseIntent:
    """A decision to close (sell) an open position. ``trigger`` records which
    threshold fired."""

    market_id: str
    side: Side
    price: float
    qty: float
    trigger: Trigger


class ThresholdExitConfig(BaseModel):
    take_profit_pct: float = Field(
        default=0.20,
        ge=0.0,
        le=10.0,
        description="Close the position when its return reaches this fraction (0.20 = +20%).",
    )
    stop_loss_pct: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Close the position when its loss reaches this fraction (0.15 = -15%).",
    )
    peak_drawdown_pct: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        description="Close when the gain has retraced this fraction from peak (0.12 = 12%).",
    )
    peak_meaningful_floor_usd: float = Field(
        default=1.0,
        ge=0.0,
        description="Skip peak_drawdown unless the peak gain in USD exceeds this floor.",
    )
    peak_meaningful_floor_pct: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Skip peak_drawdown unless the peak gain exceeds this fraction of cost basis.",
    )


class ThresholdExitV0:
    SECTION_TYPE = "exit"
    SECTION_VERSION = "0.2.0"
    REQUIRES = ["market_data", "portfolio"]
    Config = ThresholdExitConfig

    def __init__(self, config: ThresholdExitConfig) -> None:
        self.config = config

    def run(self, input: SectionInput) -> SectionOutput:
        pos = input.payload
        if not isinstance(pos, MarkedPosition):
            return SectionOutput(payload=None, verdict="skip", reason="no position upstream")
        if pos.avg_entry_price <= 0:
            return SectionOutput(payload=None, verdict="skip", reason="invalid avg_entry_price")

        return_pct = (pos.current_price - pos.avg_entry_price) / pos.avg_entry_price

        cost_basis = pos.avg_entry_price * pos.qty
        peak_gain_usd = (pos.peak_price - pos.avg_entry_price) * pos.qty
        floor = max(
            self.config.peak_meaningful_floor_usd,
            self.config.peak_meaningful_floor_pct * cost_basis,
        )
        peak_meaningful = pos.peak_price > pos.avg_entry_price and peak_gain_usd >= floor
        if peak_meaningful:
            peak_dd = (pos.peak_price - pos.current_price) / (pos.peak_price - pos.avg_entry_price)
        else:
            peak_dd = 0.0

        trigger: Trigger | None
        if return_pct <= -self.config.stop_loss_pct:
            trigger = "stop_loss"
        elif peak_meaningful and peak_dd >= self.config.peak_drawdown_pct:
            trigger = "peak_drawdown"
        elif return_pct >= self.config.take_profit_pct:
            trigger = "take_profit"
        else:
            trigger = None

        signals: dict[str, object] = {
            "return_pct": round(return_pct, 4),
            "peak_price": round(pos.peak_price, 4),
            "peak_dd": round(peak_dd, 4) if peak_meaningful else None,
            "peak_meaningful": peak_meaningful,
        }

        if trigger is None:
            return SectionOutput(
                payload=None,
                verdict="skip",
                reason="within thresholds",
                signals=signals,
            )

        intent = CloseIntent(
            market_id=pos.market_id,
            side=pos.side,
            price=pos.current_price,
            qty=pos.qty,
            trigger=trigger,
        )
        signals["trigger"] = trigger
        return SectionOutput(
            payload=intent,
            verdict="ok",
            reason=trigger,
            signals=signals,
        )

    @staticmethod
    def CONTRACT_TEST() -> None:
        inst = ThresholdExitV0(ThresholdExitConfig())

        out_skip = inst.run(SectionInput(tick_type="hard", payload=None))
        assert out_skip.verdict == "skip"

        hold = MarkedPosition(
            market_id="m1",
            side="yes",
            avg_entry_price=0.50,
            qty=20.0,
            current_price=0.52,
            peak_price=0.52,
        )
        out_hold = inst.run(SectionInput(tick_type="hard", payload=hold))
        assert out_hold.verdict == "skip"

        win = MarkedPosition(
            market_id="m1",
            side="yes",
            avg_entry_price=0.50,
            qty=20.0,
            current_price=0.65,
            peak_price=0.65,
        )
        out_tp = inst.run(SectionInput(tick_type="hard", payload=win))
        assert out_tp.verdict == "ok"
        assert isinstance(out_tp.payload, CloseIntent)
        assert out_tp.payload.trigger == "take_profit"

        loss = MarkedPosition(
            market_id="m1",
            side="yes",
            avg_entry_price=0.50,
            qty=20.0,
            current_price=0.40,
            peak_price=0.52,
        )
        out_sl = inst.run(SectionInput(tick_type="hard", payload=loss))
        assert out_sl.verdict == "ok"
        assert out_sl.payload.trigger == "stop_loss"

        # Peak drawdown: ran up to 0.62 (+24% peak gain $2.40 ≥ floor),
        # now back to 0.58 → retrace 4/12 = 33% > 12% threshold; not yet
        # at take_profit (+16% < 20%).
        retrace = MarkedPosition(
            market_id="m1",
            side="yes",
            avg_entry_price=0.50,
            qty=20.0,
            current_price=0.58,
            peak_price=0.62,
        )
        out_pd = inst.run(SectionInput(tick_type="hard", payload=retrace))
        assert out_pd.verdict == "ok"
        assert out_pd.payload.trigger == "peak_drawdown"
