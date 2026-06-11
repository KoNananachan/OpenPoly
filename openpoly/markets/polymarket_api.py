"""Polymarket Gamma + CLOB API fetch layer.

Async httpx client for market discovery. Reads only — Gamma's ``/events``
endpoint needs no auth. ``discover_events`` returns each raw market dict paired
with its parent event, so the normalize step (``openpoly.markets.models``) has
the event metadata (tags / title / id) it threads onto every ``Market``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from openpoly.markets.models import (
    Market,
    OrderBook,
    normalize_gamma_market,
    parse_clob_book,
    parse_price_history,
)

logger = logging.getLogger(__name__)

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
DATA_API_BASE_URL = "https://data-api.polymarket.com"
DEFAULT_TIMEOUT = 30.0

# (raw_market, parent_event)
EventMarketPair = tuple[dict[str, Any], dict[str, Any]]


async def discover_events(
    *,
    limit: int = 100,
    base_url: str = GAMMA_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> list[EventMarketPair]:
    """Fetch active events from Gamma, sorted by 24h volume descending.

    Returns a flat list of ``(raw_market, event)`` pairs — one entry per market
    nested under each event. Note ``limit`` bounds the number of *events*, not
    markets; one event can hold dozens of markets.

    Retries once on transient failure; raises the underlying error after the
    second attempt. A caller-supplied ``client`` is reused and never closed; an
    internally created one is always closed.
    """
    params = {
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        "limit": str(limit),
    }
    events = await _get_json(f"{base_url}/events", params, timeout, client)

    if not isinstance(events, list):
        logger.warning("Gamma /events returned non-list payload; treating as empty")
        return []

    pairs: list[EventMarketPair] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        markets = event.get("markets")
        if not isinstance(markets, list):
            continue
        for market in markets:
            if isinstance(market, dict):
                pairs.append((market, event))

    logger.info("Gamma discovery: %d events -> %d markets", len(events), len(pairs))
    return pairs


async def fetch_markets_by_condition_id(
    condition_ids: list[str],
    *,
    base_url: str = GAMMA_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch *resolved* markets by their conditionId — used for settlement.

    Passes ``closed=true``: Gamma's ``/markets`` defaults to returning only
    open markets, so omitting the param silently drops every resolved market
    (which is exactly what settlement needs) — they come back empty. Settlement
    only acts on resolved markets, so ``closed=true`` is both necessary and
    sufficient; still-open markets simply aren't returned and the caller skips
    them. ``condition_ids`` MUST be a repeated query param — Gamma treats a
    comma-joined value as one (unmatched) id and returns nothing.

    Returns the raw market dicts (caller normalizes). Empty input → empty
    output without a network call. Retries once on transient failure.
    """
    if not condition_ids:
        return []
    # List value → httpx serializes ``condition_ids=A&condition_ids=B``.
    # ``closed=true`` is required — Gamma /markets defaults to open-only.
    params: dict[str, str | list[str]] = {
        "condition_ids": condition_ids,
        "closed": "true",
        "limit": str(len(condition_ids)),
    }
    raw = await _get_json(f"{base_url}/markets", params, timeout, client)
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        items = raw.get("data")
        if isinstance(items, list):
            return [m for m in items if isinstance(m, dict)]
    logger.warning("Gamma /markets returned unexpected shape for condition_ids fetch")
    return []


