"""Tests for openpoly.markets.manager — discovery polling loop + lifecycle.

The ``fetcher`` seam is injected so no network is touched.
"""

from __future__ import annotations

import asyncio

from openpoly.markets.manager import MarketSourceConfig, MarketSourceManager


def _raw_pair(market_id: str = "m1", **over):
    raw = {
        "id": market_id,
        "conditionId": f"0x{market_id}",
        "question": "Q?",
        "clobTokenIds": '["y", "n"]',
        "endDate": "2027-01-01T00:00:00Z",
        "bestBid": 0.40,
        "bestAsk": 0.42,
        "spread": 0.02,
        "volume24hr": 50_000.0,
        "liquidityNum": 20_000.0,
        "feesEnabled": False,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
    }
    raw.update(over)
    event = {"id": "e1", "title": "Event", "tags": []}
    return (raw, event)


def _fetcher(pairs):
    async def fetch(*, limit):
        return list(pairs)

    return fetch


async def _wait_until(predicate, *, iterations: int = 300) -> None:
    for _ in range(iterations):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


async def test_initial_state_stopped():
    mgr = MarketSourceManager(fetcher=_fetcher([]))
    snap = mgr.status()
    assert snap.state == "stopped"
    assert snap.catalog_size == 0
    assert snap.poll_count == 0
    assert snap.last_poll is None


async def test_poll_once_pipeline():
    mgr = MarketSourceManager(fetcher=_fetcher([_raw_pair("a"), _raw_pair("b")]))
    mgr._config = MarketSourceConfig()  # start() would set this; isolate the pipeline
    summary = await mgr._poll_once()
    assert summary.fetched == 2
    assert summary.kept == 2
    assert mgr.store.get("a") is not None
    assert len(mgr.store) == 2


async def test_poll_once_filters_and_drops():
    pairs = [
        _raw_pair("good"),
        _raw_pair("lowvol", volume24hr=10.0),  # rejected: low_volume
        _raw_pair("broken", clobTokenIds=None),  # dropped at normalize
    ]
    mgr = MarketSourceManager(fetcher=_fetcher(pairs))
    mgr._config = MarketSourceConfig()
    summary = await mgr._poll_once()
    assert summary.fetched == 3  # raw count from the fetcher
    assert summary.kept == 1  # only "good"
    assert summary.reason_counts.get("low_volume") == 1
    assert {m.market_id for m in mgr.store.snapshot()} == {"good"}


async def test_start_polls_then_stop():
    mgr = MarketSourceManager(fetcher=_fetcher([_raw_pair("a"), _raw_pair("b")]))
    await mgr.start(MarketSourceConfig(poll_interval_seconds=3600))
    await _wait_until(lambda: mgr.status().poll_count >= 1)

    snap = mgr.status()
    assert snap.state == "running"
    assert snap.catalog_size == 2
    assert snap.last_poll is not None
    assert any(e.kind == "poll_ok" for e in mgr.events())

    await mgr.stop()
    assert mgr.status().state == "stopped"


async def test_poll_error_sets_error_state_but_loop_survives():
    async def boom(*, limit):
        raise RuntimeError("gamma down")

    mgr = MarketSourceManager(fetcher=boom)
    await mgr.start(MarketSourceConfig(poll_interval_seconds=3600))
    await _wait_until(lambda: mgr.status().state == "error")

    snap = mgr.status()
    assert snap.state == "error"
    assert "gamma down" in (snap.last_error or "")
    assert any(e.kind == "poll_error" for e in mgr.events())

    await mgr.stop()
    assert mgr.status().state == "stopped"


async def test_double_start_is_idempotent():
    mgr = MarketSourceManager(fetcher=_fetcher([_raw_pair("a")]))
    await mgr.start(MarketSourceConfig(poll_interval_seconds=3600))
    await _wait_until(lambda: mgr.status().poll_count >= 1)
    snap2 = await mgr.start(MarketSourceConfig(poll_interval_seconds=3600))
    assert snap2.state == "running"
    assert mgr.status().poll_count >= 1  # not reset, not double-started
    await mgr.stop()


async def test_status_to_dict_shape():
    mgr = MarketSourceManager(fetcher=_fetcher([_raw_pair("a")]))
    await mgr.start(MarketSourceConfig(poll_interval_seconds=3600))
    await _wait_until(lambda: mgr.status().poll_count >= 1)
    d = mgr.status().to_dict()
    assert set(d) == {
        "state",
        "started_at",
        "last_poll_at",
        "catalog_size",
        "poll_count",
        "last_error",
        "running_config",
        "last_poll",
    }
    assert d["last_poll"]["kept"] == 1
    await mgr.stop()


async def test_stop_when_not_started_is_safe():
    mgr = MarketSourceManager(fetcher=_fetcher([]))
    snap = await mgr.stop()
    assert snap.state == "stopped"


# ---------------------------------------------------------------------------
# Holding-sync helpers and tests (Task 5)
# ---------------------------------------------------------------------------


def _market(market_id: str):
    """Build a minimal Market dataclass for holding sync tests."""
    from openpoly.markets.models import Market

    return Market(
        market_id=market_id,
        condition_id=f"0x{market_id}",
        question="?",
        slug=market_id,
        yes_token_id=f"y_{market_id}",
        no_token_id=f"n_{market_id}",
        end_date=None,
        best_bid=None,
        best_ask=None,
        spread=None,
        last_trade_price=None,
        volume_24h=0.0,
        liquidity=0.0,
        taker_fee_rate=None,
        closed=False,
        accepting_orders=True,
        enable_order_book=True,
        event_id=None,
        event_title=None,
        event_tags=(),
    )


