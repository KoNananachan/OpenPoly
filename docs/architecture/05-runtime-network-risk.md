# Runtime, network scope, risk budget

## Network scope: Polygon mainnet only

Polymarket contracts (CTF + CLOB) deploy **only on Polygon mainnet**. There is no Mumbai testnet equivalent.

- **M1–M3**: paper backend (simulated fills against live order books). No on-chain action.
- **M4**: mainnet live trading. Skip testnet — there is no testnet.

Real-money risk shows up the moment we go past paper. There is no halfway environment.

## Risk budget

Initial live capital is grain-scale — **$5–$50 per run**.

- M4 dry-run standard: a strategy passes paper for ≥ 2 weeks of decisions before mainnet enable.
- Kill switch (see [02-strategy-sections.md](02-strategy-sections.md#fixed-system-services-parameter-panel-only) fixed services) runs warn-only for ≥ 2 weeks before enforcing.
- The single wallet funds only grain-scale capital, capping absolute blast radius even if the kill switch fails to fire.

## Exit policy: four paths, no others

The only ways a position closes:

1. **Settlement** — market resolves on-chain; no fee, no slippage.
2. **Kill switch** — a system-level circuit breaker force-closes all open positions for the run.
3. **Strategy active close** — the strategy's `exit_monitor` section emits a close decision.
4. **Manual** — operator UI / CLI action.

Explicitly **not** in this list:

- `review_position` (no continuous mid-life "review" cycle outside `exit_monitor`)
- `hard_exit` as a runtime concept distinct from `exit_monitor` (collapsed into the strategy active close path)
- `peak_exit_cost` as a runtime-tracked state (sections can compute it internally if they need it; not a system-owned field)

## Operational gotchas

### Async reconnect starvation

The pattern "sync DB call inside async function + dedup fast-path" can yield zero `await` points in the hot loop, starving reconnect / poll tasks on the same loop.

- WebSocket reconnect loops + poll loops **must `await asyncio.sleep(0)`** at the top of each iteration (cooperative yield), independent of whether the iteration did anything else.
- Better: keep DB sync code on a thread executor when reachable from an async loop. Section workers are sync (see [02-strategy-sections.md](02-strategy-sections.md#design-decisions)), so this concern is for runtime / capability layers.

### Long pytest output

Long-running tests piped to `head` / `tail` truncate mid-run and lose signal. Always `tee` to a log file:

```bash
pytest -v 2>&1 | tee /tmp/openpoly-test.log
```

## Clocks

All wall-clock timestamps are **UTC**. Use `datetime.now(timezone.utc)`. Strategy decision-time references (e.g. v8 Rule 1's `reference_time = news.data_time`) are emphatically **not** wall-clock — see [02-strategy-sections.md](02-strategy-sections.md#design-decisions) and the v8 logic doc.
