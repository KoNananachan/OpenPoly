from __future__ import annotations

import pytest

from openpoly.news.ring_buffer import NewsItem, NewsRingBuffer


def _make(id_: str, ts: float, urgency: str = "medium") -> NewsItem:
    return NewsItem(
        id=id_,
        content="c",
        urgency=urgency,  # type: ignore[arg-type]
        sentiment=None,
        published_at=ts,
        received_at=ts,
    )


def test_append_and_len() -> None:
    buf = NewsRingBuffer(maxsize=10)
    assert len(buf) == 0
    buf.append(_make("a", 1.0))
    buf.append(_make("b", 2.0))
    assert len(buf) == 2


def test_bounded_eviction_keeps_newest() -> None:
    buf = NewsRingBuffer(maxsize=3)
    for i in range(5):
        buf.append(_make(f"n{i}", float(i)))
    snap = buf.snapshot()
    assert [it.id for it in snap] == ["n2", "n3", "n4"]


def test_read_since_window() -> None:
    buf = NewsRingBuffer(maxsize=10)
    for i in range(5):
        buf.append(_make(f"n{i}", float(i)))
    items = buf.read_since(2.5)
    assert [it.id for it in items] == ["n3", "n4"]


def test_read_since_all_when_zero_threshold() -> None:
    buf = NewsRingBuffer(maxsize=10)
    buf.append(_make("a", 5.0))
    assert len(buf.read_since(0.0)) == 1


def test_zero_maxsize_raises() -> None:
    with pytest.raises(ValueError):
        NewsRingBuffer(maxsize=0)


def test_negative_maxsize_raises() -> None:
    with pytest.raises(ValueError):
        NewsRingBuffer(maxsize=-1)
