"""Discovery-time market filtering.

Takes normalized ``Market`` objects (the raw Polymarket universe) and selects
the tradeable candidate set that feeds the analyzer. This is *discovery*
filtering — "is this market a valid candidate at all" — **not** the per-trade
entry gate (that runs later, on the single matched market, with fresh data).

Filter logic mirrors a prior project's ``sync_markets`` + the v8 trading spec:

  - §0.1 zero-fee rule — only ``taker_fee_rate == 0`` (fail-closed on ``None``)
  - §6 Gate 2 liveness / Gate 4 expiry / Gate 5 market quality

Each market is either kept or rejected with a single stable reason label.
Rules are ordered structural-and-permanent first, quality last — so a market
that fails several rules reports the most fundamental cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from openpoly.markets.models import Market

# Reject reason labels — stable strings for logging / status counters.
REJECT_RESOLVED = "market_resolved"
REJECT_EXCLUDED_TAG = "excluded_tag"
REJECT_NULL_FEE = "null_fee_rate"
REJECT_FEE_NOT_ZERO = "fee_not_zero"
REJECT_MISSING_END_DATE = "missing_end_date"
REJECT_NEAR_EXPIRY = "near_expiry"
REJECT_LOW_VOLUME = "low_volume"
REJECT_LOW_LIQUIDITY = "low_liquidity"
REJECT_PRICE_EXTREME = "price_extreme"
REJECT_HIGH_SPREAD = "high_spread"


class MarketFilterConfig(BaseModel):
    """Tunable thresholds for the discovery filter. Pydantic so it can be
    embedded in the market_source section Config and auto-rendered in the UI."""

    require_zero_fee: bool = Field(
        default=True,
        description="Drop markets with a non-zero taker fee (v8 zero-fee rule).",
    )
    min_hours_to_expiry: float = Field(
        default=24.0,
        ge=0.0,
        description="Drop markets resolving within this many hours.",
    )
    min_volume_24h: float = Field(
        default=1000.0,
        ge=0.0,
        description="Minimum 24h USD volume.",
    )
    min_liquidity: float = Field(
        default=500.0,
        ge=0.0,
        description="Minimum liquidity (USD).",
    )
    min_price: float = Field(
        default=0.03,
        ge=0.0,
        le=0.5,
        description=(
            "Drop markets whose YES price falls outside [min_price, "
            "1 - min_price] — i.e. either side sits at an untradeable extreme."
        ),
    )
    max_spread: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Drop markets with a wider spread.",
    )
    exclude_event_tags: tuple[str, ...] = Field(
        default=("sports",),
        description="Drop markets whose event carries any of these tag slugs.",
    )


@dataclass(frozen=True)
class FilterDecision:
    """Outcome of evaluating one market. ``detail`` carries the triggering
    value for observability (e.g. the actual volume that was too low)."""

    verdict: str  # "keep" | "reject"
    reason: str | None = None
    detail: str | None = None

    @property
    def kept(self) -> bool:
        return self.verdict == "keep"


_KEEP = FilterDecision("keep")


def evaluate_market(
    market: Market, config: MarketFilterConfig, *, now: datetime | None = None
) -> FilterDecision:
    """Apply the discovery filters to one market. First failing rule wins."""
    now = now or datetime.now(timezone.utc)

    # 1. Liveness — structural, permanent disqualifiers.
    if market.closed or not market.accepting_orders or not market.enable_order_book:
        return FilterDecision("reject", REJECT_RESOLVED)

    # 2. Excluded event tags (e.g. sports).
    hit = sorted(set(config.exclude_event_tags) & set(market.event_tags))
    if hit:
        return FilterDecision("reject", REJECT_EXCLUDED_TAG, ",".join(hit))

    # 3. Zero-fee rule — fail-closed when the fee is unknown.
    if config.require_zero_fee:
        if market.taker_fee_rate is None:
            return FilterDecision("reject", REJECT_NULL_FEE)
        if market.taker_fee_rate > 0:
            return FilterDecision("reject", REJECT_FEE_NOT_ZERO, f"{market.taker_fee_rate:g}")

    # 4. Near expiry.
    if market.end_date is None:
        return FilterDecision("reject", REJECT_MISSING_END_DATE)
    hours_left = (market.end_date - now).total_seconds() / 3600.0
    if hours_left < config.min_hours_to_expiry:
        return FilterDecision("reject", REJECT_NEAR_EXPIRY, f"{hours_left:.1f}h")

    # 5. Volume floor.
    if market.volume_24h < config.min_volume_24h:
        return FilterDecision("reject", REJECT_LOW_VOLUME, f"{market.volume_24h:.0f}")

    # 6. Liquidity floor.
    if market.liquidity < config.min_liquidity:
        return FilterDecision("reject", REJECT_LOW_LIQUIDITY, f"{market.liquidity:.0f}")

    # 7. Price band — reject markets where either side sits at an untradeable
    #    extreme. ``reference_price`` is YES-side; the NO side mirrors it
    #    (NO ~= 1 - YES), so a symmetric band guards both sides at once.
    #    Unknown price counts as untradeable.
    price = market.reference_price
    if price is None:
        return FilterDecision("reject", REJECT_PRICE_EXTREME, "unknown")
    if price < config.min_price or price > 1.0 - config.min_price:
        return FilterDecision("reject", REJECT_PRICE_EXTREME, f"{price:.3f}")

    # 8. Spread — lenient when unknown (re-checked at decision time on fresh book).
    if market.spread is not None and market.spread > config.max_spread:
        return FilterDecision("reject", REJECT_HIGH_SPREAD, f"{market.spread:.3f}")

    return _KEEP


@dataclass(frozen=True)
class FilterReport:
    """Result of filtering a batch: the kept candidates, the rejected markets
    with their reasons, and a reason histogram for status / observability."""

    kept: list[Market]
    rejected: list[tuple[Market, FilterDecision]]
    reason_counts: dict[str, int]

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.rejected)


def filter_markets(
    markets: list[Market], config: MarketFilterConfig, *, now: datetime | None = None
) -> FilterReport:
    """Run the discovery filter over a batch of normalized markets."""
    now = now or datetime.now(timezone.utc)
    kept: list[Market] = []
    rejected: list[tuple[Market, FilterDecision]] = []
    counts: dict[str, int] = {}
    for market in markets:
        decision = evaluate_market(market, config, now=now)
        if decision.kept:
            kept.append(market)
        else:
            rejected.append((market, decision))
            key = decision.reason or "unknown"
            counts[key] = counts.get(key, 0) + 1
    return FilterReport(kept=kept, rejected=rejected, reason_counts=counts)
