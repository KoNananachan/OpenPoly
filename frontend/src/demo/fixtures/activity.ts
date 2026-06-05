/**
 * Demo fixtures — Activity page (M3).
 *
 * Serves the three Activity tabs:
 *   - Overview  → GET /api/portfolio/equity   (upward equity curve + summary)
 *   - Positions → GET /api/positions, /api/fills
 *               → GET /api/positions/{id}      (detail)
 *               → POST /api/positions/close-all (mutation → toast)
 *               → GET /api/inspect/order-books/{token_id} (detail chart)
 *   - News      → GET /api/inspect/news + the 4 section logs (pipeline join)
 *
 * D3: 4 positions (2 open / 2 closed; one closed win, one closed loss) with a
 * clickable detail. Timestamps are epoch *seconds*. Curves are deterministic
 * (sin-based) so screenshots are stable across opens.
 */
import type { MockRoute } from '../mockServer'
import type {
  EquityPoint,
  EquityResponse,
  EquitySummary,
} from '../../routes/activity/equityClient'
import type {
  AnalyzerDecision,
  Fill,
  PositionRecord,
} from '../../routes/activity/portfolioTypes'
import type {
  AnalyzerCallEntry,
  EmbeddingCall,
  EntryDecision,
  NewsItem,
} from '../../routes/activity/newsTypes'
import type {
  OrderBookHistory,
  OrderBookSnapshot,
} from '../../routes/activity/orderBookClient'
import type { CloseAllResult } from '../../setting/walletStore'
// The canvas StatusIndicators read the FULL log envelope (counters / last_at /
// queue_depth / state …), not just `entries` like the News tab does. Returning
// only `{entries}` left `last_at` undefined → formatUTC(undefined) crashed the
// Strategy page. Type against the real response shapes so a missing field is a
// compile error, not a runtime RangeError.
import type { EntryLogResponse } from '../../sections/entry/logStore'
import type { AnalyzerLogResponse } from '../../sections/analyzer/logStore'
import type { EmbeddingLogResponse } from '../../sections/embedding/logStore'
import type { ExitLogResponse } from '../../sections/exit/logStore'

const NOW = Math.floor(Date.now() / 1000)
const HOUR = 3600

// ---- positions (D3) -------------------------------------------------------

const analyzerDecisionsFor = (
  p: number,
  conf: string,
  reason: string,
  ts: number,
): AnalyzerDecision[] => [
  { rationale: reason, p_model: p, confidence: conf, ts },
]

const positions: PositionRecord[] = [
  {
    id: 101,
    market_id: 'mkt-fed-pause',
    side: 'yes',
    token_id: 'tok-101-yes',
    condition_id: '0xcond101',
    qty: 19.2,
    avg_entry_price: 0.52,
    status: 'open',
    opened_at: NOW - 5 * HOUR,
    closed_at: null,
    close_reason: null,
    realized_pnl: null,
    market_question: 'Will the Fed hold rates at the next FOMC meeting?',
    analyzer_decisions: analyzerDecisionsFor(
      0.78,
      'high',
      'Fresh dovish commentary lifts the implied hold probability above the market price — positive edge on YES.',
      NOW - 5 * HOUR,
    ),
  },
  {
    id: 102,
    market_id: 'mkt-ecb-cut',
    side: 'no',
    token_id: 'tok-102-no',
    condition_id: '0xcond102',
    qty: 14.0,
    avg_entry_price: 0.61,
    status: 'open',
    opened_at: NOW - 3 * HOUR,
    closed_at: null,
    close_reason: null,
    realized_pnl: null,
    market_question: 'Will the ECB cut its policy rate this month?',
    analyzer_decisions: analyzerDecisionsFor(
      0.33,
      'medium',
      'Steady forward guidance argues against an imminent cut; NO carries edge versus the quoted price.',
      NOW - 3 * HOUR,
    ),
  },
  {
    id: 103,
    market_id: 'mkt-cpi-soft',
    side: 'yes',
    token_id: 'tok-103-yes',
    condition_id: '0xcond103',
    qty: 18.0,
    avg_entry_price: 0.44,
    status: 'closed',
    opened_at: NOW - 22 * HOUR,
    closed_at: NOW - 6 * HOUR,
    close_reason: 'take_profit',
    realized_pnl: 9.5,
    market_question: 'Will headline CPI come in below consensus?',
    analyzer_decisions: analyzerDecisionsFor(
      0.71,
      'high',
      'Leading indicators point to a soft print; took profit when the market repriced toward the model.',
      NOW - 22 * HOUR,
    ),
  },
  {
    id: 104,
    market_id: 'mkt-jobs-beat',
    side: 'no',
    token_id: 'tok-104-no',
    condition_id: '0xcond104',
    qty: 12.5,
    avg_entry_price: 0.58,
    status: 'closed',
    opened_at: NOW - 30 * HOUR,
    closed_at: NOW - 18 * HOUR,
    close_reason: 'stop_loss',
    realized_pnl: -2.3,
    market_question: 'Will nonfarm payrolls beat expectations?',
    analyzer_decisions: analyzerDecisionsFor(
      0.4,
      'low',
      'Thesis invalidated by a stronger-than-expected revision; stop-loss closed the position.',
      NOW - 30 * HOUR,
    ),
  },
]

