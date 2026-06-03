/**
 * Markets tab for the market_source inspector — the live catalog plus the
 * latest sampled order-book price per market. Reads GET /api/inspect/markets;
 * polls every 5s while the tab is mounted.
 */
import { useEffect } from 'react'

import { useMarketInspectStore, type InspectMarket } from './inspectStore'

const POLL_MS = 5000

function fmtPrice(v: number | null): string {
  return v === null ? '—' : v.toFixed(3)
}

function fmtNum(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
  return `${Math.round(v)}`
}

export function MarketSourceMarketsTab() {
  const data = useMarketInspectStore((s) => s.data)
  const status = useMarketInspectStore((s) => s.status)
  const refresh = useMarketInspectStore((s) => s.refresh)

  useEffect(() => {
    void refresh()
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [refresh])

  const markets = data?.markets ?? []

  return (
    <div className="flex flex-col gap-3">
      {status === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="text-[11px] text-neutral-500">
          {data ? (
            <>
              <span className="text-neutral-300">{data.catalog_size}</span> markets
              {' · '}
              <span className="text-neutral-300">{data.order_book_count}</span> with
              order book
            </>
          ) : (
            'Loading…'
          )}
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="text-[10px] text-neutral-500 hover:text-neutral-300"
        >
          Refresh
        </button>
      </div>

      {data && markets.length === 0 ? (
        <div className="text-[11px] text-neutral-600">
          No markets. Start the market source on the Live tab to populate the
          catalog.
        </div>
      ) : (
        <ul className="flex flex-col">
          {markets.map((m) => (
            <MarketRow key={m.market_id} market={m} />
          ))}
        </ul>
      )}
    </div>
  )
}

function MarketRow({ market: m }: { market: InspectMarket }) {
  return (
    <li className="border-b border-neutral-800/60 py-1.5 last:border-b-0">
      <div className="text-xs text-neutral-200 truncate" title={m.question}>
        {m.question}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[10px] text-neutral-500">
        <span>
          mid <span className="text-neutral-200">{fmtPrice(m.mid)}</span>
        </span>
        <span>bid {fmtPrice(m.best_bid)}</span>
        <span>ask {fmtPrice(m.best_ask)}</span>
        <span>spread {fmtPrice(m.spread)}</span>
        <span>vol {fmtNum(m.volume_24h)}</span>
        <span>liq {fmtNum(m.liquidity)}</span>
      </div>
    </li>
  )
}
