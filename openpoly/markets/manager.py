"""Market-source runtime manager.

Owns the discovery polling loop: every ``poll_interval_seconds`` it fetches the
Gamma event universe, normalizes + discovery-filters it, and atomically swaps
the ``MarketStore`` catalog. Tracks a status snapshot and a bounded event ring
for the Live tab. Lifecycle (start/stop) is serialized with an ``asyncio.Lock``.

The polling loop lives here rather than in the section impl: a poll source has
no inherent forever-task the way a WebSocket does, so the engine sits with the
store + status it produces. The ``PolymarketSource`` section (MS4) is a thin
section-protocol wrapper that reads this manager's store.

The ``fetcher`` seam is injectable so tests run without network.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from openpoly.markets.filters import MarketFilterConfig, filter_markets
from openpoly.markets.models import Market, OrderBook, normalize_gamma_market
from openpoly.markets.polymarket_api import (
    EventMarketPair,
    discover_events,
    fetch_book,
    fetch_market_by_id,
)
from openpoly.markets.store import MarketStore, PollSummary

logger = logging.getLogger(__name__)

State = Literal["stopped", "running", "error"]

EVENT_RING_MAXLEN = 200

# Injectable discovery seam: (*, limit) -> list[(raw_market, event)].
Fetcher = Callable[..., Awaitable[list[EventMarketPair]]]

# Injectable order-book seam: (token_id) -> OrderBook.
BookFetcher = Callable[..., Awaitable[OrderBook]]

# Injectable holding-sync seam: (market_id) -> Market | None.
MarketFetcher = Callable[..., Awaitable["Market | None"]]

# Max concurrent /book fetches per sample cycle (a prior project probed CLOB at ~100).
BOOK_SAMPLE_CONCURRENCY = 16


class MarketSourceConfig(BaseModel):
    """Runtime config for the market-source polling loop. Pydantic so it can be
    reused as the PolymarketSource section Config and auto-rendered in the UI."""

    poll_interval_seconds: int = Field(
        default=900,
        ge=10,
        le=86_400,
        description="Seconds between discovery polls.",
    )
    gamma_limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Number of events to request from Gamma per poll.",
    )
    book_sample_interval_seconds: int = Field(
        default=60,
        ge=5,
        le=3600,
        description="Seconds between order book depth samples.",
    )
    filter: MarketFilterConfig = Field(default_factory=MarketFilterConfig)


@dataclass(frozen=True)
class LogEvent:
    ts: float
    kind: str  # started|stopped|poll_ok|poll_error|book_sample_ok|book_sample_error
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class StatusSnapshot:
    state: State
    started_at: float | None
    last_poll_at: float | None
    catalog_size: int
    poll_count: int
    last_error: str | None
    running_config: dict[str, Any] | None
    last_poll: dict[str, Any] | None  # PollSummary.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started_at": self.started_at,
            "last_poll_at": self.last_poll_at,
            "catalog_size": self.catalog_size,
            "poll_count": self.poll_count,
            "last_error": self.last_error,
            "running_config": self.running_config,
            "last_poll": self.last_poll,
        }


class MarketSourceManager:
    """Singleton-style manager for the market-source discovery loop.

    Not thread-safe; FastAPI runs a single asyncio loop. Concurrent start/stop
    are serialized via ``_lock``.
    """

    def __init__(
        self,
        fetcher: Fetcher | None = None,
        book_fetcher: BookFetcher | None = None,
        market_fetcher: MarketFetcher | None = None,
        portfolio_store: Any | None = None,
    ) -> None:
        self._fetcher: Fetcher = fetcher or discover_events
        self._book_fetcher: BookFetcher = book_fetcher or fetch_book
        self._market_fetcher: MarketFetcher = market_fetcher or fetch_market_by_id
        self._portfolio_store = portfolio_store
        self.store = MarketStore()
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._events: deque[LogEvent] = deque(maxlen=EVENT_RING_MAXLEN)
        self._state: State = "stopped"
        self._config: MarketSourceConfig | None = None
        self._running_config: dict[str, Any] | None = None
        self._started_at: float | None = None
        self._poll_count: int = 0
        self._last_error: str | None = None
        self._book_persist: Callable[[OrderBook], None] | None = None

    # ---------- lifecycle ----------

    async def start(self, config: MarketSourceConfig) -> StatusSnapshot:
        async with self._lock:
            if self._state != "stopped":
                return self._snapshot()
            self._config = config
            self._running_config = config.model_dump()
            self._started_at = time.time()
            self._poll_count = 0
            self._last_error = None
            self._state = "running"
            self._stop.clear()
            self._tasks = [
                asyncio.create_task(self._run_loop()),
                asyncio.create_task(self._run_book_loop()),
            ]
            self._record_event("started")
            return self._snapshot()

    async def stop(self) -> StatusSnapshot:
        async with self._lock:
            if self._state == "stopped":
                return self._snapshot()
            self._stop.set()
            for task in self._tasks:
                task.cancel()
            for task in self._tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._tasks = []
            self._state = "stopped"
            self._started_at = None
            self._running_config = None
            self._record_event("stopped")
            return self._snapshot()

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            await self.stop()

    def set_book_persist(self, persist: Callable[[OrderBook], None] | None) -> None:
        """Install / clear the order-book persist hook — the write-behind
        writer's ``enqueue``. Wired by the FastAPI lifespan; ``None`` in tests."""
        self._book_persist = persist

    def set_portfolio_store(self, store: Any | None) -> None:
        """Install / clear the portfolio_store reference — wired by the FastAPI
        lifespan after PortfolioStore is constructed. ``None`` in tests that
        don't need holding sync.
        """
        self._portfolio_store = store

    # ---------- read queries ----------

    def status(self) -> StatusSnapshot:
        return self._snapshot()

    def events(self, limit: int | None = None) -> list[LogEvent]:
        all_events = list(self._events)
        if limit is None or limit >= len(all_events):
            return all_events
        return all_events[-limit:] if limit > 0 else []

    # ---------- internals ----------

    def _record_event(self, kind: str, detail: str | None = None) -> None:
        self._events.append(LogEvent(ts=time.time(), kind=kind, detail=detail))

    async def _poll_once(self) -> PollSummary:
        """One discovery cycle: fetch -> normalize -> filter -> store.replace.

        Then holding sync: ensure every open-position market is in the catalog
        even if its event ranked out of the top-N discovery window. Counts are
        folded into the PollSummary so /api/market/source/status reports them.
        """
        assert self._config is not None
        pairs = await self._fetcher(limit=self._config.gamma_limit)
        markets: list[Market] = []
        for raw, event in pairs:
            market = normalize_gamma_market(raw, event=event)
            if market is not None:
                markets.append(market)
        report = filter_markets(markets, self._config.filter)
        summary = PollSummary(
            ts=time.time(),
            fetched=len(pairs),
            kept=len(report.kept),
            reason_counts=report.reason_counts,
        )
        self.store.replace(report.kept, summary)

        synced, failed = await self._sync_holdings_once()
        if synced or failed:
            summary = dataclasses.replace(
                summary,
                holding_synced=synced,
                holding_sync_failed=failed,
            )
            self.store.update_last_poll(summary)
        return summary

    async def _sync_holdings_once(self) -> tuple[int, int]:
        """One holding-sync cycle: pull every open-position market that's not
        already in the discovery catalog. Returns (synced, failed).

        Failure modes are all swallowed — main poll never crashes because the
        portfolio DB hiccupped or gamma rate-limited a single lookup.
        """
        if self._portfolio_store is None:
            return 0, 0
        try:
            positions = self._portfolio_store.get_open_positions()
        except Exception as exc:  # noqa: BLE001 — main poll must survive DB hiccup
            logger.warning("holding sync: get_open_positions failed: %s", exc)
            self._record_event("holding_sync_error", repr(exc)[:200])
            return 0, 0

        open_market_ids = {p.market_id for p in positions}
        missing = open_market_ids - self.store.snapshot_ids()
        if not missing:
            return 0, 0

        synced = failed = 0
        for mid in missing:
            market = await self._market_fetcher(mid)
            if market is None:
                failed += 1
                continue
            self.store.union([market])
            synced += 1

        if synced or failed:
            self._record_event("holding_sync_ok", f"synced={synced} failed={failed}")
        return synced, failed

    async def _run_loop(self) -> None:
        assert self._config is not None
        while not self._stop.is_set():
            try:
                summary = await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop must survive any poll error
                self._state = "error"
                self._last_error = repr(exc)[:200]
                self._record_event("poll_error", repr(exc)[:200])
                logger.warning("market discovery poll failed: %s", exc)
            else:
                self._poll_count += 1
                self._state = "running"
                self._last_error = None
                self._record_event("poll_ok", f"{summary.fetched} fetched, {summary.kept} kept")
            # Cooperative yield, then sleep the interval — waking early on stop.
            await asyncio.sleep(0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._config.poll_interval_seconds
                )

    async def _sample_books_once(self) -> int:
        """One order book sample cycle: fetch /book for each catalog market's
        YES and NO token (concurrency-bounded), then atomically store the fresh
        set. Both sides are sampled so entry / exit read the held side's own
        book — a NO decision is not a flipped YES book."""
        markets = self.store.snapshot()
        if not markets:
            self.store.set_order_books([])
            return 0
        sem = asyncio.Semaphore(BOOK_SAMPLE_CONCURRENCY)

        async def _one(token_id: str) -> OrderBook | None:
            async with sem:
                try:
                    return await self._book_fetcher(token_id)
                except Exception as exc:  # noqa: BLE001 — keep cycle alive
                    logger.warning("order book fetch failed for %s: %s", token_id, exc)
                    return None

        token_ids: list[str] = []
        for m in markets:
            token_ids.append(m.yes_token_id)
            if m.no_token_id is not None:
                token_ids.append(m.no_token_id)
        results = await asyncio.gather(*(_one(t) for t in token_ids))
        books = [b for b in results if b is not None]
        self.store.set_order_books(books)
        if self._book_persist is not None:
            for book in books:
                self._book_persist(book)
        return len(books)

    async def _run_book_loop(self) -> None:
        """Second loop: samples order book depth far finer than discovery."""
        assert self._config is not None
        while not self._stop.is_set():
            try:
                count = await self._sample_books_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop must survive
                self._record_event("book_sample_error", repr(exc)[:200])
                logger.warning("order book sample cycle failed: %s", exc)
            else:
                self._record_event("book_sample_ok", f"{count} books")
            await asyncio.sleep(0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._config.book_sample_interval_seconds,
                )

    def _snapshot(self) -> StatusSnapshot:
        last_poll = self.store.last_poll
        return StatusSnapshot(
            state=self._state,
            started_at=self._started_at,
            last_poll_at=last_poll.ts if last_poll else None,
            catalog_size=len(self.store),
            poll_count=self._poll_count,
            last_error=self._last_error,
            running_config=dict(self._running_config) if self._running_config else None,
            last_poll=last_poll.to_dict() if last_poll else None,
        )


# Module-level singleton; FastAPI routes (MS5) wire to this.
manager = MarketSourceManager()
