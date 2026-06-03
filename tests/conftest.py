"""Shared pytest config.

Points the DB at a throwaway SQLite file for the whole test session, so no test
(notably the lifespan tests) ever writes the real ``openpoly.db``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _test_db_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    db_path = tmp_path_factory.mktemp("db") / "openpoly_test.db"
    os.environ["OPENPOLY_DB_URL"] = f"sqlite:///{db_path}"
    # Keep the lifespan from opening real news / market connections — tests
    # that exercise it drive the managers explicitly.
    os.environ["OPENPOLY_AUTOSTART_SOURCES"] = "0"
    yield
    os.environ.pop("OPENPOLY_DB_URL", None)
    os.environ.pop("OPENPOLY_AUTOSTART_SOURCES", None)


@pytest.fixture(autouse=True)
def _reset_orchestrator() -> Iterator[None]:
    """Reset the orchestrator singleton around every test.

    Its ``asyncio.Queue`` binds lazily to the running loop on first use; the
    singleton must not carry a loop-bound queue across pytest-asyncio's
    per-test event loops.
    """
    from openpoly.runtime.orchestrator import _reset_singleton_for_tests

    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()
