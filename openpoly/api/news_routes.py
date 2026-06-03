"""News-related HTTP routes.

POST /api/news/test
    Verifies a (endpoint, api_key_ref) pair is usable by opening a short-lived
    WebSocket connection to the endpoint with the resolved key. Used by the
    Setting page's "Test connection" button; this server-side proxy bypasses
    browser CORS restrictions that would otherwise block direct WS attempts
    from the page.

POST /api/news/source/start
POST /api/news/source/stop
GET  /api/news/source/status
    Lifecycle of the long-running news_source instance owned by
    ``openpoly.news.manager.manager`` (N3). Status responses embed the recent
    event ring so the frontend Live tab can poll a single endpoint.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote

import websockets
from fastapi import APIRouter
from pydantic import BaseModel
from websockets.exceptions import (
    InvalidStatus,
    InvalidURI,
    WebSocketException,
)

from openpoly.news.manager import manager as news_source_manager
from openpoly.news.secrets import SecretsError, resolve as resolve_secret

router = APIRouter(prefix="/api/news", tags=["news"])

# Bound the test so a misconfigured endpoint can't hang the request.
TEST_OPEN_TIMEOUT_SECS = 5.0


class NewsTestRequest(BaseModel):
    endpoint: str
    api_key_ref: str


class NewsTestResponse(BaseModel):
    ok: bool
    error: str | None = None
    latency_ms: int | None = None


@router.post("/test", response_model=NewsTestResponse)
async def test_connection(req: NewsTestRequest) -> NewsTestResponse:
    try:
        api_key = resolve_secret(req.api_key_ref)
    except SecretsError as exc:
        return NewsTestResponse(ok=False, error=f"secret resolve failed: {exc}")
    except NotImplementedError as exc:
        return NewsTestResponse(ok=False, error=f"secret scheme not supported: {exc}")

    # tradingnews requires `?api_key=` query auth; headers are ignored.
    sep = "&" if "?" in req.endpoint else "?"
    auth_endpoint = f"{req.endpoint}{sep}api_key={quote(api_key, safe='')}"

    start = time.monotonic()
    try:
        async with asyncio.timeout(TEST_OPEN_TIMEOUT_SECS):
            async with websockets.connect(
                auth_endpoint,
                open_timeout=TEST_OPEN_TIMEOUT_SECS,
                ping_interval=None,
            ):
                pass  # open + close cleanly
    except asyncio.TimeoutError:
        return NewsTestResponse(
            ok=False, error=f"connect timed out after {TEST_OPEN_TIMEOUT_SECS:.1f}s"
        )
    except InvalidStatus as exc:
        # tradingnews returns 401/403 for bad keys via Upgrade response.
        return NewsTestResponse(ok=False, error=f"server rejected upgrade: {exc}")
    except InvalidURI:
        return NewsTestResponse(ok=False, error="invalid WebSocket endpoint URI")
    except (WebSocketException, OSError) as exc:
        return NewsTestResponse(ok=False, error=f"connection failed: {exc}")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return NewsTestResponse(ok=True, latency_ms=elapsed_ms)


# ---------- /api/news/source/* — manager lifecycle (N3) ----------

# How many recent events to embed in each status response. The manager keeps
# up to EVENT_RING_MAXLEN (200) internally; frontend Live tab can ask for more
# via a future dedicated endpoint if 200 ever proves insufficient.
EVENT_RING_RESPONSE_LIMIT = 200


class NewsSourceStartRequest(BaseModel):
    endpoint: str
    api_key_ref: str
    freshness_seconds: int | None = None
    urgency_filter: str | None = None
    buffer_size: int | None = None


class SnapshotPayload(BaseModel):
    state: str
    started_at: float | None
    last_msg_at: float | None
    total_recv: int
    buffer_size: int
    running_config: dict[str, Any] | None
    last_error: str | None
    reconnect_attempts: int
    events: list[dict[str, Any]]
    recent_messages: list[dict[str, Any]]


class NewsSourceResponse(BaseModel):
    ok: bool
    error: str | None = None
    snapshot: SnapshotPayload


# Live tab shows last 5 messages per plan (module breakdown N7). Buffer holds up to
# config.buffer_size (default 1000) so older history is still available to
# Analyzer; here we only surface a small tail for human inspection.
MESSAGE_TAIL_LIMIT = 5


def _build_payload() -> SnapshotPayload:
    snap = news_source_manager.status().to_dict()
    events = [e.to_dict() for e in news_source_manager.events(limit=EVENT_RING_RESPONSE_LIMIT)]
    recent_messages = news_source_manager.recent_messages(limit=MESSAGE_TAIL_LIMIT)
    return SnapshotPayload(**snap, events=events, recent_messages=recent_messages)


@router.post("/source/start", response_model=NewsSourceResponse)
async def start_source(req: NewsSourceStartRequest) -> NewsSourceResponse:
    # Fast-fail on secret resolution before bothering the manager / source.
    try:
        resolve_secret(req.api_key_ref)
    except SecretsError as exc:
        return NewsSourceResponse(
            ok=False, error=f"secret resolve failed: {exc}", snapshot=_build_payload()
        )
    except NotImplementedError as exc:
        return NewsSourceResponse(
            ok=False, error=f"secret scheme not supported: {exc}", snapshot=_build_payload()
        )

    config = req.model_dump(exclude_none=True)
    try:
        await news_source_manager.start(config)
    except Exception as exc:  # noqa: BLE001 — surface to caller as ok=false
        return NewsSourceResponse(ok=False, error=str(exc), snapshot=_build_payload())
    return NewsSourceResponse(ok=True, snapshot=_build_payload())


@router.post("/source/stop", response_model=NewsSourceResponse)
async def stop_source() -> NewsSourceResponse:
    await news_source_manager.stop()
    return NewsSourceResponse(ok=True, snapshot=_build_payload())


@router.get("/source/status", response_model=NewsSourceResponse)
def status_source() -> NewsSourceResponse:
    return NewsSourceResponse(ok=True, snapshot=_build_payload())
