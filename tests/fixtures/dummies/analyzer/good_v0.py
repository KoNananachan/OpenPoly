"""Good fixture: passes protocol conformance and CONTRACT_TEST."""

from __future__ import annotations

from pydantic import BaseModel

from openpoly.sections._base import SectionInput, SectionOutput


class _Config(BaseModel):
    threshold: float = 0.5
    label: str = "good"


class DummyGoodAnalyzer:
    SECTION_TYPE = "analyzer"
    SECTION_VERSION = "0.0.1"
    REQUIRES = ["llm"]
    Config = _Config

    def __init__(self, config: _Config) -> None:
        self.config = config

    def run(self, input: SectionInput) -> SectionOutput:
        return SectionOutput(payload={"echo": input.payload}, verdict="ok")

    @staticmethod
    def CONTRACT_TEST() -> None:
        cfg = _Config()
        inst = DummyGoodAnalyzer(cfg)
        out = inst.run(SectionInput(tick_type="test", payload="hello"))
        assert out.verdict == "ok"
        assert out.payload == {"echo": "hello"}
