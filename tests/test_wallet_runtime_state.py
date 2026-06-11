"""Tests for RuntimeState — ~/.openpoly/runtime.json read/write."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from openpoly.wallet.runtime_state import RuntimeState, WalletSpec


@pytest.fixture
def state_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "runtime.json"
    monkeypatch.setenv("OPENPOLY_RUNTIME_STATE", str(p))
    return p


def test_defaults_when_file_absent(state_path: Path) -> None:
    rs = RuntimeState()
    rs.load()
    assert rs.exec_mode == "paper"
    assert rs.wallet is None


def test_set_wallet_persists(state_path: Path) -> None:
    rs = RuntimeState()
    rs.load()
    rs.set_wallet(
        WalletSpec(
            private_key_ref="env:OPENPOLY_POLYMARKET_PK",
            funder_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        )
    )
    rs2 = RuntimeState()
    rs2.load()
    assert rs2.wallet is not None
    assert rs2.wallet.private_key_ref == "env:OPENPOLY_POLYMARKET_PK"
    assert rs2.wallet.funder_address == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def test_set_mode_persists(state_path: Path) -> None:
    rs = RuntimeState()
    rs.load()
    rs.set_mode("live")
    rs2 = RuntimeState()
    rs2.load()
    assert rs2.exec_mode == "live"


def test_set_mode_rejects_unknown(state_path: Path) -> None:
    rs = RuntimeState()
    rs.load()
    with pytest.raises(ValueError):
        rs.set_mode("yolo")  # type: ignore[arg-type]


def test_chmod_600_on_save(state_path: Path) -> None:
    rs = RuntimeState()
    rs.load()
    rs.set_mode("live")
    mode = stat.S_IMODE(state_path.stat().st_mode)
    assert mode == 0o600


def test_corrupt_json_falls_back_to_defaults(
    state_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state_path.write_text("{not valid json")
    rs = RuntimeState()
    rs.load()
    assert rs.exec_mode == "paper"
    assert rs.wallet is None
    assert any("runtime.json" in r.message for r in caplog.records)


def test_corrupt_schema_falls_back_to_defaults(state_path: Path) -> None:
    state_path.write_text(json.dumps({"exec_mode": 42}))
    rs = RuntimeState()
    rs.load()
    assert rs.exec_mode == "paper"


def test_save_is_atomic_no_tmp_leftover(state_path: Path) -> None:
    rs = RuntimeState()
    rs.load()
    rs.set_mode("live")
    siblings = list(state_path.parent.iterdir())
    assert [p.name for p in siblings] == [state_path.name]


def test_corrupt_wallet_schema_falls_back_to_defaults(state_path: Path) -> None:
    """Wallet dict missing required fields → wallet falls back to None."""
    state_path.write_text(json.dumps({"exec_mode": "live", "wallet": {"private_key_ref": "env:X"}}))
    rs = RuntimeState()
    rs.load()
    assert rs.exec_mode == "paper"
    assert rs.wallet is None


def test_set_mode_disk_failure_does_not_mutate_memory(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rs = RuntimeState()
    rs.load()
    assert rs.exec_mode == "paper"

    def boom(self: RuntimeState) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(RuntimeState, "_save", boom)
    with pytest.raises(OSError):
        rs.set_mode("live")
    assert rs.exec_mode == "paper"  # rolled back


def test_set_wallet_disk_failure_does_not_mutate_memory(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rs = RuntimeState()
    rs.load()
    assert rs.wallet is None

    def boom(self: RuntimeState) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(RuntimeState, "_save", boom)
    with pytest.raises(OSError):
        rs.set_wallet(
            WalletSpec(
                private_key_ref="env:OPENPOLY_POLYMARKET_PK",
                funder_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            )
        )
    assert rs.wallet is None  # rolled back
