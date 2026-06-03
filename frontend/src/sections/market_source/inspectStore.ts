/**
 * Inspect store for the market_source Markets tab.
 *
 * Reads GET /api/inspect/markets (the live catalog + latest sampled order-book
 * price per market). Unlike statusStore this does not self-mount a poll loop —
 * the Markets tab drives polling via useEffect so it only runs while visible.
 */
import { create } from 'zustand'

export type InspectMarket = {
  market_id: string
  question: string
  yes_token_id: string
  volume_24h: number
  liquidity: number
  end_date: string | null
  best_bid: number | null
  best_ask: number | null
  mid: number | null
  spread: number | null
  price_ts: number | null
}

export type MarketsResponse = {
  catalog_size: number
  order_book_count: number
  last_poll: {
    ts: number
    fetched: number
    kept: number
    reason_counts: Record<string, number>
  } | null
  markets: InspectMarket[]
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  data: MarketsResponse | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

const ENDPOINT = '/api/inspect/markets'

let inflight: Promise<void> | null = null

export const useMarketInspectStore = create<StoreState>((set, get) => ({
  data: null,
  status: 'idle',
  error: null,
  refresh: async () => {
    if (inflight) return inflight
    inflight = (async () => {
      // Only flip to 'loading' on the cold first fetch — polling shouldn't
      // toggle the UI every few seconds.
      if (get().data === null) set({ status: 'loading' })
      try {
        const r = await fetch(ENDPOINT)
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const body = (await r.json()) as MarketsResponse
        set({ data: body, status: 'ready', error: null })
      } catch (e) {
        // Keep the last data so the table doesn't flash empty on a failed poll.
        set({ status: 'error', error: e instanceof Error ? e.message : String(e) })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
}))
