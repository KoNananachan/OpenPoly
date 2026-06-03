"""Tests for the CLOB /book layer — OrderBook, parse_clob_book, fetch_book."""

from __future__ import annotations

import time

import httpx

from openpoly.markets.models import OrderBook, parse_clob_book
from openpoly.markets.polymarket_api import fetch_book

# Raw CLOB /book shape: the API sends bids ascending, asks descending.
RAW_BOOK = {
    "asks": [
        {"price": "0.170", "size": "8990"},
        {"price": "0.169", "size": "8132"},
        {"price": "0.168", "size": "200409"},
    ],
    "bids": [
        {"price": "0.165", "size": "101155"},
        {"price": "0.166", "size": "53898"},
        {"price": "0.167", "size": "19235"},
    ],
    "timestamp": "1779348146511",
}


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------- parse_clob_book ----------


def test_parse_levels_best_first():
    book = parse_clob_book(RAW_BOOK, "tok-1", depth=3)
    assert book.token_id == "tok-1"
    # bids best-first = price descending
    assert book.bids == [(0.167, 19235.0), (0.166, 53898.0), (0.165, 101155.0)]
    # asks best-first = price ascending
    assert book.asks == [(0.168, 200409.0), (0.169, 8132.0), (0.170, 8990.0)]


def test_parse_timestamp_ms_to_seconds():
    book = parse_clob_book(RAW_BOOK, "tok-1")
    assert book.ts == 1779348146511 / 1000.0


def test_parse_depth_trims_each_side():
    bid_prices = ["0.10", "0.11", "0.12", "0.13", "0.14"]
    ask_prices = ["0.90", "0.89", "0.88", "0.87", "0.86"]
    raw = {
        "bids": [{"price": p, "size": "1"} for p in bid_prices],
        "asks": [{"price": p, "size": "1"} for p in ask_prices],
        "timestamp": "1000",
    }
    book = parse_clob_book(raw, "t", depth=3)
    assert len(book.bids) == 3
    assert len(book.asks) == 3
    assert book.bids[0][0] == 0.14  # best (highest) bid
    assert book.asks[0][0] == 0.86  # best (lowest) ask


def test_parse_missing_or_bad_sides():
    book = parse_clob_book({"timestamp": "1000"}, "t")
    assert book.bids == []
    assert book.asks == []
    book2 = parse_clob_book({"bids": "garbage", "asks": None}, "t")
    assert book2.bids == []
    assert book2.asks == []


def test_parse_skips_malformed_levels():
    raw = {
        "bids": [
            {"price": "0.5", "size": "10"},
            "not-a-dict",
            {"price": "bad", "size": "10"},
            {"size": "10"},  # missing price
        ],
        "asks": [],
    }
    book = parse_clob_book(raw, "t")
    assert book.bids == [(0.5, 10.0)]


def test_parse_timestamp_absent_falls_back_to_now():
    before = time.time()
    book = parse_clob_book({"bids": [], "asks": []}, "t")
    assert before <= book.ts <= time.time() + 1


# ---------- OrderBook properties ----------


def test_orderbook_properties():
    book = parse_clob_book(RAW_BOOK, "t", depth=3)
    assert book.best_bid == 0.167
    assert book.best_ask == 0.168
    assert book.mid == (0.167 + 0.168) / 2.0
    assert book.spread is not None
    assert round(book.spread, 6) == 0.001


def test_orderbook_empty_side_properties_are_none():
    book = OrderBook(token_id="t", ts=1.0, bids=[], asks=[])
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.mid is None
    assert book.spread is None


# ---------- fetch_book ----------


async def test_fetch_book_happy_path():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RAW_BOOK)

    async with _mock_client(handler) as client:
        book = await fetch_book("tok-9", depth=3, client=client)

    assert book.token_id == "tok-9"
    assert book.best_bid == 0.167
    assert book.best_ask == 0.168


async def test_fetch_book_sends_token_id():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=RAW_BOOK)

    async with _mock_client(handler) as client:
        await fetch_book("abc123", client=client)

    assert "/book" in seen["url"]
    assert "token_id=abc123" in seen["url"]


async def test_fetch_book_retries_once():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, json=RAW_BOOK)

    async with _mock_client(handler) as client:
        book = await fetch_book("t", client=client)

    assert calls["n"] == 2
    assert book.best_bid == 0.167


async def test_fetch_book_non_object_payload_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])  # list, not an object

    async with _mock_client(handler) as client:
        book = await fetch_book("t", client=client)

    assert book.bids == []
    assert book.asks == []
