"""LLM analyzer — the news → market-direction + probability section.

Input is a ``MarketCandidates`` (the embedding section's recall-oriented
shortlist). The analyzer asks an LLM, via a forced ``submit_analysis`` tool
call, to decide which ONE candidate the news materially moves — or none — and
to estimate that market's YES probability.

The prompt deliberately **withholds the current market price**: the downstream
edge calc is ``p_model - price``, so anchoring the model to the price would
collapse the edge. The model forms an independent estimate; the comparison
happens later (a prior project fed price + a delta-based self-check, but its
own v8 verdict was that the LLM ``p_model`` was unreliable and openPoly has no
price-delta data yet).

The prompt and tool schema are code-owned alpha logic; only narrow knobs
(``llm_model`` / ``temperature`` / ``api_key_ref`` / ``base_url`` /
``extra_guidance`` / ``min_confidence``) are exposed on the Config. ``base_url``
can route the call through a third-party gateway instead of the official one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from openpoly.embedding.models import MarketCandidates
from openpoly.llm import LLMClient, LLMError
from openpoly.sections._base import SectionInput, SectionOutput

logger = logging.getLogger(__name__)


Confidence = Literal["low", "medium", "high"]
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class AnalysisResult:
    market_id: str
    p_model: float
    confidence: Confidence
    rationale: str = ""


class LLMAnalyzerConfig(BaseModel):
    llm_model: str = Field(
        default="claude-haiku-4-5",
        description=(
            "Model id sent to the API. On the official Anthropic endpoint use "
            "a Claude id (claude-haiku-4-5 / claude-sonnet-4-6 / "
            "claude-opus-4-7); on a third-party gateway use whatever id that "
            "gateway publishes."
        ),
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Sampling temperature; ignored for claude-opus-4-7.",
    )
    api_key_ref: str = Field(
        default="env:ANTHROPIC_API_KEY",
        description="Reference to the LLM API key (env: / local: scheme).",
    )
    base_url: str = Field(
        default="",
        description="Third-party API base URL; empty = official Anthropic endpoint.",
    )
    extra_guidance: str = Field(
        default="",
        description=(
            "Optional extra guidance appended to the analyzer's system prompt. "
            "Cannot alter the structured-output contract."
        ),
    )
    min_confidence: Confidence = Field(default="medium")

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, v: str) -> str:
        """Trim to the root domain. The Anthropic SDK always appends
        /v1/messages, so a trailing slash or /v1 (the shape a third-party
        OpenAI-style URL is usually copied in) would otherwise yield
        /v1/v1/messages → 404."""
        v = v.strip().rstrip("/")
        if v.endswith("/v1"):
            v = v[: -len("/v1")].rstrip("/")
        return v


# --- prompt + tool schema (code-owned alpha logic) -------------------------

_SYSTEM_PROMPT = """You are a prediction-market analyst. You get a news item \
and a shortlist of candidate markets (pre-filtered by semantic similarity —
recall-oriented; some may not be moved by the news).

Before answering, work the self-check for the market you think matches:
  Q1. Is this genuinely new information, or a wire rewrite of something
      already public?
  Q2. Could the event have already resolved or shifted before this news?
  Q3. If your p_yes is a strong claim, can you cite specific checkable
      evidence? If not, downgrade confidence.

Then call submit_analysis:
  selected_index — the 1-based index of the ONE market the news materially
                   moves, or 0 if none. Abstaining is expected; do not force
                   a pick.
  p_yes          — YOUR probability (0-1) that the selected market resolves
                   YES, in light of the news.
  confidence     — HIGH only with primary-source confirmation; LOW for stale
                   news (>2h old), ambiguous resolution, or a directional
                   guess; MEDIUM otherwise.
  rationale      — one or two sentences.

"Materially moved" = the news changes the outcome or its probability, not
mere topical overlap. You are not given the current market price — do not
anchor to one; reason from the news and the market's resolution question."""


SUBMIT_ANALYSIS_TOOL: dict[str, Any] = {
    "name": "submit_analysis",
    "description": ("Report which candidate market the news moves and its YES probability."),
    "input_schema": {
        "type": "object",
        "properties": {
            "selected_index": {
                "type": "integer",
                "description": "1-based index of the moved market, or 0 if none.",
            },
            "p_yes": {
                "type": "number",
                "description": "Probability (0-1) the selected market resolves YES.",
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences.",
            },
        },
        "required": ["selected_index", "p_yes", "confidence", "rationale"],
    },
}