const positionById = new Map(positions.map((p) => [String(p.id), p]))

const fills: Fill[] = [
  {
    id: 5001,
    ts: NOW - 5 * HOUR,
    market_id: 'mkt-fed-pause',
    side: 'yes',
    action: 'buy',
    price: 0.52,
    qty: 19.2,
    fee: 0,
    position_id: 101,
    news_id: 'an-2001',
    trigger: 'edge_threshold',
  },
  {
    id: 5002,
    ts: NOW - 3 * HOUR,
    market_id: 'mkt-ecb-cut',
    side: 'no',
    action: 'buy',
    price: 0.61,
    qty: 14.0,
    fee: 0,
    position_id: 102,
    news_id: 'an-2003',
    trigger: 'edge_threshold',
  },
  {
    id: 5003,
    ts: NOW - 22 * HOUR,
    market_id: 'mkt-cpi-soft',
    side: 'yes',
    action: 'buy',
    price: 0.44,
    qty: 18.0,
    fee: 0,
    position_id: 103,
    news_id: 'an-2004',
    trigger: 'edge_threshold',
  },
  {
    id: 5004,
    ts: NOW - 6 * HOUR,
    market_id: 'mkt-cpi-soft',
    side: 'yes',
    action: 'sell',
    price: 0.97,
    qty: 18.0,
    fee: 0,
    position_id: 103,
    news_id: null,
    trigger: 'take_profit',
  },
]

// ---- equity curve (Overview) ---------------------------------------------

const EQUITY_N = 48
const EQUITY_STEP = 600 // 10-minute samples → ~8h window
const START_CAPITAL = 50

function buildEquity(): EquityResponse {
  const points: EquityPoint[] = []
  for (let i = 0; i < EQUITY_N; i++) {
    const ts = NOW - (EQUITY_N - 1 - i) * EQUITY_STEP
    const t = i / (EQUITY_N - 1)
    // Realized climbs as the winning trade banks; unrealized wiggles around
    // the current open exposure.
    const realized = round2(7.2 * Math.min(1, t * 1.15))
    const unrealized = round2(3.1 + Math.sin(i / 3) * 0.7 + Math.sin(i / 6.5) * 0.6)
    const equity = round2(START_CAPITAL + realized + unrealized)
    points.push({ ts, equity, realized, unrealized })
  }
  const last = points[points.length - 1]
  const summary: EquitySummary = {
    realized: last.realized,
    unrealized: last.unrealized,
    total: round2(last.realized + last.unrealized),
    open_positions: positions.filter((p) => p.status === 'open').length,
  }
  return { points, summary }
}

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

