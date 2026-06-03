import type { Connection, Edge } from '@xyflow/react'
import type { SectionType } from '../sections/types'

type SectionNodeLite = {
  id: string
  data: { sectionType: SectionType }
}

const VALID_PAIRS: ReadonlyArray<readonly [SectionType, SectionType]> = [
  // Pipeline flow: news → embedding → analyzer → entry.
  ['news_source', 'embedding'],
  ['embedding', 'analyzer'],
  ['analyzer', 'entry'],
  // Write-to-DB edges: the two sources + the embedding section each persist
  // into the database section (embedding writes the market_embedding cache).
  ['market_source', 'database'],
  ['news_source', 'database'],
  ['embedding', 'database'],
]

export function isValidConnection(
  conn: Connection | Edge,
  nodes: SectionNodeLite[],
  edges: Edge[],
): boolean {
  if (!conn.source || !conn.target) return false
  if (conn.source === conn.target) return false

  const src = nodes.find((n) => n.id === conn.source)
  const tgt = nodes.find((n) => n.id === conn.target)
  if (!src || !tgt) return false

  const srcType = src.data.sectionType
  const tgtType = tgt.data.sectionType
  if (!VALID_PAIRS.some(([s, t]) => s === srcType && t === tgtType)) return false

  if (edges.some((e) => e.source === conn.source && e.target === conn.target)) {
    return false
  }
  return true
}
