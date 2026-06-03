"""GET /api/inspect/* — inspect read-side endpoints (B3)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openpoly.api.inspect_routes import get_database_manager
from openpoly.api.main import app
from openpoly.db.book_store import make_order_book_sink
from openpoly.db.engine import (
    get_session_factory,
    init_db,
    make_engine,
    make_session_factory,
)
from openpoly.db.manager import DatabaseManager
from openpoly.db.news_store import make_news_sink
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import OrderBook, normalize_gamma_market
from openpoly.markets.store import MarketStore, PollSummary
from openpoly.news.ring_buffer import NewsItem


def _raw(market_id: str):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": f"Q-{market_id}?",
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


def _book(token_id: str) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        ts=100.0,
        bids=[(0.40, 100.0), (0.39, 50.0)],
        asks=[(0.42, 80.0), (0.43, 40.0)],
    )


def _news(news_id: str, received_at: float) -> NewsItem:
    return NewsItem(
        id=news_id,
        content=f"c-{news_id}",
        urgency="high",
        sentiment=None,
        published_at=received_at,
        received_at=received_at,
    )


@pytest.fixture(autouse=True)
def _reset_market_store():
    market_source_manager.store = MarketStore()
    yield
    market_source_manager.store = MarketStore()


def _seed_news(tmp_path, items):
    engine = make_engine(f"sqlite:///{tmp_path / 'news.db'}")
    init_db(engine)
    factory = make_session_factory(engine)
    make_news_sink(factory)(items)
    return factory


# ---------- /api/inspect/markets ----------


def test_inspect_markets_empty():
    body = TestClient(app).get("/api/inspect/markets").json()
    assert body["catalog_size"] == 0
    assert body["markets"] == []


def test_inspect_markets_with_order_book_prices():
    store = market_source_manager.store
    raw, event = _raw("m1")
    market = normalize_gamma_market(raw, event=event)
    assert market is not None
    store.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))
    store.set_order_books([_book("yes-m1"), _book("no-m1")])

    body = TestClient(app).get("/api/inspect/markets").json()
    assert body["catalog_size"] == 1
    assert body["order_book_count"] == 2
    mk = body["markets"][0]
    assert mk["market_id"] == "m1"
    assert mk["question"] == "Q-m1?"
    assert mk["best_bid"] == 0.40
    assert mk["best_ask"] == 0.42
    assert mk["mid"] == pytest.approx(0.41)
    assert mk["price_ts"] == 100.0
    # NO side now sampled too.
    assert mk["no_token_id"] == "no-m1"
    assert mk["no_best_bid"] == 0.40
    assert mk["no_best_ask"] == 0.42


def test_inspect_markets_without_order_book_has_null_price():
    store = market_source_manager.store
    raw, event = _raw("m1")
    market = normalize_gamma_market(raw, event=event)
    assert market is not None
    store.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))

    mk = TestClient(app).get("/api/inspect/markets").json()["markets"][0]
    assert mk["best_bid"] is None
    assert mk["mid"] is None
    assert mk["price_ts"] is None


# ---------- /api/inspect/news ----------


def test_inspect_news_returns_persisted_newest_first(tmp_path):
    factory = _seed_news(tmp_path, [_news("n1", 10.0), _news("n2", 20.0), _news("n3", 30.0)])
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/news").json()
    finally:
        app.dependency_overrides.clear()
    assert body["count"] == 3
    assert [n["news_id"] for n in body["news"]] == ["n3", "n2", "n1"]
    assert body["news"][0]["content"] == "c-n3"


def test_inspect_news_empty(tmp_path):
    factory = _seed_news(tmp_path, [])
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/news").json()
    finally:
        app.dependency_overrides.clear()
    assert body == {"count": 0, "news": []}


def test_inspect_news_limit(tmp_path):
    factory = _seed_news(tmp_path, [_news(f"n{i}", float(i)) for i in range(10)])
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/news?limit=3").json()
    finally:
        app.dependency_overrides.clear()
    assert body["count"] == 3
    assert [n["news_id"] for n in body["news"]] == ["n9", "n8", "n7"]


# ---------- /api/inspect/order-books ----------


def _seed_order_books(tmp_path, books):
    engine = make_engine(f"sqlite:///{tmp_path / 'ob.db'}")
    init_db(engine)
    factory = make_session_factory(engine)
    make_order_book_sink(factory)(books)
    return factory


def test_inspect_order_books_returns_persisted_newest_first(tmp_path):
    factory = _seed_order_books(tmp_path, [_book("t1"), _book("t2"), _book("t3")])
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/order-books").json()
    finally:
        app.dependency_overrides.clear()
    assert body["count"] == 3
    assert [ob["token_id"] for ob in body["order_books"]] == ["t3", "t2", "t1"]
    ob = body["order_books"][0]
    assert ob["bids"] == [[0.40, 100.0], [0.39, 50.0]]
    assert ob["asks"] == [[0.42, 80.0], [0.43, 40.0]]


def test_inspect_order_books_empty(tmp_path):
    factory = _seed_order_books(tmp_path, [])
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/order-books").json()
    finally:
        app.dependency_overrides.clear()
    assert body == {"count": 0, "order_books": []}


def test_inspect_order_books_limit(tmp_path):
    factory = _seed_order_books(tmp_path, [_book(f"t{i}") for i in range(10)])
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/order-books?limit=3").json()
    finally:
        app.dependency_overrides.clear()
    assert body["count"] == 3
    assert [ob["token_id"] for ob in body["order_books"]] == ["t9", "t8", "t7"]


# ---------- /api/inspect/db-status ----------


def test_inspect_db_status_unstarted_manager():
    app.dependency_overrides[get_database_manager] = lambda: DatabaseManager()
    try:
        body = TestClient(app).get("/api/inspect/db-status").json()
    finally:
        app.dependency_overrides.clear()
    assert body["tables"] == {}
    assert body["writers"] == {"order_book": None, "news": None}


# ---------- /api/inspect/order-books/{token_id} ----------


def test_order_book_history_filters_by_token(tmp_path) -> None:
    import json as _json

    from openpoly.db.engine import (
        get_session_factory,
        init_db,
        make_engine,
        make_session_factory,
    )
    from openpoly.db.tables import OrderBookSnapshot

    engine = make_engine(f"sqlite:///{tmp_path}/ob.db")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        s.add_all(
            [
                OrderBookSnapshot(
                    token_id="tok-A",
                    recorded_at=100.0,
                    bids_json=_json.dumps([[0.40, 10.0]]),
                    asks_json=_json.dumps([[0.42, 10.0]]),
                ),
                OrderBookSnapshot(
                    token_id="tok-A",
                    recorded_at=200.0,
                    bids_json=_json.dumps([[0.41, 10.0]]),
                    asks_json=_json.dumps([[0.43, 10.0]]),
                ),
                OrderBookSnapshot(
                    token_id="tok-B",
                    recorded_at=150.0,
                    bids_json=_json.dumps([[0.10, 5.0]]),
                    asks_json=_json.dumps([[0.12, 5.0]]),
                ),
            ]
        )
        s.commit()
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/order-books/tok-A").json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
    assert body["token_id"] == "tok-A"
    assert body["count"] == 2
    assert [s["recorded_at"] for s in body["snapshots"]] == [100.0, 200.0]
    assert body["snapshots"][0]["bids"] == [[0.40, 10.0]]
    assert body["snapshots"][0]["asks"] == [[0.42, 10.0]]


def test_order_book_history_since_until(tmp_path) -> None:
    import json as _json

    from openpoly.db.engine import (
        get_session_factory,
        init_db,
        make_engine,
        make_session_factory,
    )
    from openpoly.db.tables import OrderBookSnapshot

    engine = make_engine(f"sqlite:///{tmp_path}/ob.db")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        for ts in (100.0, 200.0, 300.0):
            s.add(
                OrderBookSnapshot(
                    token_id="tok-A",
                    recorded_at=ts,
                    bids_json=_json.dumps([[0.40, 1.0]]),
                    asks_json=_json.dumps([[0.42, 1.0]]),
                )
            )
        s.commit()
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/order-books/tok-A?since=150&until=250").json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
    assert [s["recorded_at"] for s in body["snapshots"]] == [200.0]


def test_order_book_history_empty(tmp_path) -> None:
    from openpoly.db.engine import (
        get_session_factory,
        init_db,
        make_engine,
        make_session_factory,
    )

    engine = make_engine(f"sqlite:///{tmp_path}/ob.db")
    init_db(engine)
    factory = make_session_factory(engine)
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/inspect/order-books/nope").json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
    assert body == {"token_id": "nope", "count": 0, "snapshots": []}
