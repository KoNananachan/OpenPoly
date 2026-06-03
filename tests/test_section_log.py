"""Unit tests for SectionLogStore (v7 P1).

Tests instantiate their own stores via ``SectionLogStore(name, maxlen=...)``
so the module-level ``analyzer_log`` / ``entry_log`` singletons stay clean.
"""

from __future__ import annotations

from dataclasses import asdict

from openpoly.runtime.section_log import (
    DEFAULT_MAXLEN,
    VERDICTS,
    AnalyzerCall,
    EmbeddingCall,
    EntryDecision,
    ExitDecision,
    SettlementDecision,
    SectionLogStore,
    analyzer_log,
    embedding_log,
    entry_log,
    exit_log,
    settlement_log,
)


def _call(ts: float = 1.0, verdict: str = "ok", news_id: str = "n1") -> AnalyzerCall:
    return AnalyzerCall(
        ts=ts,
        news_id=news_id,
        news_content_preview="hello world",
        urgency="high",
        verdict=verdict,  # type: ignore[arg-type]
        p_model=0.55 if verdict == "ok" else None,
        confidence="medium" if verdict == "ok" else None,
        market_id="m1" if verdict == "ok" else None,
        latency_ms=42,
        error=None if verdict != "error" else "boom",
    )


def _decision(ts: float = 1.0, verdict: str = "ok", news_id: str = "n1") -> EntryDecision:
    return EntryDecision(
        ts=ts,
        news_id=news_id,
        ar_p_model=0.6,
        ar_market_id="m1",
        verdict=verdict,  # type: ignore[arg-type]
        side="yes" if verdict == "ok" else None,
        qty=10.0 if verdict == "ok" else None,
        price=0.5 if verdict == "ok" else None,
        reason=None if verdict == "ok" else "skip reason",
        latency_ms=20,
        error=None if verdict != "error" else "entry boom",
    )


def _embedding_call(ts: float = 1.0, verdict: str = "ok", news_id: str = "n1") -> EmbeddingCall:
    return EmbeddingCall(
        ts=ts,
        news_id=news_id,
        news_content_preview="hello world",
        urgency="high",
        verdict=verdict,  # type: ignore[arg-type]
        candidate_count=3 if verdict == "ok" else 0,
        top_market_id="m1" if verdict == "ok" else None,
        top_score=0.91 if verdict == "ok" else None,
        catalog_size=120,
        latency_ms=12,
        error=None if verdict != "error" else "embedding boom",
    )


def _exit_decision(ts: float = 1.0, verdict: str = "ok", position_id: int = 1) -> ExitDecision:
    return ExitDecision(
        ts=ts,
        position_id=position_id,
        market_id="m1",
        side="yes",
        verdict=verdict,  # type: ignore[arg-type]
        trigger="take_profit" if verdict == "ok" else None,
        return_pct=0.23 if verdict == "ok" else 0.04,
        fill_price=0.61 if verdict == "ok" else None,
        realized_pnl=1.9 if verdict == "ok" else None,
        reason="take_profit" if verdict == "ok" else "within thresholds",
        error=None if verdict != "error" else "exit boom",
    )


# ---------- initial state ----------


def test_initial_empty_store() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    assert s.entries() == []
    assert s.counters() == {v: 0 for v in VERDICTS}
    assert s.last_at is None
    assert s.name == "analyzer"
    assert s.maxlen == DEFAULT_MAXLEN


def test_module_singletons_are_distinct() -> None:
    assert (
        len(
            {
                id(embedding_log),
                id(analyzer_log),
                id(entry_log),
                id(exit_log),
                id(settlement_log),
            }
        )
        == 5
    )
    assert embedding_log.name == "embedding"
    assert analyzer_log.name == "analyzer"
    assert entry_log.name == "entry"
    assert exit_log.name == "exit"
    assert settlement_log.name == "settlement"


# ---------- append + counters ----------


def test_append_and_entries_roundtrip() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    e = _call(ts=10.0, verdict="ok")
    s.append(e)
    assert s.entries() == [e]


def test_counters_increment_per_verdict() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(_call(verdict="ok"))
    s.append(_call(verdict="ok"))
    s.append(_call(verdict="skip"))
    s.append(_call(verdict="fail_open"))
    s.append(_call(verdict="error"))
    assert s.counters() == {"ok": 2, "skip": 1, "fail_open": 1, "error": 1}


def test_counters_unknown_verdict_ignored() -> None:
    """Defensive: a malformed entry shouldn't blow up the counters."""

    class _Bogus:
        ts = 1.0
        verdict = "weird_state"

    s: SectionLogStore[object] = SectionLogStore("misc")
    s.append(_Bogus())  # type: ignore[arg-type]
    assert s.counters() == {v: 0 for v in VERDICTS}
    assert s.last_at == 1.0  # ts still recorded


def test_last_at_tracks_latest_ts() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(_call(ts=5.0))
    s.append(_call(ts=10.0))
    assert s.last_at == 10.0


# ---------- ring truncation ----------


def test_ring_truncates_to_maxlen() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer", maxlen=5)
    for i in range(10):
        s.append(_call(ts=float(i), news_id=f"n{i}"))
    entries = s.entries()
    assert len(entries) == 5
    # Newest preserved, oldest evicted
    assert entries[-1].news_id == "n9"
    assert entries[0].news_id == "n5"


def test_entries_limit_returns_tail() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    for i in range(10):
        s.append(_call(ts=float(i), news_id=f"n{i}"))
    tail = s.entries(limit=3)
    assert [e.news_id for e in tail] == ["n7", "n8", "n9"]


