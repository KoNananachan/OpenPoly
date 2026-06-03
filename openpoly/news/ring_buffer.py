"""Bounded ring buffer for news items.

Single-producer (WS callback writes appends) / multi-consumer (section.run reads
snapshots). collections.deque append/popleft are atomic under CPython's GIL;
consumers snapshot via list(deque) to avoid iteration-during-mutation hazards.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

# Urgency is upstream-defined; tradingnews uses values like "regular" / "high"
# but other providers may differ. Treat as free-form string and rely on the
# section's urgency_filter for semantic policing.
Urgency = str


@dataclass(frozen=True)
class NewsItem:
    id: str
    content: str
    urgency: Urgency
    sentiment: float | None
    published_at: float  # epoch seconds (UTC); from upstream
    received_at: float  # epoch seconds (UTC); our wall clock at ingest
    raw: dict[str, Any] | None = None


class NewsRingBuffer:
    def __init__(self, maxsize: int = 1000) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._buf: deque[NewsItem] = deque(maxlen=maxsize)

    def append(self, item: NewsItem) -> None:
        self._buf.append(item)

    def __len__(self) -> int:
        return len(self._buf)

    def snapshot(self) -> list[NewsItem]:
        return list(self._buf)

    def read_since(self, ts: float) -> list[NewsItem]:
        """Return items whose received_at >= ts. Order: oldest → newest."""
        return [it for it in list(self._buf) if it.received_at >= ts]