async def fetch_market_by_id(
    market_id: str,
    *,
    base_url: str = GAMMA_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> Market | None:
    """Single-market lookup bypassing the /events top-100 window.

    Used by the holding-sync hook in MarketSourceManager to guarantee the
    catalog covers every open position regardless of its event's volume rank.

    Returns None on HTTP error (after one retry), empty response, or any
    normalize failure. Caller treats None as 'skip this position, retry next
    poll' — no exception bubbles up.
    """
    params = {"id": market_id}
    try:
        data = await _get_json(f"{base_url}/markets", params, timeout, client)
    except Exception as exc:  # noqa: BLE001 — caller wants None on any failure
        logger.warning("gamma /markets?id=%s failed: %s", market_id, exc)
        return None

    if not data:
        return None
    raw = data[0] if isinstance(data, list) else data
    if not isinstance(raw, dict):
        return None
    try:
        market = normalize_gamma_market(raw)
    except Exception as exc:  # noqa: BLE001 — schema drift shouldn't crash main poll
        logger.warning(
            "normalize_gamma_market failed for id=%s: %s; raw keys=%s",
            market_id,
            exc,
            list(raw.keys()),
        )
        return None
    if market is None:
        logger.warning(
            "normalize_gamma_market returned None for id=%s (missing required fields); raw keys=%s",
            market_id,
            list(raw.keys()),
        )
    return market


async def fetch_book(
    token_id: str,
    *,
    depth: int = 3,
    base_url: str = CLOB_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> OrderBook:
    """Fetch the CLOB order book for one token.

    Returns an ``OrderBook`` normalized best-first and trimmed to the top
    ``depth`` levels per side. Reads only — CLOB ``/book`` needs no auth.
    Retries once; raises the underlying error after the second attempt.
    """
    raw = await _get_json(f"{base_url}/book", {"token_id": token_id}, timeout, client)
    if not isinstance(raw, dict):
        logger.warning("CLOB /book returned non-object payload for %s", token_id)
        raw = {}
    return parse_clob_book(raw, token_id, depth=depth)


async def fetch_held_condition_sides(
    funder: str,
    *,
    base_url: str = DATA_API_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> set[tuple[str, str]]:
    """Return the ``(condition_id, side)`` pairs the wallet holds on-chain.

    Reads the Polymarket data-api ``/positions`` indexer for ``funder`` — it is
    authoritative on what the wallet actually holds and accounts for neg-risk
    wrapping (raw ``balanceOf`` on a token id does not). ``side`` is the held
    outcome lowercased (``yes`` / ``no``). Only positions with a positive size
    are included; flat (size 0) ones are omitted so the reconciliation monitor
    treats them as exited.
    """
    raw = await _get_json(
        f"{base_url}/positions",
        {"user": funder, "sizeThreshold": "0"},
        timeout,
        client,
    )
    if not isinstance(raw, list):
        return set()
    held: set[tuple[str, str]] = set()
    for pos in raw:
        if not isinstance(pos, dict):
            continue
        cid = pos.get("conditionId") or pos.get("condition_id")
        outcome = pos.get("outcome")
        try:
            size = float(pos.get("size") or 0)
        except (TypeError, ValueError):
            size = 0.0
        if not cid or not isinstance(outcome, str) or size <= 0:
            continue
        held.add((str(cid), outcome.strip().lower()))
    return held


async def fetch_wallet_positions_value(
    funder: str,
    *,
    base_url: str = DATA_API_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> float | None:
    """Return the wallet's total open-position market value in USDC.

    Reads the data-api ``/value`` indexer — shape ``[{"user", "value"}]``
    (verified against the live API; matches the per-position ``size × curPrice``
    sum). An empty list means no holdings → 0.0; an unexpected shape →
    None (unknown), never a crash.
    """
    raw = await _get_json(f"{base_url}/value", {"user": funder}, timeout, client)
    if not isinstance(raw, list):
        return None
    if not raw:
        return 0.0
    first = raw[0]
    if not isinstance(first, dict):
        return None
    try:
        return float(first["value"])
    except (KeyError, TypeError, ValueError):
        return None


async def _get_json(
    url: str,
    params: dict[str, str | list[str]],
    timeout: float,
    client: httpx.AsyncClient | None,
) -> Any:
    """GET ``url`` and parse JSON, retrying once on failure.

    Owns and closes the client only when one was not supplied by the caller.
    """
    owned = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = await http.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:  # ValueError: bad JSON
                last_exc = exc
                logger.warning("Gamma GET %s failed (attempt %d/2): %s", url, attempt, exc)
        assert last_exc is not None
        raise last_exc
    finally:
        if owned:
            await http.aclose()


# --- CLOB /prices-history (sync — consumed by a section running in a thread) -


def _sync_get_json(url: str, params: dict[str, str], timeout: float) -> Any:
    """Synchronous GET + JSON parse, retrying once on failure.

    The async ``_get_json`` above serves the discovery / book-sampling loops;
    this sync variant serves ``recent_move``, which is called from inside a
    section's ``run()`` (itself already offloaded to a worker thread).
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = httpx.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: bad JSON
            last_exc = exc
            logger.warning("CLOB GET %s failed (attempt %d/2): %s", url, attempt, exc)
    assert last_exc is not None
    raise last_exc


def fetch_price_history(
    token_id: str,
    *,
    window_min: int,
    fidelity: int = 10,
    base_url: str = CLOB_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[tuple[float, float]]:
    """Fetch one token's recent price history from CLOB ``/prices-history``.

    Returns ``(epoch_ts, price)`` points over roughly the last ``window_min``
    minutes, oldest-first. ``fidelity`` is the sample resolution in minutes.
    Reads only — no auth. Retries once; raises on a second failure.
    """
    now = int(time.time())
    params = {
        "market": token_id,
        "startTs": str(now - window_min * 60),
        "endTs": str(now),
        "fidelity": str(fidelity),
    }
    raw = _sync_get_json(f"{base_url}/prices-history", params, timeout)
    return parse_price_history(raw)


# Injectable seam: (token_id, *, window_min) -> price history points.
PriceHistoryFetcher = Callable[..., list[tuple[float, float]]]


def recent_move(
    token_id: str,
    *,
    window_min: int,
    fetcher: PriceHistoryFetcher = fetch_price_history,
) -> float | None:
    """The token's price change over the last ``window_min`` minutes —
    ``price_now - price_window_ago``.

    Returns None when the history is unavailable or too short to span a move;
    it **fails open** so a late-buy veto built on it simply does not fire on
    missing data. The ``fetcher`` seam lets tests avoid the network.
    """
    try:
        history = fetcher(token_id, window_min=window_min)
    except Exception as exc:  # noqa: BLE001 — fail open; the veto won't fire
        logger.warning("price history fetch failed for %s: %s", token_id, exc)
        return None
    if len(history) < 2:
        return None
    return history[-1][1] - history[0][1]
