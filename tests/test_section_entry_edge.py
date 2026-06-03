"""Tests for EdgeThresholdEntryV0 — the entry decision section (PF4).

The section reads the live ``MarketStore`` singleton, so each test gets a fresh
catalog via the autouse fixture and populates it with a market + order book.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass

import pytest

from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import OrderBook, normalize_gamma_market
from openpoly.markets.store import MarketStore, PollSummary
from openpoly.portfolio import PositionRecord
from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.analyzer.llm_v0 import AnalysisResult
from openpoly.sections.entry import edge_threshold_v0
from openpoly.sections.entry.edge_threshold_v0 import (
    EdgeThresholdConfig,
    EdgeThresholdEntryV0,
    OrderIntent,
)


@pytest.fixture(autouse=True)
def _isolate_market_store():
    """Each test gets a fresh market catalog singleton."""
    saved = market_source_manager.store
    market_source_manager.store = MarketStore()
    yield
    market_source_manager.store = saved


def _market(market_id: str = "m1", *, clob: str | None = None):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": "Will X happen?",
        "clobTokenIds": clob or f'["yes-{market_id}", "no-{market_id}"]',
    }
    m = normalize_gamma_market(raw, event={"id": "e1", "title": "E", "tags": []})
    assert m is not None
    return m


def _book(token_id: str, bid: float, ask: float) -> OrderBook:
    return OrderBook(token_id=token_id, ts=1.0, bids=[(bid, 100.0)], asks=[(ask, 100.0)])


def _populate(market, *books: OrderBook) -> None:
    store = market_source_manager.store
    store.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))
    store.set_order_books(list(books))


def _ar(market_id: str = "m1", p_model: float = 0.7) -> AnalysisResult:
    return AnalysisResult(market_id=market_id, p_model=p_model, confidence="medium")


def _run(inst: EdgeThresholdEntryV0, payload: object):
    return inst.run(SectionInput(tick_type="event", payload=payload))


# ---------- catalog / basic skips ----------


def test_entry_in_default_catalog() -> None:
    matches = [e for e in scan() if e.name == "EdgeThresholdEntryV0"]
    assert len(matches) == 1
    assert matches[0].type == "entry"


def test_no_analysis_skips() -> None:
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), None)
    assert out.verdict == "skip"


def test_market_not_found_skips() -> None:
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar("missing"))
    assert out.verdict == "skip"
    assert out.reason == "market not found"


def test_no_order_book_skips() -> None:
    _populate(_market())  # market in catalog, no order books sampled yet
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar())
    assert out.verdict == "skip"
    assert out.reason == "no order book"


# ---------- side selection ----------


def test_picks_yes_when_p_high() -> None:
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar(p_model=0.7))
    assert out.verdict == "ok"
    assert isinstance(out.payload, OrderIntent)
    assert out.payload.side == "yes"
    assert out.payload.price == 0.42


def test_picks_no_when_p_low() -> None:
    # NO token has its own book; p_model 0.3 -> fair(no) 0.7 -> edge 0.28.
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar(p_model=0.3))
    assert out.verdict == "ok"
    assert out.payload.side == "no"


def test_side_lock_blocks_no() -> None:
    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(side_lock=True))
    out = _run(inst, _ar(p_model=0.3))
    assert out.verdict == "skip"
    assert out.reason == "side_lock active"


def test_no_token_for_side_skips() -> None:
    # clobTokenIds carries only the YES token -> no_token_id is None.
    _populate(_market(clob='["yes-m1"]'), _book("yes-m1", bid=0.40, ask=0.42))
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar(p_model=0.3))
    assert out.verdict == "skip"
    assert out.reason == "no token for side"


# ---------- edge / spread gates ----------


def test_edge_below_min_edge_skips() -> None:
    # YES ask 0.68, p_model 0.70 -> edge 0.02 < min_edge 0.05.
    _populate(_market(), _book("yes-m1", bid=0.66, ask=0.68))
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar(p_model=0.70))
    assert out.verdict == "skip"
    assert out.reason == "edge below min_edge"
    assert out.signals["edge"] == pytest.approx(0.02)


def test_spread_above_max_spread_skips() -> None:
    # Edge fine (0.28) but spread 0.20 > max_spread 0.05.
    _populate(_market(), _book("yes-m1", bid=0.22, ask=0.42))
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar(p_model=0.70))
    assert out.verdict == "skip"
    assert out.reason == "spread above max_spread"


# ---------- ok path / sizing ----------


def test_ok_emits_order_intent_sized_by_usd() -> None:
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(order_size_usd=21.0))
    out = _run(inst, _ar(p_model=0.7))
    assert out.verdict == "ok"
    assert out.payload.market_id == "m1"
    assert out.payload.price == 0.42
    assert out.payload.qty == pytest.approx(21.0 / 0.42)
    assert out.signals["edge"] == pytest.approx(0.28)
    assert out.signals["spread"] == pytest.approx(0.02)


# ---------- late-buy veto (L5) ----------


def test_veto_disabled_by_default_does_not_consult_recent_move(
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        edge_threshold_v0,
        "recent_move",
        lambda token_id, *, window_min: calls.append(token_id) or 0.99,
    )
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    out = _run(EdgeThresholdEntryV0(EdgeThresholdConfig()), _ar(p_model=0.7))
    assert out.verdict == "ok"
    assert calls == []  # veto off → recent_move never called


def test_veto_skips_on_large_move(monkeypatch) -> None:
    monkeypatch.setattr(
        edge_threshold_v0,
        "recent_move",
        lambda token_id, *, window_min: 0.15,  # >= 0.10 threshold
    )
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(veto_enabled=True))
    out = _run(inst, _ar(p_model=0.7))
    assert out.verdict == "skip"
    assert out.reason == "late buy"
    assert out.signals["recent_move"] == pytest.approx(0.15)


def test_veto_passes_on_small_move(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake(token_id: str, *, window_min: int) -> float:
        calls.append((token_id, window_min))
        return 0.03  # < 0.10 threshold

    monkeypatch.setattr(edge_threshold_v0, "recent_move", fake)
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(veto_enabled=True, veto_window_min=45))
    out = _run(inst, _ar(p_model=0.7))
    assert out.verdict == "ok"
    assert out.signals["recent_move"] == pytest.approx(0.03)
    # The veto consults the held side's token over the configured window.
    assert calls == [("yes-m1", 45)]


def test_veto_fails_open_on_no_data(monkeypatch) -> None:
    monkeypatch.setattr(
        edge_threshold_v0,
        "recent_move",
        lambda token_id, *, window_min: None,
    )
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(veto_enabled=True))
    out = _run(inst, _ar(p_model=0.7))
    assert out.verdict == "ok"


# ---------- same_market_cooldown ----------


from openpoly.portfolio import HeldPosition  # noqa: E402


@dataclass
class _FakePortfolio:
    """Stub portfolio store — only the bits the entry gates actually read."""

    records: list[PositionRecord]

    def list_positions(self, limit: int = 100) -> list[PositionRecord]:
        return list(self.records[:limit])

    def get_open_positions(self) -> list[HeldPosition]:
        # heat_cap reads avg_entry_price * qty across all currently-open.
        return [
            HeldPosition(
                position_id=r.id,
                market_id=r.market_id,
                side=r.side,
                token_id=r.token_id,
                condition_id=r.condition_id,
                qty=r.qty,
                avg_entry_price=r.avg_entry_price,
                opened_at=r.opened_at,
            )
            for r in self.records
            if r.status == "open"
        ]


def _rec(
    market_id: str,
    side: str,
    opened_at: float,
    *,
    closed_at: float | None = None,
    position_id: int = 1,
) -> PositionRecord:
    return PositionRecord(
        id=position_id,
        market_id=market_id,
        side=side,  # type: ignore[arg-type]
        token_id=f"{side}-{market_id}",
        condition_id=f"0x{market_id}",
        qty=10.0,
        avg_entry_price=0.40,
        status="closed" if closed_at else "open",
        opened_at=opened_at,
        closed_at=closed_at,
        close_reason="stop_loss" if closed_at else None,
        realized_pnl=-1.50 if closed_at else None,
    )


def test_cooldown_disabled_by_default_no_portfolio_read() -> None:
    """Default config (cooldown_minutes=0) → portfolio_provider never invoked
    even when supplied."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    called = {"count": 0}

    def provider() -> _FakePortfolio:
        called["count"] += 1
        return _FakePortfolio([])

    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(), portfolio_provider=provider)
    out = _run(inst, _ar(p_model=0.30))  # NO side
    assert out.verdict == "ok"
    assert called["count"] == 0


