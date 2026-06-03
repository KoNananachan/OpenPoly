// Types for the News pipeline tab (v16). News carries through four
// stages — `/api/inspect/news` is the only persisted source; the other
// three (`embedding` / `analyzer` / `entry`) are short-lived in-memory
// ring logs on the backend, so any stage in a `NewsPipelineCard` may be
// null when its log has evicted the news_id.

// Matches openpoly.news.ring_buffer.Urgency. Tradingnews tends to send
// `regular`; `high`/`medium`/`low` are the bucketed view used by filters.
export type Urgency = 'high' | 'medium' | 'low' | 'regular'

// Matches openpoly.runtime.section_log.Verdict — universal across the
// three section logs (embedding / analyzer / entry).
export type Verdict = 'ok' | 'skip' | 'fail_open' | 'error'

// One persisted NewsItem from `/api/inspect/news` (returned as `news[]`
// inside a `{count, news}` envelope).
export type NewsItem = {
  id: number
  news_id: string
  content: string
  urgency: Urgency
  sentiment: number | null
  published_at: number
  received_at: number
}

// One row in `/api/embedding/log.entries[]`.
export type EmbeddingCall = {
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

// One row in `/api/analyzer/log.entries[]` — a superset of
// `AnalyzerDecision` from portfolioTypes.ts. AnalyzerRationaleBlock keeps
// using the narrower type (rationale-only view); NewsCard's analyzer
// stage uses this full shape (verdict / market_id / error matter for
// the timeline node, not for the rationale rollup).
export type AnalyzerCallEntry = {
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
  rationale: string | null
}

// One row in `/api/entry/log.entries[]`. `fill_*` and `position_id` are
// set only when entry's OrderIntent reached the executor and the
// executor responded; `verdict==ok` does not imply a fill (executor may
// still skip / error).
export type EntryDecision = {
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
  fill_status: string | null
  fill_price: number | null
  fill_qty: number | null
  position_id: number | null
}

// Terminal state of one news's journey through the pipeline.
//   - filled:   entry produced a position (fill_status === 'filled')
//   - errored:  any stage errored
//   - skipped:  embedding / analyzer / entry skipped (and no later error)
//   - pending:  news exists but no downstream stage yet (or in-flight)
// Filter chip semantics (D6) build on this.
export type CardState = 'filled' | 'skipped' | 'errored' | 'pending'

// Join product — one per NewsItem (1:1 by news_id). Each non-news stage
// may be null if the backend ring already evicted that news_id, or if
// the news has not progressed there yet (e.g. analyzer null when
// embedding skipped). UI must render each segment independently with a
// fallback — see plan §Risks/§3.
export type NewsPipelineCard = {
  news: NewsItem
  embedding: EmbeddingCall | null
  analyzer: AnalyzerCallEntry | null
  entry: EntryDecision | null
  state: CardState
}
