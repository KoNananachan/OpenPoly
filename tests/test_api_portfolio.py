"""Endpoint tests for /api/positions and /api/fills (PF5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openpoly.api.main import app
from openpoly.api.portfolio_routes import get_portfolio_store
from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.portfolio import PortfolioStore


@pytest.fixture
def env(tmp_path):
    """A PortfolioStore on a throwaway DB, wired into the app via dependency
    override, plus a TestClient."""
    engine = make_engine(f"sqlite:///{tmp_path}/portfolio.db")
    init_db(engine)
    store = PortfolioStore(make_session_factory(engine))
    app.dependency_overrides[get_portfolio_store] = lambda: store
    yield store, TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def _open(store: PortfolioStore, market_id: str, side: str, token_id: str, ts: float = 100.0):
    return store.open_position(
        market_id=market_id,
        side=side,
        token_id=token_id,
        condition_id=f"0x{market_id}",
        price=0.42,
        qty=20.0,
        ts=ts,
        news_id="n1",
    )


def test_positions_empty(env) -> None:
    _store, client = env
    assert client.get("/api/positions").json() == {"positions": []}


def test_fills_empty(env) -> None:
    _store, client = env
    assert client.get("/api/fills").json() == {"fills": []}


def test_positions_returns_open_and_closed(env) -> None:
    store, client = env
    _open(store, "m1", "yes", "ty1", ts=100.0)
    h2 = _open(store, "m2", "no", "tn2", ts=101.0)
    store.close_position(
        h2.position_id,
        sell_price=0.50,
        ts=200.0,
        close_reason="take_profit",
        trigger="take_profit",
    )

    positions = client.get("/api/positions").json()["positions"]
    assert len(positions) == 2
    # Newest first — m2 opened last.
    assert positions[0]["market_id"] == "m2"
    assert positions[0]["status"] == "closed"
    assert positions[0]["close_reason"] == "take_profit"
    assert positions[0]["realized_pnl"] is not None
    assert positions[1]["market_id"] == "m1"
    assert positions[1]["status"] == "open"
    assert positions[1]["realized_pnl"] is None


def test_fills_returns_ledger(env) -> None:
    store, client = env
    h1 = _open(store, "m1", "yes", "ty1")
    store.close_position(
        h1.position_id,
        sell_price=0.50,
        ts=200.0,
        close_reason="stop_loss",
        trigger="stop_loss",
    )

    fills = client.get("/api/fills").json()["fills"]
    assert len(fills) == 2
    # Newest first — the sell.
    assert fills[0]["action"] == "sell"
    assert fills[0]["trigger"] == "stop_loss"
    assert fills[1]["action"] == "buy"
    assert fills[1]["news_id"] == "n1"


def test_limit_param_honored(env) -> None:
    store, client = env
    for i in range(5):
        _open(store, f"m{i}", "yes", f"ty{i}", ts=float(i))
    assert len(client.get("/api/positions?limit=2").json()["positions"]) == 2
    assert len(client.get("/api/fills?limit=1").json()["fills"]) == 1


# ---------- /api/portfolio/equity ----------


def test_equity_endpoint_empty(tmp_path) -> None:
    from openpoly.db.engine import get_session_factory

    engine = make_engine(f"sqlite:///{tmp_path}/equity.db")
    init_db(engine)
    factory = make_session_factory(engine)
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/portfolio/equity").json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
    assert body == {
        "points": [],
        "summary": {
            "realized": 0.0,
            "unrealized": 0.0,
            "total": 0.0,
            "open_positions": 0,
        },
    }


def test_equity_endpoint_with_position(tmp_path) -> None:
    from openpoly.db.engine import get_session_factory

    engine = make_engine(f"sqlite:///{tmp_path}/equity.db")
    init_db(engine)
    factory = make_session_factory(engine)
    PortfolioStore(factory).open_position(
        market_id="m1",
        side="yes",
        token_id="ty1",
        condition_id="0xc1",
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n1",
    )
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        body = TestClient(app).get("/api/portfolio/equity").json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
    assert body["summary"]["open_positions"] == 1
    assert len(body["points"]) >= 1
    assert set(body["points"][0].keys()) == {
        "ts",
        "equity",
        "realized",
        "unrealized",
    }


# ---------- /api/positions/{id} ----------


def test_get_position_by_id(env) -> None:
    store, client = env
    h = _open(store, "m1", "yes", "ty1", ts=100.0)
    body = client.get(f"/api/positions/{h.position_id}").json()
    assert body["id"] == h.position_id
    assert body["market_id"] == "m1"
    assert body["token_id"] == "ty1"
    assert body["status"] == "open"
    assert body["avg_entry_price"] == 0.42


def test_get_position_by_id_404(env) -> None:
    _store, client = env
    assert client.get("/api/positions/99999").status_code == 404


# ---------- PD2: market_question lookup ----------


def test_get_position_includes_market_question_when_catalogued(env) -> None:
    """Position's condition_id resolves via MarketStore.get_by_condition →
    response carries the human question text."""
    import json
    from openpoly.markets.manager import manager as msm
    from openpoly.markets.models import normalize_gamma_market
    from openpoly.markets.store import MarketStore, PollSummary

    store, client = env
    h = _open(store, "m1", "yes", "ty1")  # _open uses condition_id="0xm1"
    # Populate catalog with a market whose conditionId matches.
    raw = {
        "id": "m1",
        "conditionId": "0xm1",
        "question": "Will the U.S. invade Iran before 2027?",
        "slug": "iran-2027",
        "clobTokenIds": json.dumps(["yes-tok", "no-tok"]),
    }
    market = normalize_gamma_market(raw, event={"id": "e", "title": "E"})
    saved_store = msm.store
    try:
        fresh = MarketStore()
        fresh.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))
        msm.store = fresh
        body = client.get(f"/api/positions/{h.position_id}").json()
    finally:
        msm.store = saved_store
    assert body["market_question"] == "Will the U.S. invade Iran before 2027?"


def test_get_position_market_question_null_when_not_catalogued(env) -> None:
    """Market evicted / never catalogued → market_question is None (UI
    falls back to condition_id truncation)."""
    from openpoly.markets.manager import manager as msm
    from openpoly.markets.store import MarketStore

    store, client = env
    h = _open(store, "m1", "yes", "ty1")
    saved_store = msm.store
    try:
        msm.store = MarketStore()  # empty catalog
        body = client.get(f"/api/positions/{h.position_id}").json()
    finally:
        msm.store = saved_store
    assert body["market_question"] is None


# ---------- PD3: analyzer_decisions lookup ----------


def test_get_position_includes_analyzer_decisions_when_log_has_match(env) -> None:
    """analyzer_log carries one or more verdict=ok entries for our news_id →
    response surfaces them newest-first with the flattened shape."""
    from openpoly.runtime.section_log import AnalyzerCall, analyzer_log

    store, client = env
    h = _open(store, "m1", "yes", "ty1")  # _open uses news_id="n1"
    saved_entries = list(analyzer_log.entries())
    try:
        analyzer_log.reset()
        # Push two ok calls + one unrelated + one error (latter two must be filtered out).
        analyzer_log.append(
            AnalyzerCall(
                ts=100.0,
                news_id="n1",
                news_content_preview="x",
                urgency="high",
                verdict="ok",
                p_model=0.55,
                confidence="medium",
                market_id="m1",
                latency_ms=20,
                rationale="first attempt rationale",
            )
        )
        analyzer_log.append(
            AnalyzerCall(
                ts=101.0,
                news_id="n1",
                news_content_preview="x",
                urgency="high",
                verdict="ok",
                p_model=0.60,
                confidence="high",
                market_id="m1",
                latency_ms=18,
                rationale="second attempt rationale",
            )
        )
        analyzer_log.append(
            AnalyzerCall(
                ts=102.0,
                news_id="other-news",
                news_content_preview="y",
                urgency="high",
                verdict="ok",
                p_model=0.7,
                confidence="high",
                market_id="m9",
                latency_ms=15,
                rationale="unrelated news — must not appear",
            )
        )
        analyzer_log.append(
            AnalyzerCall(
                ts=103.0,
                news_id="n1",
                news_content_preview="x",
                urgency="high",
                verdict="error",
                p_model=None,
                confidence=None,
                market_id=None,
                latency_ms=2,
                error="LLM down",
                rationale=None,
            )
        )
        body = client.get(f"/api/positions/{h.position_id}").json()
    finally:
        analyzer_log.reset()
        for e in saved_entries:
            analyzer_log.append(e)

    decisions = body["analyzer_decisions"]
    assert isinstance(decisions, list)
    assert len(decisions) == 2  # error + unrelated filtered out
    # newest-first
    assert decisions[0]["rationale"] == "second attempt rationale"
    assert decisions[1]["rationale"] == "first attempt rationale"
    assert decisions[0]["p_model"] == 0.60
    assert decisions[0]["confidence"] == "high"
    assert decisions[0]["ts"] == 101.0
    # No noise fields
    assert set(decisions[0].keys()) == {"rationale", "p_model", "confidence", "ts"}


def test_get_position_analyzer_decisions_empty_when_no_match(env) -> None:
    """Common case for old positions: ring has been evicted → empty list
    (UI shows 'rationale unavailable')."""
    from openpoly.runtime.section_log import analyzer_log

    store, client = env
    h = _open(store, "m1", "yes", "ty1")
    saved_entries = list(analyzer_log.entries())
    try:
        analyzer_log.reset()  # nothing matches
        body = client.get(f"/api/positions/{h.position_id}").json()
    finally:
        analyzer_log.reset()
        for e in saved_entries:
            analyzer_log.append(e)
    assert body["analyzer_decisions"] == []


def test_get_position_analyzer_decisions_empty_when_news_id_null(env) -> None:
    """Paper / manual positions may have news_id=None — lookup must skip
    cleanly without scanning the ring."""
    store, client = env
    # Create position with news_id=None explicitly.
    h = store.open_position(
        market_id="m1",
        side="yes",
        token_id="ty1",
        condition_id="0xm1",
        price=0.42,
        qty=10.0,
        ts=100.0,
        news_id=None,
    )
    body = client.get(f"/api/positions/{h.position_id}").json()
    assert body["analyzer_decisions"] == []


# ---------- v15 PR1: list endpoint augments each row with question + decisions ----------


def test_list_positions_includes_market_question_and_analyzer_decisions(env) -> None:
    """list endpoint must surface market_question + analyzer_decisions per row
    (same shape as /positions/{id}). Card-style UI relies on these being
    available list-wide so it can render question / rationale without
    fanning out to /positions/{id} per row."""
    import json
    from openpoly.markets.manager import manager as msm
    from openpoly.markets.models import normalize_gamma_market
    from openpoly.markets.store import MarketStore, PollSummary
    from openpoly.runtime.section_log import AnalyzerCall, analyzer_log

    store, client = env
    _open(store, "m1", "yes", "ty1", ts=100.0)
    _open(store, "m2", "no", "tn2", ts=101.0)  # no catalog entry — must be None

    raw = {
        "id": "m1",
        "conditionId": "0xm1",
        "question": "Will the U.S. invade Iran before 2027?",
        "slug": "iran-2027",
        "clobTokenIds": json.dumps(["yes-tok", "no-tok"]),
    }
    market = normalize_gamma_market(raw, event={"id": "e", "title": "E"})

    saved_store = msm.store
    saved_entries = list(analyzer_log.entries())
    try:
        fresh = MarketStore()
        fresh.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))
        msm.store = fresh
        analyzer_log.reset()
        analyzer_log.append(
            AnalyzerCall(
                ts=99.0,
                news_id="n1",
                news_content_preview="x",
                urgency="high",
                verdict="ok",
                p_model=0.55,
                confidence="medium",
                market_id="m1",
                latency_ms=20,
                rationale="opened m1 because reasons",
            )
        )
        positions = client.get("/api/positions").json()["positions"]
    finally:
        msm.store = saved_store
        analyzer_log.reset()
        for e in saved_entries:
            analyzer_log.append(e)

    assert len(positions) == 2
    # newest-first: m2 opened at ts=101
    by_market = {p["market_id"]: p for p in positions}

    # m1: catalogued + analyzer match → both populated
    assert by_market["m1"]["market_question"] == "Will the U.S. invade Iran before 2027?"
    decisions = by_market["m1"]["analyzer_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["rationale"] == "opened m1 because reasons"
    assert set(decisions[0].keys()) == {"rationale", "p_model", "confidence", "ts"}

    # m2: not in catalog + no analyzer match for its news_id (n1 is m1's; m2 also
    # uses news_id="n1" per _open helper, but the rationale text says "m1" — the
    # filter is by news_id, not market_id, so m2 will *also* see the same call).
    # This is correct backend behavior — the helper does not gate on market.
    assert by_market["m2"]["market_question"] is None
    # m2's news_id is also "n1" (per _open default), so it sees the same call.
    assert len(by_market["m2"]["analyzer_decisions"]) == 1


def test_list_positions_market_question_null_when_no_catalog(env) -> None:
    """Empty catalog → every row has market_question=None (UI must still
    render — card header falls back to condition_id truncation)."""
    from openpoly.markets.manager import manager as msm
    from openpoly.markets.store import MarketStore

    store, client = env
    _open(store, "m1", "yes", "ty1", ts=100.0)
    _open(store, "m2", "no", "tn2", ts=101.0)

    saved_store = msm.store
    try:
        msm.store = MarketStore()  # empty
        positions = client.get("/api/positions").json()["positions"]
    finally:
        msm.store = saved_store

    assert len(positions) == 2
    for p in positions:
        assert p["market_question"] is None
        # analyzer_decisions still present (empty list when no log match)
        assert isinstance(p["analyzer_decisions"], list)
