"""Market-source HTTP routes — discovery polling lifecycle (MS5).

POST /api/market/source/start
POST /api/market/source/stop
GET  /api/market/source/status
    Lifecycle of the long-running market_source polling loop owned by
    ``openpoly.markets.manager.manager``. Status responses embed the poll
    event ring so the frontend Live tab can poll a single endpoint.

Polymarket discovery needs no auth, so — unlike the news routes — there is no
secret resolution and no connection-test endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from openpoly.markets.manager import MarketSourceConfig
from openpoly.markets.manager import manager as market_source_manager

router = APIRouter(prefix="/api/market", tags=["market"])

# How many recent poll events to embed in each status response.
EVENT_RING_RESPONSE_LIMIT = 200


class MarketSnapshotPayload(BaseModel):
    state: str
    started_at: float | None
    last_poll_at: float | None
    catalog_size: int
    poll_count: int
    last_error: str | None
    running_config: dict[str, Any] | None
    last_poll: dict[str, Any] | None
    events: list[dict[str, Any]]


class MarketSourceResponse(BaseModel):
    ok: bool
    error: str | None = None
    snapshot: MarketSnapshotPayload


def _build_payload() -> MarketSnapshotPayload:
    snap = market_source_manager.status().to_dict()
    events = [e.to_dict() for e in market_source_manager.events(limit=EVENT_RING_RESPONSE_LIMIT)]
    return MarketSnapshotPayload(**snap, events=events)


@router.post("/source/start", response_model=MarketSourceResponse)
async def start_source(
    config: MarketSourceConfig | None = None,
) -> MarketSourceResponse:
    """Start the discovery polling loop. Body is an optional MarketSourceConfig;
    omitted / partial fields fall back to defaults."""
    cfg = config or MarketSourceConfig()
    try:
        await market_source_manager.start(cfg)
    except Exception as exc:  # noqa: BLE001 — surface to caller as ok=false
        return MarketSourceResponse(ok=False, error=str(exc), snapshot=_build_payload())
    return MarketSourceResponse(ok=True, snapshot=_build_payload())


@router.post("/source/stop", response_model=MarketSourceResponse)
async def stop_source() -> MarketSourceResponse:
    await market_source_manager.stop()
    return MarketSourceResponse(ok=True, snapshot=_build_payload())


@router.get("/source/status", response_model=MarketSourceResponse)
def status_source() -> MarketSourceResponse:
    return MarketSourceResponse(ok=True, snapshot=_build_payload())
