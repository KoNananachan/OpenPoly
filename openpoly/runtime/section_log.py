"""Per-section event log (v7 / P1).

Each section that produces observable runtime events (analyzer LLM calls,
entry decisions, …) owns its own ``SectionLogStore`` instance. The store is
a bounded ring + small derived counters + ``last_at`` timestamp. HTTP routes
read entries / counters; the pipeline orchestrator writes via ``append``.

The store is intentionally sync (no asyncio.Lock): under FastAPI's single
event loop the orchestrator worker is the only producer, and ``deque.append``
is atomic under CPython. Tests reset with ``reset()``.

Entry types are frozen dataclasses with ``to_dict()`` for HTTP serialization
and field sets sized down to UI summary needs (e.g. ``news_content_preview``
truncated to 80 chars upstream). Detail drawers (full LLM prompt / order
book snapshot) are deferred to v8.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Generic, Literal, TypeVar

E = TypeVar("E")

DEFAULT_MAXLEN = 200

# Mirrors ``Verdict`` in openpoly.sections._base; the same 4-bucket set is
# also used for orchestrator-level errors so counters stay aligned across
# the analyzer / entry logs.
VERDICTS = ("ok", "skip", "fail_open", "error")


Verdict = Literal["ok", "skip", "fail_open", "error"]


@dataclass(frozen=True)
class EmbeddingCall:
    """One embedding-filter invocation — the pipeline's first stage.

    ``candidate_count`` / ``top_market_id`` / ``top_score`` describe the
    ``MarketCandidates`` emitted on an ok verdict; they are 0 / None on a skip
    or error. ``error`` only set when verdict==error.
    """

    ts: float
    news_id: str
    news_content_preview: str
    urgency: str
    verdict: Verdict
    candidate_count: int
    top_market_id: str | None
    top_score: float | None
    catalog_size: int
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WarmEvent = Literal["warm", "model_load", "cache_load", "error"]


@dataclass(frozen=True)
class WarmCycle:
    """One embedding warm-cache event — the background catalog-embedding loop.

    Distinct from ``EmbeddingCall`` (the per-news-tick filter): this records the
    300s warm loop, the lazy model load, and the startup DB cache reload, so the
    inspector can show *why* a market is or isn't matchable yet — a market the
    warm loop has not embedded is silently skipped by ``match``. ``event``
    discriminates the kind; ``embedded_count`` is markets (re)embedded this
    cycle, ``warm_count`` the total warm afterwards. ``error`` only set when
    event==error.
    """

    ts: float
    event: WarmEvent
    embedded_count: int
    warm_count: int
    catalog_size: int
    latency_ms: int
    detail: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalyzerCall:
    """One analyzer invocation. ``error`` only set when verdict==error.

    ``rationale`` is the LLM's stated reason for the decision (the
    ``rationale`` field of the forced ``submit_analysis`` tool call). It is
    set on ok / fail_open outcomes when the model returned one, otherwise
    None. Surfaced in the PositionDetail UI by name-matching ``news_id``.
    """

    ts: float
    news_id: str
    news_content_preview: str
    urgency: str
    verdict: Verdict
    p_model: float | None
    confidence: str | None
    market_id: str | None
    latency_ms: int
    error: str | None = None
    rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntryDecision:
    """One entry invocation + its fill outcome.

    ``side`` / ``qty`` / ``price`` are the *decision* (the OrderIntent the
    section emitted — ``price`` an estimate); they are set only on an ok
    decision. ``fill_*`` are the *executor* result: ``fill_status`` is
    ``"filled"``, the executor's skip reason, or ``"error"``; it is None when
    the section produced no OrderIntent (the executor was never called).
    """

    ts: float
    news_id: str
    ar_p_model: float | None
    ar_market_id: str | None
    verdict: Verdict
    side: str | None
    qty: float | None
    price: float | None
    reason: str | None
    latency_ms: int
    error: str | None = None
    fill_status: str | None = None
    fill_price: float | None = None
    fill_qty: float | None = None
    position_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExitDecision:
    """One exit-monitor evaluation of an open position.

    ``trigger`` / ``fill_price`` / ``realized_pnl`` are set only when the
    position was actually closed (verdict==ok). ``return_pct`` is the
    position's return at evaluation time — also recorded on a
    within-thresholds skip. ``peak_price`` is the monitor's tracked peak for
    the position at this tick (None when no order book / unknown).
    ``error`` only set when verdict==error.
    """

    ts: float
    position_id: int
    market_id: str
    side: str
    verdict: Verdict
    trigger: str | None
    return_pct: float | None
    fill_price: float | None
    realized_pnl: float | None
    reason: str | None
    error: str | None = None
    peak_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SettlementDecision:
    """One settlement-monitor evaluation of an open position.

    Slice E: when the underlying market resolves, position closes at the
    settlement price (0.0 / 1.0) directly via PortfolioStore.close_position
    — no broker tx. ``final_price`` + ``realized_pnl`` are set on
    verdict==ok. ``reason`` carries the skip subkey on verdict==skip
    (e.g. ``still_trading``, ``no_outcome_prices``, ``ambiguous_outcome``)
    or the close trigger on ok (``settlement``). ``error`` only on
    verdict==error (network / DB failure during settle).
    """

    ts: float
    position_id: int
    market_id: str
    side: str
    verdict: Verdict
    final_price: float | None
    realized_pnl: float | None
    reason: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SectionLogStore(Generic[E]):
    """Ring + counters + last_at. Sync API."""

    def __init__(self, name: str, maxlen: int = DEFAULT_MAXLEN) -> None:
        self._name = name
        self._maxlen = maxlen
        self._events: deque[E] = deque(maxlen=maxlen)
        self._counters: dict[str, int] = {v: 0 for v in VERDICTS}
        self._last_at: float | None = None

    def __repr__(self) -> str:
        return (
            f"SectionLogStore(name={self._name}, "
            f"n_entries={len(self._events)}, last_at={self._last_at})"
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def maxlen(self) -> int:
        return self._maxlen

    @property
    def last_at(self) -> float | None:
        return self._last_at

    def append(self, entry: E) -> None:
        self._events.append(entry)
        verdict = getattr(entry, "verdict", None)
        if isinstance(verdict, str) and verdict in self._counters:
            self._counters[verdict] += 1
        ts = getattr(entry, "ts", None)
        if isinstance(ts, (int, float)):
            self._last_at = float(ts)

    def entries(self, limit: int | None = None) -> list[E]:
        all_entries = list(self._events)
        if limit is None or limit >= len(all_entries):
            return all_entries
        if limit <= 0:
            return []
        return all_entries[-limit:]

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def reset(self) -> None:
        """Test hook — clear ring + counters + last_at."""
        self._events.clear()
        self._counters = {v: 0 for v in VERDICTS}
        self._last_at = None


# Module-level singletons. Routes / orchestrator import these directly.
embedding_log: SectionLogStore[EmbeddingCall] = SectionLogStore("embedding")
analyzer_log: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
entry_log: SectionLogStore[EntryDecision] = SectionLogStore("entry")
exit_log: SectionLogStore[ExitDecision] = SectionLogStore("exit")
settlement_log: SectionLogStore[SettlementDecision] = SectionLogStore("settlement")
# Warm-cache events are heartbeat-frequent (one per warm interval) but only the
# recent tail matters, so this ring is smaller and the EmbeddingManager — not
# the orchestrator — is its producer.
embedding_warm_log: SectionLogStore[WarmCycle] = SectionLogStore("embedding_warm", maxlen=100)
