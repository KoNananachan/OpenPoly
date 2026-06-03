/**
 * Floating right-side inspector. After N6, the inner panel is tab-driven:
 *   header (always)
 *   tab bar (only when sectionType has more than one tab — currently only news_source)
 *   active tab body
 */
import { useEffect, useState } from 'react'

import { findEntry, TYPE_DISPLAY } from '../sections/catalog'
import { useCatalogStore } from '../sections/catalogStore'
import type { SectionType } from '../sections/types'
import { INSPECTOR_TABS } from './inspectorSpecs'
import { useCanvasStore, type SectionNodeType } from './store'

const TYPE_ACCENT: Record<SectionType, { bg: string; text: string }> = {
  news_source: { bg: 'bg-sky-500/15', text: 'text-sky-300' },
  market_source: { bg: 'bg-cyan-500/15', text: 'text-cyan-300' },
  embedding: { bg: 'bg-violet-500/15', text: 'text-violet-300' },
  analyzer: { bg: 'bg-indigo-500/15', text: 'text-indigo-300' },
  entry: { bg: 'bg-emerald-500/15', text: 'text-emerald-300' },
  exit: { bg: 'bg-rose-500/15', text: 'text-rose-300' },
  database: { bg: 'bg-amber-500/15', text: 'text-amber-300' },
}

export function SectionInspector() {
  const node = useCanvasStore((s) =>
    s.selectedNodeId ? s.nodes.find((n) => n.id === s.selectedNodeId) : undefined,
  )
  const setSelectedNodeId = useCanvasStore((s) => s.setSelectedNodeId)
  const open = !!node

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedNodeId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setSelectedNodeId])

  return (
    <aside
      aria-hidden={!open}
      className={`absolute right-3 top-3 bottom-3 z-10 w-[480px] max-w-[calc(100%-1.5rem)] flex flex-col rounded-lg border border-neutral-800 bg-neutral-950/95 shadow-2xl backdrop-blur-sm transform transition-transform duration-200 ease-out ${
        open
          ? 'translate-x-0'
          : 'translate-x-[calc(100%+1rem)] pointer-events-none'
      }`}
    >
      {node && (
        <InspectorPanel
          key={node.id}
          node={node}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
    </aside>
  )
}

function InspectorPanel({
  node,
  onClose,
}: {
  node: SectionNodeType
  onClose: () => void
}) {
  const entries = useCatalogStore((s) => s.entries)
  const source = useCatalogStore((s) => s.source)

  const sectionType = node.data.sectionType
  const display = TYPE_DISPLAY[sectionType]
  const accent = TYPE_ACCENT[sectionType]
  const entry = findEntry(sectionType, entries)
  const tabs = INSPECTOR_TABS[sectionType]

  const [activeKey, setActiveKey] = useState<string>(tabs[0]?.key ?? 'config')
  const activeTab = tabs.find((t) => t.key === activeKey) ?? tabs[0]

  return (
    <div className="flex flex-col min-h-0 flex-1">
      <header className="relative px-6 pt-5 pb-4 border-b border-neutral-800/80">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          className="absolute right-3 top-3 grid place-items-center w-7 h-7 rounded-md text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200 transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path
              d="M2 2l10 10M12 2L2 12"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>

        <div className="flex items-center gap-3 pr-8">
          <div
            className={`grid place-items-center w-9 h-9 rounded-md text-sm font-semibold ${accent.bg} ${accent.text}`}
          >
            {display.label[0]}
          </div>
          <div className="flex flex-col">
            <h2 className="text-lg font-medium text-neutral-100 leading-tight">
              {display.label}
            </h2>
            <code className="text-[11px] text-neutral-500">{sectionType}</code>
          </div>
        </div>

        <p className="mt-3 text-xs text-neutral-400 leading-relaxed">
          {display.description}
        </p>

        {entry && (
          <div className="mt-2 flex items-center gap-2 text-[11px]">
            <span className="text-neutral-500">
              <span className="text-neutral-300">{entry.name}</span>
              <span className="text-neutral-600"> v{entry.version}</span>
            </span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] ${
                source === 'runtime'
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : 'bg-amber-500/15 text-amber-300'
              }`}
            >
              schema: {source}
            </span>
          </div>
        )}

        {entry && entry.requires.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {entry.requires.map((c) => (
              <span
                key={c}
                className="px-1.5 py-0.5 text-[10px] rounded bg-neutral-800/80 text-neutral-400"
              >
                {c}
              </span>
            ))}
          </div>
        )}
      </header>

      {tabs.length > 1 && (
        <div className="flex gap-1 px-6 pt-3 border-b border-neutral-800/80">
          {tabs.map((t) => {
            const active = t.key === activeKey
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setActiveKey(t.key)}
                className={`px-3 py-1.5 text-xs rounded-t-md transition-colors ${
                  active
                    ? 'bg-neutral-900 text-neutral-100'
                    : 'text-neutral-500 hover:text-neutral-300'
                }`}
              >
                {t.label}
              </button>
            )
          })}
        </div>
      )}

      <div className="overflow-auto px-6 py-5">{activeTab?.render(node)}</div>
    </div>
  )
}
