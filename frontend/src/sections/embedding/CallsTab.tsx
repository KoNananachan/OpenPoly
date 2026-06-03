/**
 * Embedding Calls tab (EM6). Inspector log of the embedding-filter stage —
 * the pipeline's first stage. Two timelines: the background Warm cache loop
 * (model load / catalog embedding) and the per-news-tick Calls keyed by
 * news_id. Cross-reference Calls with the analyzer Calls tab to trace one
 * news item through.
 */
import { formatRelativeAgo, formatUTC } from '../news_source/time'
import {
  useEmbeddingLogStore,
  type EmbeddingLogEntry,
  type EmbeddingLogResponse,
  type EmbeddingWarmEntry,
  type Verdict,
  type WarmEvent,
} from './logStore'

const VERDICT_COLOR: Record<Verdict, string> = {
  ok: 'text-emerald-300',
  skip: 'text-neutral-400',
  fail_open: 'text-amber-300',
  error: 'text-red-300',
}

const WARM_EVENT_COLOR: Record<WarmEvent, string> = {
  warm: 'text-emerald-300',
  model_load: 'text-sky-300',
  cache_load: 'text-sky-300',
  error: 'text-red-300',
}

// Warm cycles are heartbeat-frequent; only the recent tail is worth rendering.
const WARM_RENDER_LIMIT = 20

export function EmbeddingCallsTab() {
  const data = useEmbeddingLogStore((s) => s.data)
  const status = useEmbeddingLogStore((s) => s.status)
  const error = useEmbeddingLogStore((s) => s.error)

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
      <WarmPanel warm={data.warm} />
      <CallsTimeline entries={data.entries} />
    </div>
  )
}

function SummaryHeader({ data }: { data: EmbeddingLogResponse }) {
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

function WarmPanel({ warm }: { warm: EmbeddingWarmEntry[] }) {
  const latest = warm.length > 0 ? warm[warm.length - 1] : null
  const recent = warm.slice(-WARM_RENDER_LIMIT).reverse() // newest first
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] font-semibold text-neutral-300">
          Warm cache
        </span>
        {latest && (
          <span className="text-[10px] text-neutral-500">
            {latest.warm_count} market{latest.warm_count === 1 ? '' : 's'}{' '}
            embedded
          </span>
        )}
      </div>
      {recent.length === 0 ? (
        <div className="text-[11px] text-neutral-500">
          No warm cycle yet — the background loop embeds the market catalog
          every few minutes. Until then the filter matches nothing.
        </div>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {recent.map((w, i) => (
            <li
              key={`${w.ts}-${w.event}-${i}`}
              className={`rounded border px-3 py-1.5 flex flex-col gap-0.5 text-[11px] ${
                w.event === 'error'
                  ? 'border-red-700/50 bg-red-900/10'
                  : 'border-neutral-800 bg-neutral-950'
              }`}
            >
              <div className="flex items-center gap-2 font-mono">
                <span
                  className={`uppercase tracking-wider font-semibold ${
                    WARM_EVENT_COLOR[w.event] ?? 'text-neutral-300'
                  }`}
                >
                  {w.event}
                </span>
                {w.event === 'warm' && (
                  <span
                    className="text-neutral-300"
                    title="(re)embedded this cycle / live catalog size"
                  >
                    +{w.embedded_count}/{w.catalog_size}
                  </span>
                )}
                <span className="text-neutral-500" title="markets warm total">
                  {w.warm_count} warm
                </span>
                {w.detail && (
                  <span className="text-neutral-500 truncate" title={w.detail}>
                    {w.detail}
                  </span>
                )}
                <span
                  className="text-neutral-600 ml-auto"
                  title={formatUTC(w.ts)}
                >
                  {formatRelativeAgo(w.ts)}
                </span>
                {w.latency_ms > 0 && (
                  <span className="text-neutral-600">{w.latency_ms}ms</span>
                )}
              </div>
              {w.error && (
                <div className="text-red-300 break-words">Error: {w.error}</div>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function CallsTimeline({ entries }: { entries: EmbeddingLogEntry[] }) {
  const ordered = [...entries].reverse() // newest first
  return (
    <section className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold text-neutral-300">Calls</span>
      {ordered.length === 0 ? (
        <div className="text-[11px] text-neutral-500">
          No calls yet. Start the news source from its Live tab and wait for a
          message.
        </div>
      ) : (
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
                <span
                  className="text-neutral-300"
                  title="candidates / catalog size"
                >
                  {e.candidate_count}/{e.catalog_size}
                </span>
                {e.top_market_id && (
                  <span className="text-neutral-500">@{e.top_market_id}</span>
                )}
                {e.top_score !== null && (
                  <span className="text-neutral-300">
                    s={e.top_score.toFixed(2)}
                  </span>
                )}
                <span className="text-neutral-600 ml-auto" title={formatUTC(e.ts)}>
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
      )}
    </section>
  )
}
