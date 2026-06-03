"""Embedding runtime manager — the model + the warm vector cache.

``EmbeddingManager`` owns the local sentence-transformer and the in-memory map
of ``market_id -> normalized embedding``. A background warm loop diffs the live
market catalog against that map every ``warm_interval`` seconds, encodes
whatever is new or re-titled, and persists it through ``MarketEmbeddingStore``
so a restart reloads instead of recomputing.

Encoding a full catalog is CPU-bound and would stall the event loop, so the
warm cycle runs in a worker thread (``asyncio.to_thread``). Encoding a single
news item — what the ``embedding`` section does per tick — is cheap enough to
run inline.

The ``encoder`` seam is injectable: tests pass a fake and never download the
~90MB model. The real encoder (sentence-transformers + torch) is imported
lazily on first use, never at module import.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.embedding_store import EmbeddingCache, MarketEmbeddingStore
from openpoly.embedding.models import MarketCandidate
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.models import Market
from openpoly.runtime.section_log import WarmCycle, embedding_warm_log

logger = logging.getLogger(__name__)

# Local sentence-transformer — matches a prior project's EmbeddingFilteredStrategy.
DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_MAX_QUESTION_CHARS = 200
DEFAULT_WARM_INTERVAL = 300

# Injectable encoding seam: list[str] -> (n, dim) float array.
Encoder = Callable[[list[str]], np.ndarray]


def _text_hash(text: str) -> str:
    """Stable digest of an embedded text — a re-titled market misses the cache."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vector: np.ndarray) -> np.ndarray:
    """L2-normalize a vector (float32). A zero vector is returned unchanged, so
    cosine similarity reduces to a plain dot product over the warm dict."""
    vec = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec if norm == 0.0 else vec / norm


