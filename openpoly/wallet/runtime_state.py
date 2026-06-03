"""RuntimeState — ~/.openpoly/runtime.json reader/writer.

Single source of truth for the system's exec mode (paper / live) and the
wallet config selection — Polymarket V2 DepositWallet model:
signer EOA (private_key_ref) + DepositWallet contract address (funder_address).
Mirrors the existing dotfile-based pattern of ``~/.openpoly/secrets.json``
and ``~/.openpoly/canvas.json`` — no DB rows, no schema migration.

Module-level singleton ``runtime_state`` is what production code talks to;
the FastAPI lifespan calls ``load()`` at startup. Tests construct their own
``RuntimeState`` with ``OPENPOLY_RUNTIME_STATE`` pointing at a tmp file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ExecMode = Literal["paper", "live"]
_VALID_MODES: tuple[ExecMode, ...] = ("paper", "live")

_ENV_OVERRIDE = "OPENPOLY_RUNTIME_STATE"
_DESIRED_MODE = 0o600


@dataclass(frozen=True)
class WalletSpec:
    """Persisted wallet config — secret-free (Polymarket V2 DepositWallet model).

    Polymarket V2 (post-2026-04-28) requires a DepositWallet smart contract
    that holds pUSD collateral + CTF positions, with a separate EOA that
    signs orders. The CLOB validates orders against the funder address via
    EIP-1271 (``signature_type=3`` POLY_1271), so signer != funder is the
    expected shape — not a misconfiguration.

    ``private_key_ref`` resolves to the 0x-hex EOA signer key.
    ``funder_address`` is the on-chain DepositWallet address (must be a
    contract; the CLOB rejects EOA funders post-V2 upgrade).
    """

    private_key_ref: str
    funder_address: str


def _default_path() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openpoly" / "runtime.json"


class RuntimeState:
    """In-memory cache over runtime.json with sync read/write.

    ``load()`` is called once at startup; mutators (``set_mode`` /
    ``set_wallet``) call ``_save()`` internally so persistence is atomic
    per change. Reads are O(1) memory hits.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._exec_mode: ExecMode = "paper"
        self._wallet: WalletSpec | None = None
        self._updated_at: float | None = None

    @property
    def exec_mode(self) -> ExecMode:
        return self._exec_mode

    @property
    def wallet(self) -> WalletSpec | None:
        return self._wallet

    def load(self) -> None:
        """Load from disk, fall back to defaults on missing / corrupt file."""
        if not self._path.exists():
            self._exec_mode = "paper"
            self._wallet = None
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("runtime.json unreadable (%s); using defaults", exc)
            self._exec_mode = "paper"
            self._wallet = None
            return
        try:
            self._exec_mode = self._parse_mode(raw.get("exec_mode"))
            self._wallet = self._parse_wallet(raw.get("wallet"))
            self._updated_at = raw.get("updated_at")
        except (TypeError, ValueError, KeyError) as exc:
            logger.error("runtime.json schema invalid (%s); using defaults", exc)
            self._exec_mode = "paper"
            self._wallet = None

    def set_mode(self, mode: ExecMode) -> None:
        # Save-then-mutate: a disk failure must leave in-memory state matching
        # what is on disk, so callers retrying with a different mode don't see
        # phantom success (spec §9: "if the PUT disk write fails → do not mutate in-memory state").
        if mode not in _VALID_MODES:
            raise ValueError(f"unknown exec mode: {mode!r}")
        prev = self._exec_mode
        self._exec_mode = mode
        try:
            self._save()
        except Exception:
            self._exec_mode = prev
            raise

    def set_wallet(self, spec: WalletSpec) -> None:
        if not isinstance(spec, WalletSpec):
            raise TypeError("expected WalletSpec")
        prev = self._wallet
        self._wallet = spec
        try:
            self._save()
        except Exception:
            self._wallet = prev
            raise

    # ---------- internal ----------

    @staticmethod
    def _parse_mode(value: object) -> ExecMode:
        if value is None:
            return "paper"
        if value in _VALID_MODES:
            return value  # type: ignore[return-value]
        raise ValueError(f"bad exec_mode: {value!r}")

    @staticmethod
    def _parse_wallet(value: object) -> WalletSpec | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("wallet must be object or null")
        return WalletSpec(
            private_key_ref=str(value["private_key_ref"]),
            funder_address=str(value["funder_address"]),
        )

    def _save(self) -> None:
        payload = {
            "wallet": (
                None
                if self._wallet is None
                else {
                    "private_key_ref": self._wallet.private_key_ref,
                    "funder_address": self._wallet.funder_address,
                }
            ),
            "exec_mode": self._exec_mode,
            "updated_at": time.time(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.chmod(tmp, _DESIRED_MODE)
        os.replace(tmp, self._path)
        self._updated_at = payload["updated_at"]


# Module-level singleton — production code imports this; tests new their own.
runtime_state = RuntimeState()
