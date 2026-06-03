/**
 * Entry log store (v7 P5). Mirrors useAnalyzerLogStore — only the entry
 * shape and endpoint differ.
 */
import { create } from 'zustand'

export type Verdict = 'ok' | 'skip' | 'fail_open' | 'error'

export type EntryLogEntry = {
  ts: number
  news_id: string
  ar_p_model: number | null
  ar_market_id: string | null
  verdict: Verdict
  side: string | null
  qty: number | null
  price: number | null
  reason: string | null
  latency_ms: number
  error: string | null
}

export type EntryLogResponse = {
  entries: EntryLogEntry[]
  counters: Record<Verdict, number>
  last_at: number | null
  queue_depth: number
  state: 'stopped' | 'running'
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  data: EntryLogResponse | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

const ENDPOINT = '/api/entry/log'
const POLL_INTERVAL_MS = 3000

let inflight: Promise<void> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchLog(): Promise<EntryLogResponse> {
  const r = await fetch(ENDPOINT)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as EntryLogResponse
}

export const useEntryLogStore = create<StoreState>((set, get) => ({
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
  void useEntryLogStore.getState().refresh()
  pollTimer = setInterval(() => {
    if (!isVisible()) return
    void useEntryLogStore.getState().refresh()
  }, POLL_INTERVAL_MS)
  document.addEventListener('visibilitychange', () => {
    if (isVisible()) void useEntryLogStore.getState().refresh()
  })
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      if (pollTimer !== null) clearInterval(pollTimer)
    })
  }
}
