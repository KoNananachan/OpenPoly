/**
 * Exit log store (v18). Mirrors useEntryLogStore — exit-specific shape +
 * endpoint. The exit monitor is position-driven (no news queue), so instead of
 * queue_depth it carries a tick heartbeat: `last_tick_at` / `open_positions` /
 * `blocked`. Within-threshold + no-order-book holds write NO entry — the ring
 * keeps only ok / error closes, so they never get evicted by skip churn.
 */
import { create } from 'zustand'

export type Verdict = 'ok' | 'skip' | 'fail_open' | 'error'

export type ExitLogEntry = {
  ts: number
  position_id: number
  market_id: string
  side: string
  verdict: Verdict
  trigger: string | null
  return_pct: number | null
  fill_price: number | null
  realized_pnl: number | null
  reason: string | null
  error: string | null
  peak_price: number | null
}

export type ExitLogResponse = {
  entries: ExitLogEntry[]
  counters: Record<Verdict, number>
  last_at: number | null
  // Exit monitor loop state — only ever 'stopped' | 'running' (no 'error':
  // errors surface via the latest entry's verdict, not the loop state).
  state: 'stopped' | 'running'
  // Tick heartbeat (v18).
  last_tick_at: number | null
  open_positions: number | null
  blocked: number | null
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  data: ExitLogResponse | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

const ENDPOINT = '/api/exit/log'
const POLL_INTERVAL_MS = 3000

let inflight: Promise<void> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchLog(): Promise<ExitLogResponse> {
  const r = await fetch(ENDPOINT)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as ExitLogResponse
}

export const useExitLogStore = create<StoreState>((set, get) => ({
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
  void useExitLogStore.getState().refresh()
  pollTimer = setInterval(() => {
    if (!isVisible()) return
    void useExitLogStore.getState().refresh()
  }, POLL_INTERVAL_MS)
  document.addEventListener('visibilitychange', () => {
    if (isVisible()) void useExitLogStore.getState().refresh()
  })
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      if (pollTimer !== null) clearInterval(pollTimer)
    })
  }
}
