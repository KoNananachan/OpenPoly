"""Tests for ReconciliationMonitor — closes DB-open positions the wallet no
longer holds on-chain.

The on-chain holdings source is faked so the test never hits the network.
Portfolio is a real one (sqlite tmp) so close_position math + status
transitions run end-to-end. The CloseReason "reconciled" must be a valid
Literal in portfolio.models.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.portfolio import PortfolioStore
from openpoly.runtime.section_log import settlement_log
from openpoly.runtime.reconciliation_monitor import ReconciliationMonitor


@pytest.fixture(autouse=True)
def _reset_log():
    settlement_log.reset()
    yield
    settlement_log.reset()


@pytest.fixture
def store(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path}/p.db")
    init_db(engine)
    yield PortfolioStore(make_session_factory(engine))
    engine.dispose()


def _open_position(
    store: PortfolioStore,
    *,
    side: str = "yes",
    condition_id: str = "0xcid",
    market_id: str = "m1",
    avg: float = 0.40,
    qty: float = 10.0,
    ts: float = 100.0,
) -> int:
    held = store.open_position(
        market_id=market_id,
        side=side,
        token_id=f"{side}-{condition_id}",
        condition_id=condition_id,
        price=avg,
        qty=qty,
        ts=ts,
        news_id="n",
    )
    return held.position_id


def _holdings(pairs: set[tuple[str, str]]):
    async def _fetch() -> set[tuple[str, str]]:
        return pairs

    return _fetch


async def test_no_open_positions_is_noop(store) -> None:
    rm = ReconciliationMonitor(holdings_fetcher=_holdings(set()), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    assert settlement_log.entries() == []


async def test_flat_position_is_reconciled_closed(store) -> None:
    """DB says open, on-chain holdings show nothing for that (condition, side)
    → the position was exited outside the ledger; close it as reconciled with
    realized_pnl 0 (we deliberately do not fabricate the real exit PnL)."""
    pid = _open_position(store, condition_id="0xcid", side="yes", avg=0.40, qty=10.0)
    rm = ReconciliationMonitor(holdings_fetcher=_holdings(set()), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    rec = store.get_position(pid)
    assert rec is not None
    assert rec.status == "closed"
    assert rec.close_reason == "reconciled"
    assert rec.realized_pnl == pytest.approx(0.0)
    entries = settlement_log.entries()
    assert len(entries) == 1
    assert entries[0].verdict == "ok"
    assert entries[0].reason == "reconciled"


async def test_position_still_held_on_chain_is_left_open(store) -> None:
    pid = _open_position(store, condition_id="0xcid", side="yes")
    rm = ReconciliationMonitor(holdings_fetcher=_holdings({("0xcid", "yes")}), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    rec = store.get_position(pid)
    assert rec is not None and rec.status == "open"


async def test_held_other_side_does_not_reconcile_wrong_side(store) -> None:
    """Holding the NO token must not reconcile-close an open YES position on the
    same condition (and vice-versa) — (condition, side) must match exactly."""
    pid = _open_position(store, condition_id="0xcid", side="yes")
    rm = ReconciliationMonitor(holdings_fetcher=_holdings({("0xcid", "no")}), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    rec = store.get_position(pid)
    assert rec is not None and rec.status == "closed"
    assert rec.close_reason == "reconciled"


async def test_not_live_is_noop(store) -> None:
    """In paper mode the DB holds paper positions; the on-chain indexer knows
    nothing of them. Reconciliation MUST NOT run, or it would close every paper
    position as 'flat on-chain'."""
    pid = _open_position(store, condition_id="0xcid", side="yes")
    rm = ReconciliationMonitor(
        holdings_fetcher=_holdings(set()),
        grace_seconds=0,
        live_check=lambda: False,
    )
    rm.configure(store)
    await rm._tick_once()
    rec = store.get_position(pid)
    assert rec is not None and rec.status == "open"
    assert settlement_log.entries() == []


async def test_within_grace_period_is_skipped(store) -> None:
    """A just-opened position that reads flat must NOT be reconciled — the buy's
    on-chain settlement / indexer update can lag a few minutes."""
    pid = _open_position(store, condition_id="0xcid", side="yes", ts=time.time())
    rm = ReconciliationMonitor(holdings_fetcher=_holdings(set()), grace_seconds=10_000)
    rm.configure(store)
    await rm._tick_once()
    rec = store.get_position(pid)
    assert rec is not None and rec.status == "open"
    assert settlement_log.entries() == []


# ---------- reverse diff: untracked on-chain holdings (O2) ----------


async def test_untracked_onchain_holding_alerts_but_never_opens(store) -> None:
    """Wallet holds a (condition, side) the ledger has no open position for
    (the untracked-orphan shape) → loud alert in the log, but NEVER an
    auto-opened position (cost basis unknown; external transfers possible)."""
    rm = ReconciliationMonitor(holdings_fetcher=_holdings({("0xorphan", "yes")}), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    entries = settlement_log.entries()
    assert len(entries) == 1
    assert entries[0].verdict == "skip"
    assert entries[0].reason == "untracked_onchain_holding"
    assert store.get_open_positions() == []  # nothing auto-created


async def test_untracked_alert_fires_once_per_orphan(store) -> None:
    rm = ReconciliationMonitor(holdings_fetcher=_holdings({("0xorphan", "yes")}), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    await rm._tick_once()
    assert len(settlement_log.entries()) == 1  # deduped across ticks


async def test_tracked_holding_does_not_alert(store) -> None:
    _open_position(store, condition_id="0xcid", side="yes")
    rm = ReconciliationMonitor(holdings_fetcher=_holdings({("0xcid", "yes")}), grace_seconds=0)
    rm.configure(store)
    await rm._tick_once()
    assert settlement_log.entries() == []  # ledger knows it — quiet