def test_cooldown_blocks_recent_close_same_market_side() -> None:
    """Recently-closed position on (market, side) → cooldown skip, no executor."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    recent_closed = _rec(
        "m1",
        "no",
        opened_at=now - 30 * 60,
        closed_at=now - 10 * 60,
    )
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(same_market_cooldown_minutes=30),
        portfolio_provider=lambda: _FakePortfolio([recent_closed]),
    )
    out = _run(inst, _ar(p_model=0.30))  # NO side
    assert out.verdict == "skip"
    assert out.reason == "same_market_cooldown"
    assert out.signals["cooldown_minutes"] == 30


def test_cooldown_passes_when_older_than_window() -> None:
    """Closed position outside cooldown window → entry proceeds."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    old_closed = _rec(
        "m1",
        "no",
        opened_at=now - 120 * 60,
        closed_at=now - 90 * 60,
    )
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(same_market_cooldown_minutes=30),
        portfolio_provider=lambda: _FakePortfolio([old_closed]),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_cooldown_ignores_other_market_and_other_side() -> None:
    """Recent positions on different (market, side) must not block."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    other = [
        _rec("m1", "yes", opened_at=now - 5 * 60, position_id=99),  # other side
        _rec("m2", "no", opened_at=now - 5 * 60, position_id=98),  # other market
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(same_market_cooldown_minutes=30),
        portfolio_provider=lambda: _FakePortfolio(other),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_cooldown_no_provider_is_noop_even_when_configured() -> None:
    """portfolio_provider=None — cooldown silently does nothing
    (preserves contract test + tests that don't wire portfolio)."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(same_market_cooldown_minutes=30),
        portfolio_provider=None,
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


# ---------- same_market_lifetime_lockout ----------


def test_lockout_blocks_any_prior_position_no_matter_how_old() -> None:
    """Lifetime lockout: even a 99-hour-old closed position on (market, side)
    blocks a new entry. Time window doesn't apply."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    very_old = _rec(
        "m1",
        "no",
        opened_at=_time.time() - 99 * 3600,
        closed_at=_time.time() - 98 * 3600,
    )
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(same_market_lifetime_lockout=True),
        portfolio_provider=lambda: _FakePortfolio([very_old]),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "same_market_lockout"


def test_lockout_supersedes_cooldown_minutes() -> None:
    """When both lockout=True and cooldown_minutes>0 are set, lockout wins —
    `same_market_lockout` reason, not `same_market_cooldown`."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    old = _rec(
        "m1",
        "no",
        opened_at=_time.time() - 10 * 3600,
        closed_at=_time.time() - 9 * 3600,
    )
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(
            same_market_lifetime_lockout=True,
            same_market_cooldown_minutes=30,  # would NOT trigger (9h > 30min)
        ),
        portfolio_provider=lambda: _FakePortfolio([old]),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "same_market_lockout"


