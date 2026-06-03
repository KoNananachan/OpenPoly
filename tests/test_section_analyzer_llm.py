"""Tests for LLMAnalyzerV0 — the real LLM analyzer.

A fake LLMClient is injected via the constructor seam, so tests never touch
the network.
"""

from __future__ import annotations

from typing import Any

from openpoly.embedding.models import MarketCandidate, MarketCandidates
from openpoly.llm import LLMError
from openpoly.markets.models import Market
from openpoly.news.ring_buffer import NewsItem
from openpoly.sections._base import SectionInput
from openpoly.sections._registry import scan
from openpoly.sections.analyzer.llm_v0 import (
    AnalysisResult,
    LLMAnalyzerConfig,
    LLMAnalyzerV0,
)


class FakeLLMClient:
    """Canned LLMClient: records the prompt it was called with, then returns a
    fixed result dict or raises."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._raise = raise_exc
        self.call_count = 0
        self.last_system: str | None = None
        self.last_user: str | None = None
        self.last_tool: dict[str, Any] | None = None

    def analyze(self, *, system: str, user: str, tool: dict[str, Any]) -> dict[str, Any]:
        self.call_count += 1
        self.last_system = system
        self.last_user = user
        self.last_tool = tool
        if self._raise is not None:
            raise self._raise
        assert self._result is not None
        return dict(self._result)


def _news(news_id: str = "x", content: str = "news body") -> NewsItem:
    return NewsItem(
        id=news_id,
        content=content,
        urgency="high",
        sentiment=None,
        published_at=0.0,
        received_at=0.0,
    )


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


def _candidates(*market_ids: str) -> MarketCandidates:
    return MarketCandidates(
        news=_news(),
        candidates=[
            MarketCandidate(market=_market(m), score=0.9 - i * 0.1)
            for i, m in enumerate(market_ids)
        ],
    )


def _run(config: LLMAnalyzerConfig, fake: FakeLLMClient, payload: object):
    return LLMAnalyzerV0(config, llm_client=fake).run(
        SectionInput(tick_type="event", payload=payload)
    )


# ---------- catalog / skip paths ----------


def test_analyzer_in_default_catalog() -> None:
    matches = [e for e in scan() if e.name == "LLMAnalyzerV0"]
    assert len(matches) == 1
    assert matches[0].type == "analyzer"
    assert matches[0].requires == ["llm", "market_data"]


def test_no_payload_skips() -> None:
    fake = FakeLLMClient()
    out = _run(LLMAnalyzerConfig(), fake, None)
    assert out.verdict == "skip"
    assert out.reason == "no market candidates"
    assert fake.call_count == 0


def test_empty_candidates_skips() -> None:
    fake = FakeLLMClient()
    out = _run(
        LLMAnalyzerConfig(),
        fake,
        MarketCandidates(news=_news(), candidates=[]),
    )
    assert out.verdict == "skip"
    assert fake.call_count == 0  # no LLM call on the skip path


# ---------- ok path ----------


def test_ok_maps_selected_candidate() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 2,
            "p_yes": 0.73,
            "confidence": "high",
            "rationale": "clear primary source.",
        }
    )
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a", "m-b"))
    assert out.verdict == "ok"
    assert isinstance(out.payload, AnalysisResult)
    assert out.payload.market_id == "m-b"  # index 2 → 2nd candidate
    assert out.payload.p_model == 0.73
    assert out.payload.confidence == "high"
    assert out.payload.rationale == "clear primary source."
    assert out.signals["selected_index"] == 2


def test_prompt_numbers_candidates_and_withholds_price() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 1,
            "p_yes": 0.6,
            "confidence": "medium",
            "rationale": "r",
        }
    )
    _run(LLMAnalyzerConfig(), fake, _candidates("m-a", "m-b"))
    assert fake.last_user is not None
    # Candidates are numbered in the user prompt.
    assert "[1]" in fake.last_user and "[2]" in fake.last_user
    assert "question m-a" in fake.last_user
    assert "news body" in fake.last_user
    # The current market price is withheld from the user prompt.
    assert "price" not in fake.last_user.lower()
    # The forced tool is submit_analysis.
    assert fake.last_tool is not None
    assert fake.last_tool["name"] == "submit_analysis"


# ---------- abstain / confidence gate ----------


def test_abstain_index_zero_skips() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 0,
            "p_yes": 0.5,
            "confidence": "low",
            "rationale": "none apply.",
        }
    )
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a"))
    assert out.verdict == "skip"
    assert out.reason == "no actionable market"


def test_index_out_of_range_skips() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 9,
            "p_yes": 0.7,
            "confidence": "high",
            "rationale": "r",
        }
    )
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a"))
    assert out.verdict == "skip"
    assert out.reason == "no actionable market"


def test_below_min_confidence_skips() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 1,
            "p_yes": 0.7,
            "confidence": "low",
            "rationale": "r",
        }
    )
    # Default min_confidence is "medium".
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a"))
    assert out.verdict == "skip"
    assert out.reason == "below min_confidence"


def test_low_confidence_ok_when_min_is_low() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 1,
            "p_yes": 0.7,
            "confidence": "low",
            "rationale": "r",
        }
    )
    out = _run(LLMAnalyzerConfig(min_confidence="low"), fake, _candidates("m-a"))
    assert out.verdict == "ok"
    assert out.payload.confidence == "low"


# ---------- error paths ----------


def test_llm_error_yields_error_verdict() -> None:
    fake = FakeLLMClient(raise_exc=LLMError("api down"))
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a"))
    assert out.verdict == "error"
    assert "api down" in (out.reason or "")


def test_malformed_p_yes_yields_error() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 1,
            "p_yes": 1.8,
            "confidence": "high",
            "rationale": "r",
        }
    )
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a"))
    assert out.verdict == "error"


def test_malformed_confidence_yields_error() -> None:
    fake = FakeLLMClient(
        result={
            "selected_index": 1,
            "p_yes": 0.7,
            "confidence": "maybe",
            "rationale": "r",
        }
    )
    out = _run(LLMAnalyzerConfig(), fake, _candidates("m-a"))
    assert out.verdict == "error"


# ---------- base_url / third-party gateway config ----------


def test_base_url_normalized_to_root_domain() -> None:
    # A yunwu-style URL is often copied OpenAI-style with a /v1; the Anthropic
    # SDK appends /v1/messages itself, so the suffix must be stripped.
    assert LLMAnalyzerConfig(base_url="https://yunwu.ai/v1/").base_url == "https://yunwu.ai"
    assert LLMAnalyzerConfig(base_url=" https://yunwu.ai ").base_url == "https://yunwu.ai"
    assert LLMAnalyzerConfig().base_url == ""


def test_config_base_url_reaches_llm_client() -> None:
    # The analyzer builds its own LLMClient lazily — the gateway URL must
    # flow from section config into that client.
    analyzer = LLMAnalyzerV0(LLMAnalyzerConfig(base_url="https://yunwu.ai"))
    assert analyzer._client()._base_url == "https://yunwu.ai"


def test_llm_model_accepts_arbitrary_gateway_id() -> None:
    # The model id is no longer a fixed enum — a third-party gateway may
    # publish ids the official endpoint never exposes.
    assert LLMAnalyzerConfig(llm_model="claude-opus-4-6").llm_model == ("claude-opus-4-6")
