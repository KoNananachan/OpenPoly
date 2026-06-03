/**
 * Status dot + last-call text rendered on the embedding canvas card.
 *
 * Reuses useEmbeddingLogStore (already polls /api/embedding/log every 3s) — no
 * extra fetch. The dot reflects orchestrator state + queue backlog + the latest
 * call's verdict, so embedding-filter health is visible at a glance without
 * opening the Calls tab. Mirrors AnalyzerStatusIndicator.
 *
 * Scope note: the dot tracks the per-tick filter only. Warm-cache health (model
 * load, the 300s catalog warm loop) lives in the Calls tab's Warm cache panel —
 * a green dot here does not by itself prove the catalog has been embedded.
 */
import { formatRelativeAgo, formatUTC } from '../news_source/time'
import { useEmbeddingLogStore } from './logStore'

type DotState = 'running' | 'busy' | 'error' | 'stopped'

const DOT_BY_STATE: Record<DotState, string> = {
  running: 'bg-emerald-500',
  busy: 'bg-amber-400 animate-pulse',
  error: 'bg-red-500',
  stopped: 'bg-neutral-500',
}

export function EmbeddingStatusIndicator() {
  const data = useEmbeddingLogStore((s) => s.data)
  const fetchStatus = useEmbeddingLogStore((s) => s.status)

  // Priority: unreachable / stopped → error → busy → running. A historical
  // error counter does NOT keep the dot red — only the *latest* call's verdict
  // does, so a one-off failure clears on the next healthy call.
  let dotState: DotState = 'stopped'
  let text: string
  let textTitle: string | undefined

  if (fetchStatus === 'error') {
    text = 'Backend unreachable'
  } else if (data === null) {
    text = 'Loading…'
  } else if (data.state === 'stopped') {
    text = 'Pipeline stopped'
  } else {
    const latest =
      data.entries.length > 0 ? data.entries[data.entries.length - 1] : null
    if (latest?.verdict === 'error') {
      dotState = 'error'
      text = latest.error ? `Error: ${latest.error}` : 'Last call errored'
    } else if (data.queue_depth > 0) {
      dotState = 'busy'
      text = `Matching · ${data.queue_depth} queued`
    } else if (data.last_at === null) {
      dotState = 'running'
      text = 'Idle — no calls yet'
    } else {
      dotState = 'running'
      text = `Last call ${formatRelativeAgo(data.last_at)}`
      textTitle = formatUTC(data.last_at)
    }
  }

  return (
    <>
      <span
        className={`absolute top-2 right-2 w-2 h-2 rounded-full ${DOT_BY_STATE[dotState]}`}
        title={dotState}
        aria-label={`embedding ${dotState}`}
      />
      <div
        className="text-[10px] text-neutral-500 mt-1.5 truncate"
        title={textTitle}
      >
        {text}
      </div>
    </>
  )
}