class _FakePortfolioStore:
    """Mimics PortfolioStore.get_open_positions() for tests."""

    def __init__(self, market_ids: list[str]) -> None:
        self._ids = market_ids

    def get_open_positions(self):
        from openpoly.portfolio.models import HeldPosition

        return [
            HeldPosition(
                position_id=i,
                market_id=mid,
                side="no",
                token_id=f"t_{mid}",
                condition_id=f"0x{mid}",
                qty=10.0,
                avg_entry_price=0.5,
                opened_at=0.0,
            )
            for i, mid in enumerate(self._ids, start=1)
        ]


def _market_fetcher(by_id: dict):
    async def fetch(market_id: str):
        return by_id.get(market_id)

    return fetch


async def test_sync_holdings_adds_missing_market():
    pf = _FakePortfolioStore(["pos1"])
    fetcher = _market_fetcher({"pos1": _market("pos1")})
    mgr = MarketSourceManager(
        fetcher=_fetcher([]),  # empty discovery
        market_fetcher=fetcher,
        portfolio_store=pf,
    )
    synced, failed = await mgr._sync_holdings_once()
    assert synced == 1
    assert failed == 0
    assert "pos1" in mgr.store.snapshot_ids()


async def test_sync_holdings_skips_already_present():
    pf = _FakePortfolioStore(["a"])
    fetcher = _market_fetcher({"a": _market("a")})
    mgr = MarketSourceManager(
        fetcher=_fetcher([_raw_pair("a")]),
        market_fetcher=fetcher,
        portfolio_store=pf,
    )
    mgr._config = MarketSourceConfig()
    await mgr._poll_once()  # discovery puts "a" in catalog
    synced, failed = await mgr._sync_holdings_once()
    assert synced == 0
    assert failed == 0


async def test_sync_holdings_failed_fetch_continues():
    pf = _FakePortfolioStore(["bad1", "bad2"])
    fetcher = _market_fetcher({})  # both return None
    mgr = MarketSourceManager(
        fetcher=_fetcher([]),
        market_fetcher=fetcher,
        portfolio_store=pf,
    )
    synced, failed = await mgr._sync_holdings_once()
    assert synced == 0
    assert failed == 2
    assert mgr.store.snapshot_ids() == set()


async def test_sync_holdings_no_portfolio_store_is_noop():
    mgr = MarketSourceManager(fetcher=_fetcher([]))  # no portfolio_store
    synced, failed = await mgr._sync_holdings_once()
    assert synced == 0
    assert failed == 0


async def test_sync_holdings_portfolio_store_raises_returns_zero():
    class _BoomStore:
        def get_open_positions(self):
            raise RuntimeError("db down")

    mgr = MarketSourceManager(
        fetcher=_fetcher([]),
        market_fetcher=_market_fetcher({}),
        portfolio_store=_BoomStore(),
    )
    synced, failed = await mgr._sync_holdings_once()
    assert synced == 0
    assert failed == 0  # error swallowed; main poll keeps running


# ---------------------------------------------------------------------------
# Task 6: _poll_once wires holding sync
# ---------------------------------------------------------------------------


async def test_poll_once_runs_holding_sync_and_records_counts():
    """Full poll cycle: discovery catalog + holding sync, summary reflects both."""
    pf = _FakePortfolioStore(["held1"])
    fetcher = _market_fetcher({"held1": _market("held1")})
    mgr = MarketSourceManager(
        fetcher=_fetcher([_raw_pair("disco1"), _raw_pair("disco2")]),
        market_fetcher=fetcher,
        portfolio_store=pf,
    )
    mgr._config = MarketSourceConfig()
    summary = await mgr._poll_once()
    assert summary.fetched == 2
    assert summary.kept == 2
    assert summary.holding_synced == 1
    assert summary.holding_sync_failed == 0
    # Catalog has discovery + holding
    assert mgr.store.snapshot_ids() == {"disco1", "disco2", "held1"}
    # last_poll has the counts too (used by status endpoint)
    assert mgr.store.last_poll.holding_synced == 1


async def test_poll_once_holding_sync_close_no_re_add():
    """After a position closes, next poll doesn't re-add it via holding sync."""
    pf = _FakePortfolioStore(["held1"])
    by_id = {"held1": _market("held1")}
    mgr = MarketSourceManager(
        fetcher=_fetcher([]),  # empty discovery
        market_fetcher=_market_fetcher(by_id),
        portfolio_store=pf,
    )
    mgr._config = MarketSourceConfig()
    summary1 = await mgr._poll_once()
    assert summary1.holding_synced == 1
    assert "held1" in mgr.store.snapshot_ids()

    # Position closed — portfolio store now returns no open positions
    pf._ids = []
    summary2 = await mgr._poll_once()
    assert summary2.holding_synced == 0
    # store.replace() at start of poll cleared catalog; holding sync didn't re-add
    assert mgr.store.snapshot_ids() == set()


async def test_poll_once_holding_sync_failed_records_failure_count():
    """failed arm of the `if synced or failed` guard: PollSummary records
    holding_sync_failed when no positions could be fetched."""
    pf = _FakePortfolioStore(["unfetchable"])
    mgr = MarketSourceManager(
        fetcher=_fetcher([]),  # empty discovery
        market_fetcher=_market_fetcher({}),  # returns None for every id
        portfolio_store=pf,
    )
    mgr._config = MarketSourceConfig()
    summary = await mgr._poll_once()
    assert summary.holding_synced == 0
    assert summary.holding_sync_failed == 1
    assert mgr.store.last_poll.holding_sync_failed == 1
    assert mgr.store.snapshot_ids() == set()  # nothing added
