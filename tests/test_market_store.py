"""Tests for openpoly.markets.store — in-memory market catalog."""

from __future__ import annotations

from openpoly.markets.models import Market
from openpoly.markets.store import MarketStore, PollSummary


def _market(market_id: str = "m1") -> Market:
    return Market(
        market_id=market_id,
        condition_id=f"0x{market_id}",
        question="Q?",
        slug=market_id,
        yes_token_id="y",
        no_token_id="n",
        end_date=None,
        best_bid=0.40,
        best_ask=0.42,
        spread=0.02,
        last_trade_price=0.41,
        volume_24h=1000.0,
        liquidity=1000.0,
        taker_fee_rate=0.0,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id=None,
        event_title=None,
        event_tags=(),
    )


def _summary(**over) -> PollSummary:
    base = dict(ts=1000.0, fetched=10, kept=3, reason_counts={"low_volume": 7})
    base.update(over)
    return PollSummary(**base)


def test_empty_store():
    store = MarketStore()
    assert len(store) == 0
    assert store.snapshot() == []
    assert store.get("nope") is None
    assert store.last_poll is None


def test_replace_populates_catalog():
    store = MarketStore()
    store.replace([_market("a"), _market("b"), _market("c")], _summary(kept=3))
    assert len(store) == 3
    assert {m.market_id for m in store.snapshot()} == {"a", "b", "c"}
    assert store.get("a") is not None


def test_replace_is_atomic_swap():
    store = MarketStore()
    store.replace([_market("old1"), _market("old2")], _summary())
    store.replace([_market("new1")], _summary())
    assert len(store) == 1
    assert store.get("old1") is None
    assert store.get("new1") is not None


def test_get_missing_returns_none():
    store = MarketStore()
    store.replace([_market("a")], _summary())
    assert store.get("zzz") is None


def test_last_poll_recorded():
    store = MarketStore()
    store.replace([_market("a")], _summary(ts=42.0, fetched=5, kept=1))
    poll = store.last_poll
    assert poll is not None
    assert poll.ts == 42.0
    assert poll.fetched == 5
    assert poll.kept == 1


def test_snapshot_preserves_order():
    store = MarketStore()
    store.replace([_market("z"), _market("a"), _market("m")], _summary())
    assert [m.market_id for m in store.snapshot()] == ["z", "a", "m"]


def test_poll_summary_to_dict():
    d = _summary(ts=7.0, fetched=20, kept=4, reason_counts={"sports": 16}).to_dict()
    assert d == {
        "ts": 7.0,
        "fetched": 20,
        "kept": 4,
        "reason_counts": {"sports": 16},
        "holding_synced": 0,
        "holding_sync_failed": 0,
    }


def test_poll_summary_to_dict_copies_reason_counts():
    counts = {"low_volume": 1}
    d = _summary(reason_counts=counts).to_dict()
    counts["mutated"] = 99
    assert "mutated" not in d["reason_counts"]


def test_poll_summary_defaults_holding_counts_to_zero():
    from openpoly.markets.store import PollSummary

    summary = PollSummary(ts=0.0, fetched=10, kept=5)
    assert summary.holding_synced == 0
    assert summary.holding_sync_failed == 0
    assert summary.to_dict()["holding_synced"] == 0
    assert summary.to_dict()["holding_sync_failed"] == 0


def test_poll_summary_records_holding_counts():
    from openpoly.markets.store import PollSummary

    summary = PollSummary(
        ts=0.0,
        fetched=10,
        kept=5,
        holding_synced=2,
        holding_sync_failed=1,
    )
    assert summary.holding_synced == 2
    assert summary.holding_sync_failed == 1
    d = summary.to_dict()
    assert d["holding_synced"] == 2
    assert d["holding_sync_failed"] == 1


def test_store_union_adds_missing_markets():
    def _m(mid: str) -> Market:
        return Market(
            market_id=mid,
            condition_id=f"0x{mid}",
            question="?",
            slug=mid,
            yes_token_id=f"y_{mid}",
            no_token_id=f"n_{mid}",
            end_date=None,
            best_bid=None,
            best_ask=None,
            spread=None,
            last_trade_price=None,
            volume_24h=0.0,
            liquidity=0.0,
            taker_fee_rate=None,
            closed=False,
            accepting_orders=True,
            enable_order_book=True,
            event_id=None,
            event_title=None,
            event_tags=(),
        )

    store = MarketStore()
    store.replace([_m("a"), _m("b")], _summary(kept=2))
    added = store.union([_m("b"), _m("c")])  # b duplicate, c new
    assert added == 1
    ids = store.snapshot_ids()
    assert ids == {"a", "b", "c"}


def test_store_union_preserves_existing_entry():
    """Discovery wins: existing market_id keeps its first-stored Market object."""
    discovery = Market(
        market_id="m1",
        condition_id="0xm1",
        question="discovery",
        slug="m1",
        yes_token_id="y",
        no_token_id="n",
        end_date=None,
        best_bid=None,
        best_ask=None,
        spread=None,
        last_trade_price=None,
        volume_24h=99.0,
        liquidity=0.0,
        taker_fee_rate=None,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id=None,
        event_title=None,
        event_tags=(),
    )
    holding = Market(
        market_id="m1",
        condition_id="0xm1",
        question="holding-sync",
        slug="m1",
        yes_token_id="y",
        no_token_id="n",
        end_date=None,
        best_bid=None,
        best_ask=None,
        spread=None,
        last_trade_price=None,
        volume_24h=1.0,
        liquidity=0.0,
        taker_fee_rate=None,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id=None,
        event_title=None,
        event_tags=(),
    )
    store = MarketStore()
    store.replace([discovery], _summary(kept=1))
    added = store.union([holding])
    assert added == 0
    assert store.get("m1").question == "discovery"  # discovery version retained


def test_store_update_last_poll_replaces_summary_only():
    m = Market(
        market_id="x",
        condition_id="0xx",
        question="?",
        slug="x",
        yes_token_id="y",
        no_token_id=None,
        end_date=None,
        best_bid=None,
        best_ask=None,
        spread=None,
        last_trade_price=None,
        volume_24h=0.0,
        liquidity=0.0,
        taker_fee_rate=None,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id=None,
        event_title=None,
        event_tags=(),
    )
    store = MarketStore()
    store.replace([m], _summary(kept=1))
    store.update_last_poll(
        PollSummary(ts=1.0, fetched=1, kept=1, holding_synced=2, holding_sync_failed=0)
    )
    assert store.last_poll.ts == 1.0
    assert store.last_poll.holding_synced == 2
    assert len(store) == 1  # catalog untouched
    assert store.get("x") is m