class EmbeddingManager:
    """Owns the embedding model + the warm ``market_id -> vector`` cache.

    One per process (module singleton ``manager``). Lifecycle (start/stop of
    the warm loop) is driven by the FastAPI lifespan; ``match`` is called
    synchronously by the ``embedding`` section on each news tick.
    """

    def __init__(self, encoder: Encoder | None = None) -> None:
        self._encoder: Encoder | None = encoder
        self._encoder_lock = threading.Lock()
        self._model_name: str = DEFAULT_MODEL
        self._max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS
        self._warm_interval: float = float(DEFAULT_WARM_INTERVAL)
        # market_id -> normalized vector / text hash. Swapped wholesale by the
        # warm cycle, read via atomic dict.get() by match() — no lock needed.
        self._vectors: dict[str, np.ndarray] = {}
        self._text_hashes: dict[str, str] = {}
        self._store: MarketEmbeddingStore | None = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._state: str = "stopped"

    # ---------- lifecycle ----------

    async def start(
        self,
        *,
        encoder: Encoder | None = None,
        model_name: str = DEFAULT_MODEL,
        max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS,
        warm_interval_seconds: float = DEFAULT_WARM_INTERVAL,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """Configure the manager, warm-start from the DB cache, launch the loop.

        ``encoder`` overrides the encoding seam (tests inject a fake). Idempotent
        — a second call while already running is a no-op.
        """
        async with self._lock:
            if self._state == "running":
                return
            if encoder is not None:
                self._encoder = encoder
            self._model_name = model_name
            self._max_question_chars = max_question_chars
            self._warm_interval = float(warm_interval_seconds)
            if session_factory is not None:
                self._store = MarketEmbeddingStore(session_factory)
            if self._store is not None:
                await self._load_cache()
            self._stop.clear()
            self._task = asyncio.create_task(self._warm_loop())
            self._state = "running"

    async def stop(self) -> None:
        """Cancel the warm loop. Idempotent."""
        async with self._lock:
            if self._state != "running":
                return
            self._stop.set()
            task, self._task = self._task, None
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._state = "stopped"

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            await self.stop()

    # ---------- query (called by the embedding section) ----------

    def match(
        self,
        news_text: str,
        markets: list[Market],
        *,
        top_k: int,
        threshold: float,
    ) -> list[MarketCandidate]:
        """Rank ``markets`` against ``news_text`` by embedding cosine similarity.

        Only markets the warm loop has already embedded are scored — one not
        yet warm is skipped this tick (the next warm cycle picks it up).
        Returns the ``threshold`` survivors, score-descending, capped at
        ``top_k``.
        """
        if not markets or not self._vectors:
            return []
        news_vec = _normalize(self.encode([news_text])[0])
        scored: list[MarketCandidate] = []
        for market in markets:
            vec = self._vectors.get(market.market_id)
            if vec is None:
                continue
            score = float(np.dot(news_vec, vec))
            if score >= threshold:
                scored.append(MarketCandidate(market=market, score=score))
        scored.sort(key=lambda candidate: candidate.score, reverse=True)
        return scored[:top_k]

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode ``texts`` to an ``(n, dim)`` float32 array.

        Lazy-loads the real model on first call; an injected encoder bypasses
        the load entirely.
        """
        encoder = self._ensure_encoder()
        arr = np.asarray(encoder(list(texts)), dtype=np.float32)
        return arr.reshape(1, -1) if arr.ndim == 1 else arr

    def status(self) -> dict[str, Any]:
        """Snapshot for the embedding section inspector / status route."""
        return {
            "state": self._state,
            "model_name": self._model_name,
            "warm_count": len(self._vectors),
            "encoder_loaded": self._encoder is not None,
        }

    # ---------- warm cycle ----------

    def refresh(self) -> int:
        """Run one warm cycle — sync. Diffs the live catalog against the warm
        dict, encodes the misses, persists them, swaps in the fresh vectors.
        Returns the number of markets (re)embedded this cycle.

        The warm loop runs this in a worker thread (catalog encoding is
        CPU-bound and must not block the event loop); tests call it directly.
        """
        start = time.monotonic()
        markets = market_source_manager.store.snapshot()
        vectors, hashes = self._vectors, self._text_hashes
        next_vectors: dict[str, np.ndarray] = {}
        next_hashes: dict[str, str] = {}
        misses: list[tuple[str, str, str]] = []  # (market_id, text, text_hash)
        for market in markets:
            text = market.question[: self._max_question_chars]
            text_hash = _text_hash(text)
            cached = vectors.get(market.market_id)
            if cached is not None and hashes.get(market.market_id) == text_hash:
                next_vectors[market.market_id] = cached
                next_hashes[market.market_id] = text_hash
            else:
                misses.append((market.market_id, text, text_hash))
        if misses:
            encoded = self.encode([text for _, text, _ in misses])
            persist: EmbeddingCache = {}
            for (market_id, _text, text_hash), raw in zip(misses, encoded):
                vec = _normalize(raw)
                next_vectors[market_id] = vec
                next_hashes[market_id] = text_hash
                persist[market_id] = (text_hash, vec)
            if self._store is not None:
                self._store.upsert(self._model_name, persist)
        self._vectors = next_vectors
        self._text_hashes = next_hashes
        embedding_warm_log.append(
            WarmCycle(
                ts=time.time(),
                event="warm",
                embedded_count=len(misses),
                warm_count=len(next_vectors),
                catalog_size=len(markets),
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        )
        return len(misses)

    # ---------- internals ----------

    def _ensure_encoder(self) -> Encoder:
        if self._encoder is not None:
            return self._encoder
        with self._encoder_lock:
            if self._encoder is None:
                # Heavy (pulls torch); imported lazily, never at module load.
                from sentence_transformers import SentenceTransformer

                load_start = time.monotonic()
                model = SentenceTransformer(self._model_name)

                def _encode(texts: list[str]) -> np.ndarray:
                    return model.encode(
                        list(texts),
                        convert_to_numpy=True,
                        show_progress_bar=False,
                    )

                self._encoder = _encode
                logger.info("embedding model loaded: %s", self._model_name)
                embedding_warm_log.append(
                    WarmCycle(
                        ts=time.time(),
                        event="model_load",
                        embedded_count=0,
                        warm_count=len(self._vectors),
                        catalog_size=0,
                        latency_ms=int((time.monotonic() - load_start) * 1000),
                        detail=self._model_name,
                    )
                )
        return self._encoder

    async def _load_cache(self) -> None:
        """Warm-start the in-memory dict from the DB vector cache."""
        assert self._store is not None
        try:
            cached = await asyncio.to_thread(self._store.load, self._model_name)
        except Exception as exc:  # noqa: BLE001 — a cold start must still boot
            logger.warning("embedding cache load failed: %s", exc)
            embedding_warm_log.append(
                WarmCycle(
                    ts=time.time(),
                    event="error",
                    embedded_count=0,
                    warm_count=len(self._vectors),
                    catalog_size=0,
                    latency_ms=0,
                    detail="cache load",
                    error=repr(exc)[:200],
                )
            )
            return
        self._vectors = {mid: _normalize(vec) for mid, (_h, vec) in cached.items()}
        self._text_hashes = {mid: h for mid, (h, _vec) in cached.items()}
        embedding_warm_log.append(
            WarmCycle(
                ts=time.time(),
                event="cache_load",
                embedded_count=0,
                warm_count=len(self._vectors),
                catalog_size=0,
                latency_ms=0,
            )
        )
        if cached:
            logger.info("embedding cache: %d vector(s) loaded from DB", len(cached))

    async def _warm_loop(self) -> None:
        """Background loop: re-warm the catalog every ``warm_interval`` seconds.

        The cycle runs in a worker thread so catalog encoding never blocks the
        event loop; the loop wakes early on stop.
        """
        while not self._stop.is_set():
            try:
                count = await asyncio.to_thread(self.refresh)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop must survive any error
                logger.warning("embedding warm cycle failed: %s", exc)
                embedding_warm_log.append(
                    WarmCycle(
                        ts=time.time(),
                        event="error",
                        embedded_count=0,
                        warm_count=len(self._vectors),
                        catalog_size=0,
                        latency_ms=0,
                        detail="warm cycle",
                        error=repr(exc)[:200],
                    )
                )
            else:
                if count:
                    logger.info("embedding warm: %d market(s) embedded", count)
            await asyncio.sleep(0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._warm_interval)


# Module-level singleton; the FastAPI lifespan + the embedding section wire here.
manager = EmbeddingManager()
