"""Broken fixture: missing Config class attribute — should be rejected."""

from __future__ import annotations

from openpoly.sections._base import SectionInput, SectionOutput


class DummyMissingConfig:
    SECTION_TYPE = "analyzer"
    SECTION_VERSION = "0.0.1"
    REQUIRES = []
    # Config intentionally absent.

    def run(self, input: SectionInput) -> SectionOutput:
        return SectionOutput(verdict="ok")
