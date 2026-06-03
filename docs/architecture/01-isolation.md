# Single-system architecture

> Supersedes the earlier "heavy isolation" design (per-run container / schema /
> wallet / sidecar executor). On 2026-05-21 the multi-strategy-parallel goal was
> dropped — openPoly is a **single system**, and the code was always built that
> way. This doc describes that single-system shape.

## Shape

openPoly runs as **one FastAPI process**. There is no per-run container, no
per-run database schema, no per-run wallet, no sidecar executor, no Redis.

- **One process** — the FastAPI app hosts the event-driven pipeline and the
  background loops (news WS, market-source discovery + order-book sampling).
- **One pipeline** — `news_source → orchestrator → analyzer → trader`;
  `market_source` runs alongside as a capability backend.
- **One SQLite database** — a single file (see [Why SQLite](#why-sqlite)).
- **One wallet** (prod, M4 only) — see [04-wallet-config.md](04-wallet-config.md).

## Why SQLite

The original Postgres choice existed to give each parallel strategy run its own
schema, and to avoid the concurrent-write corruption a prior project hit with
SQLite (a freelist-corruption incident).

With the multi-run goal dropped, **the system has a single writer** — the
write-behind writer drains one queue sequentially. SQLite's whole-file write
lock is therefore never contended; the corruption class was a *multi-writer*
problem and does not arise. SQLite costs zero infrastructure (one file, no
server) for this workload.

Persistence goes through SQLAlchemy (dialect-agnostic), so moving to Postgres
later — if multi-process or multi-strategy ever returns — is a URL change plus
whatever isolation work that would then need.

## Tables

All tables live in the one SQLite database — flat, no `public` / `run_<id>`
schema split. Families:

- **Config** — news-source / LLM-provider / system-settings, the strategy
  template, the section catalog (see [03-system-config.md](03-system-config.md)).
- **Wallet config** — see [04-wallet-config.md](04-wallet-config.md).
- **Runtime data** — order-book snapshots, decision / signals log,
  positions / trades (paper backend), metrics — added as their features land.

## What this drops

Gone from the earlier heavy-isolation design: per-run Docker containers, per-run
Postgres schemas (`run_<id>`), per-run wallets, per-run sidecar executors, Redis
pub-sub IPC, the multi-process worker model, and "strategy variants run in
parallel without cross-pollution" as a goal.

Two things that design cited as motivation are still served — by the **decision
audit log** (every decision persisted with its hard-data snapshot), not by
process/schema isolation:

- **Bit-level replay** (v8 §0.5) — reconstructed offline from the audit log.
- **Blast-radius bounding** — the single wallet holds only grain-scale capital; the
  kill switch force-closes on breach.
