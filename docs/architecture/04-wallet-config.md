# Wallet configuration

The wallet is system-level: registered once via the Wallet panel.
**Paper-mode runs (M1–M3) need no wallet at all** — paper uses a virtual book.

openPoly is a single system, so there is **one wallet** — no per-run wallets,
no sidecar executor (those belonged to the dropped heavy-isolation design; see
[01-isolation.md](01-isolation.md)).

## Storage (slice A — current)

For the M4 slice-A milestone (wallet config + mode switch, no live executor
yet), wallet config does not get DB tables. The split is:

| Data | Where | Why |
|---|---|---|
| Mnemonic (secret) | Secret store, recommended via `env:OPENPOLY_WALLET_MNEMONIC` (in `.env`); `local:` also supported | Open-source norm: secrets in `.env` (gitignored), not in app-managed files |
| `mnemonic_ref` selection + `derivation_path` + `exec_mode` | `~/.openpoly/runtime.json` (dotfile, like `canvas.json` / `secrets.json`) | Mutable from UI without DB migration; symmetric with existing dotfile pattern |
| Derived address | Computed on demand from mnemonic via `eth-account`; not cached | Derivation is fast (BIP-39/44); avoids stale-cache concerns |

`OPENPOLY_RUNTIME_STATE` env var can override the dotfile path (tests).

## Storage (slice C — planned)

When slice C lands (live executor via `py-clob-client`), only one new table
is mandatory: **`wallet_tx_log`** (append-only signed-transaction audit).

The originally-planned `wallet_config` and `wallet_state` tables are
**dropped from the plan** — the dotfile + on-demand derivation already cover
what they would have held. If balance caching becomes a hot-path concern in
slice C/D (e.g. dashboard polling), revisit `wallet_state` then.

## Prod (M4)

Live trading (M4) signs orders with this one wallet's key, materialized in
memory only when a signature is needed. It funds grain-scale capital ($5–$50), which
itself caps blast radius. Paper runs never touch a wallet.
