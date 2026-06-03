"""Local secret store HTTP routes (v5 / S3).

Three endpoints over ``LocalSecretStore``:

- ``POST   /api/secrets/local``        — create or overwrite a stored secret
- ``GET    /api/secrets/local``        — list entries (names + created_at only)
- ``DELETE /api/secrets/local/{name}`` — delete by name (path can contain ``/``)

**Hard contract**: no endpoint response, status line, or header ever contains
the secret value. Tests in ``tests/test_api_secrets.py`` grep response bodies
for a sentinel value to prevent regressions.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from openpoly.news.secret_store import (
    InvalidName,
    NameNotFound,
    get_store,
)

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


class CreateSecretRequest(BaseModel):
    name: str
    value: str


class SecretEntryResponse(BaseModel):
    name: str
    created_at: float


class CreateSecretResponse(BaseModel):
    ok: bool
    entry: SecretEntryResponse


class ListSecretsResponse(BaseModel):
    entries: list[SecretEntryResponse]


@router.post("/local", response_model=CreateSecretResponse)
async def create_local(req: CreateSecretRequest) -> CreateSecretResponse:
    try:
        entry = await get_store().set(req.name, req.value)
    except InvalidName as exc:
        # 400 carries only the name (or generic "value must be non-empty"),
        # never the value — InvalidName's message format is controlled in
        # secret_store.py and validated by tests.
        raise HTTPException(status_code=400, detail=str(exc))
    return CreateSecretResponse(
        ok=True,
        entry=SecretEntryResponse(name=entry.name, created_at=entry.created_at),
    )


@router.get("/local", response_model=ListSecretsResponse)
def list_local(prefix: str | None = None) -> ListSecretsResponse:
    entries = get_store().list_entries(prefix=prefix)
    return ListSecretsResponse(
        entries=[SecretEntryResponse(name=e.name, created_at=e.created_at) for e in entries]
    )


@router.delete("/local/{name:path}", status_code=204)
async def delete_local(name: str) -> Response:
    try:
        await get_store().delete(name)
    except InvalidName as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NameNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(status_code=204)
