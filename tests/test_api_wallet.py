"""Endpoint tests for /api/wallet/config (GET + PUT)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openpoly.api.main import app
from openpoly.wallet.runtime_state import RuntimeState
import openpoly.api.wallet_routes as wallet_routes

# Anvil's deterministic dev key #0 — public, well-known, safe to bake into tests.
TEST_PRIVKEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
TEST_FUNDER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENPOLY_RUNTIME_STATE", str(tmp_path / "runtime.json"))
    fresh = RuntimeState()
    fresh.load()
    monkeypatch.setattr(wallet_routes, "runtime_state", fresh)
    return TestClient(app)


def test_get_returns_null_when_unconfigured(env: TestClient) -> None:
    r = env.get("/api/wallet/config")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "private_key_ref": None,
        "funder_address": None,
        "signer_address": None,
        "error": "wallet_not_configured",
    }


def test_put_then_get_roundtrips_with_signer(
    env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", TEST_PRIVKEY)
    r = env.put(
        "/api/wallet/config",
        json={
            "private_key_ref": "env:OPENPOLY_POLYMARKET_PK",
            "funder_address": TEST_FUNDER,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["private_key_ref"] == "env:OPENPOLY_POLYMARKET_PK"
    assert body["funder_address"] == TEST_FUNDER
    assert body["signer_address"] == TEST_SIGNER_ADDRESS
    assert body["error"] is None

    g = env.get("/api/wallet/config")
    assert g.status_code == 200
    assert g.json()["signer_address"] == TEST_SIGNER_ADDRESS


def test_put_bad_ref_format_returns_400(env: TestClient) -> None:
    r = env.put(
        "/api/wallet/config",
        json={"private_key_ref": "not-a-ref", "funder_address": TEST_FUNDER},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_ref_format"


def test_put_bad_funder_returns_400(env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", TEST_PRIVKEY)
    r = env.put(
        "/api/wallet/config",
        json={
            "private_key_ref": "env:OPENPOLY_POLYMARKET_PK",
            "funder_address": "not-an-address",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_address"


def test_put_secret_missing_returns_400(env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENPOLY_POLYMARKET_PK", raising=False)
    r = env.put(
        "/api/wallet/config",
        json={
            "private_key_ref": "env:OPENPOLY_POLYMARKET_PK",
            "funder_address": TEST_FUNDER,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "wallet_secret_missing"


def test_put_bad_private_key_returns_400(env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", "not-a-private-key")
    r = env.put(
        "/api/wallet/config",
        json={
            "private_key_ref": "env:OPENPOLY_POLYMARKET_PK",
            "funder_address": TEST_FUNDER,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_private_key"


def test_response_never_includes_private_key(
    env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard contract: the resolved private key must never appear in any response."""
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", TEST_PRIVKEY)
    put = env.put(
        "/api/wallet/config",
        json={
            "private_key_ref": "env:OPENPOLY_POLYMARKET_PK",
            "funder_address": TEST_FUNDER,
        },
    )
    get = env.get("/api/wallet/config")
    assert TEST_PRIVKEY not in put.text
    assert TEST_PRIVKEY not in get.text
    # The 64-hex tail (without 0x) also must not appear
    assert TEST_PRIVKEY[2:] not in put.text
    assert TEST_PRIVKEY[2:] not in get.text


# ---------- GET /api/wallet/balance (W3) ----------


@pytest.fixture
def balance_env(env: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """env + reset the module-level balance cache between tests."""
    monkeypatch.setattr(wallet_routes, "_balance_cache", None)
    return env


def _set_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", TEST_PRIVKEY)
    wallet_routes.runtime_state.set_wallet(
        wallet_routes.WalletSpec(
            private_key_ref="env:OPENPOLY_POLYMARKET_PK",
            funder_address=TEST_FUNDER,
        )
    )


def test_balance_unconfigured_returns_configured_false(balance_env: TestClient) -> None:
    r = balance_env.get("/api/wallet/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["usdc"] is None and body["total"] is None


def test_balance_both_sources_ok(balance_env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_wallet(monkeypatch)
    monkeypatch.setattr(wallet_routes.executor, "get_collateral_balance_raw", lambda: 162_199_200)

    async def _fake_value(funder: str) -> float:
        assert funder == TEST_FUNDER
        return 162.1992

    monkeypatch.setattr(wallet_routes, "fetch_wallet_positions_value", _fake_value)

    r = balance_env.get("/api/wallet/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["usdc"] == pytest.approx(162.1992)
    assert body["positions_value"] == pytest.approx(162.1992)
    assert body["total"] == pytest.approx(324.3984)


def test_balance_single_source_failure_is_null_not_500(
    balance_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_wallet(monkeypatch)
    monkeypatch.setattr(wallet_routes.executor, "get_collateral_balance_raw", lambda: None)

    async def _fake_value(funder: str) -> float:
        return 162.1992

    monkeypatch.setattr(wallet_routes, "fetch_wallet_positions_value", _fake_value)

    r = balance_env.get("/api/wallet/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["usdc"] is None
    assert body["positions_value"] == pytest.approx(162.1992)
    assert body["total"] is None  # can't sum with an unknown


def test_balance_cached_within_ttl(
    balance_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_wallet(monkeypatch)
    calls = {"clob": 0, "value": 0}

    def _clob():
        calls["clob"] += 1
        return 100_000_000

    async def _fake_value(funder: str) -> float:
        calls["value"] += 1
        return 50.0

    monkeypatch.setattr(wallet_routes.executor, "get_collateral_balance_raw", _clob)
    monkeypatch.setattr(wallet_routes, "fetch_wallet_positions_value", _fake_value)

    r1 = balance_env.get("/api/wallet/balance")
    r2 = balance_env.get("/api/wallet/balance")
    assert r1.json()["total"] == pytest.approx(150.0)
    assert r2.json() == r1.json()
    assert calls == {"clob": 1, "value": 1}  # second hit served from cache