def _build_system(extra_guidance: str) -> str:
    guidance = extra_guidance.strip()
    if guidance:
        return f"{_SYSTEM_PROMPT}\n\nAdditional guidance:\n{guidance}"
    return _SYSTEM_PROMPT


def _build_user(candidates: MarketCandidates) -> str:
    """The per-call user prompt: news + numbered candidate markets. No price."""
    news = candidates.news
    published = datetime.fromtimestamp(news.published_at, tz=timezone.utc).isoformat()
    lines = [
        f"Current time (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"News published (UTC): {published}",
        f"NEWS (urgency: {news.urgency}):",
        news.content,
        "",
        "CANDIDATE MARKETS:",
    ]
    for i, cand in enumerate(candidates.candidates, start=1):
        market = cand.market
        end = market.end_date.isoformat() if market.end_date else "unknown"
        lines.append(f"[{i}] {market.question}  — resolves {end}")
    lines.append("")
    lines.append("Call submit_analysis.")
    return "\n".join(lines)


class LLMAnalyzerV0:
    SECTION_TYPE = "analyzer"
    SECTION_VERSION = "0.1.0"
    REQUIRES = ["llm", "market_data"]
    Config = LLMAnalyzerConfig

    def __init__(
        self,
        config: LLMAnalyzerConfig,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.config = config
        # Injection seam: tests pass a fake; production builds the real client
        # lazily on first use so construction touches no secret store.
        self._llm = llm_client

    def _client(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient(
                api_key_ref=self.config.api_key_ref,
                model=self.config.llm_model,
                temperature=self.config.temperature,
                base_url=self.config.base_url,
            )
        return self._llm

    def run(self, input: SectionInput) -> SectionOutput:
        candidates = input.payload
        if not isinstance(candidates, MarketCandidates) or not candidates.candidates:
            return SectionOutput(payload=None, verdict="skip", reason="no market candidates")

        cands = candidates.candidates
        try:
            result = self._client().analyze(
                system=_build_system(self.config.extra_guidance),
                user=_build_user(candidates),
                tool=SUBMIT_ANALYSIS_TOOL,
            )
        except LLMError as exc:
            return SectionOutput(payload=None, verdict="error", reason=repr(exc)[:200])

        idx = result.get("selected_index")
        if not isinstance(idx, int) or isinstance(idx, bool) or not (1 <= idx <= len(cands)):
            # 0 = explicit abstain; out-of-range / non-int = treat the same.
            return SectionOutput(
                payload=None,
                verdict="skip",
                reason="no actionable market",
                signals={"selected_index": idx},
            )

        confidence = result.get("confidence")
        if confidence not in _CONFIDENCE_RANK:
            return SectionOutput(
                payload=None,
                verdict="error",
                reason=f"malformed confidence: {confidence!r}",
            )
        if _CONFIDENCE_RANK[confidence] < _CONFIDENCE_RANK[self.config.min_confidence]:
            return SectionOutput(
                payload=None,
                verdict="skip",
                reason="below min_confidence",
                signals={"confidence": confidence},
            )

        p_yes = result.get("p_yes")
        if (
            isinstance(p_yes, bool)
            or not isinstance(p_yes, (int, float))
            or not 0.0 <= p_yes <= 1.0
        ):
            return SectionOutput(
                payload=None,
                verdict="error",
                reason=f"malformed p_yes: {p_yes!r}",
            )

        market = cands[idx - 1].market
        ar = AnalysisResult(
            market_id=market.market_id,
            p_model=float(p_yes),
            confidence=confidence,  # type: ignore[arg-type]
            rationale=str(result.get("rationale", "")),
        )
        return SectionOutput(
            payload=ar,
            verdict="ok",
            signals={
                "news_id": candidates.news.id,
                "selected_index": idx,
                "confidence": confidence,
                "candidate_count": len(cands),
            },
        )

    @staticmethod
    def CONTRACT_TEST() -> None:
        # payload=None → skip before any LLM call: the registry scan stays
        # light and needs no API key.
        inst = LLMAnalyzerV0(LLMAnalyzerConfig())
        out = inst.run(SectionInput(tick_type="event", payload=None))
        assert out.verdict == "skip"
