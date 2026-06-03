/**
 * Embedding log store (EM6). Polls GET /api/embedding/log every 3s while the
 * page is visible. Mirrors useAnalyzerLogStore's pattern:
 * - status === 'loading' only on the cold first fetch (no polling flicker)
 * - keep last data on transient errors so the tab can still render
 * - visibilitychange pauses polling; immediate refresh on return
 * - HMR cleans up the interval so dev double-poll doesn't accumulate
 */
import { create } from 'zustand'

export type Verdict = 'ok' | 'skip' | 'fail_open' | 'error'

export type EmbeddingLogEntry = {
  ts: number
  news_id: string
  news_content_preview: string
  urgency: string
  verdict: Verdict
  candidate_count: number
  top_market_id: string | null
  top_score: number | null
  catalog_size: number
  latency_ms: number
  error: string | null
}

export type WarmEvent = 'warm' | 'model_load' | 'cache_load' | 'error'

/** A background warm-cache event — the catalog-embedding loop, distinct from
 *  the per-news-tick EmbeddingLogEntry. `warm_count` is total markets warm. */
export type EmbeddingWarmEntry = {
  ts: number
  event: WarmEvent
  embedded_count: number
  warm_count: number
  catalog_size: number
  latency_ms: number
  detail: string | null
  error: string | null
}

export type EmbeddingLogResponse = {
  entries: EmbeddingLogEntry[]
  counters: Record<Verdict, number>
  last_at: number | null
  queue_depth: number
  state: 'stopped' | 'running'
  warm: EmbeddingWarmEntry[]
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  data: EmbeddingLogResponse | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

const ENDPOINT = '/api/embedding/log'
const POLL_INTERVAL_MS = 3000

let inflight: Promise<void> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchLog(): Promise<EmbeddingLogResponse> {
  const r = await fetch(ENDPOINT)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as EmbeddingLogResponse
}

export const useEmbeddingLogStore = create<StoreState>((set, get) => ({
  data: null,
  status: 'idle',
  error: null,
  refresh: async () => {
    if (inflight) return inflight
    inflight = (async () => {
      if (get().data === null) set({ status: 'loading' })
      try {
        const body = await fetchLog()
        set({ data: body, status: 'ready', error: null })
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        set({ status: 'error', error: msg })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
}))

function isVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

if (typeof window !== 'undefined') {
  void useEmbeddingLogStore.getState().refresh()
  pollTimer = setInterval(() => {
    if (!isVisible()) return
    void useEmbeddingLogStore.getState().refresh()
  }, POLL_INTERVAL_MS)
  document.addEventListener('visibilitychange', () => {
    if (isVisible()) void useEmbeddingLogStore.getState().refresh()
  })
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      if (pollTimer !== null) clearInterval(pollTimer)
    })
  }
}
