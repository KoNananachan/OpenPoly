"""Exit monitor — the position-driven, timer-driven close loop.

The news pipeline (orchestrator) is event-driven; closing a position is
position-driven + periodic. ``ExitMonitor`` runs a tick loop: every
``tick_interval_seconds`` it walks every open position, marks it with the held
side's current price (level-1 bid of the held token's order book), runs the
``exit`` section, and — when the section returns a ``CloseIntent`` — routes it
to ``executor.execute_sell``. Each evaluation is recorded in ``exit_log``.

It shares the one module-level ``executor`` with the orchestrator — entry buys
and exit sells go through the same fill path. The ``PortfolioStore`` is
injected by the FastAPI lifespan once the DB is up.

A position whose market has resolved drops out of the catalog → no order book →
the monitor logs a ``skip`` and leaves the position open. Settlement-close is a
separate concern, out of scope.

The tick does only sync work (DB read/write, in-memory book lookup, the pure
exit section) — all sub-millisecond — so it runs inline; the loop yields
cooperatively between ticks (docs/architecture/05).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openpoly.db.tables import OrderBookSnapshot
from openpoly.execution import ExecResult
from openpoly.execution import executor as _executor_singleton
from openpoly.markets.manager import manager as market_source_manager
from openpoly.markets.store import MarketStore
from openpoly.portfolio import HeldPosition, PortfolioStore
from openpoly.runtime.section_log import ExitDecision, exit_log
from openpoly.sections._base import SectionInput, SectionOutput
from openpoly.sections.exit.threshold_v0 import (
    CloseIntent,
    MarkedPosition,
    ThresholdExitConfig,
    ThresholdExitV0,
)

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 120  # v8 §10.1 "hard" tick

State = Literal["stopped", "running"]


class _ExitSection(Protocol):
    """Minimal exit-section shape used by the monitor."""

    def run(self, input: SectionInput) -> SectionOutput: ...


class _Executor(Protocol):
    """Minimal executor shape used by the monitor."""

    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason: str,
        ts: float,
        trigger: str | None,
    ) -> ExecResult: ...


class ExitMonitor:
    """Timer-driven loop that closes open positions via the exit section."""

    def __init__(
        self,
        *,
        exit_section: _ExitSection,
        executor: _Executor,
        tick_interval_seconds: int = DEFAULT_TICK_INTERVAL_SECONDS,
    ) -> None:
        self._exit = exit_section
        self._executor = executor
        self._tick_interval = tick_interval_seconds
        self._portfolio: PortfolioStore | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._state: State = "stopped"
        # canvas-sync v2: atomic swap lock — same model as orchestrator's
        # _sections_lock. _tick_once reads self._exit; replace happens between
        # ticks (or between in-flight section.run calls within a tick — Python
        # GC keeps the old instance alive for any caller already holding it).
        self._exit_lock = asyncio.Lock()
        # Per-position peak of the held side's current_price across this
        # process's lifetime. Rebuilt at startup by ``bootstrap_peaks`` from
        # the order_book_snapshot table; updated every tick; dropped on close.
        # Process-restart loses anything not in that table — accepted trade-off
        # for keeping runtime state out of the database schema.
        self._peak: dict[int, float] = {}
        # Tick telemetry (v18) — the "is the monitor working" heartbeat,
        # surfaced via /api/exit/log so the canvas badge / Closes tab can show
        # liveness without flooding exit_log with a skip entry per position per
        # tick. Within-threshold + no-order-book evaluations no longer write a
        # log entry at all (the ring keeps only the rare ok / error closes, so
        # they never get evicted); these counts carry that signal instead.
        self._last_tick_at: float | None = None
        self._last_tick_open: int = 0
        self._last_tick_blocked: int = 0

    @property
    def state(self) -> State:
        return self._state

    @property
    def last_tick_at(self) -> float | None:
        """Wall-clock of the last completed sweep (None before the first)."""
        return self._last_tick_at

    @property
    def open_positions(self) -> int:
        """Open positions seen on the last sweep."""
        return self._last_tick_open

    @property
    def blocked(self) -> int:
        """Positions on the last sweep that could not be evaluated (no order
        book — market resolved or data gap; their stop-loss can't fire)."""
        return self._last_tick_blocked

    def configure(self, portfolio: PortfolioStore) -> None:
        """Inject the PortfolioStore — the FastAPI lifespan calls this once the
        DB is up. Construction itself touches no DB."""
        self._portfolio = portfolio

    def bootstrap_peaks(self, session_factory: sessionmaker[Session]) -> None:
        """Rebuild per-position peaks from persisted order-book snapshots.

        For each open position, scan ``order_book_snapshot`` rows where
        ``token_id == position.token_id AND recorded_at >= opened_at`` and
        take the max of ``bids[0][0]`` (the held-side best bid — same value
        the live tick uses). Falls back to ``avg_entry_price`` when no
        snapshot exists yet. Called once at startup, before ``start()``.
        """
        if self._portfolio is None:
            return
        opens = self._portfolio.get_open_positions()
        if not opens:
            return
        with session_factory() as session:
            for held in opens:
                stmt = select(OrderBookSnapshot.bids_json).where(
                    OrderBookSnapshot.token_id == held.token_id,
                    OrderBookSnapshot.recorded_at >= held.opened_at,
                )
                peak = held.avg_entry_price
                for (bids_json,) in session.execute(stmt):
                    try:
                        bids = json.loads(bids_json)
                    except (TypeError, ValueError):
                        continue
                    if bids:
                        bid = float(bids[0][0])
                        if bid > peak:
                            peak = bid
                self._peak[held.position_id] = peak
        logger.info("exit monitor: bootstrap_peaks loaded %d positions", len(self._peak))

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._state = "running"
        # Recreate the Event each start so it binds to the *current* loop —
        # this module singleton may be start()ed across distinct loops (tests).
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._task is None:
            self._state = "stopped"
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._state = "stopped"

    # ---------- loop ----------

    async def _tick_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_once()
            except Exception:  # noqa: BLE001 — the loop must survive any tick error
                logger.exception("exit monitor: tick failed")
            # Cooperative yield, then sleep the interval — waking early on stop.
            await asyncio.sleep(0)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_interval)

    # ---------- tick ----------

    def _tick_once(self) -> None:
        """One sweep — evaluate every open position. Sync; tests drive it
        directly. Records tick telemetry (open / blocked counts + timestamp);
        within-threshold + no-order-book holds no longer write a log entry —
        only ok / error closes land in exit_log."""
        if self._portfolio is None:
            return
        ts = time.time()
        catalog = market_source_manager.store
        opens = self._portfolio.get_open_positions()
        blocked = 0
        for held in opens:
            try:
                if self._evaluate(held, catalog, ts):
                    blocked += 1
            except Exception as exc:  # noqa: BLE001 — one bad position must not abort the sweep
                logger.exception("exit monitor: position %d failed", held.position_id)
                self._log(held, ts, verdict="error", error=repr(exc)[:200])
        self._last_tick_at = ts
        self._last_tick_open = len(opens)
        self._last_tick_blocked = blocked

    def _evaluate(self, held: HeldPosition, catalog: MarketStore, ts: float) -> bool:
        """Evaluate one position. Returns True when it could not be evaluated
        (no order book — counted as ``blocked``); False when held within
        thresholds or closed. ok / error closes are logged; within-threshold
        and no-order-book holds are not (see tick telemetry)."""
        book = catalog.get_order_book(held.token_id)
        if book is None or not book.bids:
            return True
        current_price = book.bids[0][0]
        # Monotone-increasing per-position peak. New open positions seed at
        # current_price; bootstrap_peaks may have seeded a higher one already.
        prev_peak = self._peak.get(held.position_id, current_price)
        peak_price = max(prev_peak, current_price)
        self._peak[held.position_id] = peak_price

        marked = MarkedPosition(
            market_id=held.market_id,
            side=held.side,
            avg_entry_price=held.avg_entry_price,
            qty=held.qty,
            current_price=current_price,
            peak_price=peak_price,
        )
        out = self._exit.run(SectionInput(tick_type="hard", payload=marked))
        return_pct = out.signals.get("return_pct")
        if out.verdict != "ok" or not isinstance(out.payload, CloseIntent):
            # Held within thresholds — no close, no log entry (peak already
            # tracked above; tick telemetry records that this position was
            # evaluated).
            return False

        intent = out.payload
        result = self._executor.execute_sell(
            held, close_reason=intent.trigger, ts=ts, trigger=intent.trigger
        )
        if result.filled and result.price is not None:
            realized = (result.price - held.avg_entry_price) * held.qty
            # Position is closed; drop its peak so a future re-entry on the
            # same position_id (shouldn't happen, but be safe) starts fresh.
            self._peak.pop(held.position_id, None)
            self._log(
                held,
                ts,
                verdict="ok",
                trigger=intent.trigger,
                return_pct=return_pct,
                peak_price=peak_price,
                fill_price=result.price,
                realized_pnl=realized,
                reason=intent.trigger,
            )
        else:
            # The section decided to close but the fill did not land — a
            # position that should be closed is still open: surface as error.
            self._log(
                held,
                ts,
                verdict="error",
                trigger=intent.trigger,
                return_pct=return_pct,
                peak_price=peak_price,
                error=f"sell not filled: {result.skip_reason}",
            )
        return False

    def _log(
        self,
        held: HeldPosition,
        ts: float,
        *,
        verdict: str,
        trigger: str | None = None,
        return_pct: float | None = None,
        peak_price: float | None = None,
        fill_price: float | None = None,
        realized_pnl: float | None = None,
        reason: str | None = None,
        error: str | None = None,
    ) -> None:
        exit_log.append(
            ExitDecision(
                ts=ts,
                position_id=held.position_id,
                market_id=held.market_id,
                side=held.side,
                verdict=verdict,  # type: ignore[arg-type]
                trigger=trigger,
                return_pct=return_pct,
                peak_price=peak_price,
                fill_price=fill_price,
                realized_pnl=realized_pnl,
                reason=reason,
                error=error,
            )
        )


# Module-level singleton — the FastAPI lifespan injects its PortfolioStore via
# configure() and start()s it. Shares the one executor with the orchestrator.
exit_monitor = ExitMonitor(
    exit_section=ThresholdExitV0(ThresholdExitConfig()),
    executor=_executor_singleton,
)


# canvas-sync v2: hot-swap the exit section without restarting the monitor.
# Caller (api/canvas_routes._apply_canvas_reload) builds the new instance from
# the latest canvas, then awaits this. Same atomicity story as orchestrator:
# in-flight ``self._exit.run(...)`` keeps a reference to the old instance via
# Python GC; the next tick reads ``self._exit`` and gets the new one.
async def _replace_exit_section_impl(self: ExitMonitor, new_section: _ExitSection) -> None:
    async with self._exit_lock:
        self._exit = new_section


ExitMonitor.replace_exit_section = _replace_exit_section_impl  # type: ignore[attr-defined]
