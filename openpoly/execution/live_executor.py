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
    OrderPayload,
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
# The on-chain SELL is irreversible; persisting the DB close must survive a
# transient write failure (locked SQLite, brief error) or the position is left
# phantom-open with its tokens already gone (root cause of stuck phantom-open positions).
_CLOSE_PERSIST_ATTEMPTS = 5
_CLOSE_PERSIST_SLEEP = 0.5


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
    def cancel_order(self, payload: OrderPayload) -> Any: ...
    def get_order(self, order_id: str) -> dict[str, Any]: ...


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

    def _settle_resting_remainder(
        self, order_id: str | None, reported_qty: float, size: float
    ) -> float:
        """After a partial immediate fill, cancel the GTC remainder so it
        cannot fill later untracked (a live orphan incident: a partial fill whose
        resting remainder filled later invisibly). Returns the order's
        final matched qty — ``get_order`` after the cancel covers a fill that
        raced it; never less than what the response already reported."""
        if reported_qty >= size - 1e-9 or not order_id:
            return reported_qty  # full fill — nothing resting
        try:
            self._clob.cancel_order(OrderPayload(orderID=order_id))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "RESTING ORDER ALERT: cancel failed for %s (%s) — remainder "
                "may fill untracked; reverse-reconciliation will flag it",
                order_id,
                exc,
            )
            return reported_qty
        try:
            final = float(self._clob.get_order(order_id).get("size_matched") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_order after cancel failed for %s: %s", order_id, exc)
            return reported_qty
        return max(final, reported_qty)

    def get_collateral_balance_raw(self) -> int | None:
        """Wallet USDC (collateral) balance in raw 1e6 units — None when the
        read fails. Read-only; serves the wallet-balance dashboard endpoint."""
        try:
            self._clob.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            ba = self._clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return int(ba.get("balance", 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("collateral balance read failed: %s", exc)
            return None

    # ---------- lost-response confirmation (R5) ----------

    def _read_ctf_balance_raw(self, token_id: str) -> int | None:
        """Refresh + read the wallet's CTF balance for ``token_id`` (raw 1e6
        units). None when the read fails — callers treat that as 'unknown'."""
        try:
            self._clob.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
            ba = self._clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
            return int(ba.get("balance", 0))
        except (TypeError, ValueError):
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("CTF balance read failed: %s", exc)
            return None

    def _confirm_lost_order_qty(self, token_id: str, pre_raw: int, direction: str) -> float:
        """After a lost order response, poll the CTF balance to see whether the
        order actually filled. A network exception from create_and_post_order
        does NOT mean no fill — the server may have matched the order and only
        the response was lost (a confirmed live drift incident). Returns the filled token
        qty inferred from the balance delta (0.0 = no change observed)."""
        for attempt in range(_CTF_POLL_ATTEMPTS):
            now_raw = self._read_ctf_balance_raw(token_id)
            if now_raw is not None:
                delta = pre_raw - now_raw if direction == "drop" else now_raw - pre_raw
                if delta > 0:
                    return delta / (10**_CTF_DECIMALS)
            if attempt < _CTF_POLL_ATTEMPTS - 1:
                time.sleep(_CTF_POLL_SLEEP)
        return 0.0

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

        # Pre-order CTF balance — the baseline for lost-response confirmation
        # below. None = read failed; confirmation then unavailable.
        pre_raw = self._read_ctf_balance_raw(token_id)

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
            # The order may have filled despite the lost response — confirm via
            # the balance before declaring failure (R5 at-least-once).
            got = (
                self._confirm_lost_order_qty(token_id, pre_raw, "rise")
                if pre_raw is not None
                else 0.0
            )
            if got > 0:
                actual_qty = min(got, size)
                # Response (and with it the real fill price) is lost; record at
                # our limit — actual cost can only be ≤ limit, so conservative.
                logger.warning(
                    "buy response lost (%s) but CTF balance rose %.4f — "
                    "recording fill at limit price %.4f",
                    type(exc).__name__,
                    actual_qty,
                    intent.price,
                )
                held = self._store.open_position(
                    market_id=intent.market_id,
                    side=intent.side,
                    token_id=token_id,
                    condition_id=market.condition_id,
                    price=intent.price,
                    qty=actual_qty,
                    ts=ts,
                    news_id=news_id,
                    order_id=None,
                    tx_hash=None,
                )
                return ExecResult.ok(
                    price=intent.price,
                    qty=actual_qty,
                    position_id=held.position_id,
                )
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
        order_id = resp.get("orderID")
        # Partial fill → cancel the resting remainder + take the final matched
        # qty. The raced-extra portion (if any) filled at our limit price, so
        # pricing it at the response average is conservative-enough (≤1 tick).
        actual_qty = self._settle_resting_remainder(order_id, taking, size)
        actual_price = making / taking
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
        # BUY (live smoke testing saw ~3-5s lag). update_balance_allowance is
        # the documented refresh trigger; we then read to confirm.
        need_raw = int(size * (10**_CTF_DECIMALS))
        synced = False
        pre_raw = 0  # balance at gate-sync time — lost-response baseline
        for attempt in range(_CTF_POLL_ATTEMPTS):
            have_raw = self._read_ctf_balance_raw(position.token_id)
            if have_raw is not None and have_raw >= need_raw:
                synced = True
                pre_raw = have_raw
                break
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
            # The order may have filled despite the lost response — confirm via
            # the balance before declaring failure (R5 at-least-once).
            sold = self._confirm_lost_order_qty(position.token_id, pre_raw, "drop")
            if sold > 0:
                # Response (and with it the real fill price) is lost; record at
                # our limit (bid) — actual proceeds can only be ≥ bid, so
                # conservative.
                logger.warning(
                    "sell response lost (%s) but CTF balance dropped %.4f — "
                    "recording fill at limit price %.4f",
                    type(exc).__name__,
                    sold,
                    bid_price,
                )
                return self._persist_sell(
                    position,
                    actual_price=bid_price,
                    actual_qty=min(sold, size),
                    ts=ts,
                    close_reason=close_reason,
                    trigger=trigger,
                    order_id=None,
                    tx_hash=None,
                )
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
        sell_order_id = resp.get("orderID")
        # Same resting hygiene as BUY: an unsold remainder must not sit on the
        # book filling invisibly (record_sell keeps the position open with the
        # truly-unsold qty instead).
        sold_qty = self._settle_resting_remainder(sell_order_id, making, size)
        return self._persist_sell(
            position,
            actual_price=taking / making,
            actual_qty=sold_qty,
            ts=ts,
            close_reason=close_reason,
            trigger=trigger,
            order_id=sell_order_id,
            tx_hash=(resp.get("transactionsHashes") or [None])[0],
        )

    def _persist_sell(
        self,
        position: HeldPosition,
        *,
        actual_price: float,
        actual_qty: float,
        ts: float,
        close_reason: CloseReason,
        trigger: str | None,
        order_id: str | None,
        tx_hash: str | None,
    ) -> ExecResult:
        """Persist an already-executed on-chain sell. The fill is irreversible,
        so the DB write retries transient failures — dropping it would leave a
        phantom-open position for the reconciliation monitor to clean up.
        Records the ACTUAL filled qty: a GTC sell can partially fill, and
        record_sell keeps the position open with the remainder in that case."""
        for attempt in range(_CLOSE_PERSIST_ATTEMPTS):
            try:
                self._store.record_sell(
                    position.position_id,
                    sold_qty=actual_qty,
                    sell_price=actual_price,
                    ts=ts,
                    close_reason=close_reason,
                    trigger=trigger,
                    order_id=order_id,
                    tx_hash=tx_hash,
                )
                break
            except Exception as exc:  # noqa: BLE001 — on-chain fill already happened
                if attempt < _CLOSE_PERSIST_ATTEMPTS - 1:
                    logger.warning(
                        "close_position attempt %d failed for position %d: %s; retrying",
                        attempt + 1,
                        position.position_id,
                        exc,
                    )
                    time.sleep(_CLOSE_PERSIST_SLEEP)
                    continue
                logger.error(
                    "CRITICAL: on-chain sell filled (order=%s tx=%s) but close_position "
                    "failed after %d attempts for position %d: %s — tokens are gone; "
                    "leaving open for reconciliation",
                    order_id,
                    tx_hash,
                    _CLOSE_PERSIST_ATTEMPTS,
                    position.position_id,
                    exc,
                )
                return ExecResult.skip(f"close_persist_failed:{type(exc).__name__}")
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
