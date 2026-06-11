# Polymarket API surfaces

How openPoly reads from and writes to Polymarket. Three distinct HTTP surfaces,
each with a different job. All reads are **unauthenticated**; only order
placement (CLOB, via `py-clob-client`) needs API credentials.

| Surface | Base URL | Auth | Role in openPoly |
|---|---|---|---|
| **Gamma** | `gamma-api.polymarket.com` | none | market metadata — discovery, settlement detection, single-market lookup |
| **CLOB** | `clob.polymarket.com` | none for reads; keys for orders | order book, price history, order placement, CTF balance |
| **Data API** | `data-api.polymarket.com` | none | per-wallet on-chain holdings (`/positions` — reconciliation), portfolio value (`/value` — wallet-balance card), trade history (`/trades` — manual forensics) |

Source of truth: `openpoly/markets/polymarket_api.py` (Gamma + CLOB read layer),
`openpoly/markets/models.py` (`normalize_gamma_market`), and the live executor
(`py-clob-client` order placement + balance reads).

---

## Gamma — market metadata

Gamma is a **read-only metadata API**. It describes markets/events (questions,
token ids, prices, liquidity, resolution status). It does **not** place orders.
openPoly hits exactly two Gamma endpoints.

### `/events` — discovery (the main read path)

`discover_events()` — the primary market-discovery loop. Fetches active events
ranked by 24h volume; each event nests its markets.

```
GET gamma-api.polymarket.com/events
    ?closed=false &order=volume24hr &ascending=false &limit=100
```

| Param | Value | Note |
|---|---|---|
| `closed` | `false` | only live markets |
| `order` / `ascending` | `volume24hr` / `false` | top markets by volume first |
| `limit` | `100` | **bounds events, not markets** — one event can hold dozens of markets |

Returns a flat list of `(raw_market, parent_event)` pairs. The parent event is
threaded through so `normalize_gamma_market` can attach event metadata
(tags / title / id) to every `Market`. Polymarket's own guidance is to discover
via `/events` and work backwards — events embed their markets, cutting API calls.

### `/markets` — settlement detection + single-market lookup

The **same** endpoint serves two openPoly callers with different params. Note
this is a *different endpoint* from discovery (`/events`), not "discovery plus a
flag" — but the two `/markets` callers below do share one URL and differ only by
query params.

**(a) Settlement detection** — `fetch_markets_by_condition_id()`:

```
GET gamma-api.polymarket.com/markets
    ?condition_ids=<cid>&condition_ids=<cid2>&closed=true&limit=N
```

Used by `settlement_monitor`: when a tick can no longer price an open position's
market, it asks Gamma "did this condition resolve, and which way?" Resolution is
read from ordinary market fields (below), not a dedicated "is-settled" endpoint.

> **Two gotchas — both were live bugs (v21).** Neither is in the official docs;
> both are empirically verified against the real API.
>
> 1. **`closed=true` is mandatory.** `/markets` defaults to *open-only*. Omit
>    the flag and every resolved market silently comes back empty — which is
>    exactly the set settlement needs.
> 2. **`condition_ids` must be a *repeated* param** (`condition_ids=A&condition_ids=B`),
>    not comma-joined (`condition_ids=A,B`). Gamma treats the comma value as one
>    unmatched id and returns nothing. httpx serializes a Python list value into
>    the repeated form, which is why `_get_json` accepts `list[str]` param values.

**(b) Single-market lookup** — `fetch_market_by_id()`:

```
GET gamma-api.polymarket.com/markets?id=<market_id>
```

Used by the holding-sync hook in `MarketSourceManager` so the catalog covers
every open position regardless of whether its event ranks in the `/events`
top-100 window. Returns `None` on HTTP error / empty / normalize failure — the
caller treats `None` as "skip this position, retry next poll".

### Gamma market object → `Market` (field mapping)

`normalize_gamma_market(raw, event)` converts one raw Gamma market dict into the
internal `Market`. Returns `None` (untradeable) when `clobTokenIds`, `id`, or
`conditionId` is missing.

