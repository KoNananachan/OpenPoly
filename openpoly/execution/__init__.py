"""Execution layer — paper + live order fill, routed by runtime mode.

Production callers import ``executor`` from this package and call
``execute_buy`` / ``execute_sell``. The exported ``executor`` is an
``ExecutorDispatcher`` that holds a ``PaperExecutor`` (always) and a
``LiveExecutor`` (configured by lifespan once wallet + ClobClient are up).
"""

from __future__ import annotations

from openpoly.execution.dispatcher import ExecutorDispatcher
from openpoly.execution.executor import PaperExecutor
from openpoly.execution.types import ExecResult

# Module-level singleton. Lifespan calls executor.configure_paper(portfolio)
# and (optionally) executor.configure_live(live_executor).
executor = ExecutorDispatcher(paper=PaperExecutor())

__all__ = ["ExecResult", "PaperExecutor", "ExecutorDispatcher", "executor"]
