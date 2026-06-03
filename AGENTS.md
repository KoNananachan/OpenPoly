# AGENTS.md

Orientation for AI coding agents (Claude Code, Cursor, Codex, …) working in this
repo. Humans should start with [`README.md`](./README.md); this file is the
machine-facing convention layer. Keep it accurate when conventions change.

## Run / test / lint

```bash
# Backend (binds 127.0.0.1:8000, paper mode by default)
uv run uvicorn openpoly.api.main:app

# Tests
uv run pytest

# Lint / format (line length 100, target py312)
uv run ruff check .
uv run ruff format .

# Frontend (from frontend/)
yarn install
yarn dev          # Vite dev server on :5173, proxies /api → 127.0.0.1:8000
yarn typecheck    # tsc -b --noEmit
yarn lint         # eslint
```

CI enforces (must pass to merge): **`ruff check` + `pytest`** (backend) and
**`yarn typecheck` + `yarn lint`** (frontend). **`ruff format` is a local
convention, not a CI gate** — run it before pushing, but it is intentionally not
enforced (avoids ruff-version-drift churn blocking merges).

Same-machine (backend + frontend on one box) is the **default and needs no env
vars** — the frontend proxies to `127.0.0.1:8000` out of the box.

## Architecture in one breath

A **single process, single pipeline, single SQLite file** — no multi-process,
no Redis, no Postgres. The FastAPI app drives an event-driven pipeline built from
**sections**: typed, swappable strategy modules. There is no per-strategy
isolation abstraction; it is deliberately one system. See
[`docs/architecture/`](./docs/architecture/00-overview.md).

## The extension point: sections

Strategy work happens in sections. Each impl is a class satisfying the contract
in [`openpoly/sections/_base.py`](./openpoly/sections/_base.py):

```python
SECTION_TYPE: ClassVar[SectionTypeName]   # one of: news_source, market_source,
                                          # embedding, analyzer, entry, exit, database
SECTION_VERSION: ClassVar[str]
REQUIRES: ClassVar[list[Capability]]      # news, llm, market_data, order_book,
                                          # news_history, portfolio
Config: ClassVar[type[BaseModel]]         # Pydantic — single source of param schema
def run(self, input: SectionInput) -> SectionOutput: ...   # SYNC
```

- `run()` is **synchronous**. The runtime owns scheduling, capability injection,
  audit, and timeouts — a section must not spawn its own event loop or block it.
- Optionally declare a `CONTRACT_TEST` `@staticmethod`; the registry invokes it
  and rejects the impl if it raises.
- Full design: [`docs/architecture/02-strategy-sections.md`](./docs/architecture/02-strategy-sections.md).

**To add your own strategy**, drop the class into
[`openpoly/user_sections/`](./openpoly/user_sections/README.md) — the registry
discovers it at startup. That directory is **gitignored** (a trust boundary; your
impls run with full backend privileges and are not sandboxed). Do **not** modify
the framework files `sections/_base.py`, `_registry.py`, `_contract_test.py`
unless you are deliberately changing the contract.

## Conventions

- **Versioned impls** — section impl files carry a version suffix
  (`edge_threshold_v0.py`, `llm_v0.py`). Ship a new behavior as a **new file**
  (`*_v1.py`), do not silently rewrite a `_v0` in place.
- **Front/back mirror** — `frontend/src/sections/<type>/` mirrors
  `openpoly/sections/<type>/` by name. Add a section type in one, mirror it in
  the other.
- **Don't confuse layers** — `openpoly/db/` is the SQLite engine, *not*
  `sections/database/` (a swappable section). `openpoly/news/` · `markets/` are
  domain logic, *not* `sections/news_source/` · `market_source/` (section impls).

## Hard rules

- **Clocks are UTC**, always.
- **Source code, comments, and UI strings are English** (the repo is open source
  / internationalized).
- **Never commit `openpoly.db`** (or any `*.db` / WAL sidecar) — it's gitignored
  dev state. Override the path with `OPENPOLY_DB_URL` for throwaway runs.
- **No hardcoded secrets** — everything resolves via `*_ref` indirection
  (`env:` / `local:` / keychain). Copy `.env.example` → `.env` (gitignored).
- **Paper mode is the default**; live trading places real orders with real funds
  and must be an explicit opt-in. See [`DISCLAIMER.md`](./DISCLAIMER.md).

## Deployment: assume same-machine

Default topology is **same machine** — one box runs backend + frontend. Do **not**
assume a split/remote backend. A separated deployment (backend on a remote VPS,
frontend local over an SSH tunnel) exists **only** as a workaround for
Polymarket's order-placement geoblock, and is opt-in. See
[`docs/deploy/`](./docs/deploy/README.md).
