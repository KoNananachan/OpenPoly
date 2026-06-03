# Contributing to openPoly

Thanks for your interest! openPoly is an experimental, MIT-licensed live-trading
framework for Polymarket. This guide is for **human contributors** — if you're
an AI coding agent, start with [`AGENTS.md`](./AGENTS.md), the machine-facing
convention layer.

> ⚠️ openPoly can place **real orders with real money**. It defaults to **paper
> mode**. Never exercise contributions against live mode unless you fully
> understand the risk — read the [DISCLAIMER](./DISCLAIMER.md) first.

## Ways to contribute

- **New sections** — the main extension point (news sources, embeddings,
  analyzers, entry/exit logic, market/database backends).
- Bug fixes, test coverage, and documentation.
- Performance and reliability improvements to the runtime/pipeline.

## Two homes for a section — pick the right one

- **A private strategy of your own** → drop it in
  [`openpoly/user_sections/`](./openpoly/user_sections/README.md). That directory
  is **gitignored** and runs locally; it is *not* a PR target.
- **A reusable section for everyone** → it lives in `openpoly/sections/<type>/`
  with a versioned filename, a frontend mirror, and tests. That is what a pull
  request contains.

## Development setup

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/) (Python toolchain) and
Node.js + `yarn`.

```bash
# Backend — binds 127.0.0.1:8000, paper mode by default
uv run uvicorn openpoly.api.main:app

# Frontend (from frontend/) — Vite dev server on :5173, proxies /api → :8000
cd frontend && yarn install && yarn dev
```

## Before you open a PR

Run the full local gate and make sure it's green:

```bash
# Backend
uv run pytest
uv run ruff check .
uv run ruff format .

# Frontend (from frontend/)
yarn typecheck
yarn lint
```

CI **blocks merges** on `ruff check` + `pytest` (backend) and `yarn typecheck` +
`yarn lint` (frontend). `ruff format` is **not** a CI gate — run it locally to
keep formatting consistent, but it won't fail your PR.

If you added a **section**, also:

- Implement the contract in [`openpoly/sections/_base.py`](./openpoly/sections/_base.py)
  (`SECTION_TYPE`, `SECTION_VERSION`, `REQUIRES`, `Config`, sync `run()`).
- Add a `CONTRACT_TEST` `@staticmethod` so the registry can self-verify the impl.
- Mirror the section type in `frontend/src/sections/<type>/` by name.
- Add tests under `tests/`.
- Full contract: [`docs/architecture/02-strategy-sections.md`](./docs/architecture/02-strategy-sections.md).

## Conventions

The complete list lives in [`AGENTS.md`](./AGENTS.md); the ones that most often
trip people up:

- **Versioned impls** — ship new behavior as a **new file** (`edge_threshold_v1.py`),
  don't silently rewrite a `_v0` in place.
- **Clocks are UTC**, always. **Code, comments, and UI strings are English.**
- **No hardcoded secrets** — everything resolves via `*_ref` indirection
  (`env:` / `local:` / keychain). Never commit `.env`, `openpoly.db`, or any
  `*.db` sidecar.
- **Paper mode is the default**; live trading is an explicit, deliberate opt-in.
- **Don't confuse layers** — `openpoly/db/` (SQLite engine) ≠ `sections/database/`;
  `openpoly/news/` · `markets/` (domain logic) ≠ `sections/news_source/` ·
  `market_source/` (section impls).

## Pull request process

1. Branch from `main`; keep PRs small and focused.
2. Describe **what** changed and **why**; link any related issue.
3. Ensure the local gate above passes.
4. A maintainer will review. Be ready to iterate.

## Reporting security issues

Do **not** open a public issue for security problems. See
[`SECURITY.md`](./SECURITY.md) for private disclosure.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](./LICENSE).
