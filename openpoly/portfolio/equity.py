"""Equity-curve reconstruction — folds the position ledger against sampled
order books into a realized + unrealized P&L time series.

Pure read side: no state, reads ``position`` and ``order_book_snapshot``.
``build_equity_curve`` recomputes from scratch per call — fine at openPoly's
grain-of-rice scale (single-digit positions, a few thousand snapshots), so no
materialized table is needed.

Unrealized P&L marks each open position at the level-1 bid of its token's most
recent snapshot at-or-before the evaluated time (hold-last). That is the price
the exit executor would actually sell into — an honest "if I closed now" mark.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.tables import OrderBookSnapshot, PositionRow


@dataclass(frozen=True)
class EquityPoint:
    """Wallet equity at one instant. ``equity == realized + unrealized``."""

    ts: float  # epoch seconds, UTC
    equity: float
    realized: float
    unrealized: float


@dataclass(frozen=True)
class EquityCurve:
    """The full curve plus a summary snapshot (the last point's values)."""

    points: tuple[EquityPoint, ...]  # ascending by ts, de-duplicated
    realized: float
    unrealized: float
    total: float
    open_positions: int


def _best_bid(bids_json: str) -> float | None:
    """First (best) bid price from a stored depth ladder, or None if empty."""
    bids = json.loads(bids_json)
    if not bids:
        return None
    return float(bids[0][0])


def _mark_at(marks: list[tuple[float, float]], t: float) -> float | None:
    """Most recent bid at-or-before ``t`` (hold-last). ``marks`` is ascending
    by timestamp. Returns None if no mark exists yet at ``t``."""
    result: float | None = None
    for ts, bid in marks:
        if ts <= t:
            result = bid
        else:
            break
    return result


def build_equity_curve(session_factory: sessionmaker[Session]) -> EquityCurve:
    """Reconstruct the realized + unrealized equity time series."""
    now = time.time()
    with session_factory() as session:
        positions = list(session.execute(select(PositionRow)).scalars().all())
        if not positions:
            return EquityCurve((), 0.0, 0.0, 0.0, 0)

        # Per-token bid series, ascending by recorded_at (empty books dropped).
        marks: dict[str, list[tuple[float, float]]] = {}
        for token_id in {p.token_id for p in positions}:
            rows = (
                session.execute(
                    select(OrderBookSnapshot)
                    .where(OrderBookSnapshot.token_id == token_id)
                    .order_by(OrderBookSnapshot.recorded_at)
                )
                .scalars()
                .all()
            )
            series: list[tuple[float, float]] = []
            for r in rows:
                bid = _best_bid(r.bids_json)
                if bid is not None:
                    series.append((r.recorded_at, bid))
            marks[token_id] = series

    # Time axis: every open/close event + every in-window snapshot + now.
    axis: set[float] = {now}
    for p in positions:
        axis.add(p.opened_at)
        if p.closed_at is not None:
            axis.add(p.closed_at)
        end = p.closed_at if p.closed_at is not None else now
        for ts, _bid in marks.get(p.token_id, []):
            if p.opened_at <= ts <= end:
                axis.add(ts)
    timeline = sorted(axis)

    points: list[EquityPoint] = []
    for t in timeline:
        realized = 0.0
        unrealized = 0.0
        for p in positions:
            if p.closed_at is not None and p.closed_at <= t:
                realized += p.realized_pnl or 0.0
            elif p.opened_at <= t and (p.closed_at is None or p.closed_at > t):
                mark = _mark_at(marks.get(p.token_id, []), t)
                if mark is None:
                    mark = p.avg_entry_price
                unrealized += (mark - p.avg_entry_price) * p.qty
        points.append(
            EquityPoint(
                ts=t, equity=realized + unrealized, realized=realized, unrealized=unrealized
            )
        )

    last = points[-1]
    return EquityCurve(
        points=tuple(points),
        realized=last.realized,
        unrealized=last.unrealized,
        total=last.equity,
        open_positions=sum(1 for p in positions if p.status == "open"),
    )
