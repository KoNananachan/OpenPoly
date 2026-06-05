/**
 * Demo fixtures — Strategy (canvas) page (M2).
 *
 * Serves the endpoints the Strategy page pulls so a fresh open looks like a
 * deployed, *running* strategy:
 *   - GET  /api/sections/catalog       — reuse the offline catalog
 *   - GET  /api/canvas/template        — a preset wired-up strategy (+rev)
 *   - PUT  /api/canvas/template        — silent autosave ack (no toast)
 *   - GET  /api/system/mode            — paper
 *   - POST /api/system/mode            — stays paper (mutation → toast)
 *   - GET/POST /api/news/source/{status,start,stop}   — connected
 *   - GET/POST /api/market/source/{status,start,stop} — running
 *   - GET/PUT  /api/wallet/config      — a "configured" placeholder
 *   - GET/POST/DELETE /api/secrets/local* — a few stored keys
 *
 * Timestamps are epoch *seconds* (see news_source/time.ts). NOW is sampled once
 * at module load so every "Xm ago" stays internally consistent for the session.
 */
import type { MockRoute } from '../mockServer'
import { MOCK_RUNTIME_CATALOG } from '../../sections/catalog'
import { SEED_TEMPLATE } from '../../canvas/seedTemplate'
import type {
  ApiResponse as NewsApiResponse,
  Snapshot as NewsSnapshot,
} from '../../sections/news_source/statusStore'
import type {
  ApiResponse as MarketApiResponse,
  Snapshot as MarketSnapshot,
} from '../../sections/market_source/statusStore'
import type { WalletConfig } from '../../setting/walletStore'
import type { StoredKey } from '../../setting/secretsClient'

const NOW = Math.floor(Date.now() / 1000)
const TEMPLATE_REV = 'demo-rev-1'

// ---- news source (connected) ---------------------------------------------

const newsConnected: NewsSnapshot = {
  state: 'connected',
  started_at: NOW - 612,
  last_msg_at: NOW - 23,
  total_recv: 184,
  buffer_size: 1000,
  running_config: null,
  last_error: null,
  reconnect_attempts: 0,
  events: [
    { ts: NOW - 612, kind: 'connecting', detail: null },
    {
      ts: NOW - 610,
      kind: 'connected',
      detail: 'wss://api.tradingnews.press/v1/stream',
    },
  ],
  recent_messages: [
    {
      id: 'n-1001',
      content: 'Fed officials signal openness to holding rates at next meeting.',
      urgency: 'high',
      published_at: NOW - 41,
      received_at: NOW - 40,
    },
    {
      id: 'n-1000',
      content: 'ECB keeps policy rate unchanged; forward guidance steady.',
      urgency: 'medium',
      published_at: NOW - 320,
      received_at: NOW - 318,
    },
  ],
}

const newsStopped: NewsSnapshot = {
  ...newsConnected,
  state: 'stopped',
  started_at: null,
  last_msg_at: null,
  events: [...newsConnected.events, { ts: NOW, kind: 'stopped', detail: null }],
}

const newsResp = (snapshot: NewsSnapshot): NewsApiResponse => ({
  ok: true,
  error: null,
  snapshot,
})

// ---- market source (running) ---------------------------------------------

const marketRunning: MarketSnapshot = {
  state: 'running',
  started_at: NOW - 612,
  last_poll_at: NOW - 47,
  catalog_size: 38,
  poll_count: 5,
  last_error: null,
  running_config: null,
  last_poll: {
    ts: NOW - 47,
    fetched: 100,
    kept: 38,
    reason_counts: {
      min_volume_24h: 27,
      max_spread: 19,
      sports: 11,
      min_hours_to_expiry: 5,
    },
  },
  events: [
    { ts: NOW - 612, kind: 'started', detail: null },
    { ts: NOW - 47, kind: 'poll', detail: 'kept 38/100' },
  ],
}