const equityResponse = buildEquity()

// ---- order book history (Position detail) --------------------------------

const OB_N = 16
const OB_STEP = 1200

function buildOrderBook(tokenId: string): OrderBookHistory {
  const snapshots: OrderBookSnapshot[] = []
  for (let i = 0; i < OB_N; i++) {
    const recorded_at = NOW - (OB_N - 1 - i) * OB_STEP
    const t = i / (OB_N - 1)
    const mid = 0.42 + 0.19 * t + Math.sin(i / 2.5) * 0.012
    const bid = round3(mid - 0.01)
    const ask = round3(mid + 0.01)
    snapshots.push({
      recorded_at,
      bids: [
        [bid, 120 + i * 4],
        [round3(bid - 0.01), 240],
      ],
      asks: [
        [ask, 110 + i * 3],
        [round3(ask + 0.01), 220],
      ],
    })
  }
  return { token_id: tokenId, count: snapshots.length, snapshots }
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000
}

// ---- news pipeline (News tab) --------------------------------------------

const newsItems: NewsItem[] = [
  {
    id: 2001,
    news_id: 'an-2001',
    content: 'Fed officials signal openness to holding rates at the next meeting.',
    urgency: 'high',
    sentiment: 0.4,
    published_at: NOW - 5 * HOUR - 60,
    received_at: NOW - 5 * HOUR - 55,
  },
  {
    id: 2002,
    news_id: 'an-2002',
    content: 'Minor regional poll released; little market-moving content.',
    urgency: 'low',
    sentiment: 0.0,
    published_at: NOW - 2 * HOUR,
    received_at: NOW - 2 * HOUR + 3,
  },
  {
    id: 2003,
    news_id: 'an-2003',
    content: 'ECB keeps policy rate unchanged; forward guidance steady.',
    urgency: 'medium',
    sentiment: -0.1,
    published_at: NOW - 3 * HOUR - 30,
    received_at: NOW - 3 * HOUR - 25,
  },
  {
    id: 2004,
    news_id: 'an-2004',
    content: 'Early indicators suggest a softer-than-expected CPI print.',
    urgency: 'high',
    sentiment: 0.5,
    published_at: NOW - 22 * HOUR,
    received_at: NOW - 22 * HOUR + 4,
  },
  {
    id: 2005,
    news_id: 'an-2005',
    content: 'Commodity desk note: energy inventories broadly in line.',
    urgency: 'regular',
    sentiment: 0.05,
    published_at: NOW - 40 * 60,
    received_at: NOW - 40 * 60 + 2,
  },
]

// A couple of analyzer/entry rows so the News tab shows real "filled" and
// "skipped" cards instead of everything reading as pending. Embedding/exit
// logs stay empty — the join tolerates null stages.
const analyzerLog: AnalyzerCallEntry[] = [
  {
    ts: NOW - 5 * HOUR - 50,
    news_id: 'an-2001',
    news_content_preview: 'Fed officials signal openness to holding rates…',
    urgency: 'high',
    verdict: 'ok',
    p_model: 0.78,
    confidence: 'high',
    market_id: 'mkt-fed-pause',
    latency_ms: 1840,
    error: null,
    rationale: 'Dovish tone lifts the implied hold probability above price.',
  },
  {
    ts: NOW - 2 * HOUR + 4,
    news_id: 'an-2002',
    news_content_preview: 'Minor regional poll released…',
    urgency: 'low',
    verdict: 'skip',
    p_model: null,
    confidence: 'low',
    market_id: null,
    latency_ms: 920,
    error: null,
    rationale: null,
  },
]

const entryLog: EntryDecision[] = [
  {
    ts: NOW - 5 * HOUR - 45,
    news_id: 'an-2001',
    ar_p_model: 0.78,
    ar_market_id: 'mkt-fed-pause',
    verdict: 'ok',
    side: 'yes',
    qty: 19.2,
    price: 0.52,
    reason: 'edge 0.06 ≥ min_edge',
    latency_ms: 410,
    error: null,
    fill_status: 'filled',
    fill_price: 0.52,
    fill_qty: 19.2,
    position_id: 101,
  },
]

