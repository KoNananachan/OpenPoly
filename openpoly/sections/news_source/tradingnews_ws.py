"""TradingNews WebSocket news source.

Sync ``run()`` snapshots the in-process ring buffer (filtered by freshness +
urgency). The async WS task is started by ``start_async()`` (called by runtime
in an event-loop context) and stopped by ``stop_async()``. Pre-runtime, tests
and dev scripts can invoke them directly.

Secrets: ``api_key_ref`` (e.g. ``env:OPENPOLY_TRADINGNEWS_KEY``) is resolved at
``start_async()`` time, never stored in config.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from openpoly.news.ring_buffer import NewsRingBuffer
from openpoly.news.secrets import resolve as resolve_secret
from openpoly.news.ws_client import NewsWSClient, OnEventHook, OnItemHook
from openpoly.sections._base import SectionInput, SectionOutput

logger = logging.getLogger(__name__)


UrgencyFilter = Literal["all", "low", "medium", "high"]
URGENCY_RANK = {"low": 1, "medium": 2, "high": 3}


class TradingNewsWSConfig(BaseModel):
    endpoint: str = Field(
        default="wss://api.tradingnews.press/v1/stream",
        description="WebSocket endpoint URL.",
    )
    api_key_ref: str = Field(
        default="env:OPENPOLY_TRADINGNEWS_KEY",
        description="Reference to the API key (e.g. env:VAR_NAME).",
    )
    freshness_seconds: int = Field(
        default=1800,
        ge=1,
        le=86400,
        description="Only forward news younger than this when Analyzer ticks.",
    )
    urgency_filter: UrgencyFilter = Field(
        default="all",
        description="Minimum urgency level to forward.",
    )
    buffer_size: int = Field(
        default=1000,
        ge=10,
        le=100000,
        description="Max in-memory news items retained.",
    )


class TradingNewsWSSource:
    SECTION_TYPE = "news_source"
    SECTION_VERSION = "0.1.0"
    REQUIRES: list[str] = []
    Config = TradingNewsWSConfig

    def __init__(self, config: TradingNewsWSConfig) -> None:
        self.config = config
        self.buffer = NewsRingBuffer(maxsize=config.buffer_size)
        self._client: NewsWSClient | None = None
        self._task: asyncio.Task | None = None

    async def start_async(
        self,
        *,
        on_event: OnEventHook | None = None,
        on_item: OnItemHook | None = None,
    ) -> None:
        if self._client is not None:
            return
        api_key = resolve_secret(self.config.api_key_ref)
        # tradingnews uses ?api_key=... query auth (not headers). See docs:
        # https://docs.tradingnews.press/api-reference/websocket-stream.md
        sep = "&" if "?" in self.config.endpoint else "?"
        endpoint = f"{self.config.endpoint}{sep}api_key={quote(api_key, safe='')}"
        self._client = NewsWSClient(
            endpoint=endpoint,
            buffer=self.buffer,
            on_event=on_event,
            on_item=on_item,
            freshness_seconds=self.config.freshness_seconds,
        )
        self._task = asyncio.create_task(self._client.run_forever())

    async def stop_async(self) -> None:
        if self._client is None:
            return
        self._client.stop()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._client = None
        self._task = None

    def run(self, input: SectionInput) -> SectionOutput:
        now = time.time()
        items = self.buffer.read_since(now - self.config.freshness_seconds)
        items = [it for it in items if self._passes_urgency(it.urgency)]
        return SectionOutput(
            payload=items,
            verdict="ok",
            signals={"count": len(items), "buffer_total": len(self.buffer)},
        )

    def _passes_urgency(self, urgency: str) -> bool:
        if self.config.urgency_filter == "all":
            return True
        return URGENCY_RANK.get(urgency, 0) >= URGENCY_RANK[self.config.urgency_filter]

    @staticmethod
    def CONTRACT_TEST() -> None:
        cfg = TradingNewsWSConfig()
        inst = TradingNewsWSSource(cfg)
        out = inst.run(SectionInput(tick_type="warm"))
        assert out.verdict == "ok"
        assert out.payload == []
        assert out.signals["count"] == 0
