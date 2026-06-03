"""Tests for openpoly.db.engine — engine / session / bootstrap (SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from openpoly.db.engine import (
    DEFAULT_DB_URL,
    database_url,
    init_db,
    make_engine,
    make_session_factory,
)


def test_make_engine_and_session_smoke():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with factory() as session:
        result = session.execute(text("SELECT 1")).scalar_one()
    assert result == 1
    engine.dispose()


def test_init_db_runs():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)  # creates registered tables; must not raise
    engine.dispose()


def test_database_url_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENPOLY_DB_URL", raising=False)
    assert database_url() == DEFAULT_DB_URL


def test_database_url_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENPOLY_DB_URL", "sqlite:///custom.db")
    assert database_url() == "sqlite:///custom.db"
    assert str(make_engine().url) == "sqlite:///custom.db"


def test_init_db_creates_fill_with_order_id_tx_hash(tmp_path) -> None:
    """Fresh DB: order_id + tx_hash columns exist via create_all."""
    from sqlalchemy import text

    engine = make_engine(f"sqlite:///{tmp_path}/x.db")
    init_db(engine)
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(fill)")).fetchall()}
    assert "order_id" in cols
    assert "tx_hash" in cols


def test_ensure_fill_live_columns_migrates_old_db(tmp_path) -> None:
    """Old DB without those columns: migration adds them, idempotent."""
    from sqlalchemy import text
    from openpoly.db.manager import _ensure_fill_live_columns

    engine = make_engine(f"sqlite:///{tmp_path}/x.db")
    # Simulate old schema by creating fill without the new columns
    with engine.begin() as conn:
        conn.execute(
            text("""
            CREATE TABLE fill (
                id INTEGER PRIMARY KEY,
                ts FLOAT NOT NULL,
                market_id VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                price FLOAT NOT NULL,
                qty FLOAT NOT NULL,
                fee FLOAT NOT NULL,
                position_id INTEGER NOT NULL,
                news_id VARCHAR,
                "trigger" VARCHAR
            )
        """)
        )
    _ensure_fill_live_columns(engine)
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(fill)")).fetchall()}
    assert "order_id" in cols
    assert "tx_hash" in cols
    # Second run is a no-op
    _ensure_fill_live_columns(engine)
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(fill)")).fetchall()}
    assert "order_id" in cols
    assert "tx_hash" in cols
