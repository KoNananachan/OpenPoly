/**
 * Section-type → tab list map (N6). Per-section UX intentionally non-uniform:
 * sources get Live + raw-data tabs; embedding / analyzer / entry / exit each
 * get a runtime tab (Calls / Decisions / Closes) over their /api/{...}/log
 * feed; database gets a Tables tab. Only sections without a runtime surface
 * stay on a single Config tab.
 */
import type { ReactNode } from 'react'

import { AnalyzerCallsTab } from '../sections/analyzer/CallsTab'
import { DatabaseTablesTab } from '../sections/database/TablesTab'
import { EmbeddingCallsTab } from '../sections/embedding/CallsTab'
import { ExitClosesTab } from '../sections/exit/ClosesTab'
import { MarketSourceLiveTab } from '../sections/market_source/LiveTab'
import { MarketSourceMarketsTab } from '../sections/market_source/MarketsTab'
import { NewsSourceLiveTab } from '../sections/news_source/LiveTab'
import { NewsSourceNewsTab } from '../sections/news_source/NewsTab'
import { EntryDecisionsTab } from '../sections/entry/DecisionsTab'
import type { SectionType } from '../sections/types'
import { ConfigTab } from './ConfigTab'
import type { SectionNodeType } from './store'

export type TabSpec = {
  key: string
  label: string
  render: (node: SectionNodeType) => ReactNode
}

export const INSPECTOR_TABS: Record<SectionType, TabSpec[]> = {
  news_source: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'live', label: 'Live', render: (node) => <NewsSourceLiveTab node={node} /> },
    { key: 'news', label: 'News', render: () => <NewsSourceNewsTab /> },
  ],
  market_source: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'live', label: 'Live', render: (node) => <MarketSourceLiveTab node={node} /> },
    { key: 'markets', label: 'Markets', render: () => <MarketSourceMarketsTab /> },
  ],
  embedding: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'calls', label: 'Calls', render: () => <EmbeddingCallsTab /> },
  ],
  analyzer: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'calls', label: 'Calls', render: () => <AnalyzerCallsTab /> },
  ],
  entry: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'decisions', label: 'Decisions', render: () => <EntryDecisionsTab /> },
  ],
  exit: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'closes', label: 'Closes', render: () => <ExitClosesTab /> },
  ],
  database: [
    { key: 'config', label: 'Config', render: (node) => <ConfigTab node={node} /> },
    { key: 'tables', label: 'Tables', render: () => <DatabaseTablesTab /> },
  ],
}
