"""Tests for openpoly.db.news_store — news_item model + write-behind persistence."""

from __future__ import annotations

from sqlalchemy import select

from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.db.news_store import make_news_sink, news_item_to_row
from openpoly.db.tables import NewsItemRow
from openpoly.db.writer import WriteBehindWriter
from openpoly.news.manager import NewsSourceManager
from openpoly.news.ring_buffer import NewsItem


def _item(news_id: str = "n1", *, sentiment: float | None = None) -> NewsItem:
    return NewsItem(
        id=news_id,
        content=f"content {news_id}",
        urgency="high",
        sentiment=sentiment,
        published_at=1.0,
        received_at=2.0,
    )


def _engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


def test_news_item_to_row():
    row = news_item_to_row(_item("abc", sentiment=0.7))
    assert row.news_id == "abc"
    assert row.content == "content abc"
    assert row.urgency == "high"
    assert row.sentiment == 0.7
    assert row.published_at == 1.0
    assert row.received_at == 2.0


def test_news_item_to_row_null_sentiment():
    assert news_item_to_row(_item("x")).sentiment is None


def test_sink_persists_batch(tmp_path):
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    make_news_sink(factory)([_item("a"), _item("b")])
    with factory() as session:
        rows = session.execute(select(NewsItemRow)).scalars().all()
    assert {r.news_id for r in rows} == {"a", "b"}
    engine.dispose()


def test_sink_empty_batch_is_noop(tmp_path):
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    make_news_sink(factory)([])
    with factory() as session:
        assert session.execute(select(NewsItemRow)).all() == []
    engine.dispose()


# ---------- manager persist hook ----------


def test_manager_on_item_calls_persist_hook():
    received: list[NewsItem] = []
    mgr = NewsSourceManager()
    mgr.set_news_persist(received.append)
    item = _item("n1")
    mgr._on_item(item)
    assert received == [item]


def test_manager_persist_independent_of_pipeline_hook():
    """Persist must fire even when no pipeline hook is set."""
    persisted: list[NewsItem] = []
    mgr = NewsSourceManager()
    mgr.set_news_persist(persisted.append)
    mgr._on_item(_item("n1"))  # no pipeline hook wired
    assert len(persisted) == 1


def test_manager_persist_hook_cleared():
    persisted: list[NewsItem] = []
    mgr = NewsSourceManager()
    mgr.set_news_persist(persisted.append)
    mgr.set_news_persist(None)
    mgr._on_item(_item("n1"))
    assert persisted == []


async def test_news_persist_end_to_end(tmp_path):
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    writer = WriteBehindWriter(make_news_sink(factory))
    await writer.start()

    mgr = NewsSourceManager()
    mgr.set_news_persist(writer.enqueue)
    for i in range(3):
        mgr._on_item(_item(f"n{i}"))

    await writer.stop()  # flush queued items to the DB
    with factory() as session:
        rows = session.execute(select(NewsItemRow)).scalars().all()
    assert {r.news_id for r in rows} == {"n0", "n1", "n2"}
    engine.dispose()
