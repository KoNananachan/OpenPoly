"""Portfolio domain types.

These dataclasses are what ``PortfolioStore`` hands back; the ORM tables in
``openpoly.db.tables`` (``FillRow`` / ``PositionRow``) are the persisted form.

``HeldPosition`` is the open-position view used by the executor and (next
phase) the exit section — it deliberately omits ``current_price``: that is a
runtime injection the exit decision receives, not a stored field.
``PositionRecord`` is the full projection (open + closed) for the read API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["yes", "no"]
Action = Literal["buy", "sell"]
Status = Literal["open", "closed"]
CloseReason = Literal["take_profit", "stop_loss", "settlement", "kill_switch", "manual"]


@dataclass(frozen=True)
class Fill:
    """One executed fill — the in-memory view of a ``fill`` ledger row."""

    id: int
    ts: float
    market_id: str
    side: Side
    action: Action
    price: float
    qty: float
    fee: float
    position_id: int
    news_id: str | None = None
    trigger: str | None = None
    order_id: str | None = None  # NEW (slice C)
    tx_hash: str | None = None  # NEW (slice C)


@dataclass(frozen=True)
class HeldPosition:
    """An open position. ``current_price`` is intentionally absent — the exit
    section receives it as a runtime injection; it is not persisted."""

    position_id: int
    market_id: str
    side: Side
    token_id: str
    condition_id: str
    qty: float
    avg_entry_price: float
    opened_at: float


@dataclass(frozen=True)
class PositionRecord:
    """Full position projection (open + closed) — for the read API.

    ``realized_pnl`` is a materialized derived value: it equals
    ``(sell_price - avg_entry_price) * qty`` from the two fills and is
    recomputable from the ledger, not an authoritative mutable field.
    """

    id: int
    market_id: str
    side: Side
    token_id: str
    condition_id: str
    qty: float
    avg_entry_price: float
    status: Status
    opened_at: float
    closed_at: float | None
    close_reason: str | None
    realized_pnl: float | None
