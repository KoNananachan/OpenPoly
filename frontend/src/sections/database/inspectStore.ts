/**
 * Inspect store for the database section's Tables tab.
 *
 * One refresh fetches all three read-side endpoints in parallel:
 *   /api/inspect/db-status   — table row counts + write-behind writer stats
 *   /api/inspect/order-books — recent order_book_snapshot rows
 *   /api/inspect/news        — recent news_item rows
 * The Tables tab drives polling via useEffect so it only runs while visible.
 */
import { create } from 'zustand'

export type WriterStats = { written: number; dropped: number; pending: number }

export type DbStatus = {
  tables: Record<string, number>
  writers: { order_book: WriterStats | null; news: WriterStats | null }
}

export type OrderBookRow = {
  id: number
  token_id: string
  recorded_at: number
  bids: [number, number][]
  asks: [number, number][]
}

export type NewsRow = {
  id: number
  news_id: string
  content: string
  urgency: string
  sentiment: number | null
  published_at: number
  received_at: number
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

const ROW_LIMIT = 30

type StoreState = {
  status: DbStatus | null
  orderBooks: OrderBookRow[]
  news: NewsRow[]
  fetchStatus: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

let inflight: Promise<void> | null = null

export const useDatabaseInspectStore = create<StoreState>((set, get) => ({
  status: null,
  orderBooks: [],
  news: [],
  fetchStatus: 'idle',
  error: null,
  refresh: async () => {
    if (inflight) return inflight
    inflight = (async () => {
      if (get().status === null) set({ fetchStatus: 'loading' })
      try {
        const [statusR, obR, newsR] = await Promise.all([
          fetch('/api/inspect/db-status'),
          fetch(`/api/inspect/order-books?limit=${ROW_LIMIT}`),
          fetch(`/api/inspect/news?limit=${ROW_LIMIT}`),
        ])
        if (!statusR.ok || !obR.ok || !newsR.ok) throw new Error('HTTP error')
        const status = (await statusR.json()) as DbStatus
        const ob = (await obR.json()) as { order_books: OrderBookRow[] }
        const news = (await newsR.json()) as { news: NewsRow[] }
        set({
          status,
          orderBooks: ob.order_books,
          news: news.news,
          fetchStatus: 'ready',
          error: null,
        })
      } catch (e) {
        // Keep the last data so the panel doesn't flash empty on a failed poll.
        set({
          fetchStatus: 'error',
          error: e instanceof Error ? e.message : String(e),
        })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
}))
