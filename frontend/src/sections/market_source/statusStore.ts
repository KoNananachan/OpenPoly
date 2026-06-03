/**
 * Live status store for the singleton market_source backend instance.
 *
 * Auto-polls GET /api/market/source/status every 3s when the page is visible.
 * Mirrors news_source/statusStore.ts — visibility-gated polling, HMR-safe,
 * keeps the last snapshot on a failed poll so the UI never flashes empty.
 *
 * Wire format matches backend MarketSourceResponse (snake_case passed through).
 */
import { create } from 'zustand'

export type SourceState = 'stopped' | 'running' | 'error'

export type LogEvent = {
  ts: number
  kind: string
  detail: string | null
}

export type LastPoll = {
  ts: number
  fetched: number
  kept: number
  reason_counts: Record<string, number>
}

export type Snapshot = {
  state: SourceState
  started_at: number | null
  last_poll_at: number | null
  catalog_size: number
  poll_count: number
  last_error: string | null
  running_config: Record<string, unknown> | null
  last_poll: LastPoll | null
  events: LogEvent[]
}

export type ApiResponse = {
  ok: boolean
  error: string | null
  snapshot: Snapshot
}

export type StartConfig = {
  poll_interval_seconds?: number
  gamma_limit?: number
  filter?: Record<string, unknown>
  // Pydantic v2 ignores extras, so passing the full canvas node config is safe.
  [k: string]: unknown
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  snapshot: Snapshot | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
  start: (config: StartConfig) => Promise<ApiResponse>
  stop: () => Promise<ApiResponse>
}

const STATUS_ENDPOINT = '/api/market/source/status'
const START_ENDPOINT = '/api/market/source/start'
const STOP_ENDPOINT = '/api/market/source/stop'
const POLL_INTERVAL_MS = 3000

let inflight: Promise<void> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchStatusRaw(): Promise<ApiResponse> {
  const r = await fetch(STATUS_ENDPOINT)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as ApiResponse
}

export const useMarketSourceStatusStore = create<StoreState>((set, get) => ({
  snapshot: null,
  status: 'idle',
  error: null,
  refresh: async () => {
    if (inflight) return inflight
    inflight = (async () => {
      // Only flip to 'loading' on the cold first fetch — polling loops
      // shouldn't visibly toggle the UI every 3s.
      if (get().snapshot === null) set({ status: 'loading' })
      try {
        const body = await fetchStatusRaw()
        set({ snapshot: body.snapshot, status: 'ready', error: null })
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        // Keep last snapshot so consumers can still render stale data.
        set({ status: 'error', error: msg })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
  start: async (config) => {
    const r = await fetch(START_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const body = (await r.json()) as ApiResponse
    set({ snapshot: body.snapshot, status: 'ready', error: null })
    return body
  },
  stop: async () => {
    const r = await fetch(STOP_ENDPOINT, { method: 'POST' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const body = (await r.json()) as ApiResponse
    set({ snapshot: body.snapshot, status: 'ready', error: null })
    return body
  },
}))

// ---------- Polling self-mount ----------

function isVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

function startPolling(): void {
  if (pollTimer !== null) return
  pollTimer = setInterval(() => {
    if (!isVisible()) return
    void useMarketSourceStatusStore.getState().refresh()
  }, POLL_INTERVAL_MS)
}

function stopPolling(): void {
  if (pollTimer === null) return
  clearInterval(pollTimer)
  pollTimer = null
}

if (typeof window !== 'undefined') {
  void useMarketSourceStatusStore.getState().refresh()
  startPolling()
  document.addEventListener('visibilitychange', () => {
    if (isVisible()) {
      void useMarketSourceStatusStore.getState().refresh()
    }
  })

  // Vite HMR: tear the interval down so a hot reload doesn't double-poll.
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      stopPolling()
    })
  }
}
