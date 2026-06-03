"""SQLite database section.

Thin section-protocol wrapper over the persistence runtime. The engine and the
two write-behind writers live in ``DatabaseManager`` (see ``openpoly.db.manager``);
this class exists so ``database`` registers in the section catalog — giving it a
canvas node and an inspector — and so ``run()`` can hand back a status snapshot.

Lifecycle (start / stop) is driven by the FastAPI lifespan against the manager
singleton, not this class.
"""

from __future__ import annotations

from openpoly.db.manager import DatabaseConfig, manager
from openpoly.sections._base import SectionInput, SectionOutput


class SqliteDatabase:
    SECTION_TYPE = "database"
    SECTION_VERSION = "0.1.0"
    REQUIRES: list[str] = []
    Config = DatabaseConfig

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config

    def run(self, input: SectionInput) -> SectionOutput:
        """Return a status snapshot of the persistence layer.

        Reads the manager singleton — one database per process. Not called by
        the event-driven pipeline; present for protocol conformance and
        ad-hoc status access.
        """
        status = manager.status()
        tables = status["tables"]
        return SectionOutput(
            payload=status,
            verdict="ok",
            signals={
                "order_book_snapshot_rows": tables.get("order_book_snapshot"),
                "news_item_rows": tables.get("news_item"),
            },
        )

    @staticmethod
    def CONTRACT_TEST() -> None:
        out = SqliteDatabase(DatabaseConfig()).run(SectionInput(tick_type="warm"))
        assert out.verdict == "ok"
        assert isinstance(out.payload, dict)
