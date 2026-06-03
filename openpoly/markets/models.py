"""Market data model + normalization from the Polymarket Gamma API.

A ``Market`` is openPoly's normalized view of one Polymarket market (a single
YES/NO question). ``normalize_gamma_market`` converts one raw market object
from a Gamma ``/events`` response into a ``Market``, absorbing Gamma's quirks:
JSON-string-encoded array fields, ISO-8601 date variants, and liveness flags
that must fail closed when absent.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Market:
    """Normalized Polymarket market — one YES/NO question."""

    market_id: str
    condition_id: str
    question: str
    slug: str
    yes_token_id: str
    no_token_id: str | None
    end_date: datetime | None  # UTC
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    last_trade_price: float | None
    volume_24h: float
    liquidity: float
    taker_fee_rate: float | None  # None = unknown; zero-fee rule fail-closes on None
    closed: bool
    accepting_orders: bool
    enable_order_book: bool
    event_id: str | None
    event_title: str | None
    event_tags: tuple[str, ...]
    neg_risk: bool = False  # slice C — Gamma's `negRisk` field
    # Slice E: when the market resolves, Gamma stamps outcomePrices as a
    # 2-string array — ``["1", "0"]`` (YES wins) / ``["0", "1"]`` (NO wins) /
    # ``["0.5", "0.5"]`` (split, rare). None before resolution (and on parse
    # failure). Position settlement reads this + the held side to derive a
    # 0/1 final price.
    outcome_prices: tuple[float, float] | None = None

    @property
    def mid(self) -> float | None:
        """Mid price from the top of book, when both sides are known."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return None

    @property
    def reference_price(self) -> float | None:
        """Best available YES price estimate: mid, falling back to last trade."""
        mid = self.mid
        return mid if mid is not None else self.last_trade_price


def _parse_json_array(value: Any) -> list[Any]:
    """Gamma encodes clobTokenIds / outcomes / outcomePrices as JSON strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _to_float(value: Any) -> float | None:
    """Coerce to float; None on failure. Bools are rejected (not numbers here)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (date or datetime, optional 'Z') to aware UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _taker_fee_rate(raw: dict[str, Any]) -> float | None:
    """Provisional taker fee as a rate (fraction).

    Gamma exposes ``takerBaseFee`` in basis points (e.g. 1000 -> 0.10). v8 §0.1
    the zero-fee rule treats the CLOB ``/fee-rate`` endpoint as the only authority —
    fetch layer may override this. ``feesEnabled is False`` is a definitive zero.
    """
    if raw.get("feesEnabled") is False:
        return 0.0
    bps = _to_float(raw.get("takerBaseFee"))
    return None if bps is None else bps / 10_000.0


def normalize_gamma_market(
    raw: dict[str, Any], *, event: dict[str, Any] | None = None
) -> Market | None:
    """Convert one Gamma market object into a ``Market``.

    Returns ``None`` when the market is structurally unusable — no CLOB token
    ids (untradeable) or missing identity fields.
    """
    token_ids = [str(t) for t in _parse_json_array(raw.get("clobTokenIds")) if t]
    market_id = raw.get("id")
    condition_id = raw.get("conditionId") or raw.get("condition_id")
    if not token_ids or not market_id or not condition_id:
        return None

    event = event or {}
    tags = tuple(
        str(t["slug"]) for t in event.get("tags", []) if isinstance(t, dict) and t.get("slug")
    )
    title = event.get("title")

    liquidity = _to_float(raw.get("liquidityNum"))
    if liquidity is None:
        liquidity = _to_float(raw.get("liquidity")) or 0.0

    return Market(
        market_id=str(market_id),
        condition_id=str(condition_id),
        question=str(raw.get("question", "")),
        slug=str(raw.get("slug", "")),
        yes_token_id=token_ids[0],
        no_token_id=token_ids[1] if len(token_ids) > 1 else None,
        end_date=_parse_iso(raw.get("endDate") or raw.get("endDateIso")),
        best_bid=_to_float(raw.get("bestBid")),
        best_ask=_to_float(raw.get("bestAsk")),
        spread=_to_float(raw.get("spread")),
        last_trade_price=_to_float(raw.get("lastTradePrice")),
        volume_24h=_to_float(raw.get("volume24hr")) or 0.0,
        liquidity=liquidity,
        taker_fee_rate=_taker_fee_rate(raw),
        closed=bool(raw.get("closed", True)),  # fail-closed
        accepting_orders=bool(raw.get("acceptingOrders", False)),  # fail-closed
        enable_order_book=bool(raw.get("enableOrderBook", False)),  # fail-closed
        event_id=str(event["id"]) if event.get("id") else None,
        event_title=str(title) if title else None,
        event_tags=tags,
        neg_risk=bool(raw.get("negRisk", False)),
        outcome_prices=_parse_outcome_prices(raw.get("outcomePrices")),
    )


