"""Tests for openpoly.sections.embedding.minilm_v0 — EmbeddingFilterV0.

The section reads two process singletons — the ``MarketSourceManager`` catalog
and the ``EmbeddingManager`` warm cache. Both are isolated per test via an
autouse fixture; the embedding model is never loaded (a fake encoder is
injected before warming).
"""

from __future__ import annotations

import numpy as np
import pytest

from openpoly.embedding.manager import manager as embedding_manager
from openpoly.embedding.models import MarketCandidates
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import Market
from openpoly.markets.store import MarketStore, PollSummary
from openpoly.news.ring_buffer import NewsItem
from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.embedding.minilm_v0 import (
    EmbeddingFilterConfig,
    EmbeddingFilterV0,
)


# ---------- helpers ----------


def _market(market_id: str, question: str) -> Market:
    return Market(
        market_id=market_id,
        condition_id=f"c-{market_id}",
        question=question,
        slug=market_id,
        yes_token_id=f"y-{market_id}",
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


def _news(content: str, news_id: str = "n1") -> NewsItem:
    return NewsItem(
        id=news_id,
        content=content,
        urgency="high",
        sentiment=None,
        published_at=0.0,
        received_at=0.0,
    )


def _set_catalog(markets: list[Market]) -> None:
    market_source_manager.store.replace(
        markets, PollSummary(ts=0.0, fetched=len(markets), kept=len(markets))
    )


def _make_encoder(table: dict[str, list[float]]):
    def enc(texts: list[str]) -> np.ndarray:
        return np.array([table[t] for t in texts], dtype=np.float32)

    return enc


def _warm(table: dict[str, list[float]]) -> None:
    """Inject a fake encoder into the embedding singleton and warm the catalog."""
    embedding_manager._encoder = _make_encoder(table)  # noqa: SLF001
    embedding_manager.refresh()


@pytest.fixture(autouse=True)
def _isolate_singletons():
    original = market_source_manager.store
    market_source_manager.store = MarketStore()
    yield
    market_source_manager.store = original
    embedding_manager._encoder = None  # noqa: SLF001
    embedding_manager._vectors = {}  # noqa: SLF001
    embedding_manager._text_hashes = {}  # noqa: SLF001


# ---------- catalog discovery + metadata ----------


def test_embedding_in_default_catalog() -> None:
    matches = [e for e in scan() if e.name == "EmbeddingFilterV0"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry.type == "embedding"
    assert entry.requires == ["market_data"]


def test_section_metadata() -> None:
    assert EmbeddingFilterV0.SECTION_TYPE == "embedding"
    assert EmbeddingFilterV0.SECTION_VERSION
    assert EmbeddingFilterV0.REQUIRES == ["market_data"]
    assert EmbeddingFilterV0.Config is EmbeddingFilterConfig


# ---------- skip paths ----------


def test_run_no_news_skips() -> None:
    section = EmbeddingFilterV0(EmbeddingFilterConfig())
    out = section.run(SectionInput(tick_type="event", payload=None))
    assert out.verdict == "skip"
    assert out.reason == "no news item"


def test_run_empty_catalog_skips() -> None:
    # Catalog left empty by the fixture → skip before any model touch.
    section = EmbeddingFilterV0(EmbeddingFilterConfig())
    out = section.run(SectionInput(tick_type="event", payload=_news("anything")))
    assert out.verdict == "skip"
    assert out.reason == "empty market catalog"


def test_run_no_candidate_above_threshold_skips() -> None:
    _set_catalog([_market("m1", "alpha")])
    # News vector orthogonal to the only market → cosine 0 < default 0.35.
    _warm({"alpha": [1.0, 0.0], "unrelated": [0.0, 1.0]})
    section = EmbeddingFilterV0(EmbeddingFilterConfig())
    out = section.run(SectionInput(tick_type="event", payload=_news("unrelated")))
    assert out.verdict == "skip"
    assert out.reason == "no market above similarity threshold"


# ---------- happy path: topK + threshold ----------


def test_run_topk_and_threshold_ordering() -> None:
    markets = [
        _market("m1", "aligned"),
        _market("m2", "near"),
        _market("m3", "diagonal"),
        _market("m4", "orthogonal"),
    ]
    _set_catalog(markets)
    _warm(
        {
            "aligned": [1.0, 0.0],
            "near": [1.0, 0.3],
            "diagonal": [1.0, 1.0],
            "orthogonal": [0.0, 1.0],
            "fed news": [1.0, 0.0],
        }
    )
    section = EmbeddingFilterV0(EmbeddingFilterConfig(top_k=2, similarity_threshold=0.5))
    out = section.run(SectionInput(tick_type="event", payload=_news("fed news")))
    assert out.verdict == "ok"
    assert isinstance(out.payload, MarketCandidates)
    candidates = out.payload.candidates
    # m4 (cosine 0.0) filtered; top_k caps at 2; score-descending.
    assert [c.market.market_id for c in candidates] == ["m1", "m2"]
    assert candidates[0].score >= candidates[1].score
    assert out.payload.news.id == "n1"
    assert out.signals["candidate_count"] == 2
    assert out.signals["top_market_id"] == "m1"
    assert out.signals["catalog_size"] == 4
