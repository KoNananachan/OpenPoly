from __future__ import annotations

from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.exit.threshold_v0 import (
    CloseIntent,
    MarkedPosition,
    ThresholdExitConfig,
    ThresholdExitV0,
)


def _pos(
    current_price: float,
    avg_entry_price: float = 0.50,
    *,
    peak_price: float | None = None,
    qty: float = 20.0,
) -> MarkedPosition:
    return MarkedPosition(
        market_id="m1",
        side="yes",
        avg_entry_price=avg_entry_price,
        qty=qty,
        current_price=current_price,
        peak_price=current_price if peak_price is None else peak_price,
    )


def test_exit_in_default_catalog() -> None:
    entries = scan()
    matches = [e for e in entries if e.name == "ThresholdExitV0"]
    assert len(matches) == 1
    assert matches[0].type == "exit"


def test_run_no_position_skips() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    out = inst.run(SectionInput(tick_type="hard", payload=None))
    assert out.verdict == "skip"
    assert out.reason == "no position upstream"


def test_within_thresholds_holds() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.52)))
    assert out.verdict == "skip"
    assert out.reason == "within thresholds"
    assert out.signals["return_pct"] == 0.04


def test_take_profit_closes() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.65)))
    assert out.verdict == "ok"
    assert isinstance(out.payload, CloseIntent)
    assert out.payload.trigger == "take_profit"
    assert out.payload.market_id == "m1"
    assert out.payload.side == "yes"
    assert out.payload.qty == 20.0
    assert out.payload.price == 0.65
    assert out.signals["trigger"] == "take_profit"


def test_stop_loss_closes() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.40)))
    assert out.verdict == "ok"
    assert isinstance(out.payload, CloseIntent)
    assert out.payload.trigger == "stop_loss"
    assert out.payload.price == 0.40


def test_invalid_entry_price_skips() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.5, avg_entry_price=0.0)))
    assert out.verdict == "skip"
    assert out.reason == "invalid avg_entry_price"


def test_custom_thresholds() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig(take_profit_pct=0.05, stop_loss_pct=0.05))
    # +6% return → take-profit at the lowered 5% threshold
    tp = inst.run(SectionInput(tick_type="hard", payload=_pos(0.53)))
    assert tp.verdict == "ok"
    assert isinstance(tp.payload, CloseIntent)
    assert tp.payload.trigger == "take_profit"
    # -6% return → stop-loss at the lowered 5% threshold
    sl = inst.run(SectionInput(tick_type="hard", payload=_pos(0.47)))
    assert sl.verdict == "ok"
    assert isinstance(sl.payload, CloseIntent)
    assert sl.payload.trigger == "stop_loss"


def test_no_side_position_return_is_side_agnostic() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    pos = MarkedPosition(
        market_id="m2",
        side="no",
        avg_entry_price=0.30,
        qty=10.0,
        current_price=0.40,
        peak_price=0.40,
    )
    out = inst.run(SectionInput(tick_type="hard", payload=pos))
    # (0.40 - 0.30) / 0.30 = 0.333 ≥ 0.20 → take_profit; held side carried through
    assert out.verdict == "ok"
    assert isinstance(out.payload, CloseIntent)
    assert out.payload.trigger == "take_profit"
    assert out.payload.side == "no"


# ---------- peak drawdown ----------


def test_peak_drawdown_triggers_when_meaningful_retrace() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    # Peak 0.62 (+24%, peak_gain = $2.40 ≥ floor); now 0.58 (+16% so under TP).
    # peak_dd = (0.62 - 0.58) / (0.62 - 0.50) = 0.04 / 0.12 = 0.333 ≥ 0.12.
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.58, peak_price=0.62)))
    assert out.verdict == "ok"
    assert isinstance(out.payload, CloseIntent)
    assert out.payload.trigger == "peak_drawdown"
    assert out.signals["peak_meaningful"] is True
    assert out.signals["peak_dd"] == 0.3333


def test_peak_drawdown_skipped_when_peak_below_usd_floor() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    # qty 2 so even a +30% peak only banks $0.30 < $1 floor.
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.55, peak_price=0.65, qty=2.0)))
    # +10% return < TP 20%, no SL → within thresholds despite the retrace.
    assert out.verdict == "skip"
    assert out.reason == "within thresholds"
    assert out.signals["peak_meaningful"] is False


def test_peak_drawdown_skipped_when_peak_below_pct_floor() -> None:
    # qty 200 → cost basis $100. peak +0.5pt = $1 bank, but 1% floor = $1 too,
    # so peak_gain = 1.0 not strictly > floor → meaningful=True at exactly 1.0.
    # Bump qty to 1000 (cost $500): 1% floor = $5, peak +0.4pt = $4 < $5 → skip.
    inst = ThresholdExitV0(ThresholdExitConfig())
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.51, peak_price=0.504, qty=1000.0)))
    assert out.verdict == "skip"
    assert out.signals["peak_meaningful"] is False


def test_stop_loss_beats_peak_drawdown() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    # Peak 0.65 then crashed to 0.40 (-20% return). Both SL (-20% ≤ -15%) and
    # peak_dd ((0.65-0.40)/(0.65-0.50) = 1.67) fire; SL wins per precedence.
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.40, peak_price=0.65)))
    assert out.verdict == "ok"
    assert isinstance(out.payload, CloseIntent)
    assert out.payload.trigger == "stop_loss"


def test_peak_drawdown_beats_take_profit() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    # Peak 0.80 (+60%), now 0.60 (+20%, exactly at TP). peak_dd 20/30 = 67%.
    # Without peak tracking this would close as TP; with it, the trailing
    # retrace wins.
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.60, peak_price=0.80)))
    assert out.verdict == "ok"
    assert isinstance(out.payload, CloseIntent)
    assert out.payload.trigger == "peak_drawdown"


def test_peak_below_entry_does_not_trigger_peak_dd() -> None:
    inst = ThresholdExitV0(ThresholdExitConfig())
    # Peak never went above entry — peak_dd undefined; section must skip
    # (not divide by zero or trigger spuriously).
    out = inst.run(SectionInput(tick_type="hard", payload=_pos(0.48, peak_price=0.49)))
    assert out.verdict == "skip"
    assert out.signals["peak_meaningful"] is False
    assert out.signals["peak_dd"] is None
