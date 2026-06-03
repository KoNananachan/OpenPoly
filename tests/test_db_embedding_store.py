"""Tests for openpoly.db.embedding_store — market_embedding cache repository."""

from __future__ import annotations

import numpy as np
from sqlalchemy import select

from openpoly.db.embedding_store import (
    MarketEmbeddingStore,
    bytes_to_vector,
    vector_to_bytes,
)
from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.db.tables import MarketEmbeddingRow


def _engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return engine


# ---------- serialization ----------


def test_vector_bytes_roundtrip() -> None:
    v = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    blob = vector_to_bytes(v)
    assert isinstance(blob, bytes)
    back = bytes_to_vector(blob)
    assert back.dtype == np.float32
    assert np.allclose(back, v)
    # bytes_to_vector returns a writable copy — callers may normalize in place.
    assert back.flags.writeable


def test_vector_to_bytes_coerces_float64() -> None:
    v64 = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    back = bytes_to_vector(vector_to_bytes(v64))
    assert back.dtype == np.float32
    assert np.allclose(back, [1.0, 2.0, 3.0])


# ---------- table bootstrap + load ----------


def test_init_db_creates_table_and_load_empty(tmp_path) -> None:
    engine = _engine(tmp_path)
    store = MarketEmbeddingStore(make_session_factory(engine))
    assert store.load("all-MiniLM-L6-v2") == {}
    engine.dispose()


# ---------- upsert / load ----------


def test_upsert_then_load(tmp_path) -> None:
    engine = _engine(tmp_path)
    store = MarketEmbeddingStore(make_session_factory(engine))
    v1 = np.array([1.0, 0.0], dtype=np.float32)
    v2 = np.array([0.0, 1.0], dtype=np.float32)
    store.upsert("m", {"a": ("h-a", v1), "b": ("h-b", v2)})
    loaded = store.load("m")
    assert set(loaded) == {"a", "b"}
    assert loaded["a"][0] == "h-a"
    assert np.allclose(loaded["a"][1], v1)
    assert np.allclose(loaded["b"][1], v2)
    engine.dispose()


def test_upsert_updates_existing_in_place(tmp_path) -> None:
    engine = _engine(tmp_path)
    factory = make_session_factory(engine)
    store = MarketEmbeddingStore(factory)
    store.upsert("m", {"a": ("h1", np.array([1.0], dtype=np.float32))})
    store.upsert("m", {"a": ("h2", np.array([9.0], dtype=np.float32))})
    loaded = store.load("m")
    assert loaded["a"][0] == "h2"
    assert np.allclose(loaded["a"][1], [9.0])
    # The (market_id, model_name) unique key means no duplicate row.
    with factory() as session:
        assert len(session.scalars(select(MarketEmbeddingRow)).all()) == 1
    engine.dispose()


def test_upsert_empty_is_noop(tmp_path) -> None:
    engine = _engine(tmp_path)
    store = MarketEmbeddingStore(make_session_factory(engine))
    store.upsert("m", {})
    assert store.load("m") == {}
    engine.dispose()


def test_load_filters_by_model_name(tmp_path) -> None:
    engine = _engine(tmp_path)
    store = MarketEmbeddingStore(make_session_factory(engine))
    store.upsert("model-x", {"a": ("hx", np.array([1.0], dtype=np.float32))})
    store.upsert("model-y", {"a": ("hy", np.array([2.0], dtype=np.float32))})
    # Same market_id, different model — distinct rows, distinct buckets.
    assert set(store.load("model-x")) == {"a"}
    assert store.load("model-x")["a"][0] == "hx"
    assert store.load("model-y")["a"][0] == "hy"
    assert store.load("model-z") == {}
    engine.dispose()
