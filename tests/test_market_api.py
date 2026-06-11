"""Tests for openpoly.markets.polymarket_api — Gamma /events fetch layer.

Uses httpx.MockTransport so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from openpoly.markets.polymarket_api import (
    discover_events,
    fetch_held_condition_sides,
    fetch_markets_by_condition_id,
)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_flattens_markets_with_event():
    payload = [
        {"id": "e1", "title": "Event 1", "markets": [{"id": "m1"}, {"id": "m2"}]},
        {"id": "e2", "title": "Event 2", "markets": [{"id": "m3"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as client:
        pairs = await discover_events(client=client)

    assert [m["id"] for m, _ in pairs] == ["m1", "m2", "m3"]
    assert pairs[0][1]["id"] == "e1"  # parent event threaded through
    assert pairs[2][1]["id"] == "e2"


async def test_skips_events_without_markets():
    payload = [
        {"id": "e1", "markets": [{"id": "m1"}]},
        {"id": "e2"},  # no markets key
        {"id": "e3", "markets": "garbage"},  # markets not a list
        "not-an-event",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as client:
        pairs = await discover_events(client=client)

    assert [m["id"] for m, _ in pairs] == ["m1"]


async def test_non_list_payload_is_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async with _mock_client(handler) as client:
        assert await discover_events(client=client) == []


async def test_query_params():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        await discover_events(limit=50, client=client)

    assert "closed=false" in seen["url"]
    assert "order=volume24hr" in seen["url"]
    assert "ascending=false" in seen["url"]
    assert "limit=50" in seen["url"]


async def test_fetch_by_condition_id_passes_closed_true():
    # Settlement detection must request resolved markets; Gamma /markets
    # defaults to open-only, so closed=true is required or resolved markets
    # silently come back empty (the orphaned-position bug this fixes).
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        await fetch_markets_by_condition_id(["0xabc", "0xdef"], client=client)

    assert "closed=true" in seen["url"]
    # Repeated param, NOT comma-joined — Gamma returns nothing for "A,B".
    assert "condition_ids=0xabc" in seen["url"]
    assert "condition_ids=0xdef" in seen["url"]
    assert "0xabc%2C0xdef" not in seen["url"]  # guard against the old comma bug


async def test_fetch_by_condition_id_empty_input_no_call():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        assert await fetch_markets_by_condition_id([], client=client) == []
    assert called["n"] == 0  # empty input → no network call


async def test_retries_once_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, json=[{"id": "e1", "markets": [{"id": "m1"}]}])

    async with _mock_client(handler) as client:
        pairs = await discover_events(client=client)

    assert calls["n"] == 2
    assert [m["id"] for m, _ in pairs] == ["m1"]


async def test_raises_after_two_failures():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("down")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPError):
            await discover_events(client=client)

    assert calls["n"] == 2  # one retry, then give up


async def test_http_status_error_retried_then_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await discover_events(client=client)


# --- fetch_market_by_id tests ---


class _MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._json


@pytest.mark.asyncio
async def test_fetch_market_by_id_happy(monkeypatch):
    from openpoly.markets import polymarket_api

    sample = {
        "id": "2119070",
        "conditionId": "0xabc",
        "question": "Will 20 ships transit the Strait of Hormuz?",
        "slug": "hormuz",
        "clobTokenIds": '["yes_token", "no_token"]',
        "endDate": "2026-05-31T00:00:00Z",
        "bestBid": 0.48,
        "bestAsk": 0.50,
        "volume24hr": 25466.0,
        "liquidityNum": 10286.0,
        "feesEnabled": False,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "negRisk": False,
    }

    async def fake_get(self, url, params=None, timeout=None):
        assert url.endswith("/markets")
        assert params == {"id": "2119070"}
        return _MockResponse([sample])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    market = await polymarket_api.fetch_market_by_id("2119070")
    assert market is not None
    assert market.market_id == "2119070"
    assert market.yes_token_id == "yes_token"
    assert market.no_token_id == "no_token"


@pytest.mark.asyncio
async def test_fetch_market_by_id_empty_returns_none(monkeypatch):
    from openpoly.markets import polymarket_api

    async def fake_get(self, url, params=None, timeout=None):
        return _MockResponse([])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    market = await polymarket_api.fetch_market_by_id("0")
    assert market is None


@pytest.mark.asyncio
async def test_fetch_market_by_id_http_error_returns_none(monkeypatch):
    """HTTP errors propagate as None (caller treats as 'skip, retry next poll').

    Note: underlying _get_json retries once before raising; we let it raise and
    catch at the public boundary.
    """
    from openpoly.markets import polymarket_api

    call_count = {"n": 0}

    async def fake_get(self, url, params=None, timeout=None):
        call_count["n"] += 1
        return _MockResponse(None, status_code=500)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    market = await polymarket_api.fetch_market_by_id("2119070")
    assert market is None
    assert call_count["n"] == 2  # one retry


@pytest.mark.asyncio
async def test_fetch_market_by_id_normalize_returns_none(monkeypatch, caplog):
    """Valid HTTP + dict response, but missing required fields → normalize
    returns None. We log a warning and propagate None to the caller."""
    from openpoly.markets import polymarket_api

    incomplete = {"id": "2119070"}  # no conditionId, no clobTokenIds

    async def fake_get(self, url, params=None, timeout=None):
        return _MockResponse([incomplete])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with caplog.at_level("WARNING"):
        market = await polymarket_api.fetch_market_by_id("2119070")
    assert market is None
    assert any("missing required fields" in rec.message for rec in caplog.records), (
        "expected a warning log for silent-None case"
    )


# ---------- fetch_held_condition_sides (data-api positions) ----------


async def test_held_condition_sides_maps_outcome_to_side():
    """data-api /positions → set of (conditionId, side); only size>0, and
    outcome Yes/No mapped to yes/no."""
    payload = [
        {"conditionId": "0xaaa", "outcome": "Yes", "size": 34.0},
        {"conditionId": "0xbbb", "outcome": "No", "size": 18.0},
        {"conditionId": "0xccc", "outcome": "Yes", "size": 0.0},  # flat — excluded
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "data-api.polymarket.com" in str(request.url)
        assert request.url.params.get("user") == "0xFUNDER"
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as client:
        held = await fetch_held_condition_sides("0xFUNDER", client=client)

    assert held == {("0xaaa", "yes"), ("0xbbb", "no")}


async def test_held_condition_sides_empty_when_no_positions():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        held = await fetch_held_condition_sides("0xFUNDER", client=client)

    assert held == set()


# ---------- fetch_wallet_positions_value (data-api /value) ----------


async def test_wallet_positions_value_parses_singleton_list():
    """data-api /value → [{"user": ..., "value": float}] (verified against the live API)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "data-api.polymarket.com" in str(request.url)
        assert request.url.params.get("user") == "0xFUNDER"
        return httpx.Response(200, json=[{"user": "0xfunder", "value": 162.1992}])

    async with _mock_client(handler) as client:
        from openpoly.markets.polymarket_api import fetch_wallet_positions_value

        value = await fetch_wallet_positions_value("0xFUNDER", client=client)

    assert value == pytest.approx(162.1992)


async def test_wallet_positions_value_empty_list_is_zero():
    """No positions → empty list → 0.0 (a wallet with no holdings is worth 0,
    not unknown)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        from openpoly.markets.polymarket_api import fetch_wallet_positions_value

        value = await fetch_wallet_positions_value("0xFUNDER", client=client)

    assert value == 0.0


async def test_wallet_positions_value_bad_shape_is_none():
    """Unexpected shape → None (unknown), never a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async with _mock_client(handler) as client:
        from openpoly.markets.polymarket_api import fetch_wallet_positions_value

        value = await fetch_wallet_positions_value("0xFUNDER", client=client)

    assert value is None