| `Market` field | Gamma source | Note |
|---|---|---|
| `market_id` | `id` | |
| `condition_id` | `conditionId` (or `condition_id`) | on-chain CTF condition |
| `question` / `slug` | `question` / `slug` | |
| `yes_token_id` / `no_token_id` | `clobTokenIds[0]` / `[1]` | JSON-array-in-a-string; `[Yes, No]` order |
| `end_date` | `endDate` / `endDateIso` | parsed ISO |
| `best_bid` / `best_ask` / `spread` | `bestBid` / `bestAsk` / `spread` | |
| `last_trade_price` | `lastTradePrice` | |
| `volume_24h` | `volume24hr` | |
| `liquidity` | `liquidityNum` (fallback `liquidity`) | |
| `taker_fee_rate` | derived from `feesEnabled` | |
| `closed` | `closed` | **fail-closed** (default `True`) |
| `accepting_orders` | `acceptingOrders` | **fail-closed** (default `False`) |
| `enable_order_book` | `enableOrderBook` | **fail-closed** (default `False`) |
| `neg_risk` | `negRisk` | |
| `outcome_prices` | `outcomePrices` | settlement payout, e.g. `["0","1"]` |
| `event_id` / `event_title` / `event_tags` | parent event `id` / `title` / `tags[].slug` | |

**Resolution is inferred from ordinary fields**, not a special response shape.
A resolved market shows `closed=true`, `umaResolutionStatus="resolved"`, and
`outcomePrices` like `["0","1"]` (the `1` side won, indexed against `outcomes`).
There is no dedicated settlement endpoint — settlement is just `/markets` with
`closed=true` plus reading these fields.

---

## CLOB — order book, price history, trading

`clob.polymarket.com`. Reads are unauthenticated; order placement uses
`py-clob-client` with API credentials.

### `/book` — order book (read)

`fetch_book(token_id, depth=3)` — one token's order book, normalized best-first
and trimmed to the top `depth` levels per side. Feeds depth-aware sizing/exit
logic.

```
GET clob.polymarket.com/book?token_id=<token_id>
```

### `/prices-history` — recent price series (read, sync)

`fetch_price_history(token_id, window_min, fidelity=10)` → `(epoch_ts, price)`
points, oldest-first. Backs `recent_move()` (a late-buy veto). This is the
**sync** fetch variant (`_sync_get_json`) because it runs inside a section's
`run()`, already offloaded to a worker thread.

```
GET clob.polymarket.com/prices-history
    ?market=<token_id>&startTs=<t0>&endTs=<now>&fidelity=10
```

Note: `/prices-history` returns only a scalar `price` per point — **no historical
order-book depth or spread**. Depth history must be self-collected from `/book`
day one (see market-data persistence).

### Order placement + CTF balance (via `py-clob-client`)

The live executor (`live_executor.py`) places orders through `py-clob-client`,
not raw HTTP. One read worth flagging for on-chain reconciliation work:

```python
clob.get_balance_allowance(
    BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=...)
)
```

This already reads the wallet's **on-chain CTF token balance** through the CLOB —
so balance checks do not require a separate web3 dependency. Caveat: the CLOB
balance has cache-lag; the executor calls `update_balance_allowance` before
reading.

---

## Data API — per-wallet on-chain truth

Three endpoints, all keyed by the funder address:

- **`/positions?user=<funder>&sizeThreshold=0`** — what the wallet actually
  holds on-chain, neg-risk-aware. Used by `fetch_held_condition_sides` for the
  reconciliation monitor (both directions: close DB-open positions that are
  flat on-chain, and alert on untracked on-chain holdings).
- **`/value?user=<funder>`** — total open-position market value
  (`[{"user", "value"}]`; matches the per-position `size × curPrice` sum).
  Used by `fetch_wallet_positions_value` for the wallet-balance dashboard card.
- **`/trades?user=<funder>`** — each fill's `price`, `size`, `timestamp`,
  `transactionHash`. Not used in code; manual forensics only (it recovered
  actual sell prices during incident forensics). Cross-check any recovered price
  against the on-chain tx before trusting it.

---

## Conventions across all surfaces

- **Reads retry once**, then raise (async `_get_json`) or return `None` at the
  public boundary (`fetch_market_by_id`). A failed read = "skip, retry next
  poll", never a crash.
- **Fail-closed normalization**: `closed` / `acceptingOrders` / `enableOrderBook`
  default to the safe (non-tradeable) value when absent, so schema drift can't
  silently flip a market tradeable.
- **No auth for any read** used here — only order placement needs keys.
- **`condition_id` is the on-chain link**: the same value indexes the CTF
  contract on Polygon, joining Gamma metadata ↔ on-chain balance/payout.
