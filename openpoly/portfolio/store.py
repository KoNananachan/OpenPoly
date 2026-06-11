"""Synchronous transactional store over the fill ledger + position table.

The portfolio is state that must never be lost — unlike the write-behind
``order_book`` / ``news`` sinks, every call here commits synchronously inside
one transaction. openPoly is one-shot per (market, side): a position is exactly
one buy fill, later one sell fill; ``open_position`` / ``close_position`` write
the fill and the position-projection row together.

The store is stateless — it holds only a session factory and touches the DB
only when a method is called, so it is safe to construct before ``init_db``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.tables import FillRow, PositionRow
from openpoly.portfolio.models import (
    CloseReason,
    Fill,
    HeldPosition,
    PositionRecord,
    Side,
)

# A residual qty at or below this (Polymarket sizes are ≤6 decimals) counts as
# fully sold — closes the position rather than leaving a dust remainder open.
_QTY_EPS = 1e-6


def _to_held(row: PositionRow) -> HeldPosition:
    return HeldPosition(
        position_id=row.id,
        market_id=row.market_id,
        side=row.side,  # type: ignore[arg-type]
        token_id=row.token_id,
        condition_id=row.condition_id,
        qty=row.qty,
        avg_entry_price=row.avg_entry_price,
        opened_at=row.opened_at,
    )


def _to_record(row: PositionRow) -> PositionRecord:
    return PositionRecord(
        id=row.id,
        market_id=row.market_id,
        side=row.side,  # type: ignore[arg-type]
        token_id=row.token_id,
        condition_id=row.condition_id,
        qty=row.qty,
        avg_entry_price=row.avg_entry_price,
        status=row.status,  # type: ignore[arg-type]
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        close_reason=row.close_reason,
        realized_pnl=row.realized_pnl,
    )


def _to_fill(row: FillRow) -> Fill:
    return Fill(
        id=row.id,
        ts=row.ts,
        market_id=row.market_id,
        side=row.side,  # type: ignore[arg-type]
        action=row.action,  # type: ignore[arg-type]
        price=row.price,
        qty=row.qty,
        fee=row.fee,
        position_id=row.position_id,
        news_id=row.news_id,
        trigger=row.trigger,
        order_id=row.order_id,  # NEW
        tx_hash=row.tx_hash,  # NEW
    )


class PortfolioStore:
    """Repository over ``fill`` + ``position``. Construct with a session
    factory; every method opens its own short transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def open_position(
        self,
        *,
        market_id: str,
        side: Side,
        token_id: str,
        condition_id: str,
        price: float,
        qty: float,
        ts: float,
        news_id: str | None = None,
        order_id: str | None = None,  # NEW
        tx_hash: str | None = None,  # NEW
    ) -> HeldPosition:
        """Open a position: insert the position-projection row + its buy fill in
        one transaction. Raises ``IntegrityError`` if an open position for
        (market_id, side) already exists — the partial unique index backstop.
        """
        with self._session_factory() as session:
            pos = PositionRow(
                market_id=market_id,
                side=side,
                token_id=token_id,
                condition_id=condition_id,
                qty=qty,
                avg_entry_price=price,
                status="open",
                opened_at=ts,
                closed_at=None,
                close_reason=None,
                realized_pnl=None,
            )
            session.add(pos)
            session.flush()  # populate pos.id for the fill's position_id
            session.add(
                FillRow(
                    ts=ts,
                    market_id=market_id,
                    side=side,
                    action="buy",
                    price=price,
                    qty=qty,
                    fee=0.0,
                    position_id=pos.id,
                    news_id=news_id,
                    trigger=None,
                    order_id=order_id,  # NEW
                    tx_hash=tx_hash,  # NEW
                )
            )
            session.commit()
            return _to_held(pos)

    def close_position(
        self,
        position_id: int,
        *,
        sell_price: float,
        ts: float,
        close_reason: CloseReason,
        trigger: str | None = None,
        order_id: str | None = None,  # NEW
        tx_hash: str | None = None,  # NEW
    ) -> PositionRecord:
        """Close a position: insert the sell fill + flip the position row to
        ``closed`` with its realized PnL, in one transaction. Always closes the
        full quantity (one-shot model — no partial close). Raises ``ValueError``
        if the position is missing or not open.
        """
        with self._session_factory() as session:
            pos = session.get(PositionRow, position_id)
            if pos is None:
                raise ValueError(f"position {position_id} not found")
            if pos.status != "open":
                raise ValueError(f"position {position_id} is {pos.status}, not open")
            realized = (sell_price - pos.avg_entry_price) * pos.qty  # fee = 0
            session.add(
                FillRow(
                    ts=ts,
                    market_id=pos.market_id,
                    side=pos.side,
                    action="sell",
                    price=sell_price,
                    qty=pos.qty,
                    fee=0.0,
                    position_id=pos.id,
                    news_id=None,
                    trigger=trigger,
                    order_id=order_id,  # NEW
                    tx_hash=tx_hash,  # NEW
                )
            )
            pos.status = "closed"
            pos.closed_at = ts
            pos.close_reason = close_reason
            # Accrue (not overwrite): a position partially sold via record_sell
            # already carries realized PnL on the sold portion.
            pos.realized_pnl = (pos.realized_pnl or 0.0) + realized
            session.commit()
            return _to_record(pos)

    def record_sell(
        self,
        position_id: int,
        *,
        sold_qty: float,
        sell_price: float,
        ts: float,
        close_reason: CloseReason,
        trigger: str | None = None,
        order_id: str | None = None,
        tx_hash: str | None = None,
    ) -> PositionRecord:
        """Record a (possibly partial) sell against an open position.

        Closes the position only when ``sold_qty`` covers the full remaining
        qty; a partial fill reduces ``qty`` and leaves the position OPEN so the
        next exit tick sells the remainder — otherwise the unsold tokens are
        stranded on-chain as an orphan (the orphaned-remainder bug). Realized PnL accrues
        across partials. Raises ``ValueError`` if missing or not open.
        """
        with self._session_factory() as session:
            pos = session.get(PositionRow, position_id)
            if pos is None:
                raise ValueError(f"position {position_id} not found")
            if pos.status != "open":
                raise ValueError(f"position {position_id} is {pos.status}, not open")
            sold = min(sold_qty, pos.qty)
            session.add(
                FillRow(
                    ts=ts,
                    market_id=pos.market_id,
                    side=pos.side,
                    action="sell",
                    price=sell_price,
                    qty=sold,
                    fee=0.0,
                    position_id=pos.id,
                    news_id=None,
                    trigger=trigger,
                    order_id=order_id,
                    tx_hash=tx_hash,
                )
            )
            pos.realized_pnl = (pos.realized_pnl or 0.0) + (sell_price - pos.avg_entry_price) * sold
            if pos.qty - sold <= _QTY_EPS:
                pos.status = "closed"
                pos.closed_at = ts
                pos.close_reason = close_reason
            else:
                pos.qty = pos.qty - sold
            session.commit()
            return _to_record(pos)

    def get_open_position(self, market_id: str, side: Side) -> HeldPosition | None:
        """The open position for (market_id, side), or None."""
        with self._session_factory() as session:
            row = session.execute(
                select(PositionRow).where(
                    PositionRow.market_id == market_id,
                    PositionRow.side == side,
                    PositionRow.status == "open",
                )
            ).scalar_one_or_none()
            return _to_held(row) if row is not None else None

    def get_open_positions(self) -> list[HeldPosition]:
        """Every open position."""
        with self._session_factory() as session:
            rows = (
                session.execute(select(PositionRow).where(PositionRow.status == "open"))
                .scalars()
                .all()
            )
            return [_to_held(r) for r in rows]

    def get_position(self, position_id: int) -> PositionRecord | None:
        """The position with this id — any status (open or closed) — or None."""
        with self._session_factory() as session:
            row = session.get(PositionRow, position_id)
            return _to_record(row) if row is not None else None

    def list_positions(self, limit: int = 100) -> list[PositionRecord]:
        """Recent positions (open + closed), newest first."""
        with self._session_factory() as session:
            rows = (
                session.execute(select(PositionRow).order_by(PositionRow.id.desc()).limit(limit))
                .scalars()
                .all()
            )
            return [_to_record(r) for r in rows]

    def list_fills(self, limit: int = 100) -> list[Fill]:
        """Recent fills (the ledger tail), newest first."""
        with self._session_factory() as session:
            rows = (
                session.execute(select(FillRow).order_by(FillRow.id.desc()).limit(limit))
                .scalars()
                .all()
            )
            return [_to_fill(r) for r in rows]

    def news_id_for_position(self, position_id: int) -> str | None:
        """Look up the news_id that triggered this position's BUY fill.

        PositionRecord doesn't denormalize news_id (it lives on the fill
        ledger row), but PositionDetail UI / analyzer-log lookup wants to
        cross-reference the LLM call by news_id. Returns ``None`` when no
        BUY fill matches (e.g. manually-opened paper position, or the
        position id doesn't exist)."""
        with self._session_factory() as session:
            stmt = (
                select(FillRow.news_id)
                .where(FillRow.position_id == position_id)
                .where(FillRow.action == "buy")
                .order_by(FillRow.id.asc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()
