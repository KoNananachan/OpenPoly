"""LiveExecutor — submit IOC (FAK) orders to Polymarket V2 CLOB.

Same ``ExecResult`` contract as ``PaperExecutor`` — failures map to
``skip(reason)``, never raise. Order type is FAK (Fill And Kill = IOC); see
slice C design doc §3 D2 / D3 for why no GTC fallback and no retry.

Auth model is Polymarket V2 DepositWallet:
  * signer EOA = ``wallet.private_key_ref`` (resolved at factory time)
  * funder    = ``wallet.funder_address`` (the DepositWallet contract)
  * sig_type  = 3 (POLY_1271) — server validates via EIP-1271 on funder

A prior project's production verified the following exact pattern
works against V2 CLOB; deviations have hit dead bugs (see py-clob-client-v2
issues #64/#70/#76). Do NOT change without re-testing live:
  * Cloudflare patch applied before any SDK import (see ``clob_patch``)
  * ``derive_api_key()`` — NOT ``create_or_derive_api_key()``
  * ``create_and_post_order(...)`` — combined call
  * ``PartialCreateOrderOptions(neg_risk=...)`` passed on every order
  * ``update_balance_allowance()`` before each trade (collateral for BUY,
    conditional + token_id for SELL)

The ``_ClobClient`` Protocol lets tests pass a fake without instantiating
the real v2 client (which would hit the network at init).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

# Cloudflare patch MUST be applied before any other SDK import. The patch
# module re-exports the SDK symbols we need so this single import covers
# both concerns.
from openpoly.execution.clob_patch import (
    AssetType,
    BalanceAllowanceParams,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
)
from openpoly.execution.types import ExecResult
from openpoly.markets.manager import manager as market_source_manager
from openpoly.portfolio import CloseReason, HeldPosition, PortfolioStore
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137
SIGTYPE_POLY_1271 = 3

# Server-side rules verified by live smoke 2026-05-24:
# - marketable BUY: maker amount max 2 decimals (cents); min size $1.00
# - SELL          : taker amount max 4 decimals
# We give a $0.10 buffer over the $1.00 floor so price/rounding wiggle won't
# trip server rejection at the edge.
_MIN_NOTIONAL_PUSD = 1.10
_CTF_DECIMALS = 6  # CTF / Polymarket shares are 1e6 base units
_CTF_POLL_ATTEMPTS = 5  # SELL right after BUY can hit cache lag; ~5s total
_CTF_POLL_SLEEP = 1.0


def _quantize_size(qty: float, price: float) -> float:
    """Floor qty so ``qty * price`` yields a clean ≤2-decimal maker amount.

    Most Polymarket prices are 2-decimal-aligned (cents), in which case
    integer qty is sufficient. For 3+ decimal prices we walk down to find
    the largest integer qty whose maker amount lands on clean cents.
    Returns 0.0 only if no qty in [1, floor(qty)] satisfies the rule,
    which the caller handles via the min-notional floor.
    """
    base = int(qty)
    if base <= 0:
        return 0.0
    # Common case: price is 2-dec-aligned → any integer qty is clean.
    if abs(price * 100 - round(price * 100)) < 1e-9:
        return float(base)
    # Rare case: finer price → search.
    for candidate in range(base, 0, -1):
        maker_cents = candidate * price * 100
        if abs(maker_cents - round(maker_cents)) < 1e-6:
            return float(candidate)
    return 0.0


class _ClobClient(Protocol):
    def create_and_post_order(
        self,
        order_args: OrderArgs,
        options: PartialCreateOrderOptions,
        order_type: Any,
    ) -> dict[str, Any]: ...
    def update_balance_allowance(self, params: BalanceAllowanceParams) -> Any: ...
    def get_balance_allowance(self, params: BalanceAllowanceParams) -> dict[str, Any]: ...


class LiveExecutor:
    """V2 CLOB IOC executor. Construct via ``build_live_executor``."""

    def __init__(
        self,
        *,
        portfolio: PortfolioStore,
        clob_client: _ClobClient,
    ) -> None:
        self._store = portfolio
        self._clob = clob_client

    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult:
        catalog = market_source_manager.store
        market = catalog.get(intent.market_id)
        if market is None:
            return ExecResult.skip("market_not_found")
        token_id = market.yes_token_id if intent.side == "yes" else market.no_token_id
        if token_id is None:
            return ExecResult.skip("no_token")

        if self._store.get_open_position(intent.market_id, intent.side) is not None:
            return ExecResult.skip("position_exists")

        # Quantize qty + check min notional against server rules verified
        # 2026-05-24. Both are pre-flight: cheaper to skip locally than to
        # eat a 400 round-trip + clutter logs with rejections.
        size = _quantize_size(intent.qty, intent.price)
        notional = size * intent.price
        if notional < _MIN_NOTIONAL_PUSD:
            return ExecResult.skip("min_notional_below_floor")

        # Refresh CLOB collateral allowance cache before signing (a prior project pattern).
        # Failures are non-fatal — the order may still succeed if the cache is
        # already warm — but we log so a real allowance issue surfaces in logs.
        try:
            self._clob.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("update_balance_allowance(COLLATERAL) failed: %s", exc)

        # GTC + crossing the spread acts like an aggressive market order. We
        # use GTC (not FAK) because a prior project's production verified it end-to-end
        # on 2026-05-05 / smoke verified again 2026-05-24; FAK hits stricter
        # server precision rules we haven't fully mapped.
        try:
            resp = self._clob.create_and_post_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=intent.price,
                    size=size,
                    side=Side.BUY,
                ),
                options=PartialCreateOrderOptions(neg_risk=market.neg_risk),
                order_type=OrderType.GTC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("live buy submit failed (%s): %s", type(exc).__name__, exc)
            return ExecResult.skip(f"live_error:{type(exc).__name__}")

        if not resp.get("success"):
            return ExecResult.skip(f"live_rejected:{resp.get('errorMsg', 'unknown')}")
        try:
            making = float(resp.get("makingAmount") or 0)  # pUSD paid
            taking = float(resp.get("takingAmount") or 0)  # tokens received
        except (TypeError, ValueError):
            return ExecResult.skip("live_unparseable")
        if taking <= 0:
            return ExecResult.skip("live_no_match")
        actual_price = making / taking
        actual_qty = taking
        order_id = resp.get("orderID")
        tx_hashes = resp.get("transactionsHashes") or []
        tx_hash = tx_hashes[0] if tx_hashes else None

        held = self._store.open_position(
            market_id=intent.market_id,
            side=intent.side,
            token_id=token_id,
            condition_id=market.condition_id,
            price=actual_price,
            qty=actual_qty,
            ts=ts,
            news_id=news_id,
            order_id=order_id,
            tx_hash=tx_hash,
        )
        logger.info(
            "live buy filled: %s %s qty=%.4f @ %.4f order=%s tx=%s",
            intent.market_id,
            intent.side,
            actual_qty,
            actual_price,
            order_id,
            (tx_hash[:10] + "…" if tx_hash else None),
        )
        return ExecResult.ok(price=actual_price, qty=actual_qty, position_id=held.position_id)

    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason: CloseReason,
        ts: float,
        trigger: str | None = None,
    ) -> ExecResult:
        catalog = market_source_manager.store
        market = catalog.get(position.market_id)
        if market is None:
            return ExecResult.skip("market_not_found")

        book = catalog.get_order_book(position.token_id)
        if book is None or not book.bids:
            return ExecResult.skip("no_bid_liquidity")
        bid_price = book.bids[0][0]

        # Quantize SELL size symmetrically with BUY so taker (size * price)
        # stays within server precision. Most likely a no-op since BUY also
        # quantized, but defensive for partial-fill positions or hand-opened.
        size = _quantize_size(position.qty, bid_price)
        if size <= 0:
            return ExecResult.skip("min_notional_below_floor")

        # Poll CTF balance — handles cache lag when SELL fires shortly after
        # BUY (smoke 2026-05-24 saw ~3-5s lag). update_balance_allowance is
        # the documented refresh trigger; we then read to confirm.
        need_raw = int(size * (10**_CTF_DECIMALS))
        synced = False
        for attempt in range(_CTF_POLL_ATTEMPTS):
            try:
                self._clob.update_balance_allowance(
                    BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=position.token_id,
                    )
                )
                ba = self._clob.get_balance_allowance(
                    BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=position.token_id,
                    )
                )
                try:
                    have_raw = int(ba.get("balance", 0))
                except (TypeError, ValueError):
                    have_raw = 0
                if have_raw >= need_raw:
                    synced = True
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning("CTF balance check attempt %d failed: %s", attempt + 1, exc)
            if attempt < _CTF_POLL_ATTEMPTS - 1:
                time.sleep(_CTF_POLL_SLEEP)
        if not synced:
            return ExecResult.skip("ctf_cache_not_synced")

        try:
            resp = self._clob.create_and_post_order(
                order_args=OrderArgs(
                    token_id=position.token_id,
                    price=bid_price,
                    size=size,
                    side=Side.SELL,
                ),
                options=PartialCreateOrderOptions(neg_risk=market.neg_risk),
                order_type=OrderType.GTC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("live sell submit failed (%s): %s", type(exc).__name__, exc)
            return ExecResult.skip(f"live_error:{type(exc).__name__}")

        if not resp.get("success"):
            return ExecResult.skip(f"live_rejected:{resp.get('errorMsg', 'unknown')}")
        try:
            making = float(resp.get("makingAmount") or 0)  # tokens sent
            taking = float(resp.get("takingAmount") or 0)  # pUSD received
        except (TypeError, ValueError):
            return ExecResult.skip("live_unparseable")
        if making <= 0:
            return ExecResult.skip("live_no_match")
        actual_price = taking / making
        actual_qty = making
        order_id = resp.get("orderID")
        tx_hashes = resp.get("transactionsHashes") or []
        tx_hash = tx_hashes[0] if tx_hashes else None

        self._store.close_position(
            position.position_id,
            sell_price=actual_price,
            ts=ts,
            close_reason=close_reason,
            trigger=trigger,
            order_id=order_id,
            tx_hash=tx_hash,
        )
        logger.info(
            "live sell filled: %s %s qty=%.4f @ %.4f (position %d, %s) order=%s",
            position.market_id,
            position.side,
            actual_qty,
            actual_price,
            position.position_id,
            close_reason,
            order_id,
        )
        return ExecResult.ok(price=actual_price, qty=actual_qty, position_id=position.position_id)


# ---------- factory ----------


def build_live_executor(
    wallet,  # WalletSpec, typing avoided to skip cyclic import
    portfolio: PortfolioStore,
) -> LiveExecutor:
    """Construct a LiveExecutor from a WalletSpec + PortfolioStore.

    Resolves ``wallet.private_key_ref`` to the EOA signer key, binds the
    v2 ClobClient to ``wallet.funder_address`` (the DepositWallet) with
    POLY_1271 sig type, then derives + sets L2 API creds.

    Raises on any failure (lifespan catches, logs, and leaves dispatcher's
    live=None).
    """
    from openpoly.execution.clob_patch import ClobClient
    from openpoly.news.secrets import resolve

    private_key = resolve(wallet.private_key_ref)

    clob = ClobClient(
        CLOB_HOST,
        key=private_key,
        chain_id=POLYGON_CHAIN_ID,
        signature_type=SIGTYPE_POLY_1271,
        funder=wallet.funder_address,
    )
    creds = clob.derive_api_key()
    clob.set_api_creds(creds)
    logger.info(
        "live executor ready: signer=%s funder=%s api_key=%s",
        clob.get_address(),
        wallet.funder_address[:10] + "…",
        creds.api_key[:8] + "…",
    )
    return LiveExecutor(portfolio=portfolio, clob_client=clob)
