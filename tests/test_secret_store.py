"""Unit tests for LocalSecretStore (S1).

Each test gets its own tmp_path so the user's real ``~/.openpoly/secrets.json``
is never touched. The module-level singleton (``get_store()``) is not used
here — tests instantiate ``LocalSecretStore(path=...)`` directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

import pytest

from openpoly.news.secret_store import (
    InvalidName,
    LocalSecretStore,
    NameNotFound,
    SecretEntry,
    _reset_singleton_for_tests,
    get_store,
    validate_name,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "secrets.json"


@pytest.fixture
def store(store_path: Path) -> LocalSecretStore:
    return LocalSecretStore(path=store_path)


# ---------- name validation ----------


@pytest.mark.parametrize(
    "name",
    [
        "k",
        "tradingnews-main",
        "demo/news_source/tradingnews-main",
        "a-1/b_2/c",
        "A_B-c/123",
    ],
)
def test_validate_name_accepts(name: str) -> None:
    validate_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/a",
        "a/",
        "a//b",
        "a..b",
        "a b",
        "a:b",
        "a.b",
        "a/b/",
        ".",
        "..",
        "a/../b",
    ],
)
def test_validate_name_rejects(name: str) -> None:
    with pytest.raises(InvalidName):
        validate_name(name)


# ---------- set + get + persistence ----------


async def test_set_then_get_roundtrip(store: LocalSecretStore) -> None:
    entry = await store.set("k1", "v1")
    assert entry.name == "k1"
    assert entry.created_at > 0
    assert store.get("k1") == "v1"


async def test_set_persists_to_disk(store: LocalSecretStore, store_path: Path) -> None:
    await store.set("k1", "v1")
    raw = json.loads(store_path.read_text())
    assert raw["entries"]["k1"]["value"] == "v1"


async def test_set_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "subdir" / "secrets.json"
    s = LocalSecretStore(path=nested)
    await s.set("k", "v")
    assert nested.exists()


async def test_file_mode_is_0o600(store: LocalSecretStore, store_path: Path) -> None:
    await store.set("k", "v")
    mode = stat.S_IMODE(store_path.stat().st_mode)
    assert mode == 0o600


async def test_set_with_slashes_in_name(store: LocalSecretStore) -> None:
    await store.set("demo/news_source/tradingnews-main", "v")
    assert store.get("demo/news_source/tradingnews-main") == "v"


async def test_set_rejects_invalid_name(store: LocalSecretStore) -> None:
    with pytest.raises(InvalidName):
        await store.set("/leading-slash", "v")
    with pytest.raises(InvalidName):
        await store.set("trailing/", "v")
    with pytest.raises(InvalidName):
        await store.set("double//slash", "v")


async def test_set_rejects_empty_value(store: LocalSecretStore) -> None:
    with pytest.raises(InvalidName):
        await store.set("k", "")


async def test_set_overwrite_keeps_original_created_at(
    store: LocalSecretStore,
) -> None:
    e1 = await store.set("k", "v1")
    await asyncio.sleep(0.01)
    e2 = await store.set("k", "v2")
    assert e2.created_at == e1.created_at
    assert store.get("k") == "v2"


# ---------- get errors ----------


def test_get_unknown_raises(store: LocalSecretStore) -> None:
    with pytest.raises(NameNotFound):
        store.get("nope")


def test_get_invalid_name_raises(store: LocalSecretStore) -> None:
    with pytest.raises(InvalidName):
        store.get("/bad")


# ---------- list_entries ----------


async def test_list_entries_sorted(store: LocalSecretStore) -> None:
    await store.set("b", "v")
    await store.set("a", "v")
    names = [e.name for e in store.list_entries()]
    assert names == ["a", "b"]


async def test_list_entries_no_value_in_dataclass(
    store: LocalSecretStore,
) -> None:
    await store.set("k", "VERY_SECRET_VALUE_42")
    entries = store.list_entries()
    for e in entries:
        assert not hasattr(e, "value")
        assert "VERY_SECRET_VALUE_42" not in str(asdict(e))


async def test_list_entries_prefix_filter(store: LocalSecretStore) -> None:
    await store.set("demo/a", "v")
    await store.set("demo/b", "v")
    await store.set("other/c", "v")
    names = [e.name for e in store.list_entries(prefix="demo/")]
    assert names == ["demo/a", "demo/b"]


async def test_list_entries_empty(store: LocalSecretStore) -> None:
    assert store.list_entries() == []


# ---------- delete ----------


async def test_delete_removes_entry(store: LocalSecretStore) -> None:
    await store.set("k", "v")
    await store.delete("k")
    with pytest.raises(NameNotFound):
        store.get("k")


async def test_delete_persists(store: LocalSecretStore, store_path: Path) -> None:
    await store.set("k", "v")
    await store.delete("k")
    raw = json.loads(store_path.read_text())
    assert raw["entries"] == {}


async def test_delete_unknown_raises(store: LocalSecretStore) -> None:
    with pytest.raises(NameNotFound):
        await store.delete("nope")


# ---------- security: repr + permission ----------


async def test_repr_does_not_contain_value(store: LocalSecretStore) -> None:
    await store.set("k", "VERY_SECRET_VALUE_42")
    assert "VERY_SECRET_VALUE_42" not in repr(store)


async def test_permission_warning_on_loose_mode(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"entries": {}}))
    os.chmod(path, 0o644)
    with caplog.at_level("WARNING"):
        _ = LocalSecretStore(path=path)
    msgs = " ".join(record.message for record in caplog.records)
    assert "mode" in msgs.lower()


async def test_loose_mode_does_not_block_use(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"entries": {"k": {"value": "v", "created_at": 1.0}}}))
    os.chmod(path, 0o644)
    s = LocalSecretStore(path=path)
    # Still readable + writable after warning.
    assert s.get("k") == "v"
    await s.set("k2", "v2")


# ---------- env override + singleton ----------


def test_default_path_honors_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "override.json"
    monkeypatch.setenv("OPENPOLY_SECRET_STORE", str(target))
    s = LocalSecretStore()
    assert s._path == target


def test_singleton_returns_same_instance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENPOLY_SECRET_STORE", str(tmp_path / "singleton.json"))
    _reset_singleton_for_tests()
    a = get_store()
    b = get_store()
    assert a is b
    _reset_singleton_for_tests()


# ---------- concurrent set ----------


async def test_concurrent_set_serialized(store: LocalSecretStore) -> None:
    await asyncio.gather(*[store.set(f"k{i}", f"v{i}") for i in range(10)])
    for i in range(10):
        assert store.get(f"k{i}") == f"v{i}"


# ---------- SecretEntry dataclass ----------


def test_secret_entry_has_no_value_field() -> None:
    fields_present = set(SecretEntry.__dataclass_fields__.keys())
    assert "value" not in fields_present
    assert fields_present == {"name", "created_at"}
