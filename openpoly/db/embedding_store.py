"""Market embedding vector cache — the durable backing for ``EmbeddingManager``.

Persists one float32 vector per (market, embedding model) so a process restart
reloads the catalog's embeddings instead of recomputing them. The API is a
plain repository: ``load`` reads the whole cache for one model, ``upsert``
writes a batch. Vectors cross the SQLite boundary as raw little-endian float32
bytes — compact, and dialect-agnostic (no BLOB-array extension needed).
"""

from __future__ import annotations

import time

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.tables import MarketEmbeddingRow

# Cached vectors are always stored as contiguous float32.
_VECTOR_DTYPE = np.dtype(np.float32)

# {market_id: (text_hash, vector)} — the shape both load returns and upsert takes.
EmbeddingCache = dict[str, tuple[str, np.ndarray]]


def vector_to_bytes(vector: np.ndarray) -> bytes:
    """Serialize a vector to bytes for the ``vector`` column (coerced float32)."""
    return np.ascontiguousarray(vector, dtype=_VECTOR_DTYPE).tobytes()


def bytes_to_vector(blob: bytes) -> np.ndarray:
    """Deserialize a ``vector`` column blob into a writable float32 ndarray.

    ``np.frombuffer`` aliases the immutable ``bytes`` read-only; a copy is
    returned so callers (e.g. the manager normalizing in place) can mutate it.
    """
    return np.frombuffer(blob, dtype=_VECTOR_DTYPE).copy()


class MarketEmbeddingStore:
    """Repository over the ``market_embedding`` table.

    Sync — the manager's background warm loop owns it and already runs off the
    event loop's hot path.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self, model_name: str) -> EmbeddingCache:
        """Read the whole vector cache for one embedding model.

        Returns ``{market_id: (text_hash, vector)}`` — empty when nothing has
        been cached yet (first run, or after a model switch).
        """
        with self._session_factory() as session:
            rows = session.scalars(
                select(MarketEmbeddingRow).where(MarketEmbeddingRow.model_name == model_name)
            ).all()
        return {row.market_id: (row.text_hash, bytes_to_vector(row.vector)) for row in rows}

    def upsert(self, model_name: str, entries: EmbeddingCache) -> None:
        """Insert or replace cached vectors for ``model_name``.

        ``entries`` is ``{market_id: (text_hash, vector)}`` — the same shape
        ``load`` returns. Existing (market, model) rows are updated in place;
        new ones are inserted. A no-op on empty ``entries``.
        """
        if not entries:
            return
        now = time.time()
        with self._session_factory() as session:
            existing = {
                row.market_id: row
                for row in session.scalars(
                    select(MarketEmbeddingRow).where(
                        MarketEmbeddingRow.model_name == model_name,
                        MarketEmbeddingRow.market_id.in_(entries.keys()),
                    )
                )
            }
            for market_id, (text_hash, vector) in entries.items():
                blob = vector_to_bytes(vector)
                row = existing.get(market_id)
                if row is None:
                    session.add(
                        MarketEmbeddingRow(
                            market_id=market_id,
                            model_name=model_name,
                            text_hash=text_hash,
                            vector=blob,
                            created_at=now,
                        )
                    )
                else:
                    row.text_hash = text_hash
                    row.vector = blob
                    row.created_at = now
            session.commit()
