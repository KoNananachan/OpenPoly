"""Section Protocol — minimal contract for all section impls.

Each impl is a class that declares its type, version, required capabilities, a
Pydantic Config (single source of param schema), and a sync run() method.
Runtime owns scheduling / capability injection / audit / timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

Capability = Literal[
    "news",
    "llm",
    "market_data",
    "order_book",
    "news_history",
    "portfolio",
]

SectionTypeName = Literal[
    "news_source", "market_source", "embedding", "analyzer", "entry", "exit", "database"
]

Verdict = Literal["ok", "fail_open", "error", "skip"]


@dataclass(frozen=True)
class SectionInput:
    """Per-tick input passed to Section.run()."""

    tick_type: str
    payload: Any = None


@dataclass(frozen=True)
class SectionOutput:
    """Standardized output envelope from Section.run().

    `fail_open` is a first-class verdict — downstream treats it as pass-through
    but marks the relevant signal as dark.
    """

    payload: Any | None = None
    verdict: Verdict = "ok"
    reason: str | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    signal_unavailable: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


@runtime_checkable
class Section(Protocol):
    """The contract every section impl must satisfy.

    Note: runtime_checkable Protocol does not enforce ClassVar shape at isinstance
    time — registry's contract_test module performs explicit attribute checks.
    """

    SECTION_TYPE: ClassVar[SectionTypeName]
    SECTION_VERSION: ClassVar[str]
    REQUIRES: ClassVar[list[Capability]]
    Config: ClassVar[type[BaseModel]]

    def run(self, input: SectionInput) -> SectionOutput: ...
