"""Endpoint tests for POST /api/system/mode (Polymarket V2 DepositWallet)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import openpoly.api.wallet_routes as wallet_routes
from openpoly.api.main import app
from openpoly.api.portfolio_routes import get_portfolio_store
from openpoly.db.engine import init_db, make_engine, make_session_factory
from openpoly.portfolio import PortfolioStore
from openpoly.wallet.runtime_state import RuntimeState, WalletSpec

# Anvil's deterministic dev key #0 — public, well-known, safe to bake into tests.
TEST_PRIVKEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_FUNDER = "0x1234567890123456789012345678901234567890"

# V2 exchange addresses — must match the preflight thresholds in wallet_routes.
STANDARD_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEGRISK_V2 = "0xe2222d279d744050d28e00520010520000310F59"
MAX_UINT = str(2**256 - 1)


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, PortfolioStore, RuntimeState]:
    monkeypatch.setenv("OPENPOLY_RUNTIME_STATE", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", TEST_PRIVKEY)

    engine = make_engine(f"sqlite:///{tmp_path}/portfolio.db")
    init_db(engine)
    store = PortfolioStore(make_session_factory(engine))
    app.dependency_overrides[get_portfolio_store] = lambda: store

    rs = RuntimeState()
    rs.load()
    monkeypatch.setattr(wallet_routes, "runtime_state", rs)

    yield TestClient(app), store, rs

    app.dependency_overrides.clear()
    engine.dispose()


def _open_position(store: PortfolioStore) -> None:
    store.open_position(
        market_id="m1",
        side="yes",
        token_id="t1",
        condition_id="0xm1",
        price=0.40,
        qty=10.0,
        ts=100.0,
        news_id="n1",
    )


def _set_wallet(rs: RuntimeState) -> None:
    rs.set_wallet(
        WalletSpec(
            private_key_ref="env:OPENPOLY_POLYMARKET_PK",
            funder_address=TEST_FUNDER,
        )
    )


def _patch_build_with_fake(monkeypatch: pytest.MonkeyPatch, fake_clob) -> None:
    """Replace build_live_executor with a stub returning a LiveExecutor wrapping fake_clob."""
    from openpoly.execution.live_executor import LiveExecutor

    def fake_build(wallet, portfolio):
        return LiveExecutor(portfolio=portfolio, clob_client=fake_clob)

    monkeypatch.setattr(wallet_routes, "build_live_executor", fake_build)


def _allowance_dict(*, standard: str = MAX_UINT, negrisk: str = MAX_UINT) -> dict:
    return {STANDARD_V2: standard, NEGRISK_V2: negrisk}


def test_short_circuit_when_already_target(env) -> None:
    client, _store, _rs = env
    r = client.post("/api/system/mode", json={"mode": "paper"})
    assert r.status_code == 200
    assert r.json() == {"mode": "paper"}


def test_unknown_mode_returns_422(env) -> None:
    client, _store, _rs = env
    r = client.post("/api/system/mode", json={"mode": "yolo"})
    assert r.status_code == 422  # pydantic validation


def test_paper_to_live_blocked_by_open_positions(env) -> None:
    client, store, rs = env
    _set_wallet(rs)
    _open_position(store)
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["error"] == "open_positions"
    assert body["count"] == 1


def test_paper_to_live_blocked_without_wallet(env) -> None:
    client, _store, _rs = env
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "wallet_not_configured"


def test_paper_to_live_blocked_when_secret_missing(env, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store, rs = env
    _set_wallet(rs)
    monkeypatch.delenv("OPENPOLY_POLYMARKET_PK", raising=False)
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "wallet_secret_missing"


def test_paper_to_live_blocked_when_private_key_invalid(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _store, rs = env
    _set_wallet(rs)
    monkeypatch.setenv("OPENPOLY_POLYMARKET_PK", "not-a-valid-private-key")
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "bad_private_key"


def test_paper_to_live_success_when_funded_and_approved(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _store, rs = env
    _set_wallet(rs)

    class _ClobOK:
        def get_balance_allowance(self, params):
            return {"balance": "5000000", "allowances": _allowance_dict()}

    _patch_build_with_fake(monkeypatch, _ClobOK())
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 200, r.text
    assert r.json() == {"mode": "live"}
    assert rs.exec_mode == "live"


def test_live_to_paper_blocked_by_open_positions(env) -> None:
    client, store, rs = env
    _set_wallet(rs)
    rs.set_mode("live")
    _open_position(store)
    r = client.post("/api/system/mode", json={"mode": "paper"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "open_positions"


def test_live_to_paper_success_persists(env) -> None:
    client, _store, rs = env
    _set_wallet(rs)
    rs.set_mode("live")
    assert rs.exec_mode == "live"

    r = client.post("/api/system/mode", json={"mode": "paper"})
    assert r.status_code == 200, r.text
    assert r.json() == {"mode": "paper"}
    assert rs.exec_mode == "paper"


def test_switch_to_live_blocked_by_pusd_insufficient(env, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store, rs = env
    _set_wallet(rs)

    class _ClobLowBalance:
        def get_balance_allowance(self, params):
            return {"balance": "500000", "allowances": _allowance_dict()}  # 0.5 pUSD

    _patch_build_with_fake(monkeypatch, _ClobLowBalance())
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "pusd_insufficient"


def test_switch_to_live_blocked_by_standard_v2_not_approved(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _store, rs = env
    _set_wallet(rs)

    class _ClobStdMissing:
        def get_balance_allowance(self, params):
            return {
                "balance": "5000000",
                "allowances": _allowance_dict(standard="0"),
            }

    _patch_build_with_fake(monkeypatch, _ClobStdMissing())
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "standard_v2_not_approved"


def test_switch_to_live_blocked_by_negrisk_v2_not_approved(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _store, rs = env
    _set_wallet(rs)

    class _ClobNegMissing:
        def get_balance_allowance(self, params):
            return {
                "balance": "5000000",
                "allowances": _allowance_dict(negrisk="0"),
            }

    _patch_build_with_fake(monkeypatch, _ClobNegMissing())
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "negrisk_v2_not_approved"


def test_switch_to_live_blocked_by_rpc_unreachable(env, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store, rs = env
    _set_wallet(rs)

    class _ClobBlowsUp:
        def get_balance_allowance(self, params):
            raise ConnectionError("clob.polymarket.com unreachable")

    _patch_build_with_fake(monkeypatch, _ClobBlowsUp())
    r = client.post("/api/system/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "rpc_unreachable"
