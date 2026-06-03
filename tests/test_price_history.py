"""Tests for price-history parsing + fetch + recent_move.

``httpx.get`` is patched so tests never touch the network.
"""

from __future__ import annotations

import httpx
import pytest

from openpoly.markets.models import parse_price_history
from openpoly.markets.polymarket_api import fetch_price_history, recent_move


# ---------- parse_price_history ----------


def test_parse_sorts_oldest_first_and_drops_malformed() -> None:
    raw = {
        "history": [
            {"t": 200, "p": 0.3},
            {"t": 100, "p": 0.2},
            {"t": 300, "p": 0.35},
            "junk",
            {"t": None, "p": 0.4},
            {"t": 400},  # missing p
        ]
    }
    assert parse_price_history(raw) == [
        (100.0, 0.2),
        (200.0, 0.3),
        (300.0, 0.35),
    ]


def test_parse_non_dict_or_missing_history() -> None:
    assert parse_price_history([]) == []
    assert parse_price_history({"nope": 1}) == []
    assert parse_price_history({"history": "notalist"}) == []


# ---------- recent_move ----------


def test_recent_move_computes_window_delta() -> None:
    move = recent_move(
        "t",
        window_min=60,
        fetcher=lambda token_id, *, window_min: [
            (0.0, 0.20),
            (1800.0, 0.24),
            (3600.0, 0.27),
        ],
    )
    assert move == pytest.approx(0.07)  # last price - first price


def test_recent_move_none_on_too_few_points() -> None:
    assert (
        recent_move(
            "t",
            window_min=60,
            fetcher=lambda token_id, *, window_min: [(0.0, 0.2)],
        )
        is None
    )
    assert (
        recent_move(
            "t",
            window_min=60,
            fetcher=lambda token_id, *, window_min: [],
        )
        is None
    )


def test_recent_move_fails_open_on_fetch_error() -> None:
    def boom(token_id: str, *, window_min: int) -> list[tuple[float, float]]:
        raise RuntimeError("network down")

    assert recent_move("t", window_min=60, fetcher=boom) is None


# ---------- fetch_price_history ----------


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


def test_fetch_price_history_builds_params_and_parses(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp({"history": [{"t": 10, "p": 0.5}, {"t": 20, "p": 0.55}]})

    monkeypatch.setattr(httpx, "get", fake_get)
    points = fetch_price_history("tok-1", window_min=60, fidelity=5)
    assert points == [(10.0, 0.5), (20.0, 0.55)]
    assert str(captured["url"]).endswith("/prices-history")
    params = captured["params"]
    assert params["market"] == "tok-1"
    assert params["fidelity"] == "5"
    assert int(params["endTs"]) - int(params["startTs"]) == 60 * 60


def test_fetch_price_history_retries_once(monkeypatch) -> None:
    calls = {"n": 0}

    def flaky_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return _FakeResp({"history": [{"t": 1, "p": 0.4}]})

    monkeypatch.setattr(httpx, "get", flaky_get)
    points = fetch_price_history("tok-1", window_min=30)
    assert points == [(1.0, 0.4)]
    assert calls["n"] == 2
