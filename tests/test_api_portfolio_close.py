"""Endpoint tests for POST /api/positions/{id}/close — manual close (EX3).

The route's module-level ``executor`` is monkeypatched to one bound to the
test DB, so the close writes through the same store the route reads from.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import openpoly.api.portfolio_routes as portfolio_routes
from openpoly.api.main import app
from openpoly.api.portfolio_routes import get_portfolio_store
from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.execution import PaperExecutor as Executor
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import OrderBook
from openpoly.markets.store import MarketStore
from openpoly.portfolio import PortfolioStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Temp-DB PortfolioStore wired into the route (read) + executor (write),
    a fresh market catalog, and a TestClient."""
    engine = make_engine(f"sqlite:///{tmp_path}/portfolio.db")
    init_db(engine)
    store = PortfolioStore(make_session_factory(engine))
    app.dependency_overrides[get_portfolio_store] = lambda: store
    monkeypatch.setattr(portfolio_routes, "executor", Executor(store))
    saved_market = market_source_manager.store
    market_source_manager.store = MarketStore()
    yield store, TestClient(app)
    app.dependency_overrides.clear()
    market_source_manager.store = saved_market
    engine.dispose()


def _open(store: PortfolioStore, *, token_id: str = "t1", market_id: str = "m1"):
    return store.open_position(
        market_id=market_id,
        side="yes",
        token_id=token_id,
        condition_id=f"0x{market_id}",
        price=0.40,
        qty=25.0,
        ts=100.0,
        news_id="n1",
    )


def _book(token_id: str, bid: float) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        ts=1.0,
        bids=[(bid, 100.0)],
        asks=[(bid + 0.02, 100.0)],
    )


def test_close_open_position(env) -> None:
    store, client = env
    held = _open(store)
    market_source_manager.store.set_order_books([_book("t1", bid=0.55)])

    r = client.post(f"/api/positions/{held.position_id}/close")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filled"] is True
    assert body["price"] == 0.55
    assert body["position_id"] == held.position_id

    rec = store.get_position(held.position_id)
    assert rec is not None
    assert rec.status == "closed"
    assert rec.close_reason == "manual"


def test_close_nonexistent_returns_404(env) -> None:
    _store, client = env
    r = client.post("/api/positions/9999/close")
    assert r.status_code == 404


def test_close_already_closed_returns_409(env) -> None:
    store, client = env
    held = _open(store)
    store.close_position(
        held.position_id,
        sell_price=0.50,
        ts=200.0,
        close_reason="take_profit",
        trigger="take_profit",
    )
    r = client.post(f"/api/positions/{held.position_id}/close")
    assert r.status_code == 409
    assert "closed" in r.json()["detail"]


def test_close_no_bid_liquidity_returns_200_not_filled(env) -> None:
    store, client = env
    held = _open(store)
    market_source_manager.store.set_order_books(
        [OrderBook(token_id="t1", ts=1.0, bids=[], asks=[(0.5, 100.0)])]
    )
    r = client.post(f"/api/positions/{held.position_id}/close")
    assert r.status_code == 200
    body = r.json()
    assert body["filled"] is False
    assert body["skip_reason"] == "no_bid_liquidity"
    # Nothing filled — the position stays open.
    rec = store.get_position(held.position_id)
    assert rec is not None
    assert rec.status == "open"


# ---------- close-all ----------


def test_close_all_with_no_open_returns_noop(env) -> None:
    _store, client = env
    r = client.post("/api/positions/close-all")
    assert r.status_code == 200
    body = r.json()
    assert body == {"attempted": 0, "filled": 0, "skipped": 0, "errored": 0, "details": []}


def test_close_all_three_positions_all_succeed(env) -> None:
    store, client = env
    # Open 3 positions on 3 different (market, side) pairs.
    p1 = _open(store, token_id="t1", market_id="m1")
    p2 = _open(store, token_id="t2", market_id="m2")
    p3 = _open(store, token_id="t3", market_id="m3")
    market_source_manager.store.set_order_books(
        [
            _book("t1", bid=0.55),
            _book("t2", bid=0.50),
            _book("t3", bid=0.45),
        ]
    )

    r = client.post("/api/positions/close-all")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attempted"] == 3
    assert body["filled"] == 3
    assert body["skipped"] == 0
    assert body["errored"] == 0
    by_id = {d["position_id"]: d for d in body["details"]}
    assert by_id[p1.position_id]["ok"] is True
    assert by_id[p1.position_id]["price"] == 0.55
    assert by_id[p2.position_id]["price"] == 0.50
    assert by_id[p3.position_id]["price"] == 0.45

    # All actually closed in the store.
    for held in (p1, p2, p3):
        rec = store.get_position(held.position_id)
        assert rec is not None and rec.status == "closed"
        assert rec.close_reason == "manual"


def test_close_all_partial_failure_does_not_block_others(env) -> None:
    store, client = env
    p1 = _open(store, token_id="t1", market_id="m1")
    p2 = _open(store, token_id="t2", market_id="m2")  # no bid → skipped
    p3 = _open(store, token_id="t3", market_id="m3")
    market_source_manager.store.set_order_books(
        [
            _book("t1", bid=0.55),
            OrderBook(token_id="t2", ts=1.0, bids=[], asks=[(0.5, 100.0)]),
            _book("t3", bid=0.45),
        ]
    )

    r = client.post("/api/positions/close-all")
    assert r.status_code == 200
    body = r.json()
    assert body["attempted"] == 3
    assert body["filled"] == 2
    assert body["skipped"] == 1
    assert body["errored"] == 0
    by_id = {d["position_id"]: d for d in body["details"]}
    assert by_id[p2.position_id]["ok"] is False
    assert by_id[p2.position_id]["skip_reason"] == "no_bid_liquidity"
    # 1 and 3 actually closed.
    assert store.get_position(p1.position_id).status == "closed"
    assert store.get_position(p3.position_id).status == "closed"
    # 2 still open.
    assert store.get_position(p2.position_id).status == "open"