def test_lockout_passes_when_no_prior_history() -> None:
    """No prior position on (market, side) → lockout lets the entry through."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    # Prior history on YES side, not NO — should not lockout NO.
    yes_only = _rec(
        "m1",
        "yes",
        opened_at=_time.time() - 3600,
        closed_at=_time.time() - 1800,
    )
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(same_market_lifetime_lockout=True),
        portfolio_provider=lambda: _FakePortfolio([yes_only]),
    )
    out = _run(inst, _ar(p_model=0.30))  # NO side
    assert out.verdict == "ok"


# ---------- heat_cap_usd ----------


def test_heat_cap_blocks_when_open_cost_at_or_above_cap() -> None:
    """Sum of qty * avg_entry_price across opens ≥ cap → skip."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    # Three open positions on other markets: 25*0.40 + 30*0.50 + 20*0.30 = $31
    opens = [
        _rec("other1", "yes", opened_at=_time.time() - 600, position_id=10),
        _rec("other2", "no", opened_at=_time.time() - 600, position_id=11),
        _rec("other3", "yes", opened_at=_time.time() - 600, position_id=12),
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(heat_cap_usd=10.0),  # 3 opens of $4 each = $12 ≥ $10
        portfolio_provider=lambda: _FakePortfolio(opens),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "heat_cap"
    assert out.signals["heat_cap_usd"] == 10.0
    assert out.signals["open_position_count"] == 3


def test_heat_cap_passes_when_under_cap() -> None:
    """Open cost below cap → entry proceeds."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    opens = [
        _rec("other1", "yes", opened_at=_time.time() - 600, position_id=10),
    ]  # one $4 open
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(heat_cap_usd=50.0),
        portfolio_provider=lambda: _FakePortfolio(opens),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_heat_cap_ignores_closed_positions() -> None:
    """Closed positions don't count toward open exposure."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    history = [
        _rec(
            "other1",
            "yes",
            opened_at=_time.time() - 7200,
            closed_at=_time.time() - 3600,
            position_id=10,
        ),  # closed
        _rec(
            "other2",
            "yes",
            opened_at=_time.time() - 7200,
            closed_at=_time.time() - 3600,
            position_id=11,
        ),  # closed
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(heat_cap_usd=1.0),  # tiny cap; closed shouldn't count
        portfolio_provider=lambda: _FakePortfolio(history),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_heat_cap_runs_before_lockout() -> None:
    """heat_cap should short-circuit before the lockout scan — cheaper gate
    fires first, so reason should be heat_cap when both would trigger."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    # Same-market prior (would trigger lockout) AND enough open cost (heat_cap)
    records = [
        _rec(
            "m1", "no", opened_at=_time.time() - 3600, closed_at=_time.time() - 1800, position_id=1
        ),  # would lockout
        _rec(
            "other", "yes", opened_at=_time.time() - 600, position_id=2
        ),  # open, contributes to heat
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(
            heat_cap_usd=1.0,
            same_market_lifetime_lockout=True,
        ),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "heat_cap"  # not "same_market_lockout"


def test_all_gates_off_does_not_invoke_provider() -> None:
    """Default config (all gates 0/False) → provider never called even when
    supplied — preserves the 'no portfolio touch on default' contract."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    called = {"count": 0}

    def provider() -> _FakePortfolio:
        called["count"] += 1
        return _FakePortfolio([])

    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(), portfolio_provider=provider)
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"
    assert called["count"] == 0


