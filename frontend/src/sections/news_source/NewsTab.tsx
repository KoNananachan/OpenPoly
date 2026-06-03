/**
 * News tab for the news_source inspector — the persisted news_item rows
 * (newest first). Reads GET /api/inspect/news; polls every 5s while mounted.
 */
import { useEffect } from 'react'

import { useNewsInspectStore, type InspectNewsItem } from './inspectStore'
import { formatRelativeAgo, formatUTC } from './time'

const POLL_MS = 5000

function fmtSentiment(v: number | null): string {
  return v === null ? '—' : v.toFixed(2)
}

export function NewsSourceNewsTab() {
  const data = useNewsInspectStore((s) => s.data)
  const status = useNewsInspectStore((s) => s.status)
  const refresh = useNewsInspectStore((s) => s.refresh)

  useEffect(() => {
    void refresh()
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [refresh])

  const news = data?.news ?? []

  return (
    <div className="flex flex-col gap-3">
      {status === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="text-[11px] text-neutral-500">
          {data ? (
            <>
              <span className="text-neutral-300">{data.count}</span> persisted news
              items
            </>
          ) : (
            'Loading…'
          )}
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="text-[10px] text-neutral-500 hover:text-neutral-300"
        >
          Refresh
        </button>
      </div>

      {data && news.length === 0 ? (
        <div className="text-[11px] text-neutral-600">
          No persisted news yet. News is stored as it arrives once the news
          source is running.
        </div>
      ) : (
        <ul className="flex flex-col">
          {news.map((n) => (
            <NewsRow key={n.id} item={n} />
          ))}
        </ul>
      )}
    </div>
  )
}

function NewsRow({ item: n }: { item: InspectNewsItem }) {
  return (
    <li className="border-b border-neutral-800/60 py-1.5 last:border-b-0">
      <div className="text-xs text-neutral-200 leading-relaxed">{n.content}</div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[10px] text-neutral-500">
        <span className="text-neutral-400" title={formatUTC(n.received_at)}>
          {formatRelativeAgo(n.received_at)}
        </span>
        <span>urgency {n.urgency}</span>
        <span>sentiment {fmtSentiment(n.sentiment)}</span>
      </div>
    </li>
  )
}
