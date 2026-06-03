"""Tests for openpoly.embedding.manager — EmbeddingManager warm cache.

The injected ``encoder`` seam keeps every test off the real ~90MB model.
``refresh()`` reads the global ``MarketSourceManager`` catalog, so each test
isolates that singleton's store via an autouse fixture.
"""

from __future__ import annotations

import numpy as np
import pytest

from openpoly.db.embedding_store import MarketEmbeddingStore
from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.embedding.manager import EmbeddingManager
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import Market
from openpoly.markets.store import MarketStore, PollSummary


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


def _set_catalog(markets: list[Market]) -> None:
    market_source_manager.store.replace(
        markets, PollSummary(ts=0.0, fetched=len(markets), kept=len(markets))
    )


class FakeEncoder:
    """Maps known texts to fixed vectors; counts calls for cache-hit checks."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table
        self.calls = 0
        self.texts_encoded: list[str] = []

    def __call__(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        self.texts_encoded.extend(texts)
        return np.array([self._table[t] for t in texts], dtype=np.float32)


@pytest.fixture(autouse=True)
def _isolate_market_store():
    """Swap in a throwaway catalog so refresh() sees only this test's markets."""
    original = market_source_manager.store
    market_source_manager.store = MarketStore()
    yield
    market_source_manager.store = original


def _factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'emb.db'}")
    init_db(engine)
    return make_session_factory(engine)


# ---------- encode seam ----------


def test_encode_uses_injected_encoder() -> None:
    fake = FakeEncoder({"hello": [1.0, 2.0, 3.0]})
    mgr = EmbeddingManager(encoder=fake)
    arr = mgr.encode(["hello"])
    assert arr.shape == (1, 3)
    assert arr.dtype == np.float32
    assert fake.calls == 1


# ---------- warm cycle ----------


def test_refresh_warms_catalog() -> None:
    _set_catalog([_market("m1", "alpha"), _market("m2", "beta")])
    fake = FakeEncoder({"alpha": [3.0, 0.0], "beta": [0.0, 4.0]})
    mgr = EmbeddingManager(encoder=fake)
    assert mgr.refresh() == 2
    vectors = mgr._vectors  # noqa: SLF001
    assert set(vectors) == {"m1", "m2"}
    # Stored vectors are L2-normalized.
    for vec in vectors.values():
        assert np.isclose(np.linalg.norm(vec), 1.0)


def test_refresh_skips_unchanged_on_second_call() -> None:
    _set_catalog([_market("m1", "alpha"), _market("m2", "beta")])
    fake = FakeEncoder({"alpha": [1.0, 0.0], "beta": [0.0, 1.0]})
    mgr = EmbeddingManager(encoder=fake)
    assert mgr.refresh() == 2
    assert fake.calls == 1
    # Nothing changed → no re-encode.
    assert mgr.refresh() == 0
    assert fake.calls == 1


def test_refresh_reencodes_changed_question() -> None:
    _set_catalog([_market("m1", "alpha"), _market("m2", "beta")])
    fake = FakeEncoder({"alpha": [1.0, 0.0], "beta": [0.0, 1.0], "alpha v2": [1.0, 1.0]})
    mgr = EmbeddingManager(encoder=fake)
    mgr.refresh()
    # m1 keeps its id but gets a new question — only it should re-encode.
    _set_catalog([_market("m1", "alpha v2"), _market("m2", "beta")])
    assert mgr.refresh() == 1
    assert fake.texts_encoded[-1] == "alpha v2"


def test_refresh_drops_removed_market() -> None:
    _set_catalog([_market("m1", "alpha"), _market("m2", "beta")])
    fake = FakeEncoder({"alpha": [1.0, 0.0], "beta": [0.0, 1.0]})
    mgr = EmbeddingManager(encoder=fake)
    mgr.refresh()
    _set_catalog([_market("m1", "alpha")])
    mgr.refresh()
    assert set(mgr._vectors) == {"m1"}  # noqa: SLF001


# ---------- DB round-trip ----------


def test_refresh_persists_to_store(tmp_path) -> None:
    factory = _factory(tmp_path)
    _set_catalog([_market("m1", "alpha"), _market("m2", "beta")])
    fake = FakeEncoder({"alpha": [1.0, 0.0], "beta": [0.0, 1.0]})
    mgr = EmbeddingManager(encoder=fake)
    mgr._store = MarketEmbeddingStore(factory)  # noqa: SLF001
    assert mgr.refresh() == 2
    # The vectors landed in the DB cache.
    cached = MarketEmbeddingStore(factory).load("all-MiniLM-L6-v2")
    assert set(cached) == {"m1", "m2"}


async def test_start_warm_starts_from_db_cache(tmp_path) -> None:
    factory = _factory(tmp_path)
    _set_catalog([_market("m1", "alpha"), _market("m2", "beta")])
    # First manager populates the DB cache.
    seeder = EmbeddingManager(encoder=FakeEncoder({"alpha": [1.0, 0.0], "beta": [0.0, 1.0]}))
    seeder._store = MarketEmbeddingStore(factory)  # noqa: SLF001
    seeder.refresh()
    # A fresh manager start()s and warm-starts purely from the cache — its
    # encoder (empty table) would KeyError if it tried to encode anything.
    fake = FakeEncoder({})
    mgr = EmbeddingManager(encoder=fake)
    await mgr.start(session_factory=factory)
    try:
        assert set(mgr._vectors) == {"m1", "m2"}  # noqa: SLF001
        assert fake.calls == 0
    finally:
        await mgr.stop()


# ---------- match ----------


def test_match_threshold_and_topk() -> None:
    markets = [
        _market("m1", "aligned"),
        _market("m2", "near"),
        _market("m3", "diagonal"),
        _market("m4", "orthogonal"),
    ]
    _set_catalog(markets)
    fake = FakeEncoder(
        {
            "aligned": [1.0, 0.0],
            "near": [1.0, 0.3],
            "diagonal": [1.0, 1.0],
            "orthogonal": [0.0, 1.0],
            "query": [1.0, 0.0],
        }
    )
    mgr = EmbeddingManager(encoder=fake)
    mgr.refresh()
    out = mgr.match("query", markets, top_k=2, threshold=0.5)
    # m4 (cosine 0.0) is below threshold; top_k caps at 2; score-descending.
    assert [c.market.market_id for c in out] == ["m1", "m2"]
    assert out[0].score >= out[1].score


def test_match_empty_warm_dict_returns_empty() -> None:
    markets = [_market("m1", "alpha")]
    _set_catalog(markets)
    mgr = EmbeddingManager(encoder=FakeEncoder({"query": [1.0, 0.0]}))
    # Nothing warmed yet → no candidates.
    assert mgr.match("query", markets, top_k=5, threshold=0.0) == []


# ---------- lifecycle ----------


async def test_start_stop_lifecycle() -> None:
    mgr = EmbeddingManager(encoder=FakeEncoder({}))
    assert mgr.status()["state"] == "stopped"
    await mgr.start()
    try:
        assert mgr.status()["state"] == "running"
    finally:
        await mgr.stop()
    assert mgr.status()["state"] == "stopped"
