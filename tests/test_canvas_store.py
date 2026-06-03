"""Tests for canvas_store — persistence + content-hash rev (canvas-sync v2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openpoly.runtime.canvas_store import (
    compute_rev,
    load_template,
    load_template_with_rev,
    save_template,
)


@pytest.fixture(autouse=True)
def _isolate_canvas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "canvas.json"
    monkeypatch.setenv("OPENPOLY_CANVAS_STORE", str(p))
    return p


def _tpl() -> dict:
    return {
        "version": 3,
        "name": "t",
        "nodes": [
            {
                "id": "a",
                "sectionType": "entry",
                "position": {"x": 0, "y": 0},
                "config": {"min_edge": 0.05},
            }
        ],
        "edges": [],
    }


# ---------- compute_rev ----------


def test_compute_rev_is_deterministic() -> None:
    t = _tpl()
    assert compute_rev(t) == compute_rev(t)


def test_compute_rev_is_order_independent() -> None:
    """Canonical JSON sorts keys — two dicts that differ only in insertion
    order should hash to the same rev."""
    t1 = {"version": 3, "name": "x", "nodes": [], "edges": []}
    t2 = {"edges": [], "nodes": [], "name": "x", "version": 3}
    assert compute_rev(t1) == compute_rev(t2)


def test_compute_rev_changes_on_any_content_diff() -> None:
    t1 = _tpl()
    t2 = _tpl()
    t2["nodes"][0]["config"]["min_edge"] = 0.06  # one numeric change
    assert compute_rev(t1) != compute_rev(t2)


def test_compute_rev_is_sha256_hex() -> None:
    r = compute_rev(_tpl())
    assert isinstance(r, str)
    assert len(r) == 64
    int(r, 16)  # raises if non-hex


# ---------- save / load roundtrip ----------


def test_save_returns_matching_rev(_isolate_canvas: Path) -> None:
    tpl = _tpl()
    rev = save_template(tpl)
    assert rev == compute_rev(tpl)


def test_load_returns_what_save_wrote(_isolate_canvas: Path) -> None:
    tpl = _tpl()
    save_template(tpl)
    got = load_template()
    assert got == tpl


def test_load_template_with_rev_returns_tuple(_isolate_canvas: Path) -> None:
    tpl = _tpl()
    saved_rev = save_template(tpl)
    result = load_template_with_rev()
    assert result is not None
    got_tpl, got_rev = result
    assert got_tpl == tpl
    assert got_rev == saved_rev


def test_load_returns_none_when_missing(_isolate_canvas: Path) -> None:
    assert load_template() is None
    assert load_template_with_rev() is None


def test_load_returns_none_on_corrupt_json(_isolate_canvas: Path) -> None:
    _isolate_canvas.write_text("{not valid json")
    assert load_template() is None
    assert load_template_with_rev() is None


def test_save_is_atomic_no_tmp_leftover(_isolate_canvas: Path) -> None:
    save_template(_tpl())
    siblings = list(_isolate_canvas.parent.iterdir())
    assert [p.name for p in siblings] == [_isolate_canvas.name]
