/** Shared portfolio row types for the Activity tabs. */

/**
 * One verdict-ok analyzer call (LLM's stated reason for the decision).
 * Shape matches `_lookup_analyzer_decisions` in the backend (PD3).
 */
export type AnalyzerDecision = {
  rationale: string | null
  p_model: number | null
  confidence: string | null
  ts: number
}

export type PositionRecord = {
  id: number
  market_id: string
  side: 'yes' | 'no'
  token_id: string
  condition_id: string
  qty: number
  avg_entry_price: number
  status: 'open' | 'closed'
  opened_at: number
  closed_at: number | null
  close_reason: string | null
  realized_pnl: number | null
  // Both /api/positions list AND /api/positions/{id} now populate these
  // (v15 PR1). market_question is null when the market has been evicted
  // from the catalog; analyzer_decisions is [] (never undefined) when the
  // analyzer_log ring no longer holds the original call. Kept optional
  // for tolerance — older test fixtures or partial mocks may omit them.
  market_question?: string | null
  analyzer_decisions?: AnalyzerDecision[]
}

export type Fill = {
  id: number
  ts: number
  market_id: string
  side: 'yes' | 'no'
  action: 'buy' | 'sell'
  price: number
  qty: number
  fee: number
  position_id: number
  news_id: string | null
  trigger: string | null
}
