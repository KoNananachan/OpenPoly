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
        ctf_balance_sequence: list[int] | None = None,
        cancel_raises: bool = False,
        order_status: dict[str, Any] | None = None,
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
        # When set, successive CONDITIONAL balance reads consume this list
        # (last value sticks) — models balance changing across the pre-order
        # gate read and the post-exception confirmation polls.
        self._ctf_balance_sequence = list(ctf_balance_sequence) if ctf_balance_sequence else None
        self._cancel_raises = cancel_raises
        # get_order response; default "0" so the final qty falls back to the
        # reported fill (max(matched, reported)).
        self._order_status = order_status or {"size_matched": "0"}
        self.posted: list[dict[str, Any]] = []
        self.allowance_updates: list[Any] = []
        self.cancelled: list[str] = []

    def create_and_post_order(self, order_args, options, order_type):
        self.posted.append({"order_args": order_args, "options": options, "order_type": order_type})
        if self._exception is not None:
            raise self._exception
        return self._response

    def update_balance_allowance(self, params):
        self.allowance_updates.append(params)
        if self._allowance_update_raises:
            raise RuntimeError("cache refresh failed")

    def cancel_order(self, payload):
        self.cancelled.append(payload.orderID)
        if self._cancel_raises:
            raise RuntimeError("cancel failed")

    def get_order(self, order_id):
        return self._order_status

    def get_balance_allowance(self, params):
        # CONDITIONAL queries return the CTF balance the SELL poll checks;
        # COLLATERAL queries don't matter for these tests.
        if self._ctf_balance_sequence is not None:
            val = (
                self._ctf_balance_sequence.pop(0)
                if len(self._ctf_balance_sequence) > 1
                else self._ctf_balance_sequence[0]
            )
            return {"balance": str(val), "allowances": {}}
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
    # Collateral allowance refresh + the CONDITIONAL pre-read (the
    # lost-response confirmation baseline) both ran before the order
    assert [p.asset_type for p in clob.allowance_updates] == [
        "COLLATERAL",
        "CONDITIONAL",
    ]
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


def test_buy_clob_network_error_skips(store, monkeypatch) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(exception=ConnectionError("RPC down"))
    # R5 confirmation polls after the exception — patch out its sleeps.
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
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


# ---------- lost-response confirmation (R5: at-least-once hardening) ----------
#
# A network exception from create_and_post_order does NOT mean the order
# didn't fill — the server may have matched it and only the response was
# lost (a confirmed live drift incident). The executor must confirm via
# the CTF balance before declaring failure.


def test_sell_network_error_but_balance_dropped_records_fill(store, monkeypatch) -> None:
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
    # Gate read sees 10 tokens (synced); post-exception confirm sees 0 → sold.
    clob = _FakeClob(
        exception=RuntimeError("request exception"),
        ctf_balance_sequence=[10_000_000, 0],
    )
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.filled is True
    assert r.qty == pytest.approx(10.0)
    assert r.price == pytest.approx(0.55)  # recorded at the limit (bid) price
    rec = store.get_position(held.position_id)
    assert rec is not None and rec.status == "closed"


def test_sell_network_error_balance_unchanged_skips(store, monkeypatch) -> None:
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
        exception=RuntimeError("request exception"),
        ctf_balance_sequence=[10_000_000, 10_000_000],  # never drops
    )
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.skip_reason == "live_error:RuntimeError"
    rec = store.get_position(held.position_id)
    assert rec is not None and rec.status == "open"


def test_sell_network_error_partial_drop_records_partial(store, monkeypatch) -> None:
    m = _market("m1")
    _populate(m, _book(m.yes_token_id, bid=0.55))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=18.0,
        ts=100.0,
        news_id="n",
    )
    # Gate sees 18; confirm sees 3 → 15 sold, 3 unsold remain open.
    clob = _FakeClob(
        exception=RuntimeError("request exception"),
        ctf_balance_sequence=[18_000_000, 3_000_000],
    )
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.filled is True
    assert r.qty == pytest.approx(15.0)
    rec = store.get_position(held.position_id)
    assert rec is not None and rec.status == "open"
    assert rec.qty == pytest.approx(3.0)