const embeddingLog: EmbeddingCall[] = []

// Full log envelopes for the canvas StatusIndicators. `state: 'running'` keeps
// the node dots green; last_at drives the "Last decision Xm ago" caption.
const entryLogResponse: EntryLogResponse = {
  entries: entryLog,
  counters: { ok: 1, skip: 0, fail_open: 0, error: 0 },
  last_at: entryLog.length ? entryLog[entryLog.length - 1].ts : null,
  queue_depth: 0,
  state: 'running',
}

const analyzerLogResponse: AnalyzerLogResponse = {
  entries: analyzerLog,
  counters: { ok: 1, skip: 1, fail_open: 0, error: 0 },
  last_at: analyzerLog.length ? analyzerLog[analyzerLog.length - 1].ts : null,
  queue_depth: 0,
  state: 'running',
}

const embeddingLogResponse: EmbeddingLogResponse = {
  entries: embeddingLog,
  counters: { ok: 0, skip: 0, fail_open: 0, error: 0 },
  last_at: null,
  queue_depth: 0,
  state: 'running',
  warm: [],
}

const exitLogResponse: ExitLogResponse = {
  entries: [],
  counters: { ok: 0, skip: 0, fail_open: 0, error: 0 },
  last_at: null,
  state: 'running',
  last_tick_at: NOW - 30,
  open_positions: 2,
  blocked: 0,
}

const closeAllResult: CloseAllResult = {
  attempted: 2,
  filled: 2,
  skipped: 0,
  errored: 0,
  details: positions
    .filter((p) => p.status === 'open')
    .map((p) => ({
      position_id: p.id,
      market_id: p.market_id,
      side: p.side,
      ok: true,
      price: p.avg_entry_price,
      qty: p.qty,
    })),
}

// ---- routes ---------------------------------------------------------------

export const activityRoutes: MockRoute[] = [
  // Overview.
  {
    pattern: /^\/api\/portfolio\/equity$/,
    handler: () => equityResponse,
  },

  // Positions list + fills ledger.
  {
    pattern: /^\/api\/positions$/,
    handler: () => ({ positions }),
  },
  {
    pattern: /^\/api\/fills$/,
    handler: () => ({ fills }),
  },
  // close-all must precede the {id} matcher; it's POST so they don't collide.
  {
    method: 'POST',
    pattern: /^\/api\/positions\/close-all$/,
    handler: () => closeAllResult,
  },
  // Position detail by id — 404 for unknown so the UI shows "not found".
  {
    pattern: /^\/api\/positions\/([^/]+)$/,
    handler: (ctx) => {
      const row = positionById.get(decodeURIComponent(ctx.params[1]))
      return row ?? new Response(JSON.stringify({ detail: 'not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    },
  },

  // Order book history for the detail chart (any token → same shaped curve).
  {
    pattern: /^\/api\/inspect\/order-books\/([^/]+)$/,
    handler: (ctx) => buildOrderBook(decodeURIComponent(ctx.params[1])),
  },

  // News tab: persisted news + the section logs it joins against.
  {
    pattern: /^\/api\/inspect\/news$/,
    handler: () => ({ count: newsItems.length, news: newsItems }),
  },
  {
    pattern: /^\/api\/embedding\/log$/,
    kind: 'read',
    handler: () => embeddingLogResponse,
  },
  {
    pattern: /^\/api\/analyzer\/log$/,
    kind: 'read',
    handler: () => analyzerLogResponse,
  },
  {
    pattern: /^\/api\/entry\/log$/,
    kind: 'read',
    handler: () => entryLogResponse,
  },
  {
    pattern: /^\/api\/exit\/log$/,
    kind: 'read',
    handler: () => exitLogResponse,
  },
]
