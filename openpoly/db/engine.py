"""Database foundation — SQLAlchemy engine, session factory, schema bootstrap.

openPoly persists to a single SQLite file. The system is single-process (see
``docs/architecture/01-isolation.md``), so SQLite needs no server. A few writer
contexts still coexist — the executor on the event loop, the write-behind
writers on worker threads — so SQLite engines run WAL journal mode + a busy
timeout (see ``make_engine``) to make a writer queue rather than fail.
``OPENPOLY_DB_URL`` overrides the path — tests point it at a throwaway file.
SQLAlchemy keeps the code dialect-agnostic.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# A single SQLite file, relative to the working directory.
DEFAULT_DB_URL = "sqlite:///openpoly.db"


class Base(DeclarativeBase):
    """Declarative base for every openPoly ORM model."""


def database_url() -> str:
    """Resolve the DB URL — ``OPENPOLY_DB_URL`` env, else the local default."""
    return os.environ.get("OPENPOLY_DB_URL", DEFAULT_DB_URL)


def make_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine. ``url`` overrides the env-resolved URL.

    SQLite engines get WAL journal mode + a 5s busy timeout on every
    connection: the executor writes ``fill`` / ``position`` synchronously on
    the event loop while the write-behind writers write from worker threads, so
    a writer must wait its turn rather than fail with ``database is locked``.
    """
    resolved = url or database_url()
    if not resolved.startswith("sqlite"):
        return create_engine(resolved)
    # The write-behind sink runs in worker threads; its drain calls are
    # serialized, so a thread-crossing connection is safe here.
    engine = create_engine(resolved, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all registered tables that do not yet exist.

    Single-schema, paper-stage table bootstrap. Alembic migrations are deferred
    until schema evolution on a live database actually matters.
    """
    Base.metadata.create_all(engine)


# Process-wide engine — lazily created, shared by the app lifespan (write-behind
# writers) and the read-side routes. Tests needing isolation build their own via
# ``make_engine`` or override the route dependency.
_process_engine: Engine | None = None


def get_engine() -> Engine:
    """The process-wide engine (lazily created)."""
    global _process_engine
    if _process_engine is None:
        _process_engine = make_engine()
    return _process_engine


def get_session_factory() -> sessionmaker[Session]:
    """Session factory on the process-wide engine — the default FastAPI
    dependency for read-side routes; overridable via ``app.dependency_overrides``.
    """
    return make_session_factory(get_engine())