def test_buy_network_error_but_balance_increased_opens_position(store, monkeypatch) -> None:
    m = _market("m1")
    _populate(m)
    # Pre-order read 0; post-exception confirm 10 tokens → buy actually filled.
    clob = _FakeClob(
        exception=RuntimeError("request exception"),
        ctf_balance_sequence=[0, 10_000_000],
    )
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(price=0.5, qty=10.0), news_id="n1", ts=100.0)
    assert r.filled is True
    assert r.qty == pytest.approx(10.0)
    assert r.price == pytest.approx(0.5)  # recorded at the limit price
    assert store.get_open_position("m1", "yes") is not None


def test_buy_network_error_balance_unchanged_skips(store, monkeypatch) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        exception=RuntimeError("request exception"),
        ctf_balance_sequence=[0, 0],
    )
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(), news_id="n1", ts=100.0)
    assert r.skip_reason == "live_error:RuntimeError"
    assert store.get_open_position("m1", "yes") is None


def test_sell_partial_fill_keeps_position_open(store) -> None:
    """A partial on-chain fill must reduce the open qty, not mark the whole
    position closed — closing it strands the unsold remainder on-chain as an
    orphan (the orphaned-remainder bug)."""
    m = _market("m1")
    _populate(m, _book(m.yes_token_id, bid=0.55))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=18.0,
        ts=100.0,
        news_id="n",
    )
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xSELL",
            "status": "matched",
            "makingAmount": "15.0",  # only 15 of 18 filled on-chain
            "takingAmount": "8.25",
            "transactionsHashes": ["0xSTX"],
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.filled is True
    assert r.qty == pytest.approx(15.0)
    rec = store.get_position(held.position_id)
    assert rec is not None
    assert rec.status == "open"  # remainder stays open, NOT closed
    assert rec.qty == pytest.approx(3.0)  # 18 - 15
    assert rec.realized_pnl == pytest.approx((0.55 - 0.40) * 15.0)


class _FlakyCloseStore:
    """Wraps a PortfolioStore; raises on the first ``fail_times`` record_sell
    calls, then delegates. Models a transient DB write failure occurring *after*
    an irreversible on-chain fill — the exact window that leaves a phantom-open
    position when the persist is dropped instead of retried."""

    def __init__(self, inner: PortfolioStore, *, fail_times: int) -> None:
        self._inner = inner
        self._remaining = fail_times
        self.close_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def record_sell(self, *args: Any, **kwargs: Any):
        self.close_attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("database is locked")
        return self._inner.record_sell(*args, **kwargs)


def test_sell_retries_db_close_after_transient_failure(store, monkeypatch) -> None:
    """The on-chain sell is irreversible. A transient close_position failure must
    be retried so the position is not left phantom-open while its tokens are
    already gone — the root cause of stuck phantom-open positions."""
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
            "makingAmount": "10.0",
            "takingAmount": "5.5",
            "transactionsHashes": ["0xSTX"],
        }
    )
    import openpoly.execution.live_executor as le_mod

    monkeypatch.setattr(le_mod.time, "sleep", lambda *_a, **_k: None)
    flaky = _FlakyCloseStore(store, fail_times=1)
    le = LiveExecutor(portfolio=flaky, clob_client=clob)
    r = le.execute_sell(held, close_reason="take_profit", ts=200.0)
    assert r.filled is True
    assert flaky.close_attempts == 2  # failed once, retried, then persisted
    rec = store.get_position(held.position_id)
    assert rec is not None and rec.status == "closed"
    assert rec.close_reason == "take_profit"


# ---------- get_collateral_balance_raw (wallet-balance W2) ----------


def test_collateral_balance_raw_reads_clob(store) -> None:
    clob = _FakeClob(ctf_balance_raw=162_199_200)
    le = LiveExecutor(portfolio=store, clob_client=clob)
    assert le.get_collateral_balance_raw() == 162_199_200
    # refresh-then-read, COLLATERAL asset type
    assert clob.allowance_updates[-1].asset_type == "COLLATERAL"