# ---------- A4 kill switch ----------


def _closed_rec(
    *,
    market_id: str,
    side: str,
    closed_at: float,
    realized_pnl: float,
    position_id: int = 1,
    opened_at: float | None = None,
) -> PositionRecord:
    """Closed position helper for kill-switch tests — overrides _rec's
    hardcoded -1.50 PnL with a per-call value."""
    return PositionRecord(
        id=position_id,
        market_id=market_id,
        side=side,  # type: ignore[arg-type]
        token_id=f"{side}-{market_id}",
        condition_id=f"0x{market_id}",
        qty=10.0,
        avg_entry_price=0.40,
        status="closed",
        opened_at=opened_at if opened_at is not None else (closed_at - 600),
        closed_at=closed_at,
        close_reason="stop_loss",
        realized_pnl=realized_pnl,
    )


def test_kill_consecutive_losses_blocks_after_threshold() -> None:
    """5 most-recent closed positions all negative → consecutive trip."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    # newest-first: 5 losses in a row, then a win further back
    records = [
        _closed_rec(
            market_id=f"m{i}",
            side="yes",
            closed_at=now - i * 600,
            realized_pnl=-0.50,
            position_id=10 + i,
        )
        for i in range(1, 6)
    ] + [
        _closed_rec(
            market_id="m99",
            side="yes",
            closed_at=now - 3600 * 24,
            realized_pnl=+2.0,
            position_id=99,
        ),
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(kill_max_consecutive_losses=5),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "kill_consecutive_losses"
    assert out.signals["streak"] == 5
    assert out.signals["limit"] == 5


def test_kill_consecutive_losses_resets_on_win() -> None:
    """4 losses + 1 win in the middle → no trip (streak is broken)."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    # newest-first: loss, loss, loss, WIN, loss, loss → streak from newest = 3
    records = [
        _closed_rec(
            market_id=f"m{i}",
            side="yes",
            closed_at=now - i * 600,
            realized_pnl=-0.50 if i != 4 else +1.0,
            position_id=10 + i,
        )
        for i in range(1, 7)
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(kill_max_consecutive_losses=5),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_kill_daily_loss_blocks_when_24h_sum_exceeds_cap() -> None:
    """Sum of realized PnL in last 24h <= -limit → daily-loss trip."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    records = [
        _closed_rec(
            market_id=f"m{i}",
            side="yes",
            closed_at=now - i * 3600,
            realized_pnl=-4.0,
            position_id=10 + i,
        )
        for i in range(1, 4)  # 3 losses in last 3h, sum -12
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(kill_daily_loss_usd=10.0),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "kill_daily_loss"
    assert out.signals["daily_pnl_usd"] == -12.0
    assert out.signals["limit_usd"] == 10.0


def test_kill_daily_loss_ignores_older_than_24h() -> None:
    """Losses ≥ 24h ago don't count toward the daily cap."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    records = [
        _closed_rec(market_id="m1", side="yes", closed_at=now - 3600 * 25, realized_pnl=-20.0),
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(kill_daily_loss_usd=10.0),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_kill_drawdown_blocks_on_peak_to_trough_drop() -> None:
    """Equity curve climbs to +5, then drops to +1 → drawdown=4."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    # chronologically: +3, +2, -1, -3  → cum 3, 5, 4, 1; peak 5; drop 4
    # newest-first storage:
    records = [
        _closed_rec(
            market_id="m4", side="yes", closed_at=now - 100, realized_pnl=-3.0, position_id=4
        ),
        _closed_rec(
            market_id="m3", side="yes", closed_at=now - 200, realized_pnl=-1.0, position_id=3
        ),
        _closed_rec(
            market_id="m2", side="yes", closed_at=now - 300, realized_pnl=+2.0, position_id=2
        ),
        _closed_rec(
            market_id="m1", side="yes", closed_at=now - 400, realized_pnl=+3.0, position_id=1
        ),
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(kill_max_drawdown_usd=3.0),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "kill_drawdown"
    assert out.signals["drawdown_usd"] == 4.0
    assert out.signals["peak_usd"] == 5.0


def test_kill_drawdown_passes_when_at_or_near_peak() -> None:
    """All-time peak == current → drawdown = 0 → no trip."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    records = [
        _closed_rec(
            market_id=f"m{i}", side="yes", closed_at=now - i * 600, realized_pnl=+1.0, position_id=i
        )
        for i in range(1, 4)
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(kill_max_drawdown_usd=2.0),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_kill_switch_disabled_by_default() -> None:
    """All three kill_* fields default 0 → no portfolio touch even with
    plenty of bad PnL history. Preserves the 'no portfolio touch on
    default config' contract for back-compat."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    called = {"count": 0}

    def provider() -> _FakePortfolio:
        called["count"] += 1
        # If we ever did touch the portfolio with default config, this
        # disastrous record would trip everything.
        return _FakePortfolio(
            [
                _closed_rec(
                    market_id="m1", side="yes", closed_at=_time.time() - 100, realized_pnl=-1000.0
                ),
            ]
        )

    inst = EdgeThresholdEntryV0(EdgeThresholdConfig(), portfolio_provider=provider)
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"
    assert called["count"] == 0


def test_kill_switch_no_history_passes() -> None:
    """Enabled but zero closed positions → no trip."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(
            kill_max_consecutive_losses=5,
            kill_daily_loss_usd=10.0,
            kill_max_drawdown_usd=5.0,
        ),
        portfolio_provider=lambda: _FakePortfolio([]),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "ok"


def test_kill_consecutive_runs_before_lockout() -> None:
    """When both kill_consecutive AND lockout would trip, kill wins —
    system-level brake supersedes per-market gate."""
    _populate(_market(), _book("no-m1", bid=0.40, ask=0.42))
    now = _time.time()
    records = [
        _closed_rec(
            market_id="m1",
            side="no",
            closed_at=now - i * 600,
            realized_pnl=-0.50,
            position_id=10 + i,
        )
        for i in range(1, 4)  # 3 losers, all same (m1, no)
    ]
    inst = EdgeThresholdEntryV0(
        EdgeThresholdConfig(
            kill_max_consecutive_losses=2,  # trips on 2
            same_market_lifetime_lockout=True,  # would also trip
        ),
        portfolio_provider=lambda: _FakePortfolio(records),
    )
    out = _run(inst, _ar(p_model=0.30))
    assert out.verdict == "skip"
    assert out.reason == "kill_consecutive_losses"
