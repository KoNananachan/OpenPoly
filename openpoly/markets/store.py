"""In-memory market catalog.

``MarketStore`` holds the current discovered + filtered market universe plus a
summary of the most recent discovery poll. Single-writer (the market-source
polling task) / multi-reader (HTTP status route, sections). ``replace`` rebinds
the whole catalog dict in one assignment, so a reader always sees a complete
snapshot — no lock needed under FastAPI's single event loop.

The catalog and latest order books are in-memory reference data, rebuilt by the
next poll / sample after a restart. Persisting the order book depth ladder as a
time series is a separate concern (memory ``openpoly_market_data_persistence``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openpoly.markets.models import Market, OrderBook


@dataclass(frozen=True)
class PollSummary:
    """Outcome of one discovery poll — surfaced on the Live tab."""

    ts: float  # epoch seconds, UTC
    fetched: int  # raw markets returned by Gamma
    kept: int  # markets that passed the discovery filter
    reason_counts: dict[str, int] = field(default_factory=dict)  # reject histogram
    holding_synced: int = 0  # markets pulled in by holding sync (open positions not in discovery)
    holding_sync_failed: int = 0  # holding sync failures this poll

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "fetched": self.fetched,
            "kept": self.kept,
            "reason_counts": dict(self.reason_counts),
            "holding_synced": self.holding_synced,
            "holding_sync_failed": self.holding_sync_failed,
        }


class MarketStore:
    """Current market catalog + last-poll summary. In-memory, not persisted."""

    def __init__(self) -> None:
        self._catalog: dict[str, Market] = {}
        self._last_poll: PollSummary | None = None
        self._order_books: dict[str, OrderBook] = {}

    def __len__(self) -> int:
        return len(self._catalog)

    def replace(self, markets: list[Market], summary: PollSummary) -> None:
        """Atomically swap the catalog and record the poll summary."""
        self._catalog = {m.market_id: m for m in markets}
        self._last_poll = summary

    def get(self, market_id: str) -> Market | None:
        return self._catalog.get(market_id)

    def get_by_condition(self, condition_id: str) -> Market | None:
        """O(n) lookup by Polymarket on-chain ``conditionId`` (PositionRecord
        stores condition_id, not the Gamma market_id). Catalog is ~50
        markets in steady state — sub-millisecond walk. Returns ``None``
        when the market is no longer in the catalog (filtered out or
        resolved), which the caller must handle as "evicted"."""
        for m in self._catalog.values():
            if m.condition_id == condition_id:
                return m
        return None

    def snapshot(self) -> list[Market]:
        """All catalogued markets, in poll order (Gamma volume-desc)."""
        return list(self._catalog.values())

    def union(self, markets: list[Market]) -> int:
        """Add markets not already present in catalog. Returns count added.

        Existing market_ids keep their current entry (discovery wins).
        """
        added = 0
        for m in markets:
            if m.market_id not in self._catalog:
                self._catalog[m.market_id] = m
                added += 1
        return added

    def snapshot_ids(self) -> set[str]:
        """Set of all market_ids currently in catalog."""
        return set(self._catalog.keys())

    def update_last_poll(self, summary: PollSummary) -> None:
        """Replace the last-poll summary without touching the catalog.

        Used after holding sync to fold the holding_synced / holding_sync_failed
        counters into the already-stored summary.
        """
        self._last_poll = summary

    @property
    def last_poll(self) -> PollSummary | None:
        return self._last_poll

    # ---------- order book depth (sampled by the book loop, MS8) ----------

    def set_order_books(self, books: list[OrderBook]) -> None:
        """Atomically replace the order-book set with a fresh sample batch."""
        self._order_books = {b.token_id: b for b in books}

    def get_order_book(self, token_id: str) -> OrderBook | None:
        return self._order_books.get(token_id)

    @property
    def order_book_count(self) -> int:
        return len(self._order_books)
