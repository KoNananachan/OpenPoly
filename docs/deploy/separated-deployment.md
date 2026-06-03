# Separated deployment (geoblock workaround)

> **You only need this if you are in a Polymarket-geoblocked region and want to
> trade live.** For development, paper trading, or trading from an allowed
> region, use the same-machine setup in [`README.md`](./README.md) — it's two
> commands and none of the below applies.
>
> Why this exists (the `403 "Trading restricted in your region"` problem) is
> explained in [`README.md`](./README.md#why-a-separated-mode-exists-at-all).

The shape: **backend runs on a VPS in a Polymarket-allowed region; the frontend
runs on your laptop and reaches the backend over an SSH tunnel.** The example
below uses a small CPU-only VPS. Replace `<deploy-host>` / `<deploy-user>` with
your own.

> Determining whether this is legal where you are is **your responsibility** —
> see the repository [DISCLAIMER](../../DISCLAIMER.md).

## SSH access

Use key-based auth and **disable password login** on the VPS. Define an alias in
your local `~/.ssh/config` so the deploy script and tunnel commands stay short:

```
Host openpoly-vps
    HostName <deploy-host>
    User <deploy-user>
    IdentityFile ~/.ssh/id_ed25519
    LocalForward 18000 127.0.0.1:18000   # tunnel for UI/API access
```

`ssh openpoly-vps 'whoami'` should succeed without a password before you go on.

## Layout on the VPS

```
/opt/openpoly/                  rsync'd source (.venv / .git / node_modules excluded)
  ├─ .venv/                     uv-managed Python + deps
  ├─ .env                       chmod 600 — secrets (loaded by systemd EnvironmentFile)
  └─ openpoly/                  application code
~/.openpoly/runtime.json        chmod 600 — wallet config + exec_mode
~/.openpoly/secrets.json        canvas-managed secrets store (`local:<name>` refs)
~/.openpoly/canvas.json         persisted canvas template
/etc/systemd/system/openpoly.service   systemd unit (auto-start + auto-restart)
/var/log/openpoly.out           stdout+stderr
```

A distinct port (`18000` here) keeps openPoly from clashing with anything else
the host might bind. The backend binds `127.0.0.1` only — never public.

## First-time deploy (cold)

From your local machine with the repo checked out and SSH configured:

```bash
# 1. Install uv on the VPS (user-local; no sudo / apt needed)
ssh openpoly-vps 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# 2. Stage code + deps
ssh openpoly-vps 'mkdir -p /opt/openpoly'
./scripts/deploy.sh   # rsync + uv sync (restart step fails until the unit is installed — expected)

# 3. (CPU-only VPS) strip CUDA libs torch pulls in — recovers several GB
ssh openpoly-vps 'rm -rf /opt/openpoly/.venv/lib/python*/site-packages/{nvidia,triton}*'
ssh openpoly-vps '~/.local/bin/uv cache clean'
```

### Configure secrets

Copy `.env.example` to `/opt/openpoly/.env` on the VPS and fill in the values
(see the comments in `.env.example` for what each one is). Keep it `chmod 600`.
For live trading you need at least `OPENPOLY_POLYMARKET_PK` and
`OPENPOLY_POLYMARKET_FUNDER`; paper mode needs neither.

`OPENPOLY_AUTOSTART_SOURCES=0` is recommended on memory-constrained hosts so the
embedding model (~500MB) doesn't load at boot.

Create `~/.openpoly/runtime.json` (chmod 600) — wallet config + exec mode:

```json
{
  "wallet": {
    "private_key_ref": "env:OPENPOLY_POLYMARKET_PK",
    "funder_address": "<your-funder-address>"
  },
  "exec_mode": "paper",
  "updated_at": null
}
```

### Install the systemd unit

Survives reboot, auto-restarts on crash:

```bash
ssh openpoly-vps 'cat > /etc/systemd/system/openpoly.service' <<'UNIT'
[Unit]
Description=openPoly backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/openpoly
EnvironmentFile=/opt/openpoly/.env
ExecStart=/opt/openpoly/.venv/bin/uvicorn openpoly.api.main:app --host 127.0.0.1 --port 18000
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/openpoly.out
StandardError=append:/var/log/openpoly.out

[Install]
WantedBy=multi-user.target
UNIT
ssh openpoly-vps 'systemctl daemon-reload && systemctl enable --now openpoly'
```

## Smoke test

```bash
ssh openpoly-vps '
systemctl status openpoly --no-pager -n 5
curl -s http://127.0.0.1:18000/api/health
curl -s http://127.0.0.1:18000/api/system/mode
'
```

Expect `Active: active (running)`, `{"status":"ok"}`, and `{"mode":"paper"}`.

## Operator access (UI / API)

The backend is bound to `127.0.0.1` — reach it through the SSH tunnel (the
`LocalForward` in your ssh config, or explicitly):

```bash
ssh -L 18000:127.0.0.1:18000 openpoly-vps
```

Then on your laptop:

- `http://localhost:18000/docs` — FastAPI Swagger UI
- Run the frontend against the tunnel:
  `cd frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:18000 yarn dev`

## Update an existing deploy

```bash
./scripts/deploy.sh
```

It rsyncs your worktree to the VPS, runs `uv sync` only when `pyproject.toml` /
`uv.lock` changed, restarts the service, and polls `/api/health`.

## Stop / restart / logs

```bash
ssh openpoly-vps 'systemctl restart openpoly'
ssh openpoly-vps 'systemctl stop    openpoly'
ssh openpoly-vps 'journalctl -u openpoly -n 50 --no-pager'
```

## Known issues on small CPU-only VPSes

| Issue | Cause | Mitigation |
|---|---|---|
| `torch` import fails | CUDA libs stripped to free disk | Fine in paper mode with `OPENPOLY_AUTOSTART_SOURCES=0` (embedding never loads). When you need embedding: reinstall the CPU wheel — `uv pip install torch --index-url https://download.pytorch.org/whl/cpu --force-reinstall --no-deps` |
| Out of memory at boot | Cheap VPS tier + embedding model | Keep `OPENPOLY_AUTOSTART_SOURCES=0`, or add a swapfile before enabling the embedding section |
| `polygon-rpc.com` returns 401 | Some public RPC operators restrict it | Use `https://polygon-bor-rpc.publicnode.com` (the `.env.example` default) |

## Bringing up live trading

1. Confirm a clean paper boot (smoke test above).
2. Open the Swagger UI over the tunnel → `POST /api/system/mode` `{"mode":"live"}`.
3. Preflight runs: derives API creds and checks pUSD balance + V2 allowances.
4. If it fails with `pusd_insufficient` / `*_not_approved`, fund or approve your
   DepositWallet on polymarket.com, then retry.
5. Open a position (news flow or manual), and verify the on-chain tx via
   `/api/positions` (`order_id` + `tx_hash` on filled rows).

Re-read the [DISCLAIMER](../../DISCLAIMER.md) before this step: live mode places
real orders with real funds, and you can lose your entire stake.
