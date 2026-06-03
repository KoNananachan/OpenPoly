// fetch + join layer for the News pipeline tab (v16).
//
// Pulls four endpoints in parallel, joins by news_id, and derives each
// card's terminal state. Non-news endpoint failures degrade gracefully
// (their stage becomes null on every card); a news endpoint failure
// throws so the caller can surface a list-level error rather than
// silently rendering an empty page.

import type {
  AnalyzerCallEntry,
  CardState,
  EmbeddingCall,
  EntryDecision,
  NewsItem,
  NewsPipelineCard,
} from './newsTypes'

// Caller may bump this via the Load-more button (D5). The 3 section log
// rings are size-200 server-side, so requesting more than that returns
// at most 200 — no need to plumb the value through the other URLs.
export const DEFAULT_NEWS_LIMIT = 100

// Ring capacity (per openpoly.runtime.section_log.DEFAULT_MAXLEN). Asking
// for exactly the ring size guarantees we'll see whatever the backend
// still remembers.
const SECTION_LOG_LIMIT = 200

export async function fetchNewsPipeline(
  newsLimit: number = DEFAULT_NEWS_LIMIT,
  signal?: AbortSignal,
): Promise<NewsPipelineCard[]> {
  const [newsRes, embRes, anRes, enRes] = await Promise.allSettled([
    fetchJson<{ news?: NewsItem[] }>(`/api/inspect/news?limit=${newsLimit}`, signal),
    fetchJson<{ entries?: EmbeddingCall[] }>(`/api/embedding/log?limit=${SECTION_LOG_LIMIT}`, signal),
    fetchJson<{ entries?: AnalyzerCallEntry[] }>(`/api/analyzer/log?limit=${SECTION_LOG_LIMIT}`, signal),
    fetchJson<{ entries?: EntryDecision[] }>(`/api/entry/log?limit=${SECTION_LOG_LIMIT}`, signal),
  ])

  if (newsRes.status === 'rejected') throw newsRes.reason

  const news = ((newsRes.value?.news ?? []) as NewsItem[])
  const embByNews = indexNewest(extractEntries<EmbeddingCall>(embRes))
  const anByNews = indexNewest(extractEntries<AnalyzerCallEntry>(anRes))
  const enByNews = indexNewest(extractEntries<EntryDecision>(enRes))

  return [...news]
    .sort((a, b) => b.received_at - a.received_at)
    .map((n): NewsPipelineCard => {
      const embedding = embByNews.get(n.news_id) ?? null
      const analyzer = anByNews.get(n.news_id) ?? null
      const entry = enByNews.get(n.news_id) ?? null
      return {
        news: n,
        embedding,
        analyzer,
        entry,
        state: deriveState(embedding, analyzer, entry),
      }
    })
}

// Newest-per-news_id wins. Section rings rarely duplicate (each section
// runs once per news tick), but `entry` retries are possible — keeping
// the most recent decision surfaces the current state, not history.
function indexNewest<T extends { news_id: string; ts: number }>(
  entries: T[],
): Map<string, T> {
  const m = new Map<string, T>()
  for (const e of entries) {
    const prev = m.get(e.news_id)
    if (!prev || e.ts > prev.ts) m.set(e.news_id, e)
  }
  return m
}

// Terminal-state precedence (most-specific wins). Only `fill_status ===
// 'filled'` proves the executor produced a real position — entry's own
// verdict 'ok' just means it emitted an OrderIntent; the executor may
// still have skipped (live_not_ready) or errored downstream.
//
//   filled  — entry produced a position
//   errored — any stage hit an error or error-verdict
//   skipped — any stage skipped / fail_open, OR executor returned a non-filled status
//   pending — news exists, nothing downstream yet
function deriveState(
  emb: EmbeddingCall | null,
  an: AnalyzerCallEntry | null,
  en: EntryDecision | null,
): CardState {
  if (en?.fill_status === 'filled') return 'filled'

  const anyError = Boolean(
    emb?.verdict === 'error' || emb?.error ||
    an?.verdict === 'error' || an?.error ||
    en?.verdict === 'error' || en?.error,
  )
  if (anyError) return 'errored'

  const executorRefused =
    en?.verdict === 'ok' &&
    en?.fill_status != null &&
    en.fill_status !== 'filled'
  const anySkip = Boolean(
    emb?.verdict === 'skip' ||
    an?.verdict === 'skip' || an?.verdict === 'fail_open' ||
    en?.verdict === 'skip' ||
    executorRefused,
  )
  if (anySkip) return 'skipped'

  return 'pending'
}

function extractEntries<T>(
  settled: PromiseSettledResult<{ entries?: T[] }>,
): T[] {
  if (settled.status !== 'fulfilled') return []
  return settled.value?.entries ?? []
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal })
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`)
  return r.json() as Promise<T>
}
