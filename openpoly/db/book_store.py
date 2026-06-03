"""Order-book persistence — bridges the ``OrderBook`` domain object to the
``order_book_snapshot`` table, and builds the write-behind sink.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.tables import OrderBookSnapshot
from openpoly.db.writer import Sink
from openpoly.markets.models import OrderBook


def order_book_to_row(book: OrderBook) -> OrderBookSnapshot:
    """Serialize one ``OrderBook`` into an ``OrderBookSnapshot`` row."""
    return OrderBookSnapshot(
        token_id=book.token_id,
        recorded_at=book.ts,
        bids_json=json.dumps(book.bids),
        asks_json=json.dumps(book.asks),
    )


def make_order_book_sink(session_factory: sessionmaker[Session]) -> Sink:
    """Build a write-behind sink that persists a batch of ``OrderBook``s.

    The returned callable is sync (the writer runs it in a worker thread, off
    the loop). Drain calls are serialized, so one session per batch is safe.
    """

    def sink(books: list[OrderBook]) -> None:
        if not books:
            return
        with session_factory() as session:
            session.add_all([order_book_to_row(b) for b in books])
            session.commit()

    return sink
