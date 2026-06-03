from __future__ import annotations

from pathlib import Path

import pytest

from openpoly.news import secret_store as _store_mod
from openpoly.news.secret_store import LocalSecretStore
from openpoly.news.secrets import SecretNotFound, UnsupportedScheme, resolve


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LocalSecretStore:
    """Swap the module-level singleton for a tmp-path-backed store so tests
    don't write to the user's real ~/.openpoly/secrets.json."""
    s = LocalSecretStore(path=tmp_path / "secrets.json")
    monkeypatch.setattr(_store_mod, "_singleton", s)
    yield s
    monkeypatch.setattr(_store_mod, "_singleton", None)


def test_resolve_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_TEST_KEY", "secret123")
    assert resolve("env:OPENPOLY_TEST_KEY") == "secret123"


def test_resolve_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENPOLY_TEST_MISSING", raising=False)
    with pytest.raises(SecretNotFound, match="OPENPOLY_TEST_MISSING"):
        resolve("env:OPENPOLY_TEST_MISSING")


def test_resolve_vault_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        resolve("vault:secret/openpoly#key")


def test_resolve_keychain_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        resolve("keychain:com.openpoly/key")


def test_resolve_unknown_scheme() -> None:
    with pytest.raises(UnsupportedScheme):
        resolve("ftp:host/key")


def test_resolve_malformed_ref() -> None:
    with pytest.raises(UnsupportedScheme):
        resolve("no-scheme-here")


# ---------- local: scheme ----------


async def test_resolve_local_present(isolated_store: LocalSecretStore) -> None:
    await isolated_store.set("tradingnews-main", "tk_xyz")
    assert resolve("local:tradingnews-main") == "tk_xyz"


async def test_resolve_local_with_slashes(isolated_store: LocalSecretStore) -> None:
    await isolated_store.set("demo/news_source/k", "vv")
    assert resolve("local:demo/news_source/k") == "vv"


def test_resolve_local_missing_maps_to_secret_not_found(
    isolated_store: LocalSecretStore,
) -> None:
    with pytest.raises(SecretNotFound, match="missing-name"):
        resolve("local:missing-name")


def test_resolve_local_invalid_name_maps_to_unsupported_scheme(
    isolated_store: LocalSecretStore,
) -> None:
    with pytest.raises(UnsupportedScheme, match="Invalid local secret name"):
        resolve("local:/bad-name")
