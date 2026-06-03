import type {
  ConfigValues,
  RuntimeCatalogEntry,
  SectionType,
} from './types'

export const TYPE_DISPLAY: Record<SectionType, { label: string; description: string }> = {
  news_source: {
    label: 'News source',
    description: 'External event stream feeding the Analyzer.',
  },
  market_source: {
    label: 'Market source',
    description: 'Polls Polymarket and filters the tradeable market catalog.',
  },
  embedding: {
    label: 'Embedding',
    description: 'Ranks the market catalog against news by semantic similarity.',
  },
  analyzer: {
    label: 'Analyzer',
    description: 'News → (market_id, p_model, confidence) via LLM.',
  },
  entry: {
    label: 'Entry',
    description: 'AnalysisResult → OrderIntent (open/add position) based on edge.',
  },
  exit: {
    label: 'Exit',
    description: 'Position → CloseIntent on take-profit / stop-loss thresholds.',
  },
  database: {
    label: 'Database',
    description: 'Persists order books + news to SQLite; inspect each table.',
  },
}

export const SECTION_ORDER: SectionType[] = [
  'news_source',
  'market_source',
  'embedding',
  'analyzer',
  'entry',
  'exit',
  'database',
]

export function defaultsFromSchema(schema: Record<string, unknown>): ConfigValues {
  const props = (schema.properties ?? {}) as Record<
    string,
    { default?: unknown }
  >
  const out: ConfigValues = {}
  for (const [key, val] of Object.entries(props)) {
    if (val && 'default' in val && val.default !== undefined) {
      out[key] = val.default as ConfigValues[string]
    }
  }
  return out
}

export function defaultConfigForType(
  type: SectionType,
  catalog: RuntimeCatalogEntry[],
): ConfigValues {
  const entry = catalog.find((e) => e.type === type)
  if (!entry) return {}
  return defaultsFromSchema(entry.param_schema)
}

export function findEntry(
  type: SectionType,
  catalog: RuntimeCatalogEntry[],
): RuntimeCatalogEntry | undefined {
  return catalog.find((e) => e.type === type)
}

/**
 * Offline fallback catalog. Mirrors what /api/sections/catalog returns from a
 * stock backend with the baseline section impls (news_source / market_source /
 * analyzer / entry / exit / database).
 *
 * Used when the backend is unreachable so the canvas remains operable. When
 * runtime catalog loads, the live data takes over via catalogStore.
 */
