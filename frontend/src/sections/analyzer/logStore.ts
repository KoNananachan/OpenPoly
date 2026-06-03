/**
 * Analyzer log store (v7 P5). Polls GET /api/analyzer/log every 3s while
 * the page is visible. Mirrors useNewsSourceStatusStore's pattern:
 * - status === 'loading' only on the cold first fetch (no polling flicker)
 * - keep last data on transient errors so the tab can still render
 * - visibilitychange pauses polling; immediate refresh on return
 * - HMR cleans up the interval so dev double-poll doesn't accumulate
 */
import { create } from 'zustand'

export type Verdict = 'ok' | 'skip' | 'fail_open' | 'error'

export type AnalyzerLogEntry = {
  ts: number
  news_id: string
  news_content_preview: string
  urgency: string
  verdict: Verdict
  p_model: number | null
  confidence: string | null
  market_id: string | null
  latency_ms: number
  error: string | null
}

export type AnalyzerLogResponse = {
  entries: AnalyzerLogEntry[]
  counters: Record<Verdict, number>
  last_at: number | null
  queue_depth: number
  state: 'stopped' | 'running'
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  data: AnalyzerLogResponse | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

const ENDPOINT = '/api/analyzer/log'
const POLL_INTERVAL_MS = 3000

let inflight: Promise<void> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchLog(): Promise<AnalyzerLogResponse> {
  const r = await fetch(ENDPOINT)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as AnalyzerLogResponse
}

export const useAnalyzerLogStore = create<StoreState>((set, get) => ({
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
  void useAnalyzerLogStore.getState().refresh()
  pollTimer = setInterval(() => {
    if (!isVisible()) return
    void useAnalyzerLogStore.getState().refresh()
  }, POLL_INTERVAL_MS)
  document.addEventListener('visibilitychange', () => {
    if (isVisible()) void useAnalyzerLogStore.getState().refresh()
  })
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      if (pollTimer !== null) clearInterval(pollTimer)
    })
  }
}
