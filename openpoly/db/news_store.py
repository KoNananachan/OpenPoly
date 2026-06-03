"""News persistence — ``NewsItem`` domain object → ``news_item`` table, and the
write-behind sink the news stream feeds.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.tables import NewsItemRow
from openpoly.db.writer import Sink
from openpoly.news.ring_buffer import NewsItem


def news_item_to_row(item: NewsItem) -> NewsItemRow:
    """Serialize one ``NewsItem`` into a ``NewsItemRow``. The raw upstream
    payload (``item.raw``) is intentionally not persisted — basic inspect
    needs only the structured fields."""
    return NewsItemRow(
        news_id=item.id,
        content=item.content,
        urgency=item.urgency,
        sentiment=item.sentiment,
        published_at=item.published_at,
        received_at=item.received_at,
    )


def make_news_sink(session_factory: sessionmaker[Session]) -> Sink:
    """Build a write-behind sink that persists a batch of ``NewsItem``s.

    Sync (the writer runs it in a worker thread, off the loop). Drain calls
    are serialized, so one session per batch is safe.
    """

    def sink(items: list[NewsItem]) -> None:
        if not items:
            return
        with session_factory() as session:
            session.add_all([news_item_to_row(i) for i in items])
            session.commit()

    return sink
