"""Tests for LiveExecutor — V2 IOC submission through a faked _ClobClient."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.execution.live_executor import LiveExecutor
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import OrderBook, normalize_gamma_market
from openpoly.markets.store import MarketStore, PollSummary
from openpoly.portfolio import PortfolioStore
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent


class _FakeClob:
    """Records calls; returns canned responses set per test.

    ``ctf_balance_raw`` controls what get_balance_allowance returns for
    CONDITIONAL queries (the SELL CTF-cache poll). Set high (default 1e18)
    so SELL tests pass immediately; tests for cache-lag use 0.
    """

    def __init__(
        self,
        *,
        order_response: dict[str, Any] | None = None,
        exception: Exception | None = None,
        allowance_update_raises: bool = False,
        ctf_balance_raw: int = 10**18,
    ) -> None:
        self._response = order_response or {
            "success": True,
            "orderID": "0xDEAD",
            "status": "matched",
            "makingAmount": "5.0",
            "takingAmount": "10.0",
            "transactionsHashes": ["0xCAFE"],
        }
        self._exception = exception
        self._allowance_update_raises = allowance_update_raises
        self._ctf_balance_raw = ctf_balance_raw
        self.posted: list[dict[str, Any]] = []
        self.allowance_updates: list[Any] = []

    def create_and_post_order(self, order_args, options, order_type):
        self.posted.append({"order_args": order_args, "options": options, "order_type": order_type})
        if self._exception is not None:
            raise self._exception
        return self._response

    def update_balance_allowance(self, params):
        self.allowance_updates.append(params)
        if self._allowance_update_raises:
            raise RuntimeError("cache refresh failed")

    def get_balance_allowance(self, params):
        # CONDITIONAL queries return the CTF balance the SELL poll checks;
        # COLLATERAL queries don't matter for these tests.
        return {"balance": str(self._ctf_balance_raw), "allowances": {}}


@pytest.fixture(autouse=True)
def _isolate_market_store():
    saved = market_source_manager.store
    market_source_manager.store = MarketStore()
    yield
    market_source_manager.store = saved


@pytest.fixture
def store(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path}/p.db")
    init_db(engine)
    yield PortfolioStore(make_session_factory(engine))
    engine.dispose()


def _market(market_id: str = "m1", *, neg_risk: bool = False):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": "Q?",
        "clobTokenIds": f'["yes-{market_id}", "no-{market_id}"]',
        "negRisk": neg_risk,
    }
    m = normalize_gamma_market(raw, event={"id": "e", "title": "E", "tags": []})
    assert m is not None
    return m


def _populate(market, *books: OrderBook) -> None:
    s = market_source_manager.store
    s.replace([market], PollSummary(ts=1.0, fetched=1, kept=1, reason_counts={}))
    s.set_order_books(list(books))


def _intent(market_id="m1", side="yes", price=0.5, qty=10.0) -> OrderIntent:
    return OrderIntent(market_id=market_id, side=side, price=price, qty=qty)


# ---------- execute_buy ----------


def test_buy_success_records_actual_fill(store) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xORDER",
            "status": "matched",
            "makingAmount": "4.0",  # pUSD paid
            "takingAmount": "10.0",  # tokens received
            "transactionsHashes": ["0xTX"],
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n1", ts=100.0)
    assert r.filled is True
    assert r.price == pytest.approx(0.4)
    assert r.qty == pytest.approx(10.0)
    # GTC (verified) order type was passed
    assert str(clob.posted[0]["order_type"]).endswith("GTC")
    # BUY quantized the qty (10.0 was already integer, kept as-is)
    assert clob.posted[0]["order_args"].size == 10.0
    # Collateral allowance refresh was attempted before the order
    assert len(clob.allowance_updates) == 1
    fills = store.list_fills(limit=5)
    assert any(f.order_id == "0xORDER" and f.tx_hash == "0xTX" for f in fills)


def test_buy_skips_when_below_min_notional(store) -> None:
    """qty * price < $1.10 floor → skip without posting (server min is $1.00)."""
    m = _market("m1")
    _populate(m)
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    # qty=2 × price=0.5 = $1.00, below the $1.10 floor
    r = le.execute_buy(_intent(qty=2.0, price=0.5), news_id="n", ts=1.0)
    assert r.skip_reason == "min_notional_below_floor"
    assert clob.posted == []


def test_buy_quantizes_fractional_qty_down(store) -> None:
    """qty=5.56 → floors to 5; maker = 5 * 0.50 = $2.50 (clean cents)."""
    m = _market("m1")
    _populate(m)
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    le.execute_buy(_intent(qty=5.56, price=0.50), news_id="n", ts=1.0)
    assert clob.posted[0]["order_args"].size == 5.0


def test_buy_market_not_in_catalog_skips(store) -> None:
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(market_id="nonexistent"), news_id="n", ts=1.0)
    assert r.filled is False and r.skip_reason == "market_not_found"
    assert clob.posted == []


def test_buy_duplicate_position_skips(store) -> None:
    m = _market("m1")
    _populate(m)
    store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.4,
        qty=10.0,
        ts=50.0,
        news_id="prior",
    )
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n", ts=100.0)
    assert r.filled is False and r.skip_reason == "position_exists"
    assert clob.posted == []


def test_buy_clob_network_error_skips(store) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(exception=ConnectionError("RPC down"))
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n", ts=1.0)
    assert r.filled is False
    assert r.skip_reason == "live_error:ConnectionError"


def test_buy_response_success_false_skips(store) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(order_response={"success": False, "errorMsg": "price not tick-aligned"})
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n", ts=1.0)
    assert r.filled is False
    assert r.skip_reason == "live_rejected:price not tick-aligned"


def test_buy_zero_match_skips(store) -> None:
    """FAK with no liquidity at price → takingAmount=0 → skip."""
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0x1",
            "status": "unmatched",
            "makingAmount": "0",
            "takingAmount": "0",
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n", ts=1.0)
    assert r.skip_reason == "live_no_match"


def test_buy_neg_risk_flag_passed_to_options(store) -> None:
    m = _market("m1", neg_risk=True)
    _populate(m)
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    le.execute_buy(_intent(), news_id="n", ts=1.0)
    posted = clob.posted[0]
    assert posted["options"].neg_risk is True


def test_buy_allowance_refresh_failure_is_non_fatal(store) -> None:
    """Allowance cache refresh is best-effort — order should still attempt."""
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(allowance_update_raises=True)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n", ts=1.0)
    assert r.filled is True  # default fake response is a fill
    assert len(clob.posted) == 1


# ---------- execute_sell ----------


def _book(token_id: str, bid: float = 0.55, ask: float = 0.56) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        ts=1.0,
        bids=[(bid, 100.0)],
        asks=[(ask, 100.0)],
    )


def test_sell_success_closes_position(store) -> None:
    m = _market("m1")
    _populate(m, _book(m.yes_token_id, bid=0.55))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n",
    )
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xSELL",
            "status": "matched",
            "makingAmount": "10.0",  # tokens sold (SELL)
            "takingAmount": "5.5",  # pUSD received
            "transactionsHashes": ["0xSTX"],
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="take_profit", ts=200.0)
    assert r.filled is True
    assert r.price == pytest.approx(0.55)
    assert r.qty == pytest.approx(10.0)
    # GTC (verified) order type was passed
    assert str(clob.posted[0]["order_type"]).endswith("GTC")
    rec = store.get_position(held.position_id)
    assert rec is not None and rec.status == "closed"
    assert rec.close_reason == "take_profit"
    fills = store.list_fills(limit=5)
    sell_fill = next(f for f in fills if f.action == "sell")
    assert sell_fill.order_id == "0xSELL"
    assert sell_fill.tx_hash == "0xSTX"
    # CTF allowance refresh was called at least once (poll loop, first attempt OK)
    assert len(clob.allowance_updates) >= 1


def test_sell_skips_when_ctf_cache_never_syncs(store, monkeypatch) -> None:
    """If CTF balance never reaches position.qty within poll window → skip."""
    m = _market("m1")
    _populate(m, _book(m.yes_token_id))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n",
    )
    # CTF balance always 0 — cache "stuck"
    clob = _FakeClob(ctf_balance_raw=0)
    # Patch time.sleep so the 5×1s poll doesn't actually delay the test.
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.skip_reason == "ctf_cache_not_synced"
    assert clob.posted == []  # never attempted to POST


def test_sell_market_gone_from_catalog_skips(store) -> None:
    m = _market("m1")
    _populate(m, _book(m.yes_token_id))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n",
    )
    market_source_manager.store = MarketStore()  # empty catalog
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="take_profit", ts=200.0)
    assert r.skip_reason == "market_not_found"


def test_sell_empty_bids_skips(store) -> None:
    m = _market("m1")
    book = OrderBook(token_id=m.yes_token_id, ts=1.0, bids=[], asks=[(0.6, 100.0)])
    _populate(m, book)
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n",
    )
    clob = _FakeClob()
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="take_profit", ts=200.0)
    assert r.skip_reason == "no_bid_liquidity"
    assert clob.posted == []


def test_sell_zero_match_skips_position_stays_open(store) -> None:
    m = _market("m1")
    _populate(m, _book(m.yes_token_id))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n",
    )
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0x1",
            "makingAmount": "0",
            "takingAmount": "0",
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.skip_reason == "live_no_match"
    rec = store.get_position(held.position_id)
    assert rec is not None and rec.status == "open"
