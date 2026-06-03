"""Smoke runner for the news → embedding → analyzer → entry pipeline.

Manual end-to-end test. Starts the market-discovery loop, the embedding warm
cache, and the pipeline orchestrator, then subscribes to the live news
firehose and feeds every fresh NewsItem through ``orchestrator.enqueue``. Each
tick it tails the three per-section logs (embedding / analyzer / entry).

Useful for:

* Sanity-checking real-world behaviour after refactors to the pipeline.
* Confirming OPENPOLY_TRADINGNEWS_KEY is wired correctly.
* Watching the embedding filter + analyzer / entry stubs react to real news
  (the LLM call is still a stub until M14+; the embedding model is real).

First run downloads the sentence-transformer weights (~90MB).

Usage:
    OPENPOLY_TRADINGNEWS_KEY=... uv run python scripts/run_pipeline.py
    OPENPOLY_TRADINGNEWS_KEY=... uv run python scripts/run_pipeline.py --interval 30 --ticks 5

Stops cleanly after the configured tick count or on SIGINT.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

from openpoly.db.engine import get_engine, get_session_factory, init_db
from openpoly.embedding.manager import manager as embedding_manager
from openpoly.execution import executor
from openpoly.markets.manager import MarketSourceConfig
from openpoly.markets.manager import manager as market_manager
from openpoly.portfolio import PortfolioStore
from openpoly.runtime.orchestrator import get_orchestrator
from openpoly.runtime.section_log import analyzer_log, embedding_log, entry_log
from openpoly.sections._base import SectionInput
from openpoly.sections.news_source.tradingnews_ws import (
    TradingNewsWSConfig,
    TradingNewsWSSource,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
)
log = logging.getLogger("pipeline")

# Seconds to wait for the first discovery poll before warming embeddings.
CATALOG_WAIT_SECONDS = 30
# Seconds to let the serial worker drain freshly enqueued items each tick.
DRAIN_GRACE_SECONDS = 0.5


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Seconds between pipeline ticks (default 60).",
    )
    p.add_argument("--ticks", type=int, default=10, help="Number of ticks to run (default 10).")
    p.add_argument(
        "--freshness",
        type=int,
        default=600,
        help="Freshness window in seconds for news (default 600).",
    )
    return p.parse_args()


async def _wait_for_catalog() -> int:
    """Block until the first discovery poll fills the market catalog."""
    for _ in range(CATALOG_WAIT_SECONDS):
        if len(market_manager.store) > 0:
            break
        await asyncio.sleep(1.0)
    return len(market_manager.store)


def _log_tail(portfolio: PortfolioStore) -> None:
    """Print the newest entry + counters of each per-section log, plus a
    portfolio summary — positions land via the executor on each entry fill."""
    for name, store in (
        ("embedding", embedding_log),
        ("analyzer", analyzer_log),
        ("entry", entry_log),
    ):
        latest = store.entries(limit=1)
        log.info("  %-9s counters=%s", name, store.counters())
        if latest:
            log.info("  %-9s latest=%s", name, latest[0].to_dict())
    positions = portfolio.list_positions(limit=5)
    log.info(
        "  %-9s open=%d recent=%d",
        "portfolio",
        len(portfolio.get_open_positions()),
        len(positions),
    )
    for p in positions:
        log.info("  %-9s %s", "position", p)


async def run(args: argparse.Namespace) -> int:
    if "OPENPOLY_TRADINGNEWS_KEY" not in os.environ:
        log.error("OPENPOLY_TRADINGNEWS_KEY env var is required")
        return 2

    init_db(get_engine())
    portfolio_store = PortfolioStore(get_session_factory())
    executor.configure(portfolio_store)

    log.info("starting market discovery ...")
    await market_manager.start(MarketSourceConfig())
    catalog_size = await _wait_for_catalog()
    log.info("market catalog: %d markets", catalog_size)

    log.info("starting embedding warm cache (first run downloads the model) ...")
    await embedding_manager.start(session_factory=get_session_factory())

    orch = get_orchestrator()
    await orch.start()

    news = TradingNewsWSSource(TradingNewsWSConfig(freshness_seconds=args.freshness))
    await news.start_async()

    log.info(
        "pipeline up: interval=%.0fs ticks=%d freshness=%ds",
        args.interval,
        args.ticks,
        args.freshness,
    )
    seen: set[str] = set()
    started = time.time()
    try:
        for tick in range(1, args.ticks + 1):
            await asyncio.sleep(args.interval)

            news_out = news.run(SectionInput(tick_type="hard"))
            items = news_out.payload or []
            new_items = [it for it in items if it.id not in seen]
            seen.update(it.id for it in new_items)
            for it in new_items:
                orch.enqueue(it)
            # Give the serial worker a moment to drain what we just queued.
            await asyncio.sleep(DRAIN_GRACE_SECONDS)

            log.info(
                "tick=%d/%d uptime=%.0fs new=%d queue_depth=%d",
                tick,
                args.ticks,
                time.time() - started,
                len(new_items),
                orch.queue_depth,
            )
            _log_tail(portfolio_store)
    finally:
        log.info("shutting down ...")
        await news.stop_async()
        await orch.stop()
        await embedding_manager.stop()
        await market_manager.stop()
        log.info(
            "done; seen %d unique news ids over %.0fs",
            len(seen),
            time.time() - started,
        )

    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
