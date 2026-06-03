"""Tests for ExitMonitor — the timer-driven close loop.

The exit section is the real (pure) ThresholdExitV0 — TP / SL is driven by the
order-book bid price. The executor and portfolio are fakes, so the monitor's
own orchestration (mark → run → route → log) is tested in isolation.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.db.tables import OrderBookSnapshot
from openpoly.execution import ExecResult
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import OrderBook
from openpoly.markets.store import MarketStore
from openpoly.portfolio import HeldPosition, PortfolioStore
from openpoly.runtime.exit_monitor import ExitMonitor
from openpoly.runtime.section_log import exit_log
from openpoly.sections.exit.threshold_v0 import (
    ThresholdExitConfig,
    ThresholdExitV0,
)


@pytest.fixture(autouse=True)
def _isolate():
    """Fresh market catalog + exit log per test."""
    saved = market_source_manager.store
    market_source_manager.store = MarketStore()
    exit_log.reset()
    yield
    market_source_manager.store = saved
    exit_log.reset()


def _held(
    position_id: int,
    token_id: str,
    *,
    avg: float = 0.40,
    market_id: str = "m1",
    side: str = "yes",
) -> HeldPosition:
    return HeldPosition(
        position_id=position_id,
        market_id=market_id,
        side=side,  # type: ignore[arg-type]
        token_id=token_id,
        condition_id=f"0x{market_id}",
        qty=20.0,
        avg_entry_price=avg,
        opened_at=1.0,
    )


def _book(token_id: str, bid: float) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        ts=1.0,
        bids=[(bid, 100.0)],
        asks=[(bid + 0.02, 100.0)],
    )


class _FakePortfolio:
    def __init__(self, positions: list[HeldPosition]) -> None:
        self._positions = positions

    def get_open_positions(self) -> list[HeldPosition]:
        return list(self._positions)


class _FakeExecutor:
    """Records execute_sell calls; returns a canned ExecResult or raises."""

    def __init__(
        self,
        *,
        result: ExecResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._exc = exc
        self.calls: list[dict[str, object]] = []

    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason: str,
        ts: float,
        trigger: str | None,
    ) -> ExecResult:
        self.calls.append(
            {
                "position_id": position.position_id,
                "close_reason": close_reason,
                "trigger": trigger,
            }
        )
        if self._exc is not None:
            raise self._exc
        return self._result or ExecResult.ok(
            price=0.55, qty=position.qty, position_id=position.position_id
        )


def _monitor(portfolio: _FakePortfolio, executor: _FakeExecutor) -> ExitMonitor:
    m = ExitMonitor(
        exit_section=ThresholdExitV0(ThresholdExitConfig()),
        executor=executor,
        tick_interval_seconds=3600,
    )
    m.configure(portfolio)  # type: ignore[arg-type]
    return m


# ---------- close paths ----------


def test_take_profit_triggers_execute_sell() -> None:
    market_source_manager.store.set_order_books([_book("t1", bid=0.55)])
    ex = _FakeExecutor()  # default result: ExecResult.ok(price=0.55)
    _monitor(_FakePortfolio([_held(1, "t1", avg=0.40)]), ex)._tick_once()
    # (0.55 - 0.40) / 0.40 = 0.375 ≥ 0.20 → take_profit
    assert ex.calls == [
        {
            "position_id": 1,
            "close_reason": "take_profit",
            "trigger": "take_profit",
        }
    ]
    e = exit_log.entries()[0]
    assert e.verdict == "ok"
    assert e.trigger == "take_profit"
    assert e.fill_price == 0.55
    assert e.realized_pnl == pytest.approx((0.55 - 0.40) * 20.0)


def test_stop_loss_triggers_execute_sell() -> None:
    market_source_manager.store.set_order_books([_book("t1", bid=0.30)])
    ex = _FakeExecutor(result=ExecResult.ok(price=0.30, qty=20.0, position_id=1))
    _monitor(_FakePortfolio([_held(1, "t1", avg=0.40)]), ex)._tick_once()
    # (0.30 - 0.40) / 0.40 = -0.25 ≤ -0.15 → stop_loss
    assert ex.calls[0]["close_reason"] == "stop_loss"
    assert exit_log.entries()[0].trigger == "stop_loss"


def test_within_thresholds_holds() -> None:
    # v18: a within-threshold hold writes NO log entry (the ring keeps only
    # ok / error closes); tick telemetry records the position was evaluated.
    market_source_manager.store.set_order_books([_book("t1", bid=0.41)])
    ex = _FakeExecutor()
    m = _monitor(_FakePortfolio([_held(1, "t1", avg=0.40)]), ex)
    m._tick_once()
    assert ex.calls == []  # nothing closed
    assert exit_log.entries() == []  # no skip entry
    assert m.open_positions == 1
    assert m.blocked == 0
    assert m.last_tick_at is not None


def test_no_order_book_blocked() -> None:
    # v18: no order book → can't evaluate → counted as blocked, no log entry.
    ex = _FakeExecutor()
    m = _monitor(_FakePortfolio([_held(1, "t-missing")]), ex)
    m._tick_once()
    assert ex.calls == []
    assert exit_log.entries() == []
    assert m.open_positions == 1
    assert m.blocked == 1


def test_empty_bids_blocked() -> None:
    market_source_manager.store.set_order_books(
        [OrderBook(token_id="t1", ts=1.0, bids=[], asks=[(0.5, 100.0)])]
    )
    ex = _FakeExecutor()
    m = _monitor(_FakePortfolio([_held(1, "t1")]), ex)
    m._tick_once()
    assert ex.calls == []
    assert exit_log.entries() == []
    assert m.blocked == 1


def test_tick_telemetry_open_and_blocked() -> None:
    # One evaluable (held within thresholds) + one blocked (no order book) →
    # open=2, blocked=1, and still zero log entries.
    market_source_manager.store.set_order_books([_book("t1", bid=0.41)])
    ex = _FakeExecutor()
    positions = [_held(1, "t1", avg=0.40), _held(2, "t-missing", market_id="m2")]
    m = _monitor(_FakePortfolio(positions), ex)
    m._tick_once()
    assert m.open_positions == 2
    assert m.blocked == 1
    assert exit_log.entries() == []
    assert m.last_tick_at is not None


# ---------- error handling ----------


def test_execute_sell_raises_logged_as_error_sweep_continues() -> None:
    market_source_manager.store.set_order_books([_book("t1", bid=0.55), _book("t2", bid=0.55)])
    ex = _FakeExecutor(exc=ValueError("position already closed"))
    positions = [
        _held(1, "t1", avg=0.40),
        _held(2, "t2", avg=0.40, market_id="m2"),
    ]
    _monitor(_FakePortfolio(positions), ex)._tick_once()
    # Both TP-trigger → execute_sell raises on both → both logged error;
    # the first error did not abort the sweep.
    entries = exit_log.entries()
    assert [e.verdict for e in entries] == ["error", "error"]
    assert {e.position_id for e in entries} == {1, 2}


def test_execute_sell_not_filled_logged_as_error() -> None:
    market_source_manager.store.set_order_books([_book("t1", bid=0.55)])
    ex = _FakeExecutor(result=ExecResult.skip("no_bid_liquidity"))
    _monitor(_FakePortfolio([_held(1, "t1", avg=0.40)]), ex)._tick_once()
    e = exit_log.entries()[0]
    assert e.verdict == "error"
    assert e.error is not None
    assert "no_bid_liquidity" in e.error


# ---------- no-op paths ----------


def test_empty_positions_noop() -> None:
    ex = _FakeExecutor()
    _monitor(_FakePortfolio([]), ex)._tick_once()
    assert exit_log.entries() == []
    assert ex.calls == []


def test_not_configured_noop() -> None:
    m = ExitMonitor(
        exit_section=ThresholdExitV0(ThresholdExitConfig()),
        executor=_FakeExecutor(),
    )
    m._tick_once()  # no configure() — portfolio is None
    assert exit_log.entries() == []


# ---------- loop lifecycle ----------


async def test_tick_loop_start_stop() -> None:
    m = _monitor(_FakePortfolio([]), _FakeExecutor())
    assert m.state == "stopped"
    await m.start()
    assert m.state == "running"
    await asyncio.sleep(0)  # let the loop run one iteration
    await m.stop()
    assert m.state == "stopped"


async def test_stop_before_start_is_safe() -> None:
    m = _monitor(_FakePortfolio([]), _FakeExecutor())
    await m.stop()  # never started
    assert m.state == "stopped"


# ---------- peak tracking ----------


def test_peak_persists_across_ticks_and_triggers_drawdown() -> None:
    # TP set very high so peak_drawdown is the *only* close trigger that can
    # fire over the price path 0.46 → 0.50 → 0.47 with entry 0.40.
    monitor = ExitMonitor(
        exit_section=ThresholdExitV0(
            ThresholdExitConfig(
                take_profit_pct=0.50,
                stop_loss_pct=0.50,
                peak_drawdown_pct=0.12,
            )
        ),
        executor=_FakeExecutor(),
        tick_interval_seconds=3600,
    )
    portfolio = _FakePortfolio([_held(1, "t1", avg=0.40)])
    monitor.configure(portfolio)  # type: ignore[arg-type]
    store = market_source_manager.store

    # Tick 1: bid 0.46 → +15%, no trigger; peak seeded at 0.46.
    # v18: a hold writes no log entry — assert via the peak instead.
    store.set_order_books([_book("t1", bid=0.46)])
    monitor._tick_once()
    assert monitor._peak[1] == pytest.approx(0.46)
    assert exit_log.entries() == []

    # Tick 2: bid climbs to 0.50; peak follows.
    store.set_order_books([_book("t1", bid=0.50)])
    monitor._tick_once()
    assert monitor._peak[1] == pytest.approx(0.50)
    assert exit_log.entries() == []

    # Tick 3: bid retreats to 0.47; peak stays at 0.50.
    # peak_dd = (0.50 - 0.47) / (0.50 - 0.40) = 0.30 ≥ 0.12 → close.
    store.set_order_books([_book("t1", bid=0.47)])
    monitor._tick_once()
    last = exit_log.entries()[-1]
    assert last.verdict == "ok"
    assert last.trigger == "peak_drawdown"
    assert last.peak_price == pytest.approx(0.50)
    # On a successful close the peak entry is dropped.
    assert 1 not in monitor._peak


def test_peak_tracked_on_hold() -> None:
    # v18: held within thresholds writes no entry, but the peak is still
    # tracked in-memory for the drawdown trigger.
    market_source_manager.store.set_order_books([_book("t1", bid=0.41)])
    ex = _FakeExecutor()
    m = _monitor(_FakePortfolio([_held(1, "t1", avg=0.40)]), ex)
    m._tick_once()
    assert exit_log.entries() == []
    assert m._peak[1] == pytest.approx(0.41)


def test_bootstrap_peaks_rebuilds_from_snapshots(tmp_path) -> None:
    db_path = tmp_path / "openpoly_peak.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    sf = make_session_factory(engine)

    pf = PortfolioStore(sf)
    held = pf.open_position(
        market_id="m1",
        side="yes",
        token_id="t1",
        condition_id="0xm1",
        qty=20.0,
        price=0.40,
        ts=100.0,
        news_id="n1",
    )

    # Three snapshots after opened_at; peak bid = 0.55. One snapshot *before*
    # opened_at with a higher bid that must be ignored.
    with sf() as session:
        session.add_all(
            [
                OrderBookSnapshot(
                    token_id="t1",
                    recorded_at=99.0,  # before open — ignored
                    bids_json=json.dumps([[0.99, 100]]),
                    asks_json=json.dumps([[1.0, 100]]),
                ),
                OrderBookSnapshot(
                    token_id="t1",
                    recorded_at=110.0,
                    bids_json=json.dumps([[0.45, 100]]),
                    asks_json=json.dumps([[0.46, 100]]),
                ),
                OrderBookSnapshot(
                    token_id="t1",
                    recorded_at=120.0,
                    bids_json=json.dumps([[0.55, 100]]),
                    asks_json=json.dumps([[0.56, 100]]),
                ),
                OrderBookSnapshot(
                    token_id="t1",
                    recorded_at=130.0,
                    bids_json=json.dumps([[0.50, 100]]),
                    asks_json=json.dumps([[0.51, 100]]),
                ),
            ]
        )
        session.commit()

    monitor = ExitMonitor(
        exit_section=ThresholdExitV0(ThresholdExitConfig()),
        executor=_FakeExecutor(),
        tick_interval_seconds=3600,
    )
    monitor.configure(pf)
    monitor.bootstrap_peaks(sf)
    assert monitor._peak[held.position_id] == pytest.approx(0.55)


def test_bootstrap_peaks_no_snapshot_falls_back_to_entry(tmp_path) -> None:
    db_path = tmp_path / "openpoly_peak2.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    sf = make_session_factory(engine)

    pf = PortfolioStore(sf)
    held = pf.open_position(
        market_id="m1",
        side="yes",
        token_id="t1",
        condition_id="0xm1",
        qty=20.0,
        price=0.40,
        ts=100.0,
        news_id="n1",
    )

    monitor = ExitMonitor(
        exit_section=ThresholdExitV0(ThresholdExitConfig()),
        executor=_FakeExecutor(),
        tick_interval_seconds=3600,
    )
    monitor.configure(pf)
    monitor.bootstrap_peaks(sf)
    # No snapshots after opened_at → peak defaults to avg_entry_price (0.40).
    assert monitor._peak[held.position_id] == pytest.approx(0.40)
