/**
 * Exit Closes tab (v18). The exit monitor sweeps every open position each tick
 * but writes a log entry ONLY when it closes one (ok) or a close fails (error)
 * — within-threshold holds are not logged, so this ring is a clean close
 * ledger that never gets evicted by skip churn. The tick heartbeat (last tick /
 * open / blocked) lives in the header so liveness is visible even when there
 * are no closes yet. Cross-reference a close with the position via ``position_id``.
 */
import { formatRelativeAgo, formatUTC } from '../news_source/time'
import {
  useExitLogStore,
  type ExitLogEntry,
  type ExitLogResponse,
  type Verdict,
} from './logStore'

const VERDICT_COLOR: Record<Verdict, string> = {
  ok: 'text-emerald-300',
  skip: 'text-neutral-400',
  fail_open: 'text-amber-300',
  error: 'text-red-300',
}

export function ExitClosesTab() {
  const data = useExitLogStore((s) => s.data)
  const status = useExitLogStore((s) => s.status)
  const error = useExitLogStore((s) => s.error)

  if (data === null) {
    return (
      <div className="text-xs text-neutral-500">
        {status === 'error' ? `Backend unreachable: ${error}` : 'Loading…'}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {status === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}
      <SummaryHeader data={data} />
      <ClosesTimeline entries={data.entries} />
    </div>
  )
}

function SummaryHeader({ data }: { data: ExitLogResponse }) {
  const c = data.counters
  const open = data.open_positions ?? 0
  const blocked = data.blocked ?? 0
  return (
    <div className="rounded border border-neutral-800 px-3 py-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
      <span className="text-neutral-500">State</span>
      <span
        className={`text-right ${
          data.state === 'running' ? 'text-emerald-300' : 'text-neutral-300'
        }`}
      >
        {data.state}
      </span>
      <span className="text-neutral-500">Last tick</span>
      <span
        className="text-right text-neutral-300"
        title={data.last_tick_at ? formatUTC(data.last_tick_at) : undefined}
      >
        {data.last_tick_at ? formatRelativeAgo(data.last_tick_at) : '—'}
        <span className="text-neutral-500">
          {' · '}
          {open} open
          {blocked > 0 ? (
            <span className="text-amber-300"> · {blocked} blocked</span>
          ) : null}
        </span>
      </span>
      <span className="text-neutral-500">Closes</span>
      <span className="text-right">
        <span className="text-emerald-300">{c.ok}</span>
        <span className="text-neutral-500"> ok · </span>
        <span className="text-red-300">{c.error}</span>
        <span className="text-neutral-500"> error</span>
      </span>
      <span className="text-neutral-500">Last close</span>
      <span
        className="text-right text-neutral-300"
        title={data.last_at ? formatUTC(data.last_at) : undefined}
      >
        {data.last_at ? formatRelativeAgo(data.last_at) : '—'}
      </span>
    </div>
  )
}

function ClosesTimeline({ entries }: { entries: ExitLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="text-[11px] text-neutral-500">
        No closes yet — monitor is ticking, open positions are within thresholds.
      </div>
    )
  }
  const ordered = [...entries].reverse()
  return (
    <ol className="flex flex-col gap-1.5">
      {ordered.map((e, i) => (
        <li
          key={`${e.ts}-${e.position_id}-${i}`}
          className={`rounded border px-3 py-1.5 flex flex-col gap-0.5 text-[11px] ${
            e.verdict === 'error'
              ? 'border-red-700/50 bg-red-900/10'
              : 'border-neutral-800 bg-neutral-950'
          }`}
        >
          <div className="flex items-center gap-2 font-mono">
            <span
              className={`uppercase tracking-wider font-semibold ${
                VERDICT_COLOR[e.verdict] ?? 'text-neutral-300'
              }`}
            >
              {e.verdict}
            </span>
            <span className="text-neutral-500" title={`position ${e.position_id}`}>
              #{e.position_id}
            </span>
            <span className="text-neutral-300 uppercase">{e.side}</span>
            {e.trigger && (
              <span className="text-neutral-400">{e.trigger}</span>
            )}
            {e.return_pct !== null && (
              <span
                className={
                  e.return_pct >= 0 ? 'text-emerald-300' : 'text-red-300'
                }
              >
                {(e.return_pct * 100).toFixed(1)}%
              </span>
            )}
            <span className="text-neutral-600 ml-auto" title={formatUTC(e.ts)}>
              {formatRelativeAgo(e.ts)}
            </span>
          </div>
          {(e.fill_price !== null || e.realized_pnl !== null) && (
            <div className="text-neutral-500">
              {e.fill_price !== null ? `@ ${e.fill_price.toFixed(2)}` : ''}
              {e.realized_pnl !== null
                ? ` · pnl ${e.realized_pnl >= 0 ? '+' : ''}${e.realized_pnl.toFixed(2)}`
                : ''}
              {e.peak_price !== null ? ` · peak ${e.peak_price.toFixed(2)}` : ''}
            </div>
          )}
          {e.error && (
            <div className="text-red-300 break-words">Error: {e.error}</div>
          )}
        </li>
      ))}
    </ol>
  )
}
