"""Tests for openpoly.markets.filters — discovery-time market filtering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openpoly.markets.filters import (
    MarketFilterConfig,
    evaluate_market,
    filter_markets,
)
from openpoly.markets.models import Market

NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
CFG = MarketFilterConfig()


def _market(**overrides) -> Market:
    """A market that passes every default filter; override one field per test."""
    base = dict(
        market_id="m1",
        condition_id="0xabc",
        question="Will X happen?",
        slug="will-x",
        yes_token_id="111",
        no_token_id="222",
        end_date=NOW + timedelta(days=30),
        best_bid=0.40,
        best_ask=0.42,
        spread=0.02,
        last_trade_price=0.41,
        volume_24h=50_000.0,
        liquidity=20_000.0,
        taker_fee_rate=0.0,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id="e1",
        event_title="Event",
        event_tags=(),
    )
    base.update(overrides)
    return Market(**base)


def test_valid_market_kept():
    assert evaluate_market(_market(), CFG, now=NOW).kept


def test_closed_rejected():
    d = evaluate_market(_market(closed=True), CFG, now=NOW)
    assert not d.kept
    assert d.reason == "market_resolved"


def test_not_accepting_orders_rejected():
    assert evaluate_market(_market(accepting_orders=False), CFG, now=NOW).reason == (
        "market_resolved"
    )


def test_order_book_disabled_rejected():
    assert evaluate_market(_market(enable_order_book=False), CFG, now=NOW).reason == (
        "market_resolved"
    )


def test_excluded_tag_rejected():
    d = evaluate_market(_market(event_tags=("politics", "sports")), CFG, now=NOW)
    assert d.reason == "excluded_tag"
    assert "sports" in (d.detail or "")


def test_null_fee_rejected():
    d = evaluate_market(_market(taker_fee_rate=None), CFG, now=NOW)
    assert d.reason == "null_fee_rate"


def test_nonzero_fee_rejected():
    d = evaluate_market(_market(taker_fee_rate=0.1), CFG, now=NOW)
    assert d.reason == "fee_not_zero"


def test_nonzero_fee_kept_when_not_required():
    cfg = MarketFilterConfig(require_zero_fee=False)
    assert evaluate_market(_market(taker_fee_rate=0.1), cfg, now=NOW).kept


def test_missing_end_date_rejected():
    d = evaluate_market(_market(end_date=None), CFG, now=NOW)
    assert d.reason == "missing_end_date"


def test_near_expiry_rejected():
    d = evaluate_market(_market(end_date=NOW + timedelta(hours=6)), CFG, now=NOW)
    assert d.reason == "near_expiry"


def test_low_volume_rejected():
    d = evaluate_market(_market(volume_24h=100.0), CFG, now=NOW)
    assert d.reason == "low_volume"


def test_low_liquidity_rejected():
    d = evaluate_market(_market(liquidity=50.0), CFG, now=NOW)
    assert d.reason == "low_liquidity"


def test_price_below_band_rejected():
    d = evaluate_market(
        _market(best_bid=0.005, best_ask=0.015, last_trade_price=0.01), CFG, now=NOW
    )
    assert d.reason == "price_extreme"


def test_price_above_band_rejected():
    # YES mid ~0.99 -> NO side at ~0.01, an untradeable extreme.
    d = evaluate_market(
        _market(best_bid=0.985, best_ask=0.995, last_trade_price=0.99), CFG, now=NOW
    )
    assert d.reason == "price_extreme"


def test_missing_price_rejected():
    d = evaluate_market(_market(best_bid=None, best_ask=None, last_trade_price=None), CFG, now=NOW)
    assert d.reason == "price_extreme"
    assert d.detail == "unknown"


def test_high_spread_rejected():
    d = evaluate_market(_market(spread=0.30), CFG, now=NOW)
    assert d.reason == "high_spread"


def test_unknown_spread_is_lenient():
    assert evaluate_market(_market(spread=None), CFG, now=NOW).kept


def test_rule_ordering_liveness_beats_quality():
    # closed AND low volume -> liveness (rule 1) wins over volume (rule 5).
    d = evaluate_market(_market(closed=True, volume_24h=1.0), CFG, now=NOW)
    assert d.reason == "market_resolved"


def test_filter_markets_batch():
    markets = [
        _market(market_id="keep1"),
        _market(market_id="keep2"),
        _market(market_id="drop1", closed=True),
        _market(market_id="drop2", volume_24h=10.0),
        _market(market_id="drop3", volume_24h=20.0),
    ]
    report = filter_markets(markets, CFG, now=NOW)
    assert {m.market_id for m in report.kept} == {"keep1", "keep2"}
    assert len(report.rejected) == 3
    assert report.reason_counts == {"market_resolved": 1, "low_volume": 2}
    assert report.total == 5
