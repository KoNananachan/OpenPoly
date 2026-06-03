"""ExecutorDispatcher — route execute_buy/sell to paper or live by mode.

Implements slice A+B's D2 commitment: "push mode awareness down into a single
spot in the Executor... pulling that out in slice C is exactly this
dispatcher". The orchestrator and exit_monitor
keep their existing contract (call ``executor.execute_*``, get ``ExecResult``);
they have no idea which implementation actually fills.

The live executor is optional — it's configured by the FastAPI lifespan once
the wallet + ClobClient are ready. If mode=live and live is None, we return
``ExecResult.skip("live_not_ready")`` so paper-only deployments still work.
"""

from __future__ import annotations

import logging
from typing import Protocol

from openpoly.execution.types import ExecResult
from openpoly.portfolio import HeldPosition, PortfolioStore
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent
from openpoly.wallet.runtime_state import runtime_state

logger = logging.getLogger(__name__)


class _PaperLike(Protocol):
    def configure(self, portfolio: PortfolioStore) -> None: ...
    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult: ...
    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason,
        ts: float,
        trigger: str | None = None,
    ) -> ExecResult: ...


class _LiveLike(Protocol):
    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult: ...
    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason,
        ts: float,
        trigger: str | None = None,
    ) -> ExecResult: ...


class ExecutorDispatcher:
    """Routes execute_* to paper or live based on runtime_state.exec_mode."""

    def __init__(
        self,
        paper: _PaperLike,
        live: _LiveLike | None = None,
    ) -> None:
        self._paper = paper
        self._live = live

    def configure_paper(self, portfolio: PortfolioStore) -> None:
        """Proxy to PaperExecutor.configure — called by lifespan once DB is up.
        Always called, regardless of current mode."""
        self._paper.configure(portfolio)

    def configure_live(self, live: _LiveLike) -> None:
        """Inject the live executor — called by lifespan after wallet + ClobClient
        are constructed. Idempotent; safe to call even when mode=paper (live is
        pre-built so a UI-driven flip is cheap)."""
        self._live = live

    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult:
        if runtime_state.exec_mode == "live":
            if self._live is None:
                logger.warning("dispatch buy: live mode but live executor unconfigured")
                return ExecResult.skip("live_not_ready")
            return self._live.execute_buy(intent, news_id=news_id, ts=ts)
        return self._paper.execute_buy(intent, news_id=news_id, ts=ts)

    def execute_sell(
        self,
        position: HeldPosition,
        *,
        close_reason,
        ts: float,
        trigger: str | None = None,
    ) -> ExecResult:
        if runtime_state.exec_mode == "live":
            if self._live is None:
                logger.warning("dispatch sell: live mode but live executor unconfigured")
                return ExecResult.skip("live_not_ready")
            return self._live.execute_sell(
                position, close_reason=close_reason, ts=ts, trigger=trigger
            )
        return self._paper.execute_sell(position, close_reason=close_reason, ts=ts, trigger=trigger)
