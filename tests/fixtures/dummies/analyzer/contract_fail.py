"""Broken fixture: CONTRACT_TEST raises — should be rejected."""

from __future__ import annotations

from pydantic import BaseModel

from openpoly.sections._base import SectionInput, SectionOutput


class _Config(BaseModel):
    x: int = 1


class DummyContractFail:
    SECTION_TYPE = "analyzer"
    SECTION_VERSION = "0.0.1"
    REQUIRES = []
    Config = _Config

    def run(self, input: SectionInput) -> SectionOutput:
        return SectionOutput(verdict="ok")

    @staticmethod
    def CONTRACT_TEST() -> None:
        raise RuntimeError("intentional failure to verify rejection path")
