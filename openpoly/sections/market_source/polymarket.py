"""Polymarket market-source section.

Thin section-protocol wrapper over the market-source discovery engine. The
polling loop, lifecycle, and catalog all live in ``MarketSourceManager`` (see
``openpoly.markets.manager``); this class exists so ``market_source`` registers
in the section catalog — giving it a canvas node and an auto-rendered Config
form — and so ``run()`` can hand the current catalog snapshot to any caller.

Lifecycle (start/stop polling) is driven via the HTTP routes against the
manager singleton, not this class — hence no ``start_async`` / ``stop_async``.
"""

from __future__ import annotations

from openpoly.markets.manager import MarketSourceConfig, manager
from openpoly.sections._base import SectionInput, SectionOutput


class PolymarketSource:
    SECTION_TYPE = "market_source"
    SECTION_VERSION = "0.1.0"
    REQUIRES: list[str] = []
    Config = MarketSourceConfig

    def __init__(self, config: MarketSourceConfig) -> None:
        self.config = config

    def run(self, input: SectionInput) -> SectionOutput:
        """Return a snapshot of the current discovery catalog.

        Reads the manager singleton's store — there is exactly one market
        source per process. Not called by the event-driven pipeline; present
        for protocol conformance and ad-hoc catalog access.
        """
        markets = manager.store.snapshot()
        last_poll = manager.store.last_poll
        return SectionOutput(
            payload=markets,
            verdict="ok",
            signals={
                "catalog_size": len(markets),
                "last_poll_kept": last_poll.kept if last_poll is not None else None,
            },
        )

    @staticmethod
    def CONTRACT_TEST() -> None:
        cfg = MarketSourceConfig()
        inst = PolymarketSource(cfg)
        out = inst.run(SectionInput(tick_type="warm"))
        assert out.verdict == "ok"
        assert isinstance(out.payload, list)
