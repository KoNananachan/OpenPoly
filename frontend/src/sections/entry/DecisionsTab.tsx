/**
 * Entry Decisions tab (v7 P5). Each row shows what the entry did per
 * AnalysisResult fed in by analyzer: side/qty/price on ok, reason on skip,
 * error on raise. Cross-reference with analyzer Calls tab via ``news_id``.
 */
import { formatRelativeAgo, formatUTC } from '../news_source/time'
import {
  useEntryLogStore,
  type EntryLogEntry,
  type EntryLogResponse,
  type Verdict,
} from './logStore'

const VERDICT_COLOR: Record<Verdict, string> = {
  ok: 'text-emerald-300',
  skip: 'text-neutral-400',
  fail_open: 'text-amber-300',
  error: 'text-red-300',
}

export function EntryDecisionsTab() {
  const data = useEntryLogStore((s) => s.data)
  const status = useEntryLogStore((s) => s.status)
  const error = useEntryLogStore((s) => s.error)

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
      <DecisionsTimeline entries={data.entries} />
    </div>
  )
}

function SummaryHeader({ data }: { data: EntryLogResponse }) {
  const c = data.counters
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
      <span className="text-neutral-500">Queue depth</span>
      <span
        className={`text-right ${
          data.queue_depth > 0 ? 'text-amber-300' : 'text-neutral-300'
        }`}
      >
        {data.queue_depth}
      </span>
      <span className="text-neutral-500">Counters</span>
      <span className="text-right">
        <span className="text-emerald-300">{c.ok}</span>
        <span className="text-neutral-500"> ok · </span>
        <span className="text-neutral-300">{c.skip}</span>
        <span className="text-neutral-500"> skip · </span>
        <span className="text-amber-300">{c.fail_open}</span>
        <span className="text-neutral-500"> fail_open · </span>
        <span className="text-red-300">{c.error}</span>
        <span className="text-neutral-500"> error</span>
      </span>
      <span className="text-neutral-500">Last decision</span>
      <span
        className="text-right text-neutral-300"
        title={data.last_at ? formatUTC(data.last_at) : undefined}
      >
        {data.last_at ? formatRelativeAgo(data.last_at) : '—'}
      </span>
    </div>
  )
}

function DecisionsTimeline({ entries }: { entries: EntryLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="text-[11px] text-neutral-500">
        No decisions yet — start news source and wait for analyzer to forward.
      </div>
    )
  }
  const ordered = [...entries].reverse()
  return (
    <ol className="flex flex-col gap-1.5">
      {ordered.map((e, i) => (
        <li
          key={`${e.ts}-${e.news_id}-${i}`}
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
            <span className="text-neutral-500 truncate" title={e.news_id}>
              {e.news_id.slice(0, 18)}
            </span>
            {e.side && (
              <span className="text-neutral-300 uppercase">{e.side}</span>
            )}
            {e.qty !== null && (
              <span className="text-neutral-400">
                qty={e.qty.toFixed(2)}
              </span>
            )}
            {e.price !== null && (
              <span className="text-neutral-400">
                @ {e.price.toFixed(2)}
              </span>
            )}
            <span
              className="text-neutral-600 ml-auto"
              title={formatUTC(e.ts)}
            >
              {formatRelativeAgo(e.ts)}
            </span>
            <span className="text-neutral-600">{e.latency_ms}ms</span>
          </div>
          {e.ar_p_model !== null && (
            <div className="text-neutral-500">
              from analyzer p={e.ar_p_model.toFixed(2)}
              {e.ar_market_id ? ` @${e.ar_market_id}` : ''}
            </div>
          )}
          {e.reason && e.verdict !== 'ok' && (
            <div className="text-neutral-400 break-words">{e.reason}</div>
          )}
          {e.error && (
            <div className="text-red-300 break-words">Error: {e.error}</div>
          )}
        </li>
      ))}
    </ol>
  )
}
