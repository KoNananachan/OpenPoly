# Deployment

openPoly has two deployment shapes. **Pick based on where you are**, not on
scale — it's a single-process backend + a SQLite file + a frontend either way.

| | Default — same machine | Separated — geoblock workaround |
|---|---|---|
| **When** | Development, paper trading, or live trading from a region Polymarket allows | Live trading when your location is geoblocked by Polymarket |
| **Topology** | Backend + frontend on one box | Backend on a VPS in an allowed region; frontend on your laptop via SSH tunnel |
| **Setup** | Two commands (below) | See [`separated-deployment.md`](./separated-deployment.md) |

## Default — same machine

This is the path for almost everyone. Backend and frontend run on the same
machine; the frontend dev server proxies API calls to the local backend.

```bash
# 1. Backend — binds 127.0.0.1:8000 (uvicorn default), paper mode by default
uv run uvicorn openpoly.api.main:app

# 2. Frontend — in another terminal, proxies to the local backend on :8000
cd frontend && yarn install && yarn dev
```

That's it. The frontend's proxy target defaults to `http://127.0.0.1:8000`, so
no environment variable is needed when both run locally. Open the printed Vite
URL and the strategy canvas loads.

openPoly **defaults to paper mode** — no real funds are touched until you
explicitly switch to live (`POST /api/system/mode`). See the repository
[DISCLAIMER](../../DISCLAIMER.md) before going live.

## Why a separated mode exists at all

Polymarket's CLOB `POST /order` endpoint is **region-blocked**. Order placement
from a geoblocked location (the US and ~32 other countries — see
[Polymarket's geoblock docs](https://docs.polymarket.com/developers/CLOB/geoblock))
returns:

```
403 "Trading restricted in your region"
```

Reads (markets, prices, your positions) work from anywhere; only **order
submission** is blocked. So if you're in a blocked region and want to trade
live, the backend — the part that submits orders — has to run from an allowed
region. The frontend can stay on your laptop and reach it over an SSH tunnel.

That's the only reason for the separated topology. If you're not geoblocked,
ignore it entirely and use the same-machine setup above.

> Determining whether trading on Polymarket is legal where you are is **your
> responsibility** — see the [DISCLAIMER](../../DISCLAIMER.md). openPoly does
> not endorse circumventing any legal restriction.

For the separated setup (VPS, systemd, SSH tunnel, CPU-only install notes), see
[`separated-deployment.md`](./separated-deployment.md).
