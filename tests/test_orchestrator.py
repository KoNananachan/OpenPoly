"""Unit tests for PipelineOrchestrator (v7 / P3, EM4 four-stage).

Uses fake embedding / analyzer / entry sections injected via constructor — no
real section impls, no model load, no LLM calls, deterministic. Each test gets
its own log stores so state doesn't leak between cases.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openpoly.embedding.models import MarketCandidate, MarketCandidates
from openpoly.execution import ExecResult
from openpoly.markets.models import Market
from openpoly.news.ring_buffer import NewsItem
from openpoly.runtime.orchestrator import PipelineOrchestrator
from openpoly.runtime.section_log import (
    AnalyzerCall,
    EmbeddingCall,
    EntryDecision,
    SectionLogStore,
)
from openpoly.sections._base import SectionInput, SectionOutput
from openpoly.sections.analyzer.llm_v0 import AnalysisResult
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent


# ---------- fakes ----------


def _market(market_id: str = "m1") -> Market:
    return Market(
        market_id=market_id,
        condition_id=f"c-{market_id}",
        question=f"question {market_id}",
        slug=market_id,
        yes_token_id=f"y-{market_id}",
        no_token_id=None,
        end_date=None,
        best_bid=None,
        best_ask=None,
        spread=None,
        last_trade_price=None,
        volume_24h=0.0,
        liquidity=0.0,
        taker_fee_rate=None,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id=None,
        event_title=None,
        event_tags=(),
    )


class FakeEmbedding:
    """Returns canned MarketCandidates wrapping the input NewsItem, or raises
    if ``raise_exc`` is set. A non-ok verdict yields an empty-payload skip."""

    def __init__(
        self,
        *,
        verdict: str = "ok",
        raise_exc: Exception | None = None,
    ) -> None:
        self._verdict = verdict
        self._raise = raise_exc
        self.call_count = 0

    def run(self, input: SectionInput) -> SectionOutput:
        self.call_count += 1
        if self._raise is not None:
            raise self._raise
        if self._verdict == "ok":
            return SectionOutput(
                payload=MarketCandidates(
                    news=input.payload,
                    candidates=[MarketCandidate(market=_market(), score=0.9)],
                ),
                verdict="ok",
                signals={"catalog_size": 1, "candidate_count": 1},
            )
        return SectionOutput(
            payload=None,
            verdict=self._verdict,  # type: ignore[arg-type]
            reason="canned embedding reason",
        )


class FakeAnalyzer:
    """Returns canned SectionOutput, or raises if ``raise_exc`` is set."""

    def __init__(
        self,
        *,
        verdict: str = "ok",
        p_model: float = 0.55,
        raise_exc: Exception | None = None,
    ) -> None:
        self._verdict = verdict
        self._p_model = p_model
        self._raise = raise_exc
        self.call_count = 0

    def run(self, input: SectionInput) -> SectionOutput:
        self.call_count += 1
        if self._raise is not None:
            raise self._raise
        if self._verdict == "ok":
            return SectionOutput(
                payload=AnalysisResult(
                    market_id="m1",
                    p_model=self._p_model,
                    confidence="medium",
                ),
                verdict="ok",
            )
        return SectionOutput(
            payload=None,
            verdict=self._verdict,  # type: ignore[arg-type]
            reason="canned reason",
        )


class FakeEntry:
    def __init__(
        self,
        *,
        verdict: str = "ok",
        raise_exc: Exception | None = None,
    ) -> None:
        self._verdict = verdict
        self._raise = raise_exc
        self.call_count = 0

    def run(self, input: SectionInput) -> SectionOutput:
        self.call_count += 1
        if self._raise is not None:
            raise self._raise
        if self._verdict == "ok":
            return SectionOutput(
                payload=OrderIntent(
                    market_id="m1",
                    side="yes",
                    price=0.5,
                    qty=20.0,
                ),
                verdict="ok",
            )
        return SectionOutput(
            payload=None,
            verdict=self._verdict,  # type: ignore[arg-type]
            reason="canned entry reason",
        )


class FakeExecutor:
    """Canned executor: records calls, returns a filled result or a skip, or
    raises if ``raise_exc`` is set."""

    def __init__(
        self,
        *,
        filled: bool = True,
        skip_reason: str = "skipped",
        raise_exc: Exception | None = None,
    ) -> None:
        self._filled = filled
        self._skip_reason = skip_reason
        self._raise = raise_exc
        self.call_count = 0

    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult:
        self.call_count += 1
        if self._raise is not None:
            raise self._raise
        if self._filled:
            return ExecResult.ok(price=0.43, qty=intent.qty, position_id=1)
        return ExecResult.skip(self._skip_reason)


def _item(news_id: str = "n1", urgency: str = "high") -> NewsItem:
    return NewsItem(
        id=news_id,
        content=f"content of {news_id}",
        urgency=urgency,
        sentiment=None,
        published_at=0.0,
        received_at=0.0,
    )


def make_orchestrator(
    *,
    embedding: Any | None = None,
    analyzer: Any | None = None,
    entry: Any | None = None,
    executor: Any | None = None,
    queue_maxsize: int = 100,
) -> tuple[
    PipelineOrchestrator,
    SectionLogStore[EmbeddingCall],
    SectionLogStore[AnalyzerCall],
    SectionLogStore[EntryDecision],
]:
    emb_log: SectionLogStore[EmbeddingCall] = SectionLogStore("embedding")
    a_log: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    e_log: SectionLogStore[EntryDecision] = SectionLogStore("entry")
    orch = PipelineOrchestrator(
        embedding_section=embedding or FakeEmbedding(),
        analyzer_section=analyzer or FakeAnalyzer(),
        entry_section=entry or FakeEntry(),
        executor=executor or FakeExecutor(),
        embedding_log_store=emb_log,
        analyzer_log_store=a_log,
        entry_log_store=e_log,
        queue_maxsize=queue_maxsize,
    )
    return orch, emb_log, a_log, e_log


async def _drain(orch: PipelineOrchestrator, timeout: float = 1.0) -> None:
    """Wait until the queue is empty (worker has consumed everything)."""

    async def _wait() -> None:
        while orch.queue_depth > 0:
            await asyncio.sleep(0.01)
        # Yield once more so the worker can finish processing the last item
        await asyncio.sleep(0.02)

    await asyncio.wait_for(_wait(), timeout=timeout)


# ---------- state ----------


def test_initial_state_stopped() -> None:
    orch, _, _, _ = make_orchestrator()
    assert orch.state == "stopped"
    assert orch.queue_depth == 0


async def test_start_transitions_to_running() -> None:
    orch, _, _, _ = make_orchestrator()
    await orch.start()
    try:
        assert orch.state == "running"
    finally:
        await orch.stop()
    assert orch.state == "stopped"


async def test_start_is_idempotent() -> None:
    orch, _, _, _ = make_orchestrator()
    await orch.start()
    first_task = orch._worker_task  # noqa: SLF001
    await orch.start()
    assert orch._worker_task is first_task  # noqa: SLF001
    await orch.stop()


async def test_stop_when_stopped_is_noop() -> None:
    orch, _, _, _ = make_orchestrator()
    await orch.stop()
    assert orch.state == "stopped"


# ---------- happy path ----------


async def test_happy_path_runs_all_three_stages() -> None:
    embedding = FakeEmbedding()
    analyzer = FakeAnalyzer()
    entry = FakeEntry()
    orch, emb_log, a_log, e_log = make_orchestrator(
        embedding=embedding, analyzer=analyzer, entry=entry
    )
    await orch.start()
    try:
        accepted = orch.enqueue(_item("n1"))
        assert accepted is True
        await _drain(orch)
    finally:
        await orch.stop()
    assert embedding.call_count == 1
    assert analyzer.call_count == 1
    assert entry.call_count == 1

    assert len(emb_log.entries()) == 1
    emb_entry = emb_log.entries()[0]
    assert emb_entry.news_id == "n1"
    assert emb_entry.verdict == "ok"
    assert emb_entry.candidate_count == 1
    assert emb_entry.top_market_id == "m1"
    assert emb_entry.catalog_size == 1

    assert len(a_log.entries()) == 1
    a_entry = a_log.entries()[0]
    assert a_entry.news_id == "n1"
    assert a_entry.verdict == "ok"
    assert a_entry.p_model == 0.55
    assert a_entry.market_id == "m1"
    assert a_entry.latency_ms >= 0

    assert len(e_log.entries()) == 1
    e_entry = e_log.entries()[0]
    assert e_entry.news_id == "n1"
    assert e_entry.verdict == "ok"
    assert e_entry.side == "yes"
    assert e_entry.qty == 20.0
    assert e_entry.price == 0.5
    assert e_entry.ar_p_model == 0.55
    # Executor stage filled the intent.
    assert e_entry.fill_status == "filled"
    assert e_entry.fill_price == 0.43
    assert e_entry.fill_qty == 20.0
    assert e_entry.position_id == 1


async def test_multiple_items_processed_in_order() -> None:
    orch, emb_log, a_log, e_log = make_orchestrator()
    await orch.start()
    try:
        for i in range(5):
            orch.enqueue(_item(f"n{i}"))
        await _drain(orch)
    finally:
        await orch.stop()
    emb_ids = [e.news_id for e in emb_log.entries()]
    a_ids = [e.news_id for e in a_log.entries()]
    e_ids = [e.news_id for e in e_log.entries()]
    assert emb_ids == ["n0", "n1", "n2", "n3", "n4"]
    assert a_ids == ["n0", "n1", "n2", "n3", "n4"]
    assert e_ids == ["n0", "n1", "n2", "n3", "n4"]


# ---------- skip path ----------


async def test_embedding_skip_no_analyzer_call() -> None:
    embedding = FakeEmbedding(verdict="skip")
    analyzer = FakeAnalyzer()
    entry = FakeEntry()
    orch, emb_log, a_log, e_log = make_orchestrator(
        embedding=embedding, analyzer=analyzer, entry=entry
    )
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    assert analyzer.call_count == 0
    assert entry.call_count == 0
    assert len(emb_log.entries()) == 1
    assert emb_log.entries()[0].verdict == "skip"
    assert len(a_log.entries()) == 0
    assert len(e_log.entries()) == 0


async def test_analyzer_skip_no_entry_call() -> None:
    analyzer = FakeAnalyzer(verdict="skip")
    entry = FakeEntry()
    orch, _, a_log, e_log = make_orchestrator(analyzer=analyzer, entry=entry)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    assert entry.call_count == 0
    assert len(a_log.entries()) == 1
    assert a_log.entries()[0].verdict == "skip"
    assert len(e_log.entries()) == 0


async def test_analyzer_fail_open_no_entry_call() -> None:
    # fail_open is a "pass-through but mark dark" verdict in our protocol;
    # for v7 orchestrator we treat it the same as skip — no entry call,
    # but log entry preserves the verdict.
    analyzer = FakeAnalyzer(verdict="fail_open")
    entry = FakeEntry()
    orch, _, a_log, e_log = make_orchestrator(analyzer=analyzer, entry=entry)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    assert entry.call_count == 0
    assert a_log.entries()[0].verdict == "fail_open"
    assert len(e_log.entries()) == 0


# ---------- error paths ----------


async def test_analyzer_raises_logs_error_and_continues() -> None:
    class OnceRaisingAnalyzer:
        """Raises on first call, then behaves normally — proves the worker
        survives a section exception and keeps processing later items."""

        def __init__(self) -> None:
            self.call_count = 0

        def run(self, input: SectionInput) -> SectionOutput:
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("analyzer boom")
            return SectionOutput(
                payload=AnalysisResult(market_id="m1", p_model=0.55, confidence="medium"),
                verdict="ok",
            )

    analyzer = OnceRaisingAnalyzer()
    entry = FakeEntry()
    orch, _, a_log, e_log = make_orchestrator(analyzer=analyzer, entry=entry)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        orch.enqueue(_item("n2"))
        await _drain(orch)
    finally:
        await orch.stop()
    assert len(a_log.entries()) == 2
    err_entry = a_log.entries()[0]
    assert err_entry.news_id == "n1"
    assert err_entry.verdict == "error"
    assert err_entry.error is not None and "analyzer boom" in err_entry.error
    ok_entry = a_log.entries()[1]
    assert ok_entry.news_id == "n2"
    assert ok_entry.verdict == "ok"
    # Entry only ran for n2.
    assert entry.call_count == 1
    assert e_log.entries()[0].news_id == "n2"


async def test_entry_raises_logs_error() -> None:
    bad = FakeEntry(raise_exc=RuntimeError("entry boom"))
    orch, _, a_log, e_log = make_orchestrator(entry=bad)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    assert a_log.entries()[0].verdict == "ok"
    assert len(e_log.entries()) == 1
    e_entry = e_log.entries()[0]
    assert e_entry.verdict == "error"
    assert e_entry.error is not None and "entry boom" in e_entry.error
    assert e_entry.side is None
    assert e_entry.qty is None


async def test_entry_fill_skipped_records_skip_reason() -> None:
    # Section decided ok, but the executor skipped (e.g. position already open).
    orch, _, _, e_log = make_orchestrator(
        executor=FakeExecutor(filled=False, skip_reason="position_exists")
    )
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    e_entry = e_log.entries()[0]
    assert e_entry.verdict == "ok"  # the decision itself was ok
    assert e_entry.fill_status == "position_exists"
    assert e_entry.fill_price is None
    assert e_entry.position_id is None


async def test_executor_raises_logs_error() -> None:
    bad = FakeExecutor(raise_exc=RuntimeError("database is locked"))
    orch, _, _, e_log = make_orchestrator(executor=bad)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    e_entry = e_log.entries()[0]
    assert e_entry.verdict == "error"
    assert e_entry.error is not None and "database is locked" in e_entry.error
    assert e_entry.fill_status == "error"
    assert e_entry.side == "yes"  # the decision is still recorded


async def test_analyzer_returns_error_verdict() -> None:
    analyzer = FakeAnalyzer(verdict="error")
    entry = FakeEntry()
    orch, _, a_log, e_log = make_orchestrator(analyzer=analyzer, entry=entry)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    assert a_log.entries()[0].verdict == "error"
    assert a_log.entries()[0].error == "canned reason"
    assert entry.call_count == 0


# ---------- queue overflow ----------


async def test_queue_overflow_drops_newest_and_logs() -> None:
    # Orchestrator not started → nothing drains; fill past capacity.
    orch, emb_log, _, _ = make_orchestrator(queue_maxsize=2)
    assert orch.enqueue(_item("n1")) is True
    assert orch.enqueue(_item("n2")) is True
    # Third overflows: dropped + recorded on the embedding log (first stage).
    assert orch.enqueue(_item("n3")) is False
    err = emb_log.entries()[0]
    assert err.news_id == "n3"
    assert err.verdict == "error"
    assert err.error is not None and "queue_overflow" in err.error


# ---------- defensive: invalid payload from analyzer ----------


async def test_analyzer_returns_ok_but_non_AR_payload_skips_entry() -> None:
    class Weird:
        def run(self, input: SectionInput) -> SectionOutput:
            return SectionOutput(payload={"not": "an AR"}, verdict="ok")

    entry = FakeEntry()
    orch, _, a_log, e_log = make_orchestrator(analyzer=Weird(), entry=entry)
    await orch.start()
    try:
        orch.enqueue(_item("n1"))
        await _drain(orch)
    finally:
        await orch.stop()
    # Analyzer logged but with None fields (no valid AR)
    assert a_log.entries()[0].p_model is None
    # Entry never called
    assert entry.call_count == 0
    assert len(e_log.entries()) == 0
