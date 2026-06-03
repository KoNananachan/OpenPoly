"""POST /api/news/test: verify it can open a WS, surfaces sensible errors."""

from __future__ import annotations

import asyncio

import pytest
import websockets
from httpx import ASGITransport, AsyncClient

from openpoly.api.main import app


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_happy_path_returns_ok_with_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_NEWS_KEY", "testkey")

    received_key: dict[str, str] = {}

    async def handler(ws):
        # tradingnews-style auth: ?api_key=... in upgrade request path.
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(ws.request.path).query)
        received_key["value"] = qs.get("api_key", ["MISSING"])[0]
        # close immediately; the route just verifies the upgrade succeeded
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        endpoint = f"ws://127.0.0.1:{port}"
        client = await _client()
        try:
            r = await client.post(
                "/api/news/test",
                json={"endpoint": endpoint, "api_key_ref": "env:OPENPOLY_TEST_NEWS_KEY"},
            )
        finally:
            await client.aclose()

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert received_key.get("value") == "testkey"


async def test_missing_env_returns_secret_error() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/news/test",
            json={
                "endpoint": "ws://127.0.0.1:1",
                "api_key_ref": "env:OPENPOLY_DEFINITELY_NOT_SET",
            },
        )
    finally:
        await client.aclose()

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "secret resolve failed" in body["error"]


async def test_unreachable_endpoint_returns_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_NEWS_KEY", "k")
    client = await _client()
    try:
        # port 1 is reserved; connection should fail fast
        r = await client.post(
            "/api/news/test",
            json={
                "endpoint": "ws://127.0.0.1:1",
                "api_key_ref": "env:OPENPOLY_TEST_NEWS_KEY",
            },
        )
    finally:
        await client.aclose()

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["latency_ms"] is None
    assert body["error"]  # any non-empty error string


async def test_invalid_uri_returns_uri_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_NEWS_KEY", "k")
    client = await _client()
    try:
        r = await client.post(
            "/api/news/test",
            json={
                "endpoint": "not a uri at all",
                "api_key_ref": "env:OPENPOLY_TEST_NEWS_KEY",
            },
        )
    finally:
        await client.aclose()

    body = r.json()
    assert body["ok"] is False
    assert (
        "invalid" in body["error"].lower()
        or "uri" in body["error"].lower()
        or "connection" in body["error"].lower()
    )


async def test_keychain_scheme_returns_unsupported() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/news/test",
            json={
                "endpoint": "ws://127.0.0.1:1",
                "api_key_ref": "keychain:foo/bar",
            },
        )
    finally:
        await client.aclose()

    body = r.json()
    assert body["ok"] is False
    assert "not supported" in body["error"] or "keychain" in body["error"]


async def test_open_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server that accepts TCP but never completes WS upgrade → should hit timeout."""
    monkeypatch.setenv("OPENPOLY_TEST_NEWS_KEY", "k")
    # Patch the test timeout down so this case finishes quickly.
    from openpoly.api import news_routes

    monkeypatch.setattr(news_routes, "TEST_OPEN_TIMEOUT_SECS", 0.3)

    accepted = asyncio.Event()

    async def slow_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        accepted.set()
        # Read but never reply — the WS handshake will stall and the client times out.
        try:
            await reader.read(4096)
            await asyncio.sleep(2.0)
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(slow_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        client = await _client()
        try:
            r = await client.post(
                "/api/news/test",
                json={
                    "endpoint": f"ws://127.0.0.1:{port}",
                    "api_key_ref": "env:OPENPOLY_TEST_NEWS_KEY",
                },
            )
        finally:
            await client.aclose()

    body = r.json()
    assert body["ok"] is False
    assert (
        "timed out" in body["error"]
        or "timeout" in body["error"].lower()
        or "connect" in body["error"].lower()
    )
