"""GET /api/inspect/* — read-side endpoints for the Inspector drawer.

``markets`` reads the in-memory market store; ``news`` / ``order-books`` read
the persisted tables; ``db-status`` reports the database manager's state.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.engine import get_session_factory
from openpoly.db.manager import DatabaseManager, manager as database_manager
from openpoly.db.tables import NewsItemRow, OrderBookSnapshot
from openpoly.markets.manager import manager as market_source_manager

router = APIRouter()

NEWS_LIMIT_DEFAULT = 50
NEWS_LIMIT_MAX = 500
ORDER_BOOK_LIMIT_DEFAULT = 50
ORDER_BOOK_LIMIT_MAX = 500
ORDER_BOOK_HISTORY_LIMIT = 2000


def get_database_manager() -> DatabaseManager:
    """Default dependency — the process-wide database manager singleton.
    Overridable via ``app.dependency_overrides`` in tests."""
    return database_manager


@router.get("/api/inspect/markets")
def inspect_markets() -> dict[str, Any]:
    """Live market catalog + the latest sampled order-book price per market."""
    store = market_source_manager.store
    markets: list[dict[str, Any]] = []
    for m in store.snapshot():
        yes_ob = store.get_order_book(m.yes_token_id)
        no_ob = store.get_order_book(m.no_token_id) if m.no_token_id is not None else None
        markets.append(
            {
                "market_id": m.market_id,
                "question": m.question,
                "yes_token_id": m.yes_token_id,
                "no_token_id": m.no_token_id,
                "volume_24h": m.volume_24h,
                "liquidity": m.liquidity,
                "end_date": m.end_date.isoformat() if m.end_date else None,
                "best_bid": yes_ob.best_bid if yes_ob else None,
                "best_ask": yes_ob.best_ask if yes_ob else None,
                "mid": yes_ob.mid if yes_ob else None,
                "spread": yes_ob.spread if yes_ob else None,
                "price_ts": yes_ob.ts if yes_ob else None,
                "no_best_bid": no_ob.best_bid if no_ob else None,
                "no_best_ask": no_ob.best_ask if no_ob else None,
                "no_mid": no_ob.mid if no_ob else None,
                "no_spread": no_ob.spread if no_ob else None,
                "no_price_ts": no_ob.ts if no_ob else None,
            }
        )
    last_poll = store.last_poll
    return {
        "catalog_size": len(store),
        "order_book_count": store.order_book_count,
        "last_poll": last_poll.to_dict() if last_poll else None,
        "markets": markets,
    }


@router.get("/api/inspect/news")
def inspect_news(
    limit: int = NEWS_LIMIT_DEFAULT,
    factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    """Persisted news items, newest first (by ``received_at``)."""
    limit = max(1, min(limit, NEWS_LIMIT_MAX))
    with factory() as session:
        rows = (
            session.execute(
                select(NewsItemRow).order_by(NewsItemRow.received_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
    return {
        "count": len(rows),
        "news": [
            {
                "id": r.id,
                "news_id": r.news_id,
                "content": r.content,
                "urgency": r.urgency,
                "sentiment": r.sentiment,
                "published_at": r.published_at,
                "received_at": r.received_at,
            }
            for r in rows
        ],
    }


@router.get("/api/inspect/order-books")
def inspect_order_books(
    limit: int = ORDER_BOOK_LIMIT_DEFAULT,
    factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    """Persisted order-book snapshots, newest persisted first."""
    limit = max(1, min(limit, ORDER_BOOK_LIMIT_MAX))
    with factory() as session:
        rows = (
            session.execute(
                select(OrderBookSnapshot).order_by(OrderBookSnapshot.id.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
    return {
        "count": len(rows),
        "order_books": [
            {
                "id": r.id,
                "token_id": r.token_id,
                "recorded_at": r.recorded_at,
                "bids": json.loads(r.bids_json),
                "asks": json.loads(r.asks_json),
            }
            for r in rows
        ],
    }


@router.get("/api/inspect/order-books/{token_id}")
def inspect_order_book_history(
    token_id: str,
    since: float | None = None,
    until: float | None = None,
    factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    """Persisted order-book snapshots for one token, ascending by time.

    Optional ``since`` / ``until`` (epoch seconds) bound the window. Capped at
    ``ORDER_BOOK_HISTORY_LIMIT`` rows. The global newest-N
    ``/api/inspect/order-books`` route is unaffected.
    """
    with factory() as session:
        stmt = select(OrderBookSnapshot).where(OrderBookSnapshot.token_id == token_id)
        if since is not None:
            stmt = stmt.where(OrderBookSnapshot.recorded_at >= since)
        if until is not None:
            stmt = stmt.where(OrderBookSnapshot.recorded_at <= until)
        stmt = stmt.order_by(OrderBookSnapshot.recorded_at).limit(ORDER_BOOK_HISTORY_LIMIT)
        rows = session.execute(stmt).scalars().all()
    return {
        "token_id": token_id,
        "count": len(rows),
        "snapshots": [
            {
                "recorded_at": r.recorded_at,
                "bids": json.loads(r.bids_json),
                "asks": json.loads(r.asks_json),
            }
            for r in rows
        ],
    }


@router.get("/api/inspect/db-status")
def inspect_db_status(
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    """Persistence-layer status — table row counts + write-behind writer stats."""
    return db.status()
