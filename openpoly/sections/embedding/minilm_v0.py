"""Embedding-filter section — semantic news↔market candidate narrowing.

``EmbeddingFilterV0`` is the openPoly atomization of a prior project's
``EmbeddingFilteredStrategy._filter_markets``: for an incoming news item it
ranks the market catalog by embedding cosine similarity and emits the top
matches as ``MarketCandidates`` for the analyzer to reason over.

A thin wrapper — the model, the warm vector cache, and the cosine ranking all
live in ``EmbeddingManager`` (``openpoly.embedding.manager``). The section owns
only the per-tick policy (config thresholds) and the section I/O envelope.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from openpoly.embedding.manager import (
    DEFAULT_MAX_QUESTION_CHARS,
    DEFAULT_MODEL,
    DEFAULT_WARM_INTERVAL,
    manager as embedding_manager,
)
from openpoly.embedding.models import MarketCandidates
from openpoly.markets.manager import manager as market_source_manager
from openpoly.news.ring_buffer import NewsItem
from openpoly.sections._base import SectionInput, SectionOutput


class EmbeddingFilterConfig(BaseModel):
    """Params for the ``embedding`` section — the news↔market similarity filter."""

    embedding_model: str = Field(
        default=DEFAULT_MODEL,
        description="Local sentence-transformer used to embed news + questions.",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum candidate markets handed to the analyzer.",
    )
    similarity_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for a market to survive.",
    )
    max_question_chars: int = Field(
        default=DEFAULT_MAX_QUESTION_CHARS,
        ge=20,
        le=2000,
        description="Market question text is truncated to this before embedding.",
    )
    warm_interval_seconds: int = Field(
        default=DEFAULT_WARM_INTERVAL,
        ge=30,
        le=86_400,
        description="Seconds between background catalog embedding refreshes.",
    )


class EmbeddingFilterV0:
    SECTION_TYPE = "embedding"
    SECTION_VERSION = "0.1.0"
    REQUIRES = ["market_data"]
    Config = EmbeddingFilterConfig

    def __init__(self, config: EmbeddingFilterConfig) -> None:
        self.config = config

    def run(self, input: SectionInput) -> SectionOutput:
        """Rank the market catalog against one incoming news item.

        ``skip`` when there is no news item, an empty catalog, or no market
        clears the similarity threshold — in every skip case the pipeline
        stops here without calling the analyzer. Otherwise the payload is a
        ``MarketCandidates`` (score-descending).
        """
        item = input.payload
        if not isinstance(item, NewsItem):
            return SectionOutput(payload=None, verdict="skip", reason="no news item")
        markets = market_source_manager.store.snapshot()
        if not markets:
            return SectionOutput(payload=None, verdict="skip", reason="empty market catalog")
        candidates = embedding_manager.match(
            item.content,
            markets,
            top_k=self.config.top_k,
            threshold=self.config.similarity_threshold,
        )
        if not candidates:
            return SectionOutput(
                payload=None,
                verdict="skip",
                reason="no market above similarity threshold",
                signals={"news_id": item.id, "catalog_size": len(markets)},
            )
        return SectionOutput(
            payload=MarketCandidates(news=item, candidates=candidates),
            verdict="ok",
            signals={
                "news_id": item.id,
                "catalog_size": len(markets),
                "candidate_count": len(candidates),
                "top_market_id": candidates[0].market.market_id,
                "top_score": candidates[0].score,
            },
        )

    @staticmethod
    def CONTRACT_TEST() -> None:
        # Registry-safe path only: a non-NewsItem payload skips before the
        # catalog read and the (heavy) lazy model load — keeps registry scan
        # light. The empty-catalog / similarity paths are covered by the EM5
        # section tests with an injected fake encoder.
        cfg = EmbeddingFilterConfig()
        inst = EmbeddingFilterV0(cfg)
        out = inst.run(SectionInput(tick_type="hard", payload=None))
        assert out.verdict == "skip"
