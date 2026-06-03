"""Execution — the level-1 paper fill service.

A fixed system service (not a pluggable section): it turns an entry
``OrderIntent`` or an exit close decision into an actual fill, recorded through
``PortfolioStore``. The fill model is deliberately crude — it takes the order
book's level-1 price (BUY at the best ask, SELL at the best bid) and caps a buy
by that level's depth. No walk-book, no slippage model, no fees (zero-fee
rule). At micro-stakes ($5-$50) an order rarely walks past level 1, so this is
not worth more.

Entry and exit share this one executor so their accounting is symmetric. It
reads the live ``MarketStore`` singleton directly (same pattern as the
embedding section — no capability injection). Construction touches no DB; the
``PortfolioStore`` is injected by the FastAPI lifespan once the database is up.
"""

from __future__ import annotations

import logging

from openpoly.execution.types import ExecResult
from openpoly.markets.manager import manager as market_source_manager
from openpoly.portfolio import CloseReason, HeldPosition, PortfolioStore
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent

logger = logging.getLogger(__name__)

# A fill below this notional (USD) is not worth recording.
MIN_FILL_USD = 1.0


class PaperExecutor:
    """Level-1 paper fill service. Routed to by ExecutorDispatcher when
    ``runtime_state.exec_mode == "paper"`` (default)."""

    def __init__(self, portfolio: PortfolioStore | None = None) -> None:
        self._portfolio = portfolio

    def configure(self, portfolio: PortfolioStore) -> None:
        """Inject the PortfolioStore. The FastAPI lifespan calls this once the
        database is up; tests pass a store to ``__init__`` directly."""
        self._portfolio = portfolio

    @property
    def _store(self) -> PortfolioStore:
        if self._portfolio is None:
            raise RuntimeError("Executor has no PortfolioStore — call configure() first")
        return self._portfolio

    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult:
        """Open a position from an entry ``OrderIntent`` at the level-1 ask.

        Skips (nothing opened) when the market / order book / ask liquidity is
        missing, when a position for (market, side) is already open, or when the
        fill notional rounds to dust.
        """
        catalog = market_source_manager.store
        market = catalog.get(intent.market_id)
        if market is None:
            return ExecResult.skip("market_not_found")

        token_id = market.yes_token_id if intent.side == "yes" else market.no_token_id
        if token_id is None:
            return ExecResult.skip("no_token")

        book = catalog.get_order_book(token_id)
        if book is None:
            return ExecResult.skip("no_order_book")
        if not book.asks:
            return ExecResult.skip("no_ask_liquidity")

        if self._store.get_open_position(intent.market_id, intent.side) is not None:
            return ExecResult.skip("position_exists")

        ask_price, ask_size = book.asks[0]
        qty = min(intent.qty, ask_size)
        if qty * ask_price < MIN_FILL_USD:
            return ExecResult.skip("dust")

        held = self._store.open_position(
            market_id=intent.market_id,
            side=intent.side,
            token_id=token_id,
            condition_id=market.condition_id,
            price=ask_price,
            qty=qty,
            ts=ts,
            news_id=news_id,
        )
        logger.info(
            "buy filled: %s %s qty=%.4f @ %.4f (position %d)",
            intent.market_id,
            intent.side,
            qty,
            ask_price,
            held.position_id,
        )
        return ExecResult.ok(price=ask_price, qty=qty, position_id=held.position_id)

    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason: CloseReason,
        ts: float,
        trigger: str | None = None,
    ) -> ExecResult:
        """Close a held position at the level-1 bid of its own token's book.

        Reads ``position.token_id`` directly, so a close never depends on the
        market still being in the live catalog. Closes the full quantity
        (one-shot model). Skips when the order book / bid liquidity is missing.
        """
        book = market_source_manager.store.get_order_book(position.token_id)
        if book is None:
            return ExecResult.skip("no_order_book")
        if not book.bids:
            return ExecResult.skip("no_bid_liquidity")

        bid_price = book.bids[0][0]
        self._store.close_position(
            position.position_id,
            sell_price=bid_price,
            ts=ts,
            close_reason=close_reason,
            trigger=trigger,
        )
        logger.info(
            "sell filled: %s %s qty=%.4f @ %.4f (position %d, %s)",
            position.market_id,
            position.side,
            position.qty,
            bid_price,
            position.position_id,
            close_reason,
        )
        return ExecResult.ok(
            price=bid_price,
            qty=position.qty,
            position_id=position.position_id,
        )
