"""Payload types for the ``embedding`` section.

``embedding`` sits between ``news_source`` and ``analyzer`` in the pipeline. For
an incoming news item it scores every catalog market's ``question`` by semantic
similarity and emits the survivors as ``MarketCandidates`` — the narrowed market
set ``analyzer`` then reasons over, instead of facing the full catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openpoly.markets.models import Market
from openpoly.news.ring_buffer import NewsItem


@dataclass(frozen=True)
class MarketCandidate:
    """One market that survived the embedding similarity filter.

    ``score`` is the cosine similarity between the news text and the market
    ``question`` — in ``[-1, 1]``, and ``[0, 1]`` in practice for normalized
    sentence embeddings. Higher is more relevant.
    """

    market: Market
    score: float


@dataclass(frozen=True)
class MarketCandidates:
    """The narrowed candidate set handed from ``embedding`` to ``analyzer``.

    ``candidates`` is always sorted by ``score`` descending — ``candidates[0]``
    is the best semantic match. The constructor enforces this ordering, so
    callers may pass the list in any order.
    """

    news: NewsItem
    candidates: list[MarketCandidate] = field(default_factory=list)

    def __post_init__(self) -> None:
        ordered = sorted(self.candidates, key=lambda c: c.score, reverse=True)
        object.__setattr__(self, "candidates", ordered)