export const MOCK_RUNTIME_CATALOG: RuntimeCatalogEntry[] = [
  {
    type: 'news_source',
    name: 'TradingNewsWSSource',
    version: '0.1.0',
    module: 'openpoly.sections.news_source.tradingnews_ws',
    requires: [],
    source: 'builtin',
    param_schema: {
      type: 'object',
      title: 'TradingNewsWSConfig',
      properties: {
        endpoint: {
          type: 'string',
          title: 'Endpoint',
          description: 'WebSocket endpoint URL.',
          default: 'wss://api.tradingnews.press/v1/stream',
        },
        api_key_ref: {
          type: 'string',
          title: 'Api Key Ref',
          description: 'Reference to the API key (e.g. env:VAR_NAME).',
          default: 'env:OPENPOLY_TRADINGNEWS_KEY',
        },
        freshness_seconds: {
          type: 'integer',
          title: 'Freshness Seconds',
          description: 'Only forward news younger than this when Analyzer ticks.',
          default: 1800,
          minimum: 1,
          maximum: 86400,
        },
        urgency_filter: {
          type: 'string',
          title: 'Urgency Filter',
          description: 'Minimum urgency level to forward.',
          default: 'all',
          enum: ['all', 'low', 'medium', 'high'],
        },
        buffer_size: {
          type: 'integer',
          title: 'Buffer Size',
          description: 'Max in-memory news items retained.',
          default: 1000,
          minimum: 10,
          maximum: 100000,
        },
      },
    },
  },
  {
    type: 'market_source',
    name: 'PolymarketSource',
    version: '0.1.0',
    module: 'openpoly.sections.market_source.polymarket',
    requires: [],
    source: 'builtin',
    param_schema: {
      type: 'object',
      title: 'MarketSourceConfig',
      $defs: {
        MarketFilterConfig: {
          type: 'object',
          title: 'MarketFilterConfig',
          properties: {
            require_zero_fee: {
              type: 'boolean',
              title: 'Require Zero Fee',
              description: 'Drop markets with a non-zero taker fee (v8 zero-fee rule).',
              default: true,
            },
            min_hours_to_expiry: {
              type: 'number',
              title: 'Min Hours To Expiry',
              description: 'Drop markets resolving within this many hours.',
              default: 24.0,
              minimum: 0,
            },
            min_volume_24h: {
              type: 'number',
              title: 'Min Volume 24H',
              description: 'Minimum 24h USD volume.',
              default: 1000.0,
              minimum: 0,
            },
            min_liquidity: {
              type: 'number',
              title: 'Min Liquidity',
              description: 'Minimum liquidity (USD).',
              default: 500.0,
              minimum: 0,
            },
            min_price: {
              type: 'number',
              title: 'Min Price',
              description: 'Drop markets whose reference price is below this.',
              default: 0.03,
              minimum: 0,
              maximum: 0.5,
            },
            max_spread: {
              type: 'number',
              title: 'Max Spread',
              description: 'Drop markets with a wider spread.',
              default: 0.15,
              minimum: 0,
              maximum: 1,
            },
            exclude_event_tags: {
              type: 'array',
              title: 'Exclude Event Tags',
              description: 'Drop markets whose event carries any of these tag slugs.',
              default: ['sports'],
              items: { type: 'string' },
            },
          },
        },
      },
      properties: {
        poll_interval_seconds: {
          type: 'integer',
          title: 'Poll Interval Seconds',
          description: 'Seconds between discovery polls.',
          default: 900,
          minimum: 10,
          maximum: 86400,
        },
        gamma_limit: {
          type: 'integer',
          title: 'Gamma Limit',
          description: 'Number of events to request from Gamma per poll.',
          default: 100,
          minimum: 1,
          maximum: 500,
        },
        filter: { $ref: '#/$defs/MarketFilterConfig' },
      },
    },
  },
  {
    type: 'embedding',
    name: 'EmbeddingFilterV0',
    version: '0.1.0',
    module: 'openpoly.sections.embedding.minilm_v0',
    requires: ['market_data'],
    source: 'builtin',
    param_schema: {
      type: 'object',
      title: 'EmbeddingFilterConfig',
      properties: {
        embedding_model: {
          type: 'string',
          title: 'Embedding Model',
          description: 'Local sentence-transformer used to embed news + questions.',
          default: 'all-MiniLM-L6-v2',
        },
        top_k: {
          type: 'integer',
          title: 'Top K',
          description: 'Maximum candidate markets handed to the analyzer.',
          default: 10,
          minimum: 1,
          maximum: 100,
        },
        similarity_threshold: {
          type: 'number',
          title: 'Similarity Threshold',
          description: 'Minimum cosine similarity for a market to survive.',
          default: 0.35,
          minimum: 0,
          maximum: 1,
        },
        max_question_chars: {
          type: 'integer',
          title: 'Max Question Chars',
          description: 'Market question text is truncated to this before embedding.',
          default: 200,
          minimum: 20,
          maximum: 2000,
        },
        warm_interval_seconds: {
          type: 'integer',
          title: 'Warm Interval Seconds',
          description: 'Seconds between background catalog embedding refreshes.',
          default: 300,
          minimum: 30,
          maximum: 86400,
        },
      },
    },
  },
  {
    type: 'analyzer',
    name: 'LLMAnalyzerV0',
    version: '0.1.0',
    module: 'openpoly.sections.analyzer.llm_v0',
    requires: ['llm', 'market_data'],
    source: 'builtin',
    param_schema: {
      type: 'object',
      title: 'LLMAnalyzerConfig',
      properties: {
        llm_model: {
          type: 'string',
          title: 'Llm Model',
          default: 'claude-haiku-4-5',
          description:
            'Model id sent to the API. On the official Anthropic endpoint use a Claude id; on a third-party gateway use whatever id that gateway publishes.',
        },
        temperature: {
          type: 'number',
          title: 'Temperature',
          default: 0.2,
          minimum: 0,
          maximum: 1,
          description: 'Sampling temperature; ignored for claude-opus-4-7.',
        },
        api_key_ref: {
          type: 'string',
          title: 'Api Key Ref',
          default: 'env:ANTHROPIC_API_KEY',
          description: 'Reference to the LLM API key (env: / local: scheme).',
        },
        base_url: {
          type: 'string',
          title: 'Base Url',
          default: '',
          description:
            'Third-party API base URL; empty = official Anthropic endpoint.',
        },
        extra_guidance: {
          type: 'string',
          title: 'Extra Guidance',
          default: '',
          description:
            "Optional extra guidance appended to the analyzer's system prompt. Cannot alter the structured-output contract.",
        },
        min_confidence: {
          type: 'string',
          title: 'Min Confidence',
          default: 'medium',
          enum: ['low', 'medium', 'high'],
        },
      },
    },
  },
  {
    type: 'entry',
    name: 'EdgeThresholdEntryV0',
    version: '0.1.0',
    module: 'openpoly.sections.entry.edge_threshold_v0',
    requires: ['order_book', 'market_data'],
    source: 'builtin',
    param_schema: {
      type: 'object',
      title: 'EdgeThresholdConfig',
      properties: {
        min_edge: {
          type: 'number',
          title: 'Min Edge',
          default: 0.05,
          minimum: 0,
          maximum: 1,
        },
        order_size_usd: {
          type: 'number',
          title: 'Order Size Usd',
          default: 10,
          minimum: 1,
          maximum: 100,
        },
        max_spread: {
          type: 'number',
          title: 'Max Spread',
          default: 0.05,
          minimum: 0,
          maximum: 0.5,
        },
        slippage_tolerance: {
          type: 'number',
          title: 'Slippage Tolerance',
          default: 0.02,
          minimum: 0,
          maximum: 0.2,
        },
        side_lock: {
          type: 'boolean',
          title: 'Side Lock',
          description: 'Lock to YES only; never buy NO.',
          default: false,
        },
      },
    },
  },
  {
    type: 'exit',
    name: 'ThresholdExitV0',
    version: '0.1.0',
    module: 'openpoly.sections.exit.threshold_v0',
    requires: ['market_data', 'portfolio'],
    source: 'builtin',
    param_schema: {
      type: 'object',
      title: 'ThresholdExitConfig',
      properties: {
        take_profit_pct: {
          type: 'number',
          title: 'Take Profit Pct',
          description:
            'Close the position when its return reaches this fraction (0.20 = +20%).',
          default: 0.2,
          minimum: 0,
          maximum: 10,
        },
        stop_loss_pct: {
          type: 'number',
          title: 'Stop Loss Pct',
          description:
            'Close the position when its loss reaches this fraction (0.15 = -15%).',
          default: 0.15,
          minimum: 0,
          maximum: 1,
        },
      },
    },
  },
  {
    type: 'database',
    name: 'SqliteDatabase',
    version: '0.1.0',
    module: 'openpoly.sections.database.sqlite',
    requires: [],
    source: 'builtin',
    param_schema: { type: 'object', title: 'DatabaseConfig', properties: {} },
  },
]
