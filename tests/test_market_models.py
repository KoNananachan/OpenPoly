"""Tests for openpoly.markets.models — Gamma market normalization."""

from __future__ import annotations

from openpoly.markets.models import Market, normalize_gamma_market


def _raw(**overrides):
    base = {
        "id": "558934",
        "conditionId": "0x7976b8",
        "question": "Will X win?",
        "slug": "will-x-win",
        "clobTokenIds": '["111", "222"]',
        "endDate": "2026-07-20T00:00:00Z",
        "bestBid": 0.167,
        "bestAsk": 0.168,
        "spread": 0.001,
        "lastTradePrice": 0.167,
        "volume24hr": 302524.86,
        "liquidityNum": 491179.27,
        "feesEnabled": False,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }
    base.update(overrides)
    return base


def test_normalize_basic():
    m = normalize_gamma_market(_raw())
    assert isinstance(m, Market)
    assert m.market_id == "558934"
    assert m.condition_id == "0x7976b8"
    assert m.yes_token_id == "111"
    assert m.no_token_id == "222"
    assert m.volume_24h == 302524.86
    assert m.liquidity == 491179.27
    assert m.taker_fee_rate == 0.0  # feesEnabled False
    assert m.end_date is not None
    assert m.end_date.utcoffset().total_seconds() == 0  # aware UTC


def test_clob_token_ids_as_list():
    m = normalize_gamma_market(_raw(clobTokenIds=["aaa", "bbb"]))
    assert m.yes_token_id == "aaa"
    assert m.no_token_id == "bbb"


def test_single_token_has_no_no_side():
    m = normalize_gamma_market(_raw(clobTokenIds='["only"]'))
    assert m.yes_token_id == "only"
    assert m.no_token_id is None


def test_missing_tokens_returns_none():
    assert normalize_gamma_market(_raw(clobTokenIds=None)) is None
    assert normalize_gamma_market(_raw(clobTokenIds="[]")) is None
    assert normalize_gamma_market(_raw(clobTokenIds="not-json")) is None


def test_missing_identity_returns_none():
    assert normalize_gamma_market(_raw(id=None)) is None
    assert normalize_gamma_market(_raw(conditionId=None)) is None


def test_fail_closed_defaults():
    raw = _raw()
    del raw["closed"]
    del raw["acceptingOrders"]
    del raw["enableOrderBook"]
    m = normalize_gamma_market(raw)
    assert m.closed is True
    assert m.accepting_orders is False
    assert m.enable_order_book is False


def test_fee_basis_points_to_rate():
    # Real sports market shape: feesEnabled True, takerBaseFee 1000 bps.
    m = normalize_gamma_market(_raw(feesEnabled=True, takerBaseFee=1000))
    assert m.taker_fee_rate == 0.1


def test_fee_unknown_is_none():
    # feesEnabled true but no takerBaseFee -> fee is genuinely unknown.
    m = normalize_gamma_market(_raw(feesEnabled=True))
    assert m.taker_fee_rate is None


def test_event_metadata_and_tags():
    event = {
        "id": "30615",
        "title": "2026 FIFA World Cup",
        "tags": [{"slug": "soccer"}, {"slug": "sports"}, {"label": "no-slug"}],
    }
    m = normalize_gamma_market(_raw(), event=event)
    assert m.event_id == "30615"
    assert m.event_title == "2026 FIFA World Cup"
    assert m.event_tags == ("soccer", "sports")


def test_no_event_means_empty_tags():
    m = normalize_gamma_market(_raw())
    assert m.event_tags == ()
    assert m.event_id is None


def test_mid_and_reference_price():
    m = normalize_gamma_market(_raw())
    assert m.mid == (0.167 + 0.168) / 2.0
    assert m.reference_price == m.mid
    m2 = normalize_gamma_market(_raw(bestBid=None, bestAsk=None))
    assert m2.mid is None
    assert m2.reference_price == 0.167  # falls back to last trade


def test_normalize_parses_neg_risk_true() -> None:
    raw = {
        "id": "m1",
        "conditionId": "0xabc",
        "question": "Q?",
        "clobTokenIds": '["yes-m1", "no-m1"]',
        "negRisk": True,
    }
    m = normalize_gamma_market(raw, event={"id": "e", "title": "E", "tags": []})
    assert m is not None
    assert m.neg_risk is True


def test_normalize_parses_neg_risk_false_when_absent() -> None:
    raw = {
        "id": "m2",
        "conditionId": "0xdef",
        "question": "Q?",
        "clobTokenIds": '["yes-m2", "no-m2"]',
    }
    m = normalize_gamma_market(raw, event={"id": "e", "title": "E", "tags": []})
    assert m is not None
    assert m.neg_risk is False
