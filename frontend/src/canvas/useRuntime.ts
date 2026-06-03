/**
 * Strategy runtime aggregation: combines the news + market source live status
 * into one overall state, computes a readiness/blocker list from the canvas
 * config + stored keys, and exposes start/stop that drive both sources together.
 *
 * "Run" = start both sources (the orchestrator consumes them automatically).
 * "Pause" = stop both. Whether real orders fire is gated separately by the
 * paper/live exec mode (see ModePill).
 *
 * Pure derivation over existing stores — no new backend endpoints.
 */
import { useNewsSourceStatusStore } from '../sections/news_source/statusStore'
import { useMarketSourceStatusStore } from '../sections/market_source/statusStore'
import { useSecretsStore } from '../setting/secretsStore'
import { useCanvasStore, type SectionNodeType } from './store'
import type { SectionType } from '../sections/types'

export type Overall = 'running' | 'paused' | 'partial'

export type BlockerAction = 'keys' | 'config' | 'add'

export type Blocker = {
  sectionType: SectionType | null
  label: string
  msg: string
  action: BlockerAction
}

const LOCAL = 'local:'
// Sources "Run" literally starts; without them there's nothing to run.
const REQUIRED_SECTIONS: SectionType[] = ['news_source', 'market_source']

function nodeConfig(
  nodes: SectionNodeType[],
  type: SectionType,
): Record<string, unknown> | null {
  return (nodes.find((n) => n.data.sectionType === type)?.data.config as
    | Record<string, unknown>
    | undefined) ?? null
}

function computeBlockers(
  nodes: SectionNodeType[],
  keyNames: Set<string>,
): Blocker[] {
  const present = new Set(nodes.map((n) => n.data.sectionType))
  const blockers: Blocker[] = []

  for (const t of REQUIRED_SECTIONS) {
    if (!present.has(t)) {
      blockers.push({
        sectionType: t,
        label: t,
        msg: 'section not on canvas',
        action: 'add',
      })
    }
  }

  for (const n of nodes) {
    const type = n.data.sectionType
    for (const [k, v] of Object.entries(n.data.config)) {
      if (!k.endsWith('_ref')) continue
      const sv = String(v ?? '')
      if (sv === '') {
        // Only the news source's key is strictly required to start; other
        // empty refs aren't flagged to avoid false positives.
        if (type === 'news_source') {
          blockers.push({ sectionType: type, label: type, msg: `${k} not set`, action: 'keys' })
        }
        continue
      }
      if (sv.startsWith(LOCAL)) {
        const name = sv.slice(LOCAL.length)
        if (!keyNames.has(name)) {
          blockers.push({
            sectionType: type,
            label: type,
            msg: `missing key "${name}"`,
            action: 'keys',
          })
        }
      }
    }
  }
  return blockers
}

export type Runtime = {
  overall: Overall
  newsState: string | null
  marketState: string | null
  newsLastMsgAt: number | null
  marketCatalogSize: number | null
  marketLastPollAt: number | null
  ready: boolean
  blockers: Blocker[]
  start: () => Promise<void>
  stop: () => Promise<void>
}

export function useRuntime(): Runtime {
  const news = useNewsSourceStatusStore((s) => s.snapshot)
  const market = useMarketSourceStatusStore((s) => s.snapshot)
  const newsStart = useNewsSourceStatusStore((s) => s.start)
  const newsStop = useNewsSourceStatusStore((s) => s.stop)
  const marketStart = useMarketSourceStatusStore((s) => s.start)
  const marketStop = useMarketSourceStatusStore((s) => s.stop)
  const nodes = useCanvasStore((s) => s.nodes)
  const keys = useSecretsStore((s) => s.keys)

  const newsActive = news?.state === 'connected' || news?.state === 'connecting'
  const marketActive = market?.state === 'running'
  const overall: Overall =
    newsActive && marketActive ? 'running' : !newsActive && !marketActive ? 'paused' : 'partial'

  const keyNames = new Set(keys.map((k) => k.name))
  const blockers = computeBlockers(nodes, keyNames)

  const start = async () => {
    const newsCfg = nodeConfig(nodes, 'news_source')
    const marketCfg = nodeConfig(nodes, 'market_source')
    await Promise.all([
      newsCfg
        ? newsStart(newsCfg as Parameters<typeof newsStart>[0])
        : Promise.resolve(),
      marketStart((marketCfg ?? {}) as Parameters<typeof marketStart>[0]),
    ])
  }

  const stop = async () => {
    await Promise.all([newsStop(), marketStop()])
  }

  return {
    overall,
    newsState: news?.state ?? null,
    marketState: market?.state ?? null,
    newsLastMsgAt: news?.last_msg_at ?? null,
    marketCatalogSize: market?.catalog_size ?? null,
    marketLastPollAt: market?.last_poll_at ?? null,
    ready: blockers.length === 0,
    blockers,
    start,
    stop,
  }
}
