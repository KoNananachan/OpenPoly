/**
 * Activity › Position detail — one position's order book chart + the LLM's
 * reason for opening it. Reached by clicking a row in the Positions tab
 * (/activity/positions/:positionId).
 *
 * PD2/PD3 augment the backend response with `market_question` (catalog
 * lookup) and `analyzer_decisions` (analyzer_log lookup by news_id). PD5
 * renders both in the header / a dedicated rationale block. Per OD6, both
 * fields ride with the rest of `DetailData` through `frozenRef`, so a
 * closed position page never re-flickers when catalog / analyzer log
 * evicts the source data underneath.
 */
import { useRef } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AnalyzerRationaleBlock } from './AnalyzerRationale'
import { OrderBookChart } from './OrderBookChart'
import { fetchOrderBookHistory, type OrderBookHistory } from './orderBookClient'
import { formatPnl, pnlClass } from './format'
import type { PositionRecord } from './portfolioTypes'
import { usePoll } from './usePoll'

async function fetchPosition(id: string): Promise<PositionRecord | null> {
  const r = await fetch(`/api/positions/${encodeURIComponent(id)}`)
  if (r.status === 404) return null
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as PositionRecord
}

type DetailData = {
  position: PositionRecord | null
  history: OrderBookHistory | null
}

export function PositionDetail() {
  const { positionId } = useParams<{ positionId: string }>()
  const frozenRef = useRef<DetailData | null>(null)
  const { data, status, error } = usePoll<DetailData>(async () => {
    if (frozenRef.current !== null) return frozenRef.current
    const position = await fetchPosition(positionId ?? '')
    if (position === null) return { position: null, history: null }
    const history = await fetchOrderBookHistory(
      position.token_id,
      position.opened_at,
      position.closed_at,
    )
    const result: DetailData = { position, history }
    if (position.closed_at !== null) frozenRef.current = result
    return result
  })

  if (data === null) {
    return (
      <div className="grid place-items-center p-10">
        <p className="text-sm text-neutral-400">
          {status === 'error' ? `Backend unreachable: ${error}` : 'Loading…'}
        </p>
      </div>
    )
  }

  if (data.position === null) {
    return (
      <div className="px-6 py-10 flex flex-col gap-3">
        <Link to="/activity/positions" className="text-xs text-blue-400">
          ‹ Positions
        </Link>
        <p className="text-sm text-neutral-400">Position not found.</p>
      </div>
    )
  }

  const p = data.position
  const snapshots = data.history?.snapshots ?? []
  const exitPrice =
    p.closed_at !== null && p.realized_pnl !== null
      ? p.avg_entry_price + p.realized_pnl / p.qty
      : null

  return (
    <div className="px-6 pb-6 flex flex-col gap-4">
      <Link to="/activity/positions" className="text-xs text-blue-400">
        ‹ Positions
      </Link>

      {/* Header row: id, market identity, side, qty/price, status, PnL */}
      <div className="flex items-baseline gap-3 flex-wrap font-mono text-[12px]">
        <span className="text-neutral-100 font-semibold">#{p.id}</span>
        {/* PD2: market question, with condition_id truncation as fallback. */}
        {p.market_question ? (
          <span
            className="text-neutral-200 truncate max-w-[60ch]"
            title={`${p.market_question}\n\nmarket_id: ${p.market_id}\ncondition_id: ${p.condition_id}`}
          >
            {p.market_question}
          </span>
        ) : (
          <span
            className="text-neutral-500"
            title={`market_id: ${p.market_id}\ncondition_id: ${p.condition_id}\n(question unavailable — market evicted from catalog)`}
          >
            {p.market_id.slice(0, 18)}…
          </span>
        )}
        <span className={p.side === 'yes' ? 'text-emerald-300' : 'text-sky-300'}>
          {p.side.toUpperCase()}
        </span>
        <span className="text-neutral-300">
          {p.qty.toFixed(2)} @ {p.avg_entry_price.toFixed(3)}
        </span>
        <span
          className={p.status === 'open' ? 'text-amber-300' : 'text-neutral-400'}
        >
          {p.status}
        </span>
        {p.realized_pnl !== null && (
          <span className={pnlClass(p.realized_pnl)}>
            {formatPnl(p.realized_pnl)}
          </span>
        )}
      </div>

      {/* PD3+PD5: analyzer rationale block (LLM's stated reason for the
         decision). Empty list when the analyzer_log ring has evicted the
         original call — rendered as "unavailable" so the operator knows
         it's a data-retention gap, not a system bug. */}
      <AnalyzerRationaleBlock decisions={p.analyzer_decisions ?? []} />

      <div className="rounded border border-neutral-800 p-3">
        {status === 'error' && (
          <div className="mb-2 rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
            Backend unreachable; data may be stale.
          </div>
        )}
        {snapshots.length === 0 ? (
          <div className="h-72 grid place-items-center text-[11px] text-neutral-500">
            No order book data for this position.
          </div>
        ) : (
          <OrderBookChart
            snapshots={snapshots}
            entry={{ ts: p.opened_at, price: p.avg_entry_price }}
            exit={
              p.closed_at !== null && exitPrice !== null
                ? { ts: p.closed_at, price: exitPrice }
                : null
            }
          />
        )}
      </div>
    </div>
  )
}

