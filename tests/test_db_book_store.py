"""Tests for openpoly.db — order_book_snapshot model + write-behind persistence."""

from __future__ import annotations

import json

from sqlalchemy import select

from openpoly.db.book_store import make_order_book_sink, order_book_to_row
from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.db.tables import OrderBookSnapshot
from openpoly.db.writer import WriteBehindWriter
from openpoly.markets.manager import MarketSourceManager
from openpoly.markets.models import OrderBook, normalize_gamma_market
from openpoly.markets.store import PollSummary


def _book(token_id: str, ts: float = 1.0) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        ts=ts,
        bids=[(0.40, 100.0), (0.39, 50.0), (0.38, 25.0)],
        asks=[(0.42, 80.0), (0.43, 40.0), (0.44, 20.0)],
    )


def _engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


def test_order_book_to_row():
    row = order_book_to_row(_book("tok-1", ts=123.0))
    assert row.token_id == "tok-1"
    assert row.recorded_at == 123.0
    assert json.loads(row.bids_json) == [[0.40, 100.0], [0.39, 50.0], [0.38, 25.0]]
    assert json.loads(row.asks_json) == [[0.42, 80.0], [0.43, 40.0], [0.44, 20.0]]


def test_init_db_creates_order_book_table(tmp_path):
    engine = _engine(tmp_path)
    with make_session_factory(engine)() as session:
        assert session.execute(select(OrderBookSnapshot)).all() == []
    engine.dispose()


def test_sink_persists_batch(tmp_path):
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    make_order_book_sink(factory)([_book("a"), _book("b")])
    with factory() as session:
        rows = session.execute(select(OrderBookSnapshot)).scalars().all()
    assert {r.token_id for r in rows} == {"a", "b"}
    engine.dispose()


def test_sink_empty_batch_is_noop(tmp_path):
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    make_order_book_sink(factory)([])
    with factory() as session:
        assert session.execute(select(OrderBookSnapshot)).all() == []
    engine.dispose()


def _raw_pair(market_id: str):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": "Q?",
        "clobTokenIds": f'["yes-{market_id}", "no-{market_id}"]',
        "endDate": "2027-01-01T00:00:00Z",
        "bestBid": 0.40,
        "bestAsk": 0.42,
        "spread": 0.02,
        "volume24hr": 50_000.0,
        "liquidityNum": 20_000.0,
        "feesEnabled": False,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }
    return raw, {"id": "e1", "title": "E", "tags": []}


async def test_sample_books_persists_end_to_end(tmp_path):
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    writer = WriteBehindWriter(make_order_book_sink(factory))
    await writer.start()

    async def book_fetch(token_id: str) -> OrderBook:
        return _book(token_id)

    mgr = MarketSourceManager(book_fetcher=book_fetch)
    mgr.set_book_persist(writer.enqueue)
    raw_a, event = _raw_pair("a")
    raw_b, _ = _raw_pair("b")
    markets = [
        normalize_gamma_market(raw_a, event=event),
        normalize_gamma_market(raw_b, event=event),
    ]
    assert all(markets)  # normalize must succeed for the fixture raws
    mgr.store.replace(markets, PollSummary(ts=1.0, fetched=2, kept=2, reason_counts={}))

    sampled = await mgr._sample_books_once()
    assert sampled == 4  # 2 markets x YES + NO

    await writer.stop()  # flush queued books to the DB
    with factory() as session:
        rows = session.execute(select(OrderBookSnapshot)).scalars().all()
    assert {r.token_id for r in rows} == {"yes-a", "no-a", "yes-b", "no-b"}
    engine.dispose()