def _parse_outcome_prices(value: Any) -> tuple[float, float] | None:
    """Parse Gamma ``outcomePrices`` (JSON string of a 2-element array of
    stringified floats). Returns None if absent, malformed, or not a clean
    2-tuple of finite floats — settlement caller must handle None as "not
    yet resolved" (don't close)."""
    items = _parse_json_array(value)
    if len(items) != 2:
        return None
    a = _to_float(items[0])
    b = _to_float(items[1])
    if a is None or b is None:
        return None
    return (a, b)


@dataclass(frozen=True)
class OrderBook:
    """Normalized CLOB order book for one token. Levels are best-first:
    bids descending by price, asks ascending by price; trimmed to top-N."""

    token_id: str
    ts: float  # epoch seconds, UTC
    bids: list[tuple[float, float]]  # [(price, size), ...] best-first
    asks: list[tuple[float, float]]

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid


def _book_levels(raw: Any, *, descending: bool, depth: int) -> list[tuple[float, float]]:
    """Parse one raw CLOB book side into best-first (price, size) tuples.

    Sorts explicitly rather than trusting the API's array order: bids
    best-first = price descending, asks best-first = price ascending. Then
    trims to ``depth`` levels. Malformed levels are dropped.
    """
    if not isinstance(raw, list):
        return []
    levels: list[tuple[float, float]] = []
    for level in raw:
        if not isinstance(level, dict):
            continue
        price = _to_float(level.get("price"))
        size = _to_float(level.get("size"))
        if price is None or size is None:
            continue
        levels.append((price, size))
    levels.sort(key=lambda pair: pair[0], reverse=descending)
    return levels[:depth]


def parse_clob_book(raw: dict[str, Any], token_id: str, *, depth: int = 3) -> OrderBook:
    """Convert a raw CLOB ``/book`` payload into an ``OrderBook``.

    Keeps the top ``depth`` levels per side, best-first. The book's
    millisecond ``timestamp`` becomes epoch seconds; absent -> local clock.
    """
    ts_ms = _to_float(raw.get("timestamp"))
    ts = ts_ms / 1000.0 if ts_ms is not None else time.time()
    return OrderBook(
        token_id=token_id,
        ts=ts,
        bids=_book_levels(raw.get("bids"), descending=True, depth=depth),
        asks=_book_levels(raw.get("asks"), descending=False, depth=depth),
    )


def parse_price_history(raw: Any) -> list[tuple[float, float]]:
    """Parse a CLOB ``/prices-history`` payload into ``(epoch_ts, price)``
    points, oldest-first.

    The payload shape is ``{"history": [{"t": <epoch>, "p": <price>}, ...]}``;
    malformed entries are dropped and the result is sorted by timestamp.
    """
    if not isinstance(raw, dict):
        return []
    points = raw.get("history")
    if not isinstance(points, list):
        return []
    parsed: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        ts = _to_float(point.get("t"))
        price = _to_float(point.get("p"))
        if ts is None or price is None:
            continue
        parsed.append((ts, price))
    parsed.sort(key=lambda pair: pair[0])
    return parsed
