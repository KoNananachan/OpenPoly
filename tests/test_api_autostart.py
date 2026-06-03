"""Lifespan auto-start of the news + market sources.

``OPENPOLY_AUTOSTART_SOURCES`` defaults on; conftest sets it to ``0`` for the
session, so each test here sets it explicitly.
"""

from __future__ import annotations

from typing import Any

import pytest

from openpoly.api.main import _autostart_enabled, app, lifespan
from openpoly.markets.manager import manager as market_manager
from openpoly.news.manager import manager as news_manager


def test_autostart_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENPOLY_AUTOSTART_SOURCES", raising=False)
    assert _autostart_enabled() is True  # on by default
    monkeypatch.setenv("OPENPOLY_AUTOSTART_SOURCES", "0")
    assert _autostart_enabled() is False
    monkeypatch.setenv("OPENPOLY_AUTOSTART_SOURCES", "1")
    assert _autostart_enabled() is True


async def test_lifespan_autostarts_both_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPOLY_AUTOSTART_SOURCES", "1")
    market_calls: list[Any] = []
    news_calls: list[Any] = []

    async def fake_market_start(config: Any) -> None:
        market_calls.append(config)

    async def fake_news_start(config: Any) -> None:
        news_calls.append(config)

    monkeypatch.setattr(market_manager, "start", fake_market_start)
    monkeypatch.setattr(news_manager, "start", fake_news_start)

    async with lifespan(app):
        pass

    assert len(market_calls) == 1
    assert len(news_calls) == 1
    # News autostart passes the full config dict with the local key ref.
    assert news_calls[0]["api_key_ref"] == "local:tradingnews-key"
    assert "endpoint" in news_calls[0]


async def test_lifespan_skips_autostart_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPOLY_AUTOSTART_SOURCES", "0")
    market_calls: list[Any] = []
    news_calls: list[Any] = []

    async def fake_market_start(config: Any) -> None:
        market_calls.append(config)

    async def fake_news_start(config: Any) -> None:
        news_calls.append(config)

    monkeypatch.setattr(market_manager, "start", fake_market_start)
    monkeypatch.setattr(news_manager, "start", fake_news_start)

    async with lifespan(app):
        pass

    assert market_calls == []
    assert news_calls == []
