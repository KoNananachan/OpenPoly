"""HTTP routes for wallet config + system exec mode (Polymarket V2).

- GET /api/wallet/config — current wallet config + signer EOA derived from PK
- PUT /api/wallet/config — set private_key_ref + funder_address
- POST /api/system/mode  — switch exec mode with structured guards

The response never carries the private key itself. The signer EOA is
computed on demand from the resolved PK (no caching), so a swapped env var
or rotated local secret is reflected on the next GET without an explicit
refresh step.

Mode-switch preflight for live: probes the V2 CLOB via the wallet's actual
configuration (Cloudflare patch + sig_type=3 + funder=DepositWallet),
calling ``derive_api_key`` and ``get_balance_allowance`` to verify the
funder holds pUSD and has approved the V2 exchanges. This mirrors the
slice C design's "fail-loud at mode switch, never silently on first trade"
guarantee.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Literal

from eth_account import Account
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from openpoly.api.portfolio_routes import get_portfolio_store
from openpoly.execution import executor
from openpoly.execution.live_executor import build_live_executor
from openpoly.markets.polymarket_api import fetch_wallet_positions_value
from openpoly.news.secrets import SecretsError, resolve
from openpoly.portfolio import PortfolioStore
from openpoly.wallet.runtime_state import WalletSpec, runtime_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["wallet"])

_HEX_PRIVKEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Minimum pUSD balance to consider the funder funded for live (6 decimals).
# Set deliberately low ($1) so grain-scale dry-runs aren't blocked.
_MIN_PUSD_BALANCE_RAW = 1_000_000

# Required allowance per V2 exchange (handoff readiness check used 10k as
# "fully approved" threshold; we keep the same to detect cap-style approvals).
_MIN_EXCHANGE_ALLOWANCE_RAW = 10_000 * 1_000_000

# V2 exchange addresses — orders are signed against one of these depending on
# the market's neg_risk flag, so both must be approved before mode flip.
_STANDARD_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
_NEGRISK_V2 = "0xe2222d279d744050d28e00520010520000310F59"


class WalletConfigResponse(BaseModel):
    private_key_ref: str | None
    funder_address: str | None
    signer_address: str | None
    error: str | None  # wallet_not_configured | wallet_secret_missing | bad_private_key


class PutWalletConfigRequest(BaseModel):
    private_key_ref: str
    funder_address: str


def _validate_ref_format(ref: str) -> None:
    if not isinstance(ref, str) or ":" not in ref:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bad_ref_format",
                "message": "private_key_ref must look like 'scheme:value' (e.g. env:NAME or local:name)",
            },
        )


def _validate_address(addr: str, field: str) -> None:
    if not isinstance(addr, str) or not _ETH_ADDRESS_RE.match(addr):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bad_address",
                "message": f"{field} must be a 0x-prefixed 40-hex-char EVM address",
            },
        )


def _derive_signer_address(private_key: str) -> str:
    if not _HEX_PRIVKEY_RE.match(private_key):
        raise ValueError("private key must be 0x-prefixed 64-hex-char string")
    return Account.from_key(private_key).address


@router.get("/api/wallet/config", response_model=WalletConfigResponse)
def get_wallet_config() -> WalletConfigResponse:
    wallet = runtime_state.wallet
    if wallet is None:
        return WalletConfigResponse(
            private_key_ref=None,
            funder_address=None,
            signer_address=None,
            error="wallet_not_configured",
        )
    signer: str | None = None
    err: str | None = None
    try:
        pk = resolve(wallet.private_key_ref)
    except SecretsError as exc:
        logger.warning("wallet secret missing during GET: %s", exc)
        err = "wallet_secret_missing"
    else:
        try:
            signer = _derive_signer_address(pk)
        except (ValueError, Exception) as exc:  # noqa: BLE001
            logger.warning("signer derivation failed during GET: %s", exc)
            err = "bad_private_key"
    return WalletConfigResponse(
        private_key_ref=wallet.private_key_ref,
        funder_address=wallet.funder_address,
        signer_address=signer,
        error=err,
    )


@router.put("/api/wallet/config", response_model=WalletConfigResponse)
def put_wallet_config(body: PutWalletConfigRequest) -> WalletConfigResponse:
    _validate_ref_format(body.private_key_ref)
    _validate_address(body.funder_address, "funder_address")
    try:
        pk = resolve(body.private_key_ref)
    except SecretsError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "wallet_secret_missing", "message": str(exc)},
        ) from exc
    try:
        signer = _derive_signer_address(pk)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_private_key", "message": str(exc)},
        ) from exc
    runtime_state.set_wallet(
        WalletSpec(
            private_key_ref=body.private_key_ref,
            funder_address=body.funder_address,
        )
    )
    logger.info(
        "wallet config updated; signer=%s funder=%s",
        signer,
        body.funder_address[:10] + "…",
    )
    return WalletConfigResponse(
        private_key_ref=body.private_key_ref,
        funder_address=body.funder_address,
        signer_address=signer,
        error=None,
    )


class SetModeRequest(BaseModel):
    mode: Literal["paper", "live"]


class SetModeResponse(BaseModel):
    mode: Literal["paper", "live"]


@router.post("/api/system/mode", response_model=SetModeResponse)
def set_mode(
    body: SetModeRequest,
    store: PortfolioStore = Depends(get_portfolio_store),
) -> SetModeResponse:
    target = body.mode
    if target == runtime_state.exec_mode:
        return SetModeResponse(mode=target)

    open_positions = store.get_open_positions()
    if open_positions:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "open_positions",
                "count": len(open_positions),
                "message": "close all open positions before switching modes",
            },
        )

    if target == "live":
        wallet = runtime_state.wallet
        if wallet is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "wallet_not_configured",
                    "message": "configure wallet before switching to live",
                },
            )
        try:
            pk = resolve(wallet.private_key_ref)
        except SecretsError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "wallet_secret_missing", "message": str(exc)},
            ) from exc
        try:
            _derive_signer_address(pk)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail={"error": "bad_private_key", "message": str(exc)},
            ) from exc

        # Build the same live executor lifespan will use — proves Cloudflare
        # patch + L1 init + derive_api_key + L2 read all work end-to-end
        # before flipping the mode. Any of these throwing aborts the switch.
        try:
            le = build_live_executor(wallet, store)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "live_executor_build_failed",
                    "message": str(exc),
                },
            ) from exc

        from openpoly.execution.clob_patch import (
            AssetType,
            BalanceAllowanceParams,
        )

        try:
            ba = le._clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail={"error": "rpc_unreachable", "message": str(exc)},
            ) from exc

        # V2 SDK returns balance as raw uint256 string (pUSD has 6 decimals)
        # and allowances as a {exchange_addr: uint256} dict.
        try:
            balance = int(ba.get("balance", 0))
        except (TypeError, ValueError):
            balance = 0
        if balance < _MIN_PUSD_BALANCE_RAW:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "pusd_insufficient",
                    "balance": balance,
                    "message": "fund the DepositWallet with at least 1 pUSD first",
                },
            )

        allowances_raw = ba.get("allowances") or {}
        if not isinstance(allowances_raw, dict):
            allowances_raw = {}

        def _allow(addr: str) -> int:
            for k, v in allowances_raw.items():
                if isinstance(k, str) and k.lower() == addr.lower():
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return 0
            return 0

        if _allow(_STANDARD_V2) < _MIN_EXCHANGE_ALLOWANCE_RAW:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "standard_v2_not_approved",
                    "message": (
                        "approve pUSD to the Standard V2 Exchange at "
                        "polymarket.com/settings before switching"
                    ),
                },
            )
        if _allow(_NEGRISK_V2) < _MIN_EXCHANGE_ALLOWANCE_RAW:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "negrisk_v2_not_approved",
                    "message": (
                        "approve pUSD to the NegRisk V2 Exchange at "
                        "polymarket.com/settings before switching"
                    ),
                },
            )

    runtime_state.set_mode(target)
    logger.info("exec mode -> %s", target)
    return SetModeResponse(mode=target)


@router.get("/api/system/mode", response_model=SetModeResponse)
def get_mode() -> SetModeResponse:
    return SetModeResponse(mode=runtime_state.exec_mode)


# ---------- wallet balance (dashboard) ----------

_BALANCE_CACHE_TTL_SECONDS = 30.0
# (fetched_at, payload) — one wallet, one slot. Keeps the frontend's poll from
# hammering the CLOB / data-api; 30s staleness is fine for a display card.
_balance_cache: tuple[float, dict] | None = None


class WalletBalanceResponse(BaseModel):
    configured: bool
    usdc: float | None = None
    positions_value: float | None = None
    total: float | None = None
    ts: float | None = None


@router.get("/api/wallet/balance", response_model=WalletBalanceResponse)
async def get_wallet_balance() -> WalletBalanceResponse:
    """USDC cash + open-position market value for the configured wallet.

    Mode-independent — the wallet is an on-chain fact, so the card reads the
    same in paper and live mode. No wallet → ``configured: false`` (200, not
    an error: the unconfigured paper deployment is the open-source default).
    Either source failing yields ``null`` for its field, never a 500.
    """
    global _balance_cache
    wallet = runtime_state.wallet
    if wallet is None:
        return WalletBalanceResponse(configured=False)

    now = time.time()
    if _balance_cache is not None and now - _balance_cache[0] < _BALANCE_CACHE_TTL_SECONDS:
        return WalletBalanceResponse(**_balance_cache[1])

    # The collateral read is a sync CLOB network round-trip — offload it so a
    # slow CLOB can't starve the event loop (docs/architecture/05).
    raw = await asyncio.to_thread(executor.get_collateral_balance_raw)
    usdc = raw / 1e6 if raw is not None else None

    try:
        positions_value = await fetch_wallet_positions_value(wallet.funder_address)
    except Exception as exc:  # noqa: BLE001 — display endpoint, degrade to null
        logger.warning("positions value fetch failed: %s", exc)
        positions_value = None

    total = usdc + positions_value if usdc is not None and positions_value is not None else None
    payload = {
        "configured": True,
        "usdc": usdc,
        "positions_value": positions_value,
        "total": total,
        "ts": now,
    }
    _balance_cache = (now, payload)
    return WalletBalanceResponse(**payload)
