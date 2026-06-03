"""Endpoint tests for /api/secrets/local/* (S3).

Singleton ``LocalSecretStore`` is swapped to a tmp-path instance per test so
the user's real ``~/.openpoly/secrets.json`` is never touched. Every endpoint's
response is grepped for the sentinel value to guard against leakage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from openpoly.api.main import app
from openpoly.news import secret_store as _store_mod
from openpoly.news.secret_store import LocalSecretStore


SENTINEL_VALUE = "THIS_IS_SECRET_XYZ_42_DO_NOT_LEAK"


@pytest.fixture(autouse=True)
def _isolate_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    s = LocalSecretStore(path=tmp_path / "secrets.json")
    monkeypatch.setattr(_store_mod, "_singleton", s)
    return s


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _assert_no_value_leak(resp: Any) -> None:
    """Belt-and-braces: response text, headers, and JSON serialization
    must not contain the sentinel value."""
    assert SENTINEL_VALUE not in resp.text
    for k, v in resp.headers.items():
        assert SENTINEL_VALUE not in str(v), f"value leaked into header {k}"


# ---------- POST /api/secrets/local ----------


async def test_create_happy_path() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/secrets/local",
            json={"name": "tradingnews-main", "value": SENTINEL_VALUE},
        )
    finally:
        await client.aclose()
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["entry"]["name"] == "tradingnews-main"
    assert body["entry"]["created_at"] > 0
    assert "value" not in body["entry"]
    _assert_no_value_leak(r)


async def test_create_with_slashes_in_name() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/secrets/local",
            json={
                "name": "demo-baseline/news_source/tradingnews-main",
                "value": SENTINEL_VALUE,
            },
        )
    finally:
        await client.aclose()
    assert r.status_code == 200
    assert r.json()["entry"]["name"] == "demo-baseline/news_source/tradingnews-main"
    _assert_no_value_leak(r)


async def test_create_invalid_name_returns_400() -> None:
    client = await _client()
    try:
        for bad in ("/leading", "trailing/", "double//slash", "..", "a..b"):
            r = await client.post(
                "/api/secrets/local",
                json={"name": bad, "value": SENTINEL_VALUE},
            )
            assert r.status_code == 400, f"name={bad!r} unexpectedly accepted"
            _assert_no_value_leak(r)
    finally:
        await client.aclose()


async def test_create_empty_value_returns_400() -> None:
    client = await _client()
    try:
        r = await client.post(
            "/api/secrets/local",
            json={"name": "k", "value": ""},
        )
    finally:
        await client.aclose()
    assert r.status_code == 400


async def test_create_overwrites_keeps_created_at(_isolate_store: LocalSecretStore) -> None:
    client = await _client()
    try:
        r1 = await client.post(
            "/api/secrets/local",
            json={"name": "k", "value": SENTINEL_VALUE},
        )
        ts1 = r1.json()["entry"]["created_at"]
        r2 = await client.post(
            "/api/secrets/local",
            json={"name": "k", "value": "newval"},
        )
    finally:
        await client.aclose()
    assert r2.json()["entry"]["created_at"] == ts1
    assert _isolate_store.get("k") == "newval"


# ---------- GET /api/secrets/local ----------


async def test_list_empty_initially() -> None:
    client = await _client()
    try:
        r = await client.get("/api/secrets/local")
    finally:
        await client.aclose()
    assert r.status_code == 200
    assert r.json() == {"entries": []}


async def test_list_returns_entries_no_values(
    _isolate_store: LocalSecretStore,
) -> None:
    await _isolate_store.set("a", SENTINEL_VALUE)
    await _isolate_store.set("b", "another-secret")
    client = await _client()
    try:
        r = await client.get("/api/secrets/local")
    finally:
        await client.aclose()
    body = r.json()
    names = [e["name"] for e in body["entries"]]
    assert names == ["a", "b"]
    for e in body["entries"]:
        assert "value" not in e
        assert set(e.keys()) == {"name", "created_at"}
    _assert_no_value_leak(r)


async def test_list_prefix_filter(_isolate_store: LocalSecretStore) -> None:
    await _isolate_store.set("demo/a", SENTINEL_VALUE)
    await _isolate_store.set("demo/b", SENTINEL_VALUE)
    await _isolate_store.set("other/c", SENTINEL_VALUE)
    client = await _client()
    try:
        r = await client.get("/api/secrets/local", params={"prefix": "demo/"})
    finally:
        await client.aclose()
    names = [e["name"] for e in r.json()["entries"]]
    assert names == ["demo/a", "demo/b"]
    _assert_no_value_leak(r)


# ---------- DELETE /api/secrets/local/{name:path} ----------


async def test_delete_existing_returns_204(
    _isolate_store: LocalSecretStore,
) -> None:
    await _isolate_store.set("k", SENTINEL_VALUE)
    client = await _client()
    try:
        r = await client.delete("/api/secrets/local/k")
    finally:
        await client.aclose()
    assert r.status_code == 204
    assert r.text == ""
    assert _isolate_store.list_entries() == []


async def test_delete_with_slashes_in_name(
    _isolate_store: LocalSecretStore,
) -> None:
    await _isolate_store.set("demo/news/k1", SENTINEL_VALUE)
    client = await _client()
    try:
        r = await client.delete("/api/secrets/local/demo/news/k1")
    finally:
        await client.aclose()
    assert r.status_code == 204
    assert _isolate_store.list_entries() == []


async def test_delete_unknown_returns_404() -> None:
    client = await _client()
    try:
        r = await client.delete("/api/secrets/local/nope")
    finally:
        await client.aclose()
    assert r.status_code == 404


async def test_delete_invalid_name_returns_400() -> None:
    client = await _client()
    try:
        # Trailing slash would be normalized by httpx as a 404 from FastAPI's
        # routing, so use a name that hits the route but fails validation
        # (`..` segment).
        r = await client.delete("/api/secrets/local/a..b")
    finally:
        await client.aclose()
    assert r.status_code == 400


# ---------- value-leak across the full lifecycle ----------


async def test_no_value_leak_full_lifecycle(
    _isolate_store: LocalSecretStore,
) -> None:
    """One canonical sentinel through POST → GET → DELETE; assert it never
    surfaces in any response body or header."""
    name = "demo/news_source/tradingnews-main"
    client = await _client()
    try:
        r_post = await client.post(
            "/api/secrets/local",
            json={"name": name, "value": SENTINEL_VALUE},
        )
        r_list_all = await client.get("/api/secrets/local")
        r_list_pref = await client.get("/api/secrets/local", params={"prefix": "demo/"})
        r_del = await client.delete(f"/api/secrets/local/{name}")
        r_list_after = await client.get("/api/secrets/local")
    finally:
        await client.aclose()
    for resp in (r_post, r_list_all, r_list_pref, r_del, r_list_after):
        _assert_no_value_leak(resp)
