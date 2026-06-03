"""Endpoint tests for /api/canvas/template (GET + PUT, canvas-sync v2).

Covers:
- bare GET/PUT roundtrip (rev computed from content)
- If-Match optimistic-lock semantics (first write / matching rev / stale rev)
- shape validation
- ETag header parity with body.rev field
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openpoly.api.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Isolate the canvas store to a tmp file so tests don't touch
    # ~/.openpoly/canvas.json on the dev machine.
    monkeypatch.setenv("OPENPOLY_CANVAS_STORE", str(tmp_path / "canvas.json"))
    return TestClient(app)


def _valid_template() -> dict:
    return {
        "version": 3,
        "name": "test",
        "nodes": [
            {
                "id": "analyzer-seed",
                "sectionType": "analyzer",
                "position": {"x": 0, "y": 0},
                "config": {
                    "llm_model": "claude-sonnet-4-5-20250929",
                    "api_key_ref": "local:yunwu-key",
                    "base_url": "https://yunwu.ai/v1",
                    "temperature": 0.2,
                },
            }
        ],
        "edges": [],
    }


def _strip_rev(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "rev"}


# ---------- GET / PUT roundtrip ----------


def test_get_returns_404_when_empty(client: TestClient) -> None:
    r = client.get("/api/canvas/template")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "no_template"


def test_put_first_write_accepts_no_if_match(client: TestClient) -> None:
    """First PUT against empty store needs no If-Match — there's nothing
    to be stale relative to."""
    tpl = _valid_template()
    put = client.put("/api/canvas/template", json=tpl)
    assert put.status_code == 200, put.text
    body = put.json()
    assert _strip_rev(body) == tpl
    assert isinstance(body.get("rev"), str)
    assert len(body["rev"]) == 64
    # ETag header carries the same rev as the body
    assert put.headers.get("etag") == body["rev"]


def test_get_after_put_returns_template_with_rev(client: TestClient) -> None:
    tpl = _valid_template()
    put = client.put("/api/canvas/template", json=tpl)
    saved_rev = put.json()["rev"]

    get = client.get("/api/canvas/template")
    assert get.status_code == 200
    body = get.json()
    assert _strip_rev(body) == tpl
    assert body["rev"] == saved_rev
    assert get.headers.get("etag") == saved_rev


def test_put_persists_across_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two TestClient instances on the same OPENPOLY_CANVAS_STORE path should
    see each other's writes — proves the backing file is the source of truth."""
    monkeypatch.setenv("OPENPOLY_CANVAS_STORE", str(tmp_path / "canvas.json"))
    c1 = TestClient(app)
    c1.put("/api/canvas/template", json=_valid_template())

    c2 = TestClient(app)
    r = c2.get("/api/canvas/template")
    assert r.status_code == 200
    assert _strip_rev(r.json()) == _valid_template()


# ---------- If-Match optimistic-lock ----------


def test_put_missing_if_match_on_existing_template_rejected(client: TestClient) -> None:
    """Operator must opt in to overwriting an existing canvas — bare PUT
    is a foot-gun (today's stale-localStorage bug)."""
    client.put("/api/canvas/template", json=_valid_template())
    # Second PUT with no If-Match → 400 if_match_required
    r = client.put("/api/canvas/template", json=_valid_template())
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "if_match_required"


def test_put_with_matching_if_match_succeeds(client: TestClient) -> None:
    first = client.put("/api/canvas/template", json=_valid_template())
    rev = first.json()["rev"]
    updated = _valid_template()
    updated["nodes"][0]["config"]["temperature"] = 0.5
    r = client.put(
        "/api/canvas/template",
        json=updated,
        headers={"If-Match": rev},
    )
    assert r.status_code == 200, r.text
    new_rev = r.json()["rev"]
    assert new_rev != rev  # content changed → rev changed


