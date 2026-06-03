# System-level configuration

> **v9 update.** The standalone Setting page (News / LLM / Wallet panels) was
> removed — secrets are now managed from the canvas (a `Keys` drawer + a
> `RefWidget` dropdown on each section's `*_ref` field; see **UI: secret
> management** below). The `*_ref` indirection and the local-store security
> model are unchanged. The **Config tables** section and any "capability
> injection" wording below predate the single-system pivot (see
> [01-isolation.md](01-isolation.md)) and are kept only as historical context.

Three concerns are explicitly **system-level**, shared system-wide:
News-source API, LLM API, and Wallet.

## The three system configs

| Concern | Purpose | Secret indirection |
|---|---|---|
| **News source API** | feeds raw news items into NewsIngest | `news_api_ref` |
| **LLM API** | provides chat / embedding clients | `llm_api_ref` |
| **Wallet** | signs orders (prod runs only) | see [04-wallet-config.md](04-wallet-config.md) |

## Secret indirection: `*_ref`

Secrets never live in DB rows, templates, or section configs. Stored values are **reference strings** that the runtime resolves at use time.

| Scheme | Form | Status |
|---|---|---|
| `env` | `env:OPENPOLY_TRADINGNEWS_KEY` | ✅ implemented |
| `local` | `local:demo-baseline/news_source/tradingnews-main` | ✅ implemented (v5) |
| `vault` | `vault:secret/data/openpoly/llm#api_key` | reserved |
| `keychain` | `keychain:com.openpoly.llm/api_key` | reserved (mainnet upgrade path) |

The store treats `local:` names as flat, opaque keys. `/` is still permitted, but v9 dropped the `<strategy>/<section_type>/<keyname>` convention — with a single strategy the prefix disambiguates nothing, so new keys are flat (e.g. `local:tradingnews-key`).

### Security model (local store)

- File at `~/.openpoly/secrets.json`, chmod `0o600`. Override path via env `OPENPOLY_SECRET_STORE`.
- **Plaintext at rest**. Same-user processes can read. Acceptable for grain-scale paper; mainnet should swap this for an OS-keychain backed store (`keychain:` scheme).
- Backend HTTP **must bind loopback** — no public exposure of the store.
- Endpoints **never return secret values**; only names + `created_at` (enforced by Pydantic response models + grep tests).
- Section impls never see the resolved value either — runtime injects the initialized client (per capability injection in [02-strategy-sections.md](02-strategy-sections.md)).

## Config tables

All tables live in the one SQLite database — flat, no schema split (see
[01-isolation.md](01-isolation.md)).

| Table | Holds |
|---|---|
| `news_source_config` | name, endpoint, `api_ref`, rate limits |
| `llm_provider_config` | provider, base_url, model, `api_ref` |
| `system_settings` | scalar flags (paper/prod default, log level, retention) |
| `strategy_template` | canvas serialization (section choices + defaults) |
| `section_catalog` | registered impls + their contract-test snapshot |

(Wallet uses its own 3 tables — see [04-wallet-config.md](04-wallet-config.md).)

## UI: secret management

There is **no separate Setting page** (removed v9). Secrets are managed from
within the canvas:

- **`Keys` drawer** (canvas top bar) — the stored-key CRUD: add / list /
  delete `local:` entries. Values are write-only from the UI's perspective.
- **`RefWidget`** — every section config field ending in `_ref` renders as a
  dropdown of stored keys, with a "+ New key" shortcut and a manual-entry
  mode for `env:` / `vault:` / `keychain:` refs. A dangling `local:` ref
  (referenced key deleted) is flagged inline.
- **Test connection** — the news_source section's Live tab opens a
  short-lived WS to verify (endpoint, `api_key_ref`).

Canvas section forms only ever hold `*_ref` strings, never raw secrets, so
strategy templates stay **shareable / open-source-safe** — importing one only
requires the receiving operator to register the referenced secrets in their
own local store (or env).
