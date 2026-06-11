"""FastAPI app: section catalog endpoint, news-source and market-source routes.

Catalog is scanned once at app startup and cached in-process. To pick up new
impls, restart the backend (per v2 plan: full rescan, never incremental).

Lifespan handles graceful shutdown of the NewsSourceManager singleton so a
running WS task is cancelled cleanly on SIGINT (per v4 plan risk 1). It also
auto-starts the news + market sources so a fresh process (every uvicorn
--reload included) comes up already streaming — opt out with
``OPENPOLY_AUTOSTART_SOURCES=0`` (tests do this).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, AsyncIterator

from fastapi import FastAPI

from openpoly.api.canvas_routes import router as canvas_router
from openpoly.api.inspect_routes import router as inspect_router
from openpoly.api.market_routes import router as market_router
from openpoly.api.news_routes import router as news_router
from openpoly.api.portfolio_routes import router as portfolio_router
from openpoly.api.runtime_routes import router as runtime_router
from openpoly.api.secrets_routes import router as secrets_router
from openpoly.api.wallet_routes import router as wallet_router
from openpoly.db.engine import get_session_factory
from openpoly.db.manager import manager as database_manager
from openpoly.embedding.manager import manager as embedding_manager
from openpoly.execution import executor
from openpoly.markets.manager import MarketSourceConfig
from openpoly.markets.manager import manager as market_source_manager
from openpoly.news.manager import manager as news_source_manager
from openpoly.portfolio import PortfolioStore
from openpoly.runtime.exit_monitor import exit_monitor
from openpoly.runtime.settlement_monitor import settlement_monitor
from openpoly.runtime import reconciliation_monitor as _recon_mod
from openpoly.runtime.reconciliation_monitor import ReconciliationMonitor
from openpoly.runtime.orchestrator import get_orchestrator
from openpoly.sections._registry import CatalogEntry, scan
from openpoly.sections.news_source.tradingnews_ws import TradingNewsWSConfig
from openpoly.wallet.runtime_state import runtime_state

logger = logging.getLogger(__name__)

# Surface openpoly's INFO logs (canvas reload, source connect, executor
# fills, etc.) — uvicorn's default log config attaches handlers only to its
# own loggers, so app-level INFO went nowhere. Attach an explicit handler
# scoped to ``openpoly.*`` so the canvas-sync "rebuilt X section" trail
# and similar operational signals reach the same stream as uvicorn's
# access log, without polluting it with third-party noise.
_openpoly_logger = logging.getLogger("openpoly")
if not any(getattr(h, "_openpoly_handler", False) for h in _openpoly_logger.handlers):
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    _h._openpoly_handler = True  # type: ignore[attr-defined]  # idempotent marker
    _openpoly_logger.addHandler(_h)
_openpoly_logger.setLevel(logging.INFO)
# Propagation stays ON: pytest's caplog hooks the root logger, and turning
# propagation off blackholes our log records away from test assertions
# (caught 3 regressions when this was False). The cost of "double-log under
# uvicorn" is theoretical — uvicorn's default root handler is not attached
# unless --log-config sets one, so in practice messages emit exactly once
# via our handler.

# News + market sources auto-start with the app by default; set this to "0"
# to opt out (tests do, so the lifespan never opens real connections).
_AUTOSTART_ENV = "OPENPOLY_AUTOSTART_SOURCES"

# api_key_ref the autostart uses for the news WS. Default reads from the
# local secret store; OPENPOLY_NEWS_API_KEY_REF lets an operator point at
# an env: or other scheme without needing a secret store entry (e.g. for
# remote-VPS deploys where the secret is already in process env).
_NEWS_AUTOSTART_KEY_REF = os.environ.get("OPENPOLY_NEWS_API_KEY_REF", "local:tradingnews-key")


def _autostart_enabled() -> bool:
    return os.environ.get(_AUTOSTART_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "",
    )


async def _autostart_sources() -> None:
    """Best-effort start of both sources. A failure is logged and surfaced via
    the source's own status snapshot; it never aborts app startup."""
    try:
        await market_source_manager.start(MarketSourceConfig())
    except Exception:  # noqa: BLE001 — startup must survive a bad source
        logger.exception("market_source autostart failed")
    try:
        news_cfg = TradingNewsWSConfig(api_key_ref=_NEWS_AUTOSTART_KEY_REF)
        await news_source_manager.start(news_cfg.model_dump())
    except Exception:  # noqa: BLE001 — startup must survive a bad source
        logger.exception("news_source autostart failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Startup: wire the pipeline. Manager forwards each fresh NewsItem
    # via its sync ``_on_item`` hook → orchestrator.enqueue → worker.
    # Wallet + exec_mode state — read first so the dispatcher routes to the
    # correct executor at the moment the orchestrator starts dispatching.
    runtime_state.load()
    orch = get_orchestrator()
    news_source_manager.set_pipeline_hook(orch.enqueue)
    await orch.start()
    # Persistence — the database section's manager owns the engine + the two
    # write-behind writers (order-book sampling loop + news stream).
    await database_manager.start()
    # Executor — inject a PortfolioStore now the DB engine + tables are up;
    # entry fills and exit sells write fill / position through it.
    portfolio = PortfolioStore(get_session_factory())
    executor.configure_paper(portfolio)
    # Try-build the live executor whether or not we're currently in live
    # mode — pre-construct so a UI flip is cheap. Failure leaves live=None
    # and the dispatcher falls back to live_not_ready.
    if runtime_state.wallet is not None:
        try:
            from openpoly.execution.live_executor import build_live_executor

            live_exec = build_live_executor(runtime_state.wallet, portfolio)
            executor.configure_live(live_exec)
        except Exception as exc:  # noqa: BLE001 — startup must survive a bad wallet
            logger.exception("live executor construction failed: %s", exc)
    # Exit monitor — the timer-driven close loop; shares the executor with the
    # orchestrator. Configure with its own PortfolioStore, then start ticking.
    exit_monitor.configure(PortfolioStore(get_session_factory()))
    await exit_monitor.start()
    # Settlement monitor (slice E) — closes resolved-market positions at 0/1
    # directly via PortfolioStore (no broker tx). Independent from exit_monitor
    # so a Gamma outage doesn't stall the TP/SL loop, and vice versa.
    settlement_monitor.configure(PortfolioStore(get_session_factory()))
    await settlement_monitor.start()
    # Reconciliation monitor — closes positions the wallet no longer holds
    # on-chain (exited outside the ledger). Needs the funder to query the
    # data-api, and only acts in live mode (paper positions aren't on-chain),
    # so it's wired only when a wallet is configured.
    if runtime_state.wallet is not None:
        from openpoly.markets.polymarket_api import fetch_held_condition_sides

        _funder = runtime_state.wallet.funder_address

        async def _held_condition_sides() -> set[tuple[str, str]]:
            return await fetch_held_condition_sides(_funder)

        _recon_mod.reconciliation_monitor = ReconciliationMonitor(
            holdings_fetcher=_held_condition_sides,
            live_check=lambda: runtime_state.exec_mode == "live",
        )
        _recon_mod.reconciliation_monitor.configure(PortfolioStore(get_session_factory()))
        await _recon_mod.reconciliation_monitor.start()
    # Embedding warm cache — uses the engine database_manager just bootstrapped
    # (init_db has created the market_embedding table); the warm loop reloads
    # cached vectors so a restart skips the cold recompute.
    await embedding_manager.start(session_factory=get_session_factory())
    market_source_manager.set_book_persist(database_manager.enqueue_order_book)
    market_source_manager.set_portfolio_store(PortfolioStore(get_session_factory()))
    news_source_manager.set_news_persist(database_manager.enqueue_news)
    # Auto-start both sources so a fresh process is already streaming — no
    # manual Start needed after a restart. Runs last so the persist hooks
    # above are in place before the first poll / message arrives.
    if _autostart_enabled():
        await _autostart_sources()
    yield
    # Shutdown (reverse order): drain orchestrator first so it doesn't try
    # to enqueue against a torn-down manager, then stop the WS source.
    market_source_manager.set_book_persist(None)
    market_source_manager.set_portfolio_store(None)
    news_source_manager.set_news_persist(None)
    if _recon_mod.reconciliation_monitor is not None:
        await _recon_mod.reconciliation_monitor.stop()
    await settlement_monitor.stop()
    await exit_monitor.stop()
    await embedding_manager.stop()
    await database_manager.stop()
    await orch.stop()
    news_source_manager.set_pipeline_hook(None)
    await news_source_manager.shutdown()
    await market_source_manager.shutdown()


app = FastAPI(title="openPoly", version="0.0.0", lifespan=lifespan)
app.include_router(news_router)
app.include_router(market_router)
app.include_router(inspect_router)
app.include_router(secrets_router)
app.include_router(runtime_router)
app.include_router(portfolio_router)
app.include_router(wallet_router)
app.include_router(canvas_router)

_catalog_cache: list[CatalogEntry] | None = None


def _catalog() -> list[CatalogEntry]:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = scan()
    return _catalog_cache


def reset_catalog() -> None:
    """Test hook: force the next /api/sections/catalog call to rescan."""
    global _catalog_cache
    _catalog_cache = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/sections/catalog")
def get_catalog() -> dict[str, list[dict[str, Any]]]:
    return {"sections": [asdict(e) for e in _catalog()]}