const marketStopped: MarketSnapshot = {
  ...marketRunning,
  state: 'stopped',
  started_at: null,
  last_poll_at: null,
  events: [
    ...marketRunning.events,
    { ts: NOW, kind: 'stopped', detail: null },
  ],
}

const marketResp = (snapshot: MarketSnapshot): MarketApiResponse => ({
  ok: true,
  error: null,
  snapshot,
})

// ---- wallet + secrets (configured placeholders, D4) ----------------------

// Obviously-fake placeholder values — never real wallet material. Addresses
// are valid 40-hex but spell hex words so no one mistakes them for live data.
const walletConfigured: WalletConfig = {
  private_key_ref: 'local:demo_signer',
  funder_address: '0xFEEDFACEFEEDFACEFEEDFACEFEEDFACEFEEDFACE',
  signer_address: '0xDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF',
  error: null,
}

const storedKeys: StoredKey[] = [
  { name: 'demo_signer', created_at: NOW - 86400 * 1 },
  { name: 'demo_news_key', created_at: NOW - 86400 * 2 },
  { name: 'demo_llm_key', created_at: NOW - 86400 * 3 },
]

// ---- routes ---------------------------------------------------------------

export const canvasRoutes: MockRoute[] = [
  // Section catalog — reuse the offline fallback verbatim.
  {
    pattern: /^\/api\/sections\/catalog$/,
    handler: () => ({ sections: MOCK_RUNTIME_CATALOG }),
  },

  // Canvas template: GET serves the preset graph; PUT is the autosave ack and
  // is deliberately a *read* (silent) so routine syncing never pops the toast.
  {
    pattern: /^\/api\/canvas\/template$/,
    handler: () => ({ ...SEED_TEMPLATE, rev: TEMPLATE_REV }),
  },
  {
    method: 'PUT',
    pattern: /^\/api\/canvas\/template$/,
    kind: 'read',
    handler: () => ({ rev: TEMPLATE_REV }),
  },

  // Exec mode — paper, and stays paper (switching is a no-op in demo).
  {
    pattern: /^\/api\/system\/mode$/,
    handler: () => ({ mode: 'paper' }),
  },
  {
    method: 'POST',
    pattern: /^\/api\/system\/mode$/,
    handler: () => ({ mode: 'paper' }),
  },

  // News source — connected.
  {
    pattern: /^\/api\/news\/source\/status$/,
    handler: () => newsResp(newsConnected),
  },
  {
    method: 'POST',
    pattern: /^\/api\/news\/source\/start$/,
    handler: () => newsResp(newsConnected),
  },
  {
    method: 'POST',
    pattern: /^\/api\/news\/source\/stop$/,
    handler: () => newsResp(newsStopped),
  },

  // Market source — running.
  {
    pattern: /^\/api\/market\/source\/status$/,
    handler: () => marketResp(marketRunning),
  },
  {
    method: 'POST',
    pattern: /^\/api\/market\/source\/start$/,
    handler: () => marketResp(marketRunning),
  },
  {
    method: 'POST',
    pattern: /^\/api\/market\/source\/stop$/,
    handler: () => marketResp(marketStopped),
  },

  // Wallet config.
  {
    pattern: /^\/api\/wallet\/config$/,
    handler: () => walletConfigured,
  },
  {
    method: 'PUT',
    pattern: /^\/api\/wallet\/config$/,
    handler: () => walletConfigured,
  },

  // Local secrets store.
  {
    pattern: /^\/api\/secrets\/local$/,
    handler: () => ({ entries: storedKeys }),
  },
  {
    method: 'POST',
    pattern: /^\/api\/secrets\/local$/,
    handler: () => ({
      ok: true,
      entry: { name: 'new_key', created_at: NOW },
    }),
  },
  {
    method: 'DELETE',
    pattern: /^\/api\/secrets\/local\/.+$/,
    handler: () => new Response(null, { status: 204 }),
  },
]
