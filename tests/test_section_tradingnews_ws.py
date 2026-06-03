from __future__ import annotations

import time

from openpoly.news.ring_buffer import NewsItem
from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.news_source.tradingnews_ws import (
    TradingNewsWSConfig,
    TradingNewsWSSource,
)


def _item(id_: str, urgency: str, ts: float) -> NewsItem:
    return NewsItem(
        id=id_,
        content="x",
        urgency=urgency,  # type: ignore[arg-type]
        sentiment=None,
        published_at=ts,
        received_at=ts,
    )


def test_section_in_default_catalog() -> None:
    entries = scan()
    matches = [e for e in entries if e.name == "TradingNewsWSSource"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry.type == "news_source"
    assert entry.source == "builtin"
    assert entry.version == "0.1.0"
    schema = entry.param_schema
    props = schema["properties"]
    for k in ("endpoint", "api_key_ref", "freshness_seconds", "urgency_filter", "buffer_size"):
        assert k in props


def test_run_empty_buffer_returns_ok_empty() -> None:
    inst = TradingNewsWSSource(TradingNewsWSConfig())
    out = inst.run(SectionInput(tick_type="hard"))
    assert out.verdict == "ok"
    assert out.payload == []
    assert out.signals["count"] == 0
    assert out.signals["buffer_total"] == 0


def test_run_freshness_filter() -> None:
    inst = TradingNewsWSSource(TradingNewsWSConfig(freshness_seconds=60))
    now = time.time()
    inst.buffer.append(_item("old", "high", now - 3600))
    inst.buffer.append(_item("new", "high", now))
    out = inst.run(SectionInput(tick_type="hard"))
    ids = [it.id for it in out.payload]
    assert ids == ["new"]


def test_run_urgency_filter_high() -> None:
    inst = TradingNewsWSSource(TradingNewsWSConfig(urgency_filter="high"))
    now = time.time()
    for u, name in (("low", "lo"), ("medium", "md"), ("high", "hi")):
        inst.buffer.append(_item(name, u, now))
    out = inst.run(SectionInput(tick_type="hard"))
    ids = [it.id for it in out.payload]
    assert ids == ["hi"]


def test_run_urgency_filter_medium_includes_high() -> None:
    inst = TradingNewsWSSource(TradingNewsWSConfig(urgency_filter="medium"))
    now = time.time()
    for u, name in (("low", "lo"), ("medium", "md"), ("high", "hi")):
        inst.buffer.append(_item(name, u, now))
    out = inst.run(SectionInput(tick_type="hard"))
    ids = sorted(it.id for it in out.payload)
    assert ids == ["hi", "md"]