def test_put_with_stale_if_match_returns_409(client: TestClient) -> None:
    first = client.put("/api/canvas/template", json=_valid_template())
    rev1 = first.json()["rev"]

    # Backend-side change (operator scripts / second window)
    updated = _valid_template()
    updated["nodes"][0]["config"]["temperature"] = 0.5
    client.put(
        "/api/canvas/template",
        json=updated,
        headers={"If-Match": rev1},
    )
    # Stale write attempt using the OLD rev → 409 + current state in body
    again = _valid_template()
    again["nodes"][0]["config"]["temperature"] = 0.9
    r = client.put(
        "/api/canvas/template",
        json=again,
        headers={"If-Match": rev1},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "stale_rev"
    assert detail["current_rev"] is not None and detail["current_rev"] != rev1
    # current_rev's template field == what we'd get from a fresh GET
    fresh = client.get("/api/canvas/template").json()
    assert _strip_rev(detail["template"]) == _strip_rev(fresh)


def test_put_with_wildcard_if_match_force_overwrites(client: TestClient) -> None:
    """``If-Match: *`` is the explicit force-overwrite escape hatch (mirrors
    the standard HTTP-ETag semantics)."""
    client.put("/api/canvas/template", json=_valid_template())
    forced = _valid_template()
    forced["nodes"][0]["config"]["temperature"] = 0.99
    r = client.put(
        "/api/canvas/template",
        json=forced,
        headers={"If-Match": "*"},
    )
    assert r.status_code == 200, r.text


def test_put_strips_incoming_rev_before_persisting(client: TestClient) -> None:
    """Frontend may echo back the rev field it read; backend must strip it
    before persisting so the rev is purely content-derived."""
    tpl = _valid_template()
    tpl["rev"] = "deadbeef"
    put = client.put("/api/canvas/template", json=tpl)
    assert put.status_code == 200
    # The persisted file should not contain "rev"
    get = client.get("/api/canvas/template").json()
    persisted = _strip_rev(get)
    assert "rev" not in persisted


def test_put_first_write_with_stale_if_match_returns_409(client: TestClient) -> None:
    """Empty store + non-wildcard If-Match → 409 (caller thinks something
    was there, but it isn't)."""
    r = client.put(
        "/api/canvas/template",
        json=_valid_template(),
        headers={"If-Match": "some-rev"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "stale_rev"


# ---------- shape validation (unchanged from canvas-sync v1) ----------


def test_put_rejects_non_object_body(client: TestClient) -> None:
    r = client.put("/api/canvas/template", json=["not", "an", "object"])
    assert r.status_code in (400, 422)


def test_put_rejects_missing_version(client: TestClient) -> None:
    bad = _valid_template()
    del bad["version"]
    r = client.put("/api/canvas/template", json=bad)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_shape"


def test_put_rejects_string_version(client: TestClient) -> None:
    bad = _valid_template()
    bad["version"] = "3"
    r = client.put("/api/canvas/template", json=bad)
    assert r.status_code == 400


def test_put_rejects_missing_nodes(client: TestClient) -> None:
    bad = _valid_template()
    del bad["nodes"]
    r = client.put("/api/canvas/template", json=bad)
    assert r.status_code == 400


def test_put_rejects_non_list_nodes(client: TestClient) -> None:
    bad = _valid_template()
    bad["nodes"] = "should-be-array"
    r = client.put("/api/canvas/template", json=bad)
    assert r.status_code == 400


def test_put_accepts_empty_nodes(client: TestClient) -> None:
    tpl = {"version": 3, "name": "blank", "nodes": [], "edges": []}
    r = client.put("/api/canvas/template", json=tpl)
    assert r.status_code == 200


def test_put_preserves_unknown_top_level_fields(client: TestClient) -> None:
    tpl = _valid_template()
    tpl["custom_metadata"] = {"layout_version": 42, "favorite": True}
    client.put("/api/canvas/template", json=tpl)
    got = client.get("/api/canvas/template").json()
    assert got["custom_metadata"] == {"layout_version": 42, "favorite": True}
