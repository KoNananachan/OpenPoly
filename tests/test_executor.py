"""Tests for Executor — the level-1 paper fill service (PF3).

The executor reads the live MarketStore singleton, so each test gets a fresh
catalog via the autouse fixture and a fresh PortfolioStore on a throwaway DB.
"""

from __future__ import annotations

import pytest

from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.execution import PaperExecutor as Executor
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import OrderBook, normalize_gamma_market
from openpoly.markets.store import MarketStore, PollSummary
from openpoly.portfolio import PortfolioStore
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent


@pytest.fixture(autouse=True)
def _isolate_market_store():
    saved = market_source_manager.store
    market_source_manager.store = MarketStore()
    yield
    market_source_manager.store = saved


@pytest.fixture
def store(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/portfolio.db")
    init_db(engine)
    yield PortfolioStore(make_session_factory(engine))
    engine.dispose()


def _market(market_id: str = "m1", *, clob: str | None = None):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": "Q?",
        "clobTokenIds": clob or f'["yes-{market_id}", "no-{market_id}"]',
    }
    m = normalize_gamma_market(raw, event={"id": "e", "title": "E", "tags": []})
    assert m is not None
    return m


def _book(
    token_id: str,
    *,
    bid: float = 0.40,
    ask: float = 0.42,
    bid_size: float = 100.0,
    ask_size: float = 100.0,
) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        ts=1.0,
        bids=[(bid, bid_size)] if bid_size else [],
        asks=[(ask, ask_size)] if ask_size else [],
    )


def _populate(market, *books: OrderBook) -> None:
    s = market_source_manager.store
    s.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))
    s.set_order_books(list(books))


def _intent(market_id: str = "m1", side: str = "yes", qty: float = 20.0) -> OrderIntent:
    return OrderIntent(market_id=market_id, side=side, price=0.42, qty=qty)


# ---------- execute_buy ----------


def test_buy_fills_at_level1_ask(store) -> None:
    _populate(_market(), _book("yes-m1", ask=0.42))
    r = Executor(store).execute_buy(_intent(qty=20.0), news_id="n1", ts=100.0)
    assert r.filled
    assert r.price == 0.42
    assert r.qty == 20.0
    assert r.position_id is not None
    assert store.get_open_position("m1", "yes") is not None


def test_buy_qty_capped_by_level1_depth(store) -> None:
    _populate(_market(), _book("yes-m1", ask=0.42, ask_size=5.0))
    r = Executor(store).execute_buy(_intent(qty=100.0), news_id="n1", ts=1.0)
    assert r.filled
    assert r.qty == 5.0  # capped to the level-1 ask depth


def test_buy_dust_skip(store) -> None:
    _populate(_market(), _book("yes-m1", ask=0.42))
    # qty 1 @ 0.42 = $0.42 notional, below the $1 floor.
    r = Executor(store).execute_buy(_intent(qty=1.0), news_id="n1", ts=1.0)
    assert not r.filled
    assert r.skip_reason == "dust"


def test_buy_position_exists_skip(store) -> None:
    _populate(_market(), _book("yes-m1", ask=0.42))
    ex = Executor(store)
    assert ex.execute_buy(_intent(), news_id="n1", ts=1.0).filled
    r = ex.execute_buy(_intent(), news_id="n2", ts=2.0)
    assert not r.filled
    assert r.skip_reason == "position_exists"


def test_buy_market_not_found_skip(store) -> None:
    r = Executor(store).execute_buy(_intent(market_id="zzz"), news_id="n1", ts=1.0)
    assert not r.filled
    assert r.skip_reason == "market_not_found"


def test_buy_no_order_book_skip(store) -> None:
    _populate(_market())  # market in catalog, no books sampled
    r = Executor(store).execute_buy(_intent(), news_id="n1", ts=1.0)
    assert not r.filled
    assert r.skip_reason == "no_order_book"


def test_buy_no_ask_liquidity_skip(store) -> None:
    _populate(_market(), _book("yes-m1", ask_size=0.0))  # bids only
    r = Executor(store).execute_buy(_intent(), news_id="n1", ts=1.0)
    assert not r.filled
    assert r.skip_reason == "no_ask_liquidity"


def test_buy_no_token_skip(store) -> None:
    # Market carries only the YES token; side=no has no token.
    _populate(_market(clob='["yes-m1"]'), _book("yes-m1", ask=0.42))
    r = Executor(store).execute_buy(_intent(side="no"), news_id="n1", ts=1.0)
    assert not r.filled
    assert r.skip_reason == "no_token"


def test_buy_no_side_reads_no_token_book(store) -> None:
    _populate(_market(), _book("yes-m1", ask=0.42), _book("no-m1", ask=0.55))
    r = Executor(store).execute_buy(_intent(side="no", qty=20.0), news_id="n1", ts=1.0)
    assert r.filled
    assert r.price == 0.55  # the NO token's own book, not a flipped YES book
    held = store.get_open_position("m1", "no")
    assert held is not None and held.token_id == "no-m1"


# ---------- execute_sell ----------


def test_sell_fills_at_level1_bid(store) -> None:
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    ex = Executor(store)
    ex.execute_buy(_intent(qty=20.0), news_id="n1", ts=1.0)
    held = store.get_open_position("m1", "yes")
    assert held is not None
    r = ex.execute_sell(held, close_reason="take_profit", ts=200.0, trigger="take_profit")
    assert r.filled
    assert r.price == 0.40  # level-1 bid
    assert store.get_open_position("m1", "yes") is None  # now closed


def test_sell_no_order_book_skip(store) -> None:
    _populate(_market(), _book("yes-m1", bid=0.40, ask=0.42))
    ex = Executor(store)
    ex.execute_buy(_intent(qty=20.0), news_id="n1", ts=1.0)
    held = store.get_open_position("m1", "yes")
    assert held is not None
    market_source_manager.store.set_order_books([])  # book gone
    r = ex.execute_sell(held, close_reason="manual", ts=2.0)
    assert not r.filled
    assert r.skip_reason == "no_order_book"


# ---------- configuration ----------


def test_unconfigured_executor_raises() -> None:
    _populate(_market(), _book("yes-m1", ask=0.42))
    with pytest.raises(RuntimeError, match="PortfolioStore"):
        Executor().execute_buy(_intent(), news_id="n1", ts=1.0)
