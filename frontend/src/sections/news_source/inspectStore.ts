/**
 * Inspect store for the news_source News tab.
 *
 * Reads GET /api/inspect/news (persisted news_item rows, newest first). Like
 * market_source/inspectStore it does not self-mount a poll loop — the News tab
 * drives polling via useEffect so it only runs while visible.
 */
import { create } from 'zustand'

export type InspectNewsItem = {
  id: number
  news_id: string
  content: string
  urgency: string
  sentiment: number | null
  published_at: number
  received_at: number
}

export type NewsResponse = {
  count: number
  news: InspectNewsItem[]
}

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  data: NewsResponse | null
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
}

const ENDPOINT = '/api/inspect/news'

let inflight: Promise<void> | null = null

export const useNewsInspectStore = create<StoreState>((set, get) => ({
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
        const body = (await r.json()) as NewsResponse
        set({ data: body, status: 'ready', error: null })
      } catch (e) {
        // Keep the last data so the list doesn't flash empty on a failed poll.
        set({ status: 'error', error: e instanceof Error ? e.message : String(e) })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
}))