def test_collateral_balance_raw_error_is_none(store) -> None:
    class _Broken:
        def update_balance_allowance(self, params):
            raise RuntimeError("clob down")

        def get_balance_allowance(self, params):
            raise RuntimeError("clob down")

        def create_and_post_order(self, *a, **k):
            raise AssertionError("not used")

    le = LiveExecutor(portfolio=store, clob_client=_Broken())
    assert le.get_collateral_balance_raw() is None


# ---------- cancel-on-partial-fill (O1: resting-remainder hygiene) ----------
#
# A GTC order that partially fills leaves its remainder resting on the book;
# later fills against it are invisible to openPoly (a live orphan incident: a partially-filled
# GTC buy whose resting remainder filled later untracked). After a partial
# fill the executor must cancel the remainder and record the order's FINAL
# matched size.


def test_buy_partial_fill_cancels_resting_remainder(store) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xPART",
            "status": "matched",
            "makingAmount": "6.0",  # pUSD paid
            "takingAmount": "12.0",  # only 12 of 30 filled immediately
            "transactionsHashes": ["0xTX"],
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(price=0.5, qty=30.0), news_id="n", ts=1.0)
    assert r.filled is True
    assert r.qty == pytest.approx(12.0)
    assert clob.cancelled == ["0xPART"]  # remainder cancelled, nothing rests
    held = store.get_open_position("m1", "yes")
    assert held is not None and held.qty == pytest.approx(12.0)


def test_buy_partial_fill_records_late_matched_qty(store) -> None:
    """A fill racing the cancel: get_order's final size_matched (13) beats the
    response-reported 12 — record 13 so the extra token isn't orphaned."""
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xPART",
            "status": "matched",
            "makingAmount": "6.0",
            "takingAmount": "12.0",
            "transactionsHashes": ["0xTX"],
        },
        order_status={"size_matched": "13.0"},
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(price=0.5, qty=30.0), news_id="n", ts=1.0)
    assert r.qty == pytest.approx(13.0)


def test_buy_full_fill_does_not_cancel(store) -> None:
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xFULL",
            "status": "matched",
            "makingAmount": "5.0",
            "takingAmount": "10.0",
            "transactionsHashes": ["0xTX"],
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(price=0.5, qty=10.0), news_id="n", ts=1.0)
    assert r.filled is True
    assert clob.cancelled == []  # nothing resting — no cancel round-trip


def test_buy_cancel_failure_records_reported_qty(store) -> None:
    """Cancel failing must not lose the recorded fill — record the reported
    qty; the resting remainder is the reverse-reconciliation alert's job."""
    m = _market("m1")
    _populate(m)
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xPART",
            "status": "matched",
            "makingAmount": "6.0",
            "takingAmount": "12.0",
            "transactionsHashes": ["0xTX"],
        },
        cancel_raises=True,
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_buy(_intent(price=0.5, qty=30.0), news_id="n", ts=1.0)
    assert r.filled is True
    assert r.qty == pytest.approx(12.0)


def test_sell_partial_fill_cancels_resting_remainder(store) -> None:
    m = _market("m1")
    _populate(m, _book(m.yes_token_id, bid=0.55))
    held = store.open_position(
        market_id="m1",
        side="yes",
        token_id=m.yes_token_id,
        condition_id=m.condition_id,
        price=0.40,
        qty=18.0,
        ts=100.0,
        news_id="n",
    )
    clob = _FakeClob(
        order_response={
            "success": True,
            "orderID": "0xSPART",
            "status": "matched",
            "makingAmount": "15.0",  # 15 of 18 sold immediately
            "takingAmount": "8.25",
            "transactionsHashes": ["0xSTX"],
        }
    )
    le = LiveExecutor(portfolio=store, clob_client=clob)
    r = le.execute_sell(held, close_reason="stop_loss", ts=200.0)
    assert r.filled is True
    assert clob.cancelled == ["0xSPART"]  # the unsold 3 don't rest
