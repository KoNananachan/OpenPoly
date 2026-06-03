"""Pipeline orchestrator (v7 / P3, EM4 four-stage).

Event-driven serial worker. ``enqueue(item)`` is sync (called from ws_client's
on_item hook); a single asyncio worker drains the queue, runs ``embedding →
analyzer → entry`` per item, and appends per-step results into the section log
stores.

Concurrency model: **one worker, one queue**. LLM rate limits + LLM cost
favor serial over fan-out (micro-stakes paper). Queue is bounded; overflow drops
the newest item and logs a sentinel error entry so the user can see it on
the Calls tab.

Lifecycle is owned by ``PipelineOrchestrator.start() / stop()``. FastAPI
lifespan wires this in P4 (shutdown order: orchestrator first, manager
second).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from openpoly.embedding.models import MarketCandidates
from openpoly.news.ring_buffer import NewsItem
from openpoly.runtime.section_log import (
    AnalyzerCall,
    EmbeddingCall,
    EntryDecision,
    SectionLogStore,
)
from openpoly.sections._base import SectionInput, SectionOutput
from openpoly.execution import ExecResult, executor
from openpoly.sections.analyzer.llm_v0 import AnalysisResult
from openpoly.sections.entry.edge_threshold_v0 import OrderIntent

logger = logging.getLogger(__name__)


DEFAULT_QUEUE_MAXSIZE = 100


class _SyncSection(Protocol):
    """Minimal section shape used by the orchestrator."""

    def run(self, input: SectionInput) -> SectionOutput: ...


class _Executor(Protocol):
    """Minimal executor shape used by the orchestrator."""

    def execute_buy(self, intent: OrderIntent, *, news_id: str | None, ts: float) -> ExecResult: ...


State = Literal["stopped", "running"]


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        embedding_section: _SyncSection,
        analyzer_section: _SyncSection,
        entry_section: _SyncSection,
        executor: _Executor,
        embedding_log_store: SectionLogStore[EmbeddingCall],
        analyzer_log_store: SectionLogStore[AnalyzerCall],
        entry_log_store: SectionLogStore[EntryDecision],
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self._embedding = embedding_section
        self._analyzer = analyzer_section
        self._entry = entry_section
        self._executor = executor
        self._embedding_log = embedding_log_store
        self._analyzer_log = analyzer_log_store
        self._entry_log = entry_log_store
        self._queue: asyncio.Queue[NewsItem] = asyncio.Queue(maxsize=queue_maxsize)
        self._worker_task: asyncio.Task[None] | None = None
        self._state: State = "stopped"
        # canvas-sync v2: lock for atomic section swap (called from
        # /api/canvas/template PUT handler when a section's config changes).
        # In-flight section.run(...) keeps its own reference; Python GC holds
        # the old instance alive until that call returns. Next call uses the
        # new instance.
        self._sections_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"PipelineOrchestrator(state={self._state}, queue_depth={self.queue_depth})"

    # ---------- read-only properties ----------

    @property
    def state(self) -> State:
        return self._state

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._state = "running"
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task is None:
            self._state = "stopped"
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        finally:
            self._worker_task = None
            self._state = "stopped"

    # ---------- enqueue (sync, called from ws_client hook) ----------

    def enqueue(self, item: NewsItem) -> bool:
        """Returns True if accepted, False if dropped due to queue full.

        Overflow drops the **newest** (this item) — older items in the queue
        keep their slot. This matches plan §OD1 and is simpler than peeking
        + evicting oldest. The dropped item is recorded as an error entry in
        ``embedding_log`` (the pipeline's first stage) so it's not silently
        lost.
        """
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._embedding_log.append(
                EmbeddingCall(
                    ts=time.time(),
                    news_id=item.id,
                    news_content_preview=item.content[:80],
                    urgency=item.urgency,
                    verdict="error",
                    candidate_count=0,
                    top_market_id=None,
                    top_score=None,
                    catalog_size=0,
                    latency_ms=0,
                    error=f"queue_overflow (depth={self._queue.qsize()})",
                )
            )
            logger.warning("orchestrator queue full; dropped news_id=%s", item.id)
            return False

    # ---------- internals ----------

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._process(item)
            except Exception:  # noqa: BLE001 — defense-in-depth; _process catches
                logger.exception(
                    "orchestrator: unexpected error after _process for %s",
                    item.id,
                )
            finally:
                self._queue.task_done()

    async def _process(self, item: NewsItem) -> None:
        candidates = self._run_embedding(item)
        if candidates is None:
            return
        ar_payload = await self._run_analyzer(item, candidates)
        if ar_payload is None:
            return
        await self._run_entry(item, ar_payload)

    def _run_embedding(self, item: NewsItem) -> MarketCandidates | None:
        """Stage 1 — narrow the market catalog for this news item.

        Returns the ``MarketCandidates`` on ok, or None on skip / error (the
        pipeline then stops without calling the analyzer).
        """
        ts = time.time()
        start = time.monotonic()
        verdict: str
        error: str | None
        out: SectionOutput | None
        try:
            out = self._embedding.run(SectionInput(tick_type="event", payload=item))
            verdict = str(out.verdict)
            error = out.reason if verdict == "error" else None
        except Exception as exc:  # noqa: BLE001 — section impl is user code
            out = None
            verdict = "error"
            error = repr(exc)[:200]
        latency_ms = int((time.monotonic() - start) * 1000)

        candidates = (
            out.payload if out is not None and isinstance(out.payload, MarketCandidates) else None
        )
        signals = out.signals if out is not None else {}
        top = candidates.candidates[0] if candidates is not None and candidates.candidates else None

        self._embedding_log.append(
            EmbeddingCall(
                ts=ts,
                news_id=item.id,
                news_content_preview=item.content[:80],
                urgency=item.urgency,
                verdict=verdict,  # type: ignore[arg-type]
                candidate_count=(len(candidates.candidates) if candidates is not None else 0),
                top_market_id=top.market.market_id if top is not None else None,
                top_score=top.score if top is not None else None,
                catalog_size=int(signals.get("catalog_size", 0) or 0),
                latency_ms=latency_ms,
                error=error,
            )
        )

        if verdict != "ok" or candidates is None:
            return None
        return candidates

    async def _run_analyzer(
        self, item: NewsItem, candidates: MarketCandidates
    ) -> AnalysisResult | None:
        """Stage 2 — analyze the narrowed candidate set.

        ``item`` supplies the news fields for the log entry; ``candidates`` is
        the section payload. Returns the AnalysisResult on ok, else None.

        The analyzer makes a blocking LLM API call, so its ``run()`` is
        offloaded to a worker thread — the event loop stays free for the WS
        reconnect / market-poll tasks (docs/architecture/05).
        """
        ts = time.time()
        start = time.monotonic()
        verdict: str
        error: str | None
        out: SectionOutput | None
        try:
            out = await asyncio.to_thread(
                self._analyzer.run,
                SectionInput(tick_type="event", payload=candidates),
            )
            verdict = str(out.verdict)
            error = out.reason if verdict == "error" else None
        except Exception as exc:  # noqa: BLE001 — section impl is user code
            out = None
            verdict = "error"
            error = repr(exc)[:200]
        latency_ms = int((time.monotonic() - start) * 1000)

        ar = out.payload if out is not None and isinstance(out.payload, AnalysisResult) else None

        self._analyzer_log.append(
            AnalyzerCall(
                ts=ts,
                news_id=item.id,
                news_content_preview=item.content[:80],
                urgency=item.urgency,
                verdict=verdict,  # type: ignore[arg-type]
                p_model=ar.p_model if ar is not None else None,
                confidence=ar.confidence if ar is not None else None,
                market_id=ar.market_id if ar is not None else None,
                latency_ms=latency_ms,
                error=error,
                # PD1: surface the LLM's stated reason for the decision so
                # PositionDetail UI can show it next to the position. Empty
                # string from the model is also informative ("intentionally
                # said nothing") — keep it; only None when ar itself is None.
                rationale=ar.rationale if ar is not None else None,
            )
        )

        # Only forward to entry on ok + valid AR payload.
        if verdict != "ok" or ar is None:
            return None
        return ar

    async def _run_entry(self, item: NewsItem, ar: AnalysisResult) -> None:
        """Stage 3 — entry decision + execution.

        The entry section may do a blocking HTTP fetch (the late-buy veto), so
        its ``run()`` is offloaded to a worker thread; the executor's DB write
        stays inline (sub-millisecond — see PF5 risk notes).
        """
        ts = time.time()
        start = time.monotonic()
        verdict: str
        reason: str | None
        error: str | None
        intent: OrderIntent | None = None
        try:
            out = await asyncio.to_thread(
                self._entry.run,
                SectionInput(tick_type="event", payload=ar),
            )
            verdict = str(out.verdict)
            reason = out.reason
            error = out.reason if verdict == "error" else None
            if verdict == "ok" and isinstance(out.payload, OrderIntent):
                intent = out.payload
        except Exception as exc:  # noqa: BLE001 — section impl is user code
            verdict = "error"
            reason = None
            error = repr(exc)[:200]

        # Execution stage — only when the section produced an OrderIntent.
        fill_status: str | None = None
        fill_price: float | None = None
        fill_qty: float | None = None
        position_id: int | None = None
        if intent is not None:
            try:
                result = self._executor.execute_buy(intent, news_id=item.id, ts=ts)
                if result.filled:
                    fill_status = "filled"
                    fill_price = result.price
                    fill_qty = result.qty
                    position_id = result.position_id
                else:
                    fill_status = result.skip_reason
            except Exception as exc:  # noqa: BLE001 — DB write may raise
                verdict = "error"
                error = repr(exc)[:200]
                fill_status = "error"

        latency_ms = int((time.monotonic() - start) * 1000)

        self._entry_log.append(
            EntryDecision(
                ts=ts,
                news_id=item.id,
                ar_p_model=ar.p_model,
                ar_market_id=ar.market_id,
                verdict=verdict,  # type: ignore[arg-type]
                side=intent.side if intent is not None else None,
                qty=intent.qty if intent is not None else None,
                price=intent.price if intent is not None else None,
                reason=reason,
                latency_ms=latency_ms,
                error=error,
                fill_status=fill_status,
                fill_price=fill_price,
                fill_qty=fill_qty,
                position_id=position_id,
            )
        )


# Module-level singleton built lazily so tests can substitute. Real
# wire-up (with manager hook) lives in main.py's lifespan (P4).
_singleton: PipelineOrchestrator | None = None

_C = TypeVar("_C", bound=BaseModel)


def _canvas_config(config_cls: type[_C], section_type: str) -> _C:
    """Build a section Config from the persisted canvas node config, falling
    back to the Config's own defaults on a missing node or invalid values — so
    a stale or hand-broken canvas can never block pipeline startup."""
    from openpoly.runtime.canvas_store import section_config

    raw = section_config(section_type)
    if not raw:
        return config_cls()
    try:
        return config_cls(**raw)
    except Exception as exc:  # noqa: BLE001 — bad canvas config must not break startup
        logger.warning(
            "invalid canvas config for section %r (%s); using defaults",
            section_type,
            exc,
        )
        return config_cls()


def get_orchestrator() -> PipelineOrchestrator:
    """Lazily build the pipeline orchestrator. Each section's params come from
    the persisted canvas (``canvas_store``); restart the backend to apply a
    canvas edit, same as picking up a new section impl."""
    global _singleton
    if _singleton is None:
        from openpoly.runtime.section_log import (
            analyzer_log,
            embedding_log,
            entry_log,
        )
        from openpoly.sections.analyzer.llm_v0 import (
            LLMAnalyzerConfig,
            LLMAnalyzerV0,
        )
        from openpoly.sections.embedding.minilm_v0 import (
            EmbeddingFilterConfig,
            EmbeddingFilterV0,
        )
        from openpoly.sections.entry.edge_threshold_v0 import (
            EdgeThresholdConfig,
            EdgeThresholdEntryV0,
        )

        _singleton = PipelineOrchestrator(
            embedding_section=EmbeddingFilterV0(_canvas_config(EmbeddingFilterConfig, "embedding")),
            analyzer_section=LLMAnalyzerV0(_canvas_config(LLMAnalyzerConfig, "analyzer")),
            entry_section=EdgeThresholdEntryV0(
                _canvas_config(EdgeThresholdConfig, "entry"),
                # Lazy: executor's portfolio is configured *after* the
                # orchestrator (and entry section) is built, so we hand
                # entry a closure it calls per run() instead of the store
                # itself. Returns None until executor.configure() lands.
                portfolio_provider=lambda: getattr(
                    getattr(executor, "_paper", executor), "_portfolio", None
                ),
            ),
            executor=executor,
            embedding_log_store=embedding_log,
            analyzer_log_store=analyzer_log,
            entry_log_store=entry_log,
        )
    return _singleton


def _reset_singleton_for_tests() -> None:
    global _singleton
    _singleton = None


# ---------- canvas-sync v2: hot-reload section swap ----------


async def replace_section(section_type: str, new_inst: _SyncSection) -> None:
    """Module-level entry point — atomically replace a section in the running
    orchestrator (if any). No-op when no singleton has been built yet (e.g.
    PUT arrived before first news; the orchestrator's next lazy build will
    read the updated canvas anyway).

    Callers: ``api/canvas_routes._apply_canvas_reload`` after a PUT diff
    detects a section's config changed. Build the new instance in the
    caller so failures there don't pollute the orchestrator with a
    half-constructed object.
    """
    if _singleton is None:
        return
    await _singleton.replace_section(section_type, new_inst)


# Patch the class with the swap method (kept here, not next to __init__,
# so the lock-protected reload code path is co-located with the module-level
# helper above for a single point of canvas-sync logic to review).
async def _replace_section_impl(
    self: PipelineOrchestrator, section_type: str, new_inst: _SyncSection
) -> None:
    async with self._sections_lock:
        if section_type == "embedding":
            self._embedding = new_inst
        elif section_type == "analyzer":
            self._analyzer = new_inst
        elif section_type == "entry":
            self._entry = new_inst
        else:
            raise ValueError(f"unknown orchestrator section_type: {section_type!r}")


PipelineOrchestrator.replace_section = _replace_section_impl  # type: ignore[attr-defined]
