# Overview & ethos

openPoly is an experimental live-trading system for Polymarket prediction
markets. It is a fresh git repo seeded by lessons from a prior internal
project, but is **not** a port — the strategy decision logic is re-atomized
into modular Section modules.

## Mission

Validate signal alpha on real Polymarket live trades at grain-scale capital ($5–$50
initial), iterating fast on one strategy. Pure paper mode is the M1–M3 default;
live trading kicks in at M4 against Polygon mainnet only.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python + FastAPI, single process | shared language with strategy logic |
| DB | SQLite | single system, single writer — zero infra |
| Polymarket SDK | `py-clob-client` | official client (order placement) |
| Frontend | React + React Flow | strategy canvas UI |

openPoly is a **single system** — one process, one pipeline, one SQLite file.
See [01-isolation.md](01-isolation.md).

## License & ethos

- **MIT**. Open-source is a mindset, not a future milestone.
- **Default paper mode** — live trading requires explicit opt-in.
- **Zero hardcoded secrets** — all secrets via `*_ref` indirection (env / vault / keychain).
- **Cross-platform** — Linux / macOS first; no Windows-only flows.
- **Disclaimer ships with the repo**.

## Repo layout

- `docs/architecture/` — design decisions (this folder)
- `openpoly/` — Python backend (FastAPI app + event-driven pipeline + sections)
- `frontend/` — React Flow canvas + system config panels
- `tests/` — pytest suite
