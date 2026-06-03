/**
 * PositionCard — one position as a vertical card:
 * Header (question + #id + status badge + opened_at) → BUY block →
 * SELL block (only when closed) → AnalyzerRationaleBlock footer.
 *
 * The Link wraps only the trading-info sections so navigation triggers
 * when the operator clicks the header / BUY / SELL row. The rationale
 * footer sits OUTSIDE the Link because it has its own buttons
 * ("Show N earlier attempts", per-group expand toggles) — nesting
 * buttons inside an anchor is invalid HTML and would intercept clicks.
 *
 * SELL block math: exit_price = avg_entry_price + realized_pnl / qty.
 * Same formula PositionDetail uses (single source of truth lives there;
 * we mirror it here so the card is self-contained).
 */
import { Link } from 'react-router-dom'
import { formatRelativeAgo, formatUTC } from '../../sections/news_source/time'
import { AnalyzerRationaleBlock } from './AnalyzerRationale'
import { formatPnl, pnlClass } from './format'
import type { PositionRecord } from './portfolioTypes'

const CLOSE_REASON_TONE: Record<string, string> = {
  take_profit: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
  stop_loss: 'bg-red-900/40 text-red-300 border-red-700/50',
  kill_switch: 'bg-orange-900/40 text-orange-300 border-orange-700/50',
  manual: 'bg-neutral-800 text-neutral-300 border-neutral-700/50',
  settlement: 'bg-sky-900/40 text-sky-300 border-sky-700/50',
}

const OPEN_TONE = 'bg-amber-900/40 text-amber-300 border-amber-700/50'

function StatusBadge({
  status,
  closeReason,
}: {
  status: 'open' | 'closed'
  closeReason: string | null
}) {
  if (status === 'open') {
    return (
      <span
        className={`px-1.5 py-0.5 text-[10px] uppercase font-mono rounded border ${OPEN_TONE}`}
      >
        OPEN
      </span>
    )
  }
  const tone = CLOSE_REASON_TONE[closeReason ?? ''] ?? CLOSE_REASON_TONE.manual
  return (
    <span
      className={`px-1.5 py-0.5 text-[10px] uppercase font-mono rounded border ${tone}`}
    >
      {closeReason ?? 'closed'}
    </span>
  )
}

export function PositionCard({ p }: { p: PositionRecord }) {
  const exitPrice =
    p.closed_at !== null && p.realized_pnl !== null
      ? p.avg_entry_price + p.realized_pnl / p.qty
      : null
  const cost = p.qty * p.avg_entry_price
  const sideTone = p.side === 'yes' ? 'text-emerald-300' : 'text-sky-300'

  return (
    <article className="rounded border border-neutral-800 bg-neutral-950 overflow-hidden">
      <Link
        to={`/activity/positions/${p.id}`}
        className="block hover:bg-neutral-900/40 transition-colors"
      >
        {/* Header */}
        <div className="px-4 py-3 flex items-baseline gap-3 flex-wrap">
          <span className="text-neutral-500 font-mono text-[11px]">#{p.id}</span>
          {p.market_question ? (
            <span
              className="flex-1 min-w-0 text-neutral-100 text-[13px] font-medium truncate"
              title={`${p.market_question}\n\nmarket_id: ${p.market_id}\ncondition_id: ${p.condition_id}`}
            >
              {p.market_question}
            </span>
          ) : (
            <span
              className="flex-1 min-w-0 text-neutral-500 text-[12px] font-mono truncate"
              title={`market_id: ${p.market_id}\ncondition_id: ${p.condition_id}\n(question unavailable — market evicted from catalog)`}
            >
              {p.condition_id.slice(0, 18)}…
            </span>
          )}
          <StatusBadge status={p.status} closeReason={p.close_reason} />
        </div>

        <hr className="border-neutral-800" />

        {/* BUY block */}
        <div className="px-4 py-2.5 flex items-baseline gap-4 flex-wrap font-mono text-[12px]">
          <span className={`font-semibold ${sideTone}`}>
            BUY_{p.side.toUpperCase()}
          </span>
          <span className="text-neutral-300">
            {p.qty.toFixed(2)} @ {p.avg_entry_price.toFixed(3)}
          </span>
          <span className="text-neutral-500">(${cost.toFixed(2)})</span>
          <span
            className="ml-auto text-[10px] text-neutral-500"
            title={formatUTC(p.opened_at)}
          >
            {formatRelativeAgo(p.opened_at)}
          </span>
        </div>

        {/* SELL block (only when closed) */}
        {p.closed_at !== null && exitPrice !== null && p.realized_pnl !== null && (
          <>
            <hr className="border-neutral-800" />
            <div className="px-4 py-2.5 flex items-baseline gap-4 flex-wrap font-mono text-[12px]">
              <span className={`font-semibold ${sideTone}`}>
                SELL_{p.side.toUpperCase()}
              </span>
              <span className="text-neutral-300">
                {p.qty.toFixed(2)} @ {exitPrice.toFixed(3)}
              </span>
              <span className={pnlClass(p.realized_pnl)}>
                {formatPnl(p.realized_pnl)}
              </span>
              <span
                className="ml-auto text-[10px] text-neutral-500"
                title={formatUTC(p.closed_at)}
              >
                {formatRelativeAgo(p.closed_at)}
              </span>
            </div>
          </>
        )}
      </Link>

      {/* Footer: LLM rationale — OUTSIDE the Link so its toggle buttons
          don't get hijacked by navigation. Rendered whenever the backend
          provided the field (even as []), so the "unavailable" fallback
          surfaces when the analyzer_log ring has evicted the call. */}
      {p.analyzer_decisions !== undefined && (
        <>
          <hr className="border-neutral-800" />
          <div className="px-4 py-3">
            <AnalyzerRationaleBlock decisions={p.analyzer_decisions} />
          </div>
        </>
      )}
    </article>
  )
}
