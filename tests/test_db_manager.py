"""Tests for openpoly.db.manager — DatabaseManager."""

from __future__ import annotations

from sqlalchemy import select

from openpoly.db.engine import make_engine, make_session_factory
from openpoly.db.manager import DatabaseManager
from openpoly.db.tables import NewsItemRow, OrderBookSnapshot
from openpoly.markets.models import OrderBook
from openpoly.news.ring_buffer import NewsItem


def _engine(tmp_path):
    return make_engine(f"sqlite:///{tmp_path / 'mgr.db'}")


def _book(token_id: str) -> OrderBook:
    return OrderBook(token_id=token_id, ts=1.0, bids=[(0.40, 10.0)], asks=[(0.42, 8.0)])


def _news(news_id: str) -> NewsItem:
    return NewsItem(
        id=news_id,
        content=f"c-{news_id}",
        urgency="high",
        sentiment=None,
        published_at=1.0,
        received_at=2.0,
    )


def test_status_before_start_is_empty():
    status = DatabaseManager().status()
    assert status["tables"] == {}
    assert status["writers"]["order_book"] is None
    assert status["writers"]["news"] is None


def test_enqueue_before_start_returns_false():
    mgr = DatabaseManager()
    assert mgr.enqueue_order_book(_book("t")) is False
    assert mgr.enqueue_news(_news("n")) is False


async def test_start_then_enqueue_persists(tmp_path):
    engine = _engine(tmp_path)
    mgr = DatabaseManager()
    await mgr.start(engine=engine)
    assert mgr.enqueue_order_book(_book("tok-a")) is True
    assert mgr.enqueue_news(_news("n1")) is True
    await mgr.stop()  # flush queued rows
    with make_session_factory(engine)() as session:
        books = session.execute(select(OrderBookSnapshot)).scalars().all()
        news = session.execute(select(NewsItemRow)).scalars().all()
    assert [b.token_id for b in books] == ["tok-a"]
    assert [n.news_id for n in news] == ["n1"]
    engine.dispose()


async def test_status_reports_table_counts(tmp_path):
    mgr = DatabaseManager()
    await mgr.start(engine=_engine(tmp_path))
    mgr.enqueue_order_book(_book("a"))
    mgr.enqueue_order_book(_book("b"))
    mgr.enqueue_news(_news("n1"))
    await mgr.stop()
    tables = mgr.status()["tables"]
    assert tables["order_book_snapshot"] == 2
    assert tables["news_item"] == 1


async def test_status_reports_writer_stats(tmp_path):
    mgr = DatabaseManager()
    await mgr.start(engine=_engine(tmp_path))
    mgr.enqueue_order_book(_book("a"))
    await mgr.stop()
    assert mgr.status()["writers"]["order_book"] == {
        "written": 1,
        "dropped": 0,
        "pending": 0,
    }