def test_entries_limit_zero_returns_empty() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(_call())
    assert s.entries(limit=0) == []


def test_entries_limit_larger_than_len_returns_all() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(_call(news_id="a"))
    s.append(_call(news_id="b"))
    assert len(s.entries(limit=100)) == 2


# ---------- reset ----------


def test_reset_clears_everything() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(_call())
    s.append(_call(verdict="error"))
    s.reset()
    assert s.entries() == []
    assert s.counters() == {v: 0 for v in VERDICTS}
    assert s.last_at is None


# ---------- repr ----------


def test_repr_does_not_include_entry_content() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(
        AnalyzerCall(
            ts=1.0,
            news_id="secret-news-id-DO-NOT-LEAK",
            news_content_preview="confidential-text-DO-NOT-LEAK",
            urgency="high",
            verdict="ok",
            p_model=0.55,
            confidence="medium",
            market_id="m1",
            latency_ms=1,
        )
    )
    r = repr(s)
    assert "secret-news-id" not in r
    assert "confidential-text" not in r
    # Sanity: meta info is in there
    assert "analyzer" in r
    assert "n_entries=1" in r


# ---------- entry serialization ----------


def test_analyzer_call_to_dict_shape() -> None:
    e = _call()
    d = e.to_dict()
    assert set(d.keys()) == {
        "ts",
        "news_id",
        "news_content_preview",
        "urgency",
        "verdict",
        "p_model",
        "confidence",
        "market_id",
        "latency_ms",
        "error",
        "rationale",
    }
    assert d == asdict(e)


def test_analyzer_call_rationale_default_none() -> None:
    """PD1: ``rationale`` is optional with default None so existing callers
    (and the test fixture above) keep compiling without each one being
    updated. Orchestrator passes ``ar.rationale`` explicitly on real calls."""
    e = _call()
    assert e.rationale is None
    assert e.to_dict()["rationale"] is None


def test_analyzer_call_rationale_round_trips_when_set() -> None:
    """PD1: when the LLM returns a rationale, it survives to_dict so the
    HTTP layer can surface it (PD3 reads via to_dict serialization)."""
    e = AnalyzerCall(
        ts=1.0,
        news_id="n1",
        news_content_preview="x",
        urgency="high",
        verdict="ok",  # type: ignore[arg-type]
        p_model=0.6,
        confidence="medium",
        market_id="m1",
        latency_ms=10,
        error=None,
        rationale="selected YES because primary-source confirms breakthrough",
    )
    assert e.rationale.startswith("selected YES")
    assert e.to_dict()["rationale"] == e.rationale


def test_entry_decision_to_dict_shape() -> None:
    e = _decision()
    d = e.to_dict()
    assert set(d.keys()) == {
        "ts",
        "news_id",
        "ar_p_model",
        "ar_market_id",
        "verdict",
        "side",
        "qty",
        "price",
        "reason",
        "latency_ms",
        "error",
        "fill_status",
        "fill_price",
        "fill_qty",
        "position_id",
    }
    assert d == asdict(e)


def test_embedding_call_to_dict_shape() -> None:
    e = _embedding_call()
    d = e.to_dict()
    assert set(d.keys()) == {
        "ts",
        "news_id",
        "news_content_preview",
        "urgency",
        "verdict",
        "candidate_count",
        "top_market_id",
        "top_score",
        "catalog_size",
        "latency_ms",
        "error",
    }
    assert d == asdict(e)


def test_exit_decision_to_dict_shape() -> None:
    e = _exit_decision()
    d = e.to_dict()
    assert set(d.keys()) == {
        "ts",
        "position_id",
        "market_id",
        "side",
        "verdict",
        "trigger",
        "return_pct",
        "fill_price",
        "realized_pnl",
        "reason",
        "error",
        "peak_price",
    }
    assert d == asdict(e)


def test_settlement_decision_to_dict_shape() -> None:
    e = SettlementDecision(
        ts=1.0,
        position_id=1,
        market_id="m1",
        side="yes",
        verdict="ok",
        final_price=1.0,
        realized_pnl=6.0,
        reason="settlement",
        error=None,
    )
    d = e.to_dict()
    assert set(d.keys()) == {
        "ts",
        "position_id",
        "market_id",
        "side",
        "verdict",
        "final_price",
        "realized_pnl",
        "reason",
        "error",
    }
    assert d == asdict(e)


def test_error_entry_carries_error_message() -> None:
    s: SectionLogStore[AnalyzerCall] = SectionLogStore("analyzer")
    s.append(_call(verdict="error"))
    e = s.entries()[0]
    assert e.verdict == "error"
    assert e.error == "boom"


# ---------- generic typing sanity ----------


def test_store_is_generic_for_entry_too() -> None:
    s: SectionLogStore[EntryDecision] = SectionLogStore("entry")
    s.append(_decision(verdict="ok"))
    s.append(_decision(verdict="skip"))
    assert s.counters()["ok"] == 1
    assert s.counters()["skip"] == 1
    assert len(s.entries()) == 2


def test_store_is_generic_for_embedding_too() -> None:
    s: SectionLogStore[EmbeddingCall] = SectionLogStore("embedding")
    s.append(_embedding_call(verdict="ok"))
    s.append(_embedding_call(verdict="skip"))
    s.append(_embedding_call(verdict="error"))
    assert s.counters()["ok"] == 1
    assert s.counters()["skip"] == 1
    assert s.counters()["error"] == 1
    assert len(s.entries()) == 3
    assert s.entries()[-1].error == "embedding boom"
