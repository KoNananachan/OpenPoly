"""Database runtime manager.

Owns the persistence layer's runtime objects — the SQLAlchemy engine and the
two write-behind writers (order book + news). Lifted out of the FastAPI
lifespan so the ``database`` section has a manager to back it, mirroring
``MarketSourceManager`` / ``NewsSourceManager``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine, func, select, text

from openpoly.db.book_store import make_order_book_sink
from openpoly.db.engine import get_engine, init_db, make_session_factory
from openpoly.db.news_store import make_news_sink
from openpoly.db.tables import (
    FillRow,
    NewsItemRow,
    OrderBookSnapshot,
    PositionRow,
)
from openpoly.db.writer import WriteBehindWriter
from openpoly.markets.models import OrderBook
from openpoly.news.ring_buffer import NewsItem

logger = logging.getLogger(__name__)


def _ensure_fill_live_columns(engine: Engine) -> None:
    """Idempotent migration: add order_id / tx_hash columns to fill table if
    they are missing (older DBs predate slice C). New DBs get the columns
    via init_db()'s create_all and skip this entirely.

    SQLite's ALTER TABLE ADD COLUMN only fails if the column exists, so we
    PRAGMA-check first instead of catching."""
    with engine.begin() as conn:
        existing = {r[1] for r in conn.execute(text("PRAGMA table_info(fill)")).fetchall()}
        if "order_id" not in existing:
            conn.execute(text("ALTER TABLE fill ADD COLUMN order_id VARCHAR"))
            logger.info("migration: added fill.order_id")
        if "tx_hash" not in existing:
            conn.execute(text("ALTER TABLE fill ADD COLUMN tx_hash VARCHAR"))
            logger.info("migration: added fill.tx_hash")


class DatabaseConfig(BaseModel):
    """Config for the ``database`` section.

    The DB is system infrastructure — no tunable params; the persistence
    wiring (one SQLite file, two write-behind writers) is fixed.
    """


class DatabaseManager:
    """Owns the persistence runtime: the engine + the two write-behind writers.

    Lifecycle (start / stop) is driven by the FastAPI lifespan. Backs the
    ``database`` section; ``status`` powers its inspector.
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._book_writer: WriteBehindWriter | None = None
        self._news_writer: WriteBehindWriter | None = None

    # ---------- lifecycle ----------

    async def start(self, engine: Engine | None = None) -> None:
        """Create the engine + tables + write-behind writers and start them.

        ``engine`` overrides the process engine — tests pass a throwaway one.
        """
        self._engine = engine or get_engine()
        init_db(self._engine)
        _ensure_fill_live_columns(self._engine)
        factory = make_session_factory(self._engine)
        self._book_writer = WriteBehindWriter(make_order_book_sink(factory))
        self._news_writer = WriteBehindWriter(make_news_sink(factory))
        await self._book_writer.start()
        await self._news_writer.start()

    async def stop(self) -> None:
        """Stop both writers, flushing whatever is still queued."""
        if self._book_writer is not None:
            await self._book_writer.stop()
        if self._news_writer is not None:
            await self._news_writer.stop()

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            await self.stop()

    # ---------- persist hooks (wired into the source managers) ----------

    def enqueue_order_book(self, book: OrderBook) -> bool:
        """Queue one order book for write-behind persistence. Returns False if
        the manager has not started."""
        if self._book_writer is None:
            return False
        return self._book_writer.enqueue(book)

    def enqueue_news(self, item: NewsItem) -> bool:
        """Queue one news item for write-behind persistence."""
        if self._news_writer is None:
            return False
        return self._news_writer.enqueue(item)

    # ---------- status (powers the database section inspector) ----------

    def status(self) -> dict[str, Any]:
        """Snapshot of the persistence layer — table row counts + writer stats."""
        return {
            "tables": self._table_counts(),
            "writers": {
                "order_book": self._writer_stats(self._book_writer),
                "news": self._writer_stats(self._news_writer),
            },
        }

    def _table_counts(self) -> dict[str, int]:
        if self._engine is None:
            return {}
        with make_session_factory(self._engine)() as session:
            return {
                "order_book_snapshot": session.execute(
                    select(func.count()).select_from(OrderBookSnapshot)
                ).scalar_one(),
                "news_item": session.execute(
                    select(func.count()).select_from(NewsItemRow)
                ).scalar_one(),
                "fill": session.execute(select(func.count()).select_from(FillRow)).scalar_one(),
                "position": session.execute(
                    select(func.count()).select_from(PositionRow)
                ).scalar_one(),
            }

    @staticmethod
    def _writer_stats(writer: WriteBehindWriter | None) -> dict[str, int] | None:
        if writer is None:
            return None
        return {
            "written": writer.written,
            "dropped": writer.dropped,
            "pending": writer.pending,
        }


# Module-level singleton; the FastAPI lifespan + the database section wire to this.
manager = DatabaseManager()
