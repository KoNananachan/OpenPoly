"""FastAPI /api/sections/catalog endpoint shape."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from openpoly.api import main as api_main
from openpoly.sections._registry import scan


client = TestClient(api_main.app)


def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_catalog_empty_by_default() -> None:
    """Built-in dirs are empty in M9; endpoint should still return valid shape."""
    api_main.reset_catalog()
    r = client.get("/api/sections/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "sections" in body
    assert isinstance(body["sections"], list)


def test_catalog_serves_fixture_entries() -> None:
    """Force the catalog to scan the fixture package and verify good entry surfaces."""
    api_main.reset_catalog()
    fixture_entries = scan(packages=[("tests.fixtures.dummies", "builtin")])
    with patch.object(api_main, "_catalog_cache", fixture_entries):
        r = client.get("/api/sections/catalog")

    assert r.status_code == 200
    sections = r.json()["sections"]
    names = {s["name"] for s in sections}
    assert "DummyGoodAnalyzer" in names
    assert "DummyMissingConfig" not in names

    good = next(s for s in sections if s["name"] == "DummyGoodAnalyzer")
    assert good["type"] == "analyzer"
    assert good["version"] == "0.0.1"
    assert good["requires"] == ["llm"]
    assert good["source"] == "builtin"
    assert good["param_schema"]["properties"]["threshold"]["default"] == 0.5


def test_catalog_cache_resets() -> None:
    api_main.reset_catalog()
    r1 = client.get("/api/sections/catalog").json()
    api_main.reset_catalog()
    r2 = client.get("/api/sections/catalog").json()
    assert r1 == r2  # deterministic; just ensure reset hook does not crash
