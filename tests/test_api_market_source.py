"""POST/GET /api/market/source/* — manager lifecycle endpoints (MS5).

Resets the manager module singleton between tests with a no-network fake
fetcher so polling never hits the live Gamma API.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from openpoly.api.main import app, lifespan
from openpoly.markets import manager as manager_module
from openpoly.markets.manager import MarketSourceManager, manager


def _raw_pair(market_id: str = "m1"):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": "Q?",
        "clobTokenIds": '["y", "n"]',
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
    event = {"id": "e1", "title": "Event", "tags": []}
    return (raw, event)


async def _fake_fetcher(*, limit: int) -> list:
    return [_raw_pair("a"), _raw_pair("b")]


@pytest.fixture(autouse=True)
async def _reset_manager() -> Any:
    """Reset the singleton between tests with a no-network fetcher."""
    await manager.shutdown()
    MarketSourceManager.__init__(manager, fetcher=_fake_fetcher)
    yield
    await manager.shutdown()
    MarketSourceManager.__init__(manager)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _wait_status(client: AsyncClient, predicate, *, iterations: int = 200) -> dict[str, Any]:
    for _ in range(iterations):
        snap = (await client.get("/api/market/source/status")).json()["snapshot"]
        if predicate(snap):
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError("status condition not met within timeout")


# ---------- status ----------


async def test_status_initial_is_stopped() -> None:
    client = await _client()
    try:
        r = await client.get("/api/market/source/status")
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    snap = body["snapshot"]
    assert snap["state"] == "stopped"
    assert snap["catalog_size"] == 0
    assert snap["poll_count"] == 0
    assert snap["running_config"] is None
    assert snap["last_poll"] is None
    assert snap["events"] == []


# ---------- start ----------


async def test_start_then_poll_populates_catalog() -> None:
    client = await _client()
    try:
        r = await client.post("/api/market/source/start", json={})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["snapshot"]["state"] == "running"

        snap = await _wait_status(client, lambda s: s["poll_count"] >= 1)
        assert snap["catalog_size"] == 2
        assert snap["last_poll"]["kept"] == 2
        kinds = [e["kind"] for e in snap["events"]]
        assert "started" in kinds
        assert "poll_ok" in kinds
    finally:
        await client.aclose()


async def test_start_with_config_reflected_in_running_config() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/market/source/start",
            json={"poll_interval_seconds": 1234, "gamma_limit": 50},
        )
    finally:
        await client.aclose()
    rc = r.json()["snapshot"]["running_config"]
    assert rc["poll_interval_seconds"] == 1234
    assert rc["gamma_limit"] == 50


async def test_start_is_idempotent() -> None:
    client = await _client()
    try:
        r1 = await client.post("/api/market/source/start", json={})
        r2 = await client.post("/api/market/source/start", json={})
    finally:
        await client.aclose()
    assert r1.json()["snapshot"]["state"] == "running"
    assert r2.json()["ok"] is True
    assert r2.json()["snapshot"]["state"] == "running"


# ---------- stop ----------


async def test_stop_when_running() -> None:
    client = await _client()
    try:
        await client.post("/api/market/source/start", json={})
        r = await client.post("/api/market/source/stop")
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshot"]["state"] == "stopped"
    assert body["snapshot"]["running_config"] is None


async def test_stop_when_already_stopped_is_noop() -> None:
    client = await _client()
    try:
        r = await client.post("/api/market/source/stop")
    finally:
        await client.aclose()
    assert r.json()["ok"] is True
    assert r.json()["snapshot"]["state"] == "stopped"


# ---------- lifespan ----------


async def test_lifespan_calls_market_manager_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    original = manager_module.manager.shutdown

    async def fake_shutdown() -> None:
        called.append(True)
        await original()

    monkeypatch.setattr(manager_module.manager, "shutdown", fake_shutdown)

    async with lifespan(app):
        pass

    assert called == [True]
