"""Tests for the database section (SqliteDatabase) + registry discovery."""

from __future__ import annotations

from openpoly.db.manager import DatabaseConfig
from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.database.sqlite import SqliteDatabase


def test_section_attrs():
    assert SqliteDatabase.SECTION_TYPE == "database"
    assert SqliteDatabase.SECTION_VERSION == "0.1.0"
    assert SqliteDatabase.REQUIRES == []


def test_run_returns_status_dict():
    out = SqliteDatabase(DatabaseConfig()).run(SectionInput(tick_type="warm"))
    assert out.verdict == "ok"
    assert isinstance(out.payload, dict)
    assert "tables" in out.payload
    assert "writers" in out.payload


def test_contract_test_passes():
    SqliteDatabase.CONTRACT_TEST()  # must not raise


def test_registry_discovers_database_section():
    db_entries = [e for e in scan() if e.type == "database"]
    assert len(db_entries) == 1
    assert db_entries[0].name == "SqliteDatabase"
    assert db_entries[0].version == "0.1.0"
