"""Tests for openpoly.sections.market_source.polymarket — PolymarketSource."""

from __future__ import annotations

from openpoly.markets.manager import MarketSourceConfig, manager
from openpoly.markets.models import Market
from openpoly.markets.store import MarketStore, PollSummary
from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.market_source.polymarket import PolymarketSource


def _market(market_id: str) -> Market:
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


def test_section_metadata():
    assert PolymarketSource.SECTION_TYPE == "market_source"
    assert isinstance(PolymarketSource.SECTION_VERSION, str)
    assert PolymarketSource.SECTION_VERSION
    assert PolymarketSource.REQUIRES == []
    assert PolymarketSource.Config is MarketSourceConfig


def test_run_returns_catalog_snapshot():
    original = manager.store
    try:
        manager.store = MarketStore()
        manager.store.replace(
            [_market("x"), _market("y")],
            PollSummary(ts=1.0, fetched=5, kept=2, reason_counts={"low_volume": 3}),
        )
        section = PolymarketSource(MarketSourceConfig())
        out = section.run(SectionInput(tick_type="warm"))
        assert out.verdict == "ok"
        assert {m.market_id for m in out.payload} == {"x", "y"}
        assert out.signals["catalog_size"] == 2
        assert out.signals["last_poll_kept"] == 2
    finally:
        manager.store = original


def test_contract_test_passes():
    PolymarketSource.CONTRACT_TEST()  # raises ContractFailure-equivalent on failure


def test_registered_in_default_catalog():
    entries = scan()
    market = [e for e in entries if e.type == "market_source"]
    assert any(e.name == "PolymarketSource" for e in market)
    entry = next(e for e in market if e.name == "PolymarketSource")
    assert entry.requires == []
    assert "poll_interval_seconds" in entry.param_schema["properties"]
