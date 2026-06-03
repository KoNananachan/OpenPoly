import { Handle, Position, type NodeProps } from '@xyflow/react'
import { findEntry, TYPE_DISPLAY } from '../sections/catalog'
import { useCatalogStore } from '../sections/catalogStore'
import { AnalyzerStatusIndicator } from '../sections/analyzer/StatusIndicator'
import { EmbeddingStatusIndicator } from '../sections/embedding/StatusIndicator'
import { EntryStatusIndicator } from '../sections/entry/StatusIndicator'
import { ExitStatusIndicator } from '../sections/exit/StatusIndicator'
import { MarketSourceStatusIndicator } from '../sections/market_source/StatusIndicator'
import { NewsSourceStatusIndicator } from '../sections/news_source/StatusIndicator'
import type { SectionNodeType } from './store'

export function SectionNode({ data, selected }: NodeProps<SectionNodeType>) {
  const display = TYPE_DISPLAY[data.sectionType]
  const entries = useCatalogStore((s) => s.entries)
  const entry = findEntry(data.sectionType, entries)
  const hasInput =
    data.sectionType === 'entry' ||
    data.sectionType === 'embedding' ||
    data.sectionType === 'analyzer' ||
    data.sectionType === 'database'
  const hasOutput =
    data.sectionType === 'analyzer' ||
    data.sectionType === 'embedding' ||
    data.sectionType === 'news_source' ||
    data.sectionType === 'market_source'
  const isNewsSource = data.sectionType === 'news_source'
  const isMarketSource = data.sectionType === 'market_source'
  const isAnalyzer = data.sectionType === 'analyzer'
  const isEmbedding = data.sectionType === 'embedding'
  const isEntry = data.sectionType === 'entry'
  const isExit = data.sectionType === 'exit'

  return (
    <div
      className={[
        'relative rounded border bg-neutral-900 px-3 py-2 min-w-[200px] shadow-sm',
        selected ? 'border-indigo-400' : 'border-neutral-700',
      ].join(' ')}
    >
      {hasInput && (
        <Handle
          type="target"
          position={Position.Top}
          className="!w-2.5 !h-2.5 !bg-neutral-400 !border-neutral-900"
        />
      )}
      <div
        className={`flex items-baseline gap-2 ${
          isNewsSource ||
          isMarketSource ||
          isAnalyzer ||
          isEmbedding ||
          isEntry ||
          isExit
            ? 'pr-5'
            : ''
        }`}
      >
        <div className="text-sm font-medium text-neutral-100">{display.label}</div>
        <code className="text-[10px] text-neutral-500">{data.sectionType}</code>
      </div>
      {entry && (
        <div className="text-[10px] text-neutral-500 mt-0.5">
          {entry.name}
          <span className="text-neutral-600"> v{entry.version}</span>
        </div>
      )}
      {entry && entry.requires.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {entry.requires.map((c) => (
            <span
              key={c}
              className="px-1.5 py-0.5 text-[10px] rounded bg-neutral-800 text-neutral-400"
            >
              {c}
            </span>
          ))}
        </div>
      )}
      {isNewsSource && <NewsSourceStatusIndicator />}
      {isMarketSource && <MarketSourceStatusIndicator />}
      {isAnalyzer && <AnalyzerStatusIndicator />}
      {isEmbedding && <EmbeddingStatusIndicator />}
      {isEntry && <EntryStatusIndicator />}
      {isExit && <ExitStatusIndicator />}
      {hasOutput && (
        <Handle
          type="source"
          position={Position.Bottom}
          className="!w-2.5 !h-2.5 !bg-neutral-400 !border-neutral-900"
        />
      )}
    </div>
  )
}
