"""Tests for ExecutorDispatcher — routes by runtime_state.exec_mode."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# Force-load the submodule before package __init__ shadows it (same trick as
# in the deprecated test_executor_live_mode.py).
importlib.import_module("openpoly.execution.dispatcher")
dispatcher_module = sys.modules["openpoly.execution.dispatcher"]

from openpoly.execution import ExecResult  # noqa: E402
from openpoly.execution.dispatcher import ExecutorDispatcher  # noqa: E402
from openpoly.portfolio import HeldPosition  # noqa: E402
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent  # noqa: E402
from openpoly.wallet.runtime_state import RuntimeState  # noqa: E402


@pytest.fixture
def fresh_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENPOLY_RUNTIME_STATE", str(tmp_path / "runtime.json"))
    rs = RuntimeState()
    rs.load()
    monkeypatch.setattr(dispatcher_module, "runtime_state", rs)
    return rs


@dataclass
class _Calls:
    paper_buys: int = 0
    paper_sells: int = 0
    live_buys: int = 0
    live_sells: int = 0


class _FakeExecutor:
    """Records calls; returns a canned ok result."""

    def __init__(self, calls: _Calls, kind: str) -> None:
        self._calls = calls
        self._kind = kind

    def execute_buy(self, intent, *, news_id, ts):
        if self._kind == "paper":
            self._calls.paper_buys += 1
        else:
            self._calls.live_buys += 1
        return ExecResult.ok(price=0.5, qty=10.0, position_id=1)

    def execute_sell(self, position, *, close_reason, ts, trigger=None):
        if self._kind == "paper":
            self._calls.paper_sells += 1
        else:
            self._calls.live_sells += 1
        return ExecResult.ok(price=0.5, qty=10.0, position_id=position.position_id)

    def configure(self, portfolio) -> None:
        pass  # paper executor stub


def _intent() -> OrderIntent:
    return OrderIntent(market_id="m1", side="yes", price=0.5, qty=10.0)


def _held() -> HeldPosition:
    return HeldPosition(
        position_id=1,
        market_id="m1",
        side="yes",
        token_id="t1",
        condition_id="0xm1",
        qty=10.0,
        avg_entry_price=0.5,
        opened_at=1.0,
    )


def test_paper_mode_routes_to_paper(fresh_runtime_state) -> None:
    assert fresh_runtime_state.exec_mode == "paper"
    calls = _Calls()
    d = ExecutorDispatcher(
        paper=_FakeExecutor(calls, "paper"),
        live=_FakeExecutor(calls, "live"),
    )
    d.execute_buy(_intent(), news_id="n", ts=1.0)
    d.execute_sell(_held(), close_reason="manual", ts=2.0)
    assert calls.paper_buys == 1 and calls.paper_sells == 1
    assert calls.live_buys == 0 and calls.live_sells == 0


def test_live_mode_routes_to_live(fresh_runtime_state) -> None:
    fresh_runtime_state.set_mode("live")
    calls = _Calls()
    d = ExecutorDispatcher(
        paper=_FakeExecutor(calls, "paper"),
        live=_FakeExecutor(calls, "live"),
    )
    d.execute_buy(_intent(), news_id="n", ts=1.0)
    d.execute_sell(_held(), close_reason="manual", ts=2.0)
    assert calls.live_buys == 1 and calls.live_sells == 1
    assert calls.paper_buys == 0 and calls.paper_sells == 0


def test_live_mode_without_live_executor_returns_live_not_ready(
    fresh_runtime_state,
) -> None:
    fresh_runtime_state.set_mode("live")
    calls = _Calls()
    d = ExecutorDispatcher(paper=_FakeExecutor(calls, "paper"), live=None)
    r1 = d.execute_buy(_intent(), news_id="n", ts=1.0)
    r2 = d.execute_sell(_held(), close_reason="manual", ts=2.0)
    assert not r1.filled and r1.skip_reason == "live_not_ready"
    assert not r2.filled and r2.skip_reason == "live_not_ready"
    assert calls.paper_buys == 0 and calls.live_buys == 0


def test_configure_live_late_injection(fresh_runtime_state) -> None:
    fresh_runtime_state.set_mode("live")
    calls = _Calls()
    d = ExecutorDispatcher(paper=_FakeExecutor(calls, "paper"), live=None)
    r = d.execute_buy(_intent(), news_id="n", ts=1.0)
    assert r.skip_reason == "live_not_ready"
    d.configure_live(_FakeExecutor(calls, "live"))
    r = d.execute_buy(_intent(), news_id="n", ts=2.0)
    assert r.filled and calls.live_buys == 1


def test_configure_paper_proxies_to_paper_executor(fresh_runtime_state) -> None:
    configured_with = {"obj": None}

    class _PaperRecorder(_FakeExecutor):
        def configure(self, portfolio):
            configured_with["obj"] = portfolio

    paper = _PaperRecorder(_Calls(), "paper")
    d = ExecutorDispatcher(paper=paper)
    sentinel = object()
    d.configure_paper(sentinel)  # type: ignore[arg-type]
    assert configured_with["obj"] is sentinel
