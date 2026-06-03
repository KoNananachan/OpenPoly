/**
 * Analyzer Calls tab (v7 P5). Inspector log of every analyzer invocation
 * keyed by news_id; users cross-reference with news_source Recent
 * messages and entry Decisions tab to trace a single news through.
 */
import { formatRelativeAgo, formatUTC } from '../news_source/time'
import {
  useAnalyzerLogStore,
  type AnalyzerLogEntry,
  type AnalyzerLogResponse,
  type Verdict,
} from './logStore'

const VERDICT_COLOR: Record<Verdict, string> = {
  ok: 'text-emerald-300',
  skip: 'text-neutral-400',
  fail_open: 'text-amber-300',
  error: 'text-red-300',
}

export function AnalyzerCallsTab() {
  const data = useAnalyzerLogStore((s) => s.data)
  const status = useAnalyzerLogStore((s) => s.status)
  const error = useAnalyzerLogStore((s) => s.error)

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
      <CallsTimeline entries={data.entries} />
    </div>
  )
}

function SummaryHeader({ data }: { data: AnalyzerLogResponse }) {
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
      <span className="text-neutral-500">Last call</span>
      <span
        className="text-right text-neutral-300"
        title={data.last_at ? formatUTC(data.last_at) : undefined}
      >
        {data.last_at ? formatRelativeAgo(data.last_at) : '—'}
      </span>
    </div>
  )
}

function CallsTimeline({ entries }: { entries: AnalyzerLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <div className="text-[11px] text-neutral-500">
        No calls yet. Start the news source from its Live tab and wait for a
        message.
      </div>
    )
  }
  const ordered = [...entries].reverse() // newest first
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
            {e.p_model !== null && (
              <span className="text-neutral-300">
                p={e.p_model.toFixed(2)}
              </span>
            )}
            {e.confidence && (
              <span className="text-neutral-500">{e.confidence}</span>
            )}
            {e.market_id && e.market_id !== 'pending_llm_wiring' && (
              <span className="text-neutral-500">@{e.market_id}</span>
            )}
            <span
              className="text-neutral-600 ml-auto"
              title={formatUTC(e.ts)}
            >
              {formatRelativeAgo(e.ts)}
            </span>
            <span className="text-neutral-600">{e.latency_ms}ms</span>
          </div>
          {e.news_content_preview && (
            <div
              className="text-neutral-400 truncate"
              title={e.news_content_preview}
            >
              {e.news_content_preview}
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
