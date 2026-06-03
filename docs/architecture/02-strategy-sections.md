# Strategy section pipeline

> **Status (2026-05-19)**: This document is retained as the full v8 baseline reference. The currently implemented scope is narrower than what is described here — see the section types under `openpoly/sections/`. Use this doc when extending the pipeline with further modules (entry veto, exit monitor, risk guard, …).
>
> Captured 2026-05-19 from multi-role design discussion (`/tm`). Five open questions at the end await user decision before code scaffolding.

## Premise

Users compose strategies in a **canvas UI** (React Flow). The canvas is **section-based**: a strategy is a pipeline of typed slots, each slot is filled by a different **implementation script** that satisfies the slot's interface. Users tune parameters per impl through auto-generated forms.

This is more constrained than a free DAG (easier to validate, replay, backtest) and more flexible than a hardcoded pipeline (impls are pluggable).

## Inputs to the design

- A prior project's v8 trading logic — the alpha decision model being abstracted.
- Single-system runtime — section impls are auto-discovered from the filesystem and loaded into the one process (see [01-isolation.md](01-isolation.md)).
- System-level config — section impls do not hold secrets; they declare `requires: [llm, news, ...]` and runtime injects clients.

## Architecture

**4 pluggable sections** (user can change the impl on canvas) + **5 fixed system services** (algorithm fixed; only parameters exposed on canvas).

### Pluggable sections

| Section | v8 reference | REQUIRES capabilities |
|---|---|---|
| `market_matcher` | §3 (news → market_id, p_model, confidence) | `[llm, market_data]` |
| `entry_veto` | §4 (late-buy / bad-exec / cluster-dup) | `[market_data, order_book, history_prices, news_history]` |
| `position_sizer` | §7 (fixed / edge_scaled / kelly) | `[portfolio]` |
| `exit_monitor` | §10 (V2 4-rule hard + V6 3-rule incremental + optional LLM consult) | `[market_data, order_book, history_prices, portfolio, llm?]` |

### Fixed system services (parameter panel only)

| Service | v8 reference | Tunable params |
|---|---|---|
| News ingest | §2 (freshness decay + cluster/dedup) | freshness τ, similarity thresholds |
| Edge calculator | §5 (spread-aware edge + net_edge) | slippage fallback, fee source |
| Entry gate | §6 (13-gate ordered checks) | all 13 thresholds |
| Execution simulator | §8 (3-tier price discovery + walk_book) | slippage_pct, max_liquidity_ratio, walk_book gating |
| Risk guard / kill switch | §11 (daily_loss / max_drawdown / consecutive_losses) | all three thresholds + enforce flag |

## Section Protocol

All section impls implement the same protocol:

```python
class Section(Protocol):
    SECTION_TYPE: ClassVar[str]              # "market_matcher" etc.
    SECTION_VERSION: ClassVar[str]           # impl's own semver
    REQUIRES: ClassVar[list[Capability]]
    TIMEOUT_MS: ClassVar[int] = 5000
    Config: ClassVar[type[BaseModel]]        # pydantic, single source of param schema

    def __init__(self, config: Config, ctx: SectionContext): ...
    def warmup(self) -> None: ...            # default no-op
    def shutdown(self) -> None: ...
    def run(self, input: SectionInput) -> SectionOutput: ...   # SYNC
```

### Runtime context (capability injection)

```python
@dataclass(frozen=True)
class SectionContext:
    run_id: str
    audit: AuditLogger           # always available
    clock: Clock                 # UTC-only

    # injected only when declared in REQUIRES; otherwise None and unreachable
    llm: LLMClient | None = None
    news_history: NewsHistoryStore | None = None
    market_data: MarketDataReader | None = None
    order_book: OrderBookReader | None = None
    history_prices: PriceHistoryReader | None = None
    portfolio: PortfolioReader | None = None
    # NOTE: no `wallet` field — decision side cannot read wallet state
```

### Output envelope

```python
@dataclass(frozen=True)
class SectionOutput[T]:
    payload: T | None
    verdict: Literal["ok", "fail_open", "error", "skip"]
    reason: str | None
    signals: dict[str, Any]              # hard-data snapshot for audit + downstream
    signal_unavailable: list[str]        # signals that were dark this call
    elapsed_ms: int
```

`fail_open` is a first-class verdict (lesson from v8 §4.4): downstream treats it as "pass-through but mark dark". The runtime accumulates `signal_unavailable` across the pipeline; section impls do not track cross-section state.

## Design decisions

1. **Sync `run()`, not async.** Runtime schedules sections in a thread pool. Avoids the async-reconnect-starvation class of bugs that bit a prior project. LLM clients can be async internally — section impl doesn't see it.
2. **Capability minimum-privilege.** Unrequested capabilities are `None` in ctx. Runtime enforces; impl cannot reach what it didn't declare.
3. **No `wallet` capability for sections.** v8 §0 + §14 are explicit: decision side outputs `(side, price, qty)`, does not see wallet state. Sizing reads `portfolio` (paper-mode-parity) instead.
4. **Runtime owns error / timeout / retry / audit.** Section impl writes only business logic. Runtime wraps `run()` with `TIMEOUT_MS`, persists audit, applies retry policy (default: 0; LLM sections: 2).
5. **Pydantic `Config` is single source of param schema.** UI auto-renders from JSON-schema export; deserialization re-validates.
6. **No PROTOCOL_VERSION in v0.** Add when first breaking change actually happens — premature now.
7. **`tick_type` on input for scheduled sections** (`exit_monitor` runs at both "hard" 120s and "full" 900s ticks per v8 §10.1). Scheduling is runtime's job; section just dispatches by tick_type.
8. **Determinism contract.** Section `run()` must be deterministic given the same input + ctx state — no hidden RNG, no `time.time()` outside `ctx.clock`. Enables v8 §0.5 "bit-level replay".

## Section discovery & loading

- File-system based: `openpoly/sections/<type>/<name>.py` is auto-discovered at runtime startup.
- **No dynamic URL loading** (e.g. clone-from-GitHub) in v0 — attack surface too large.
- On discovery, runtime calls a **contract test** (impl must ship one); failure → impl is rejected from catalog, does not reach canvas.

## Open questions (await user decision)

1. **Paper vs prod: same section code?** Lean: yes; only ctx capability impls differ (paper readers hit paper schema). Confirms the isolation invariant.
2. **Template serialization format**: JSON (lean) vs YAML.
3. **Strict determinism enforcement** — should runtime fingerprint ctx state pre/post and reject sections with hidden state mutation, or rely on contract tests?
4. **Contract test mandate** — every impl ships one fixed-input/fixed-output snapshot; runtime rejects impls that fail. Confirm.
5. **First-version section impls** — port v8 defaults (`MarketMatcherV8`, `EntryVetoV8`, `PositionSizerKelly`+`EdgeScaled`, `ExitMonitorV6V8`) as baselines before exposing the canvas?
