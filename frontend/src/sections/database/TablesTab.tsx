/**
 * Tables tab for the database section's inspector — DB status (table row counts
 * + write-behind writer stats) plus a raw row view of each persisted table.
 * Reads /api/inspect/{db-status,order-books,news}; polls every 5s while mounted.
 */
import { useEffect, useState, type ReactNode } from 'react'

import {
  useDatabaseInspectStore,
  type NewsRow,
  type OrderBookRow,
  type WriterStats,
} from './inspectStore'

const POLL_MS = 5000

type TableKey = 'order_books' | 'news'

function fmtAgo(epochSeconds: number): string {
  const d = Math.max(0, Date.now() / 1000 - epochSeconds)
  if (d < 60) return 'just now'
  if (d < 3600) return `${Math.floor(d / 60)}m ago`
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`
  return `${Math.floor(d / 86400)}d ago`
}

function fmtWriter(s: WriterStats | null): string {
  if (s === null) return 'idle'
  return `${s.written} written · ${s.dropped} dropped · ${s.pending} pending`
}

export function DatabaseTablesTab() {
  const status = useDatabaseInspectStore((s) => s.status)
  const orderBooks = useDatabaseInspectStore((s) => s.orderBooks)
  const news = useDatabaseInspectStore((s) => s.news)
  const fetchStatus = useDatabaseInspectStore((s) => s.fetchStatus)
  const refresh = useDatabaseInspectStore((s) => s.refresh)

  const [table, setTable] = useState<TableKey>('order_books')

  useEffect(() => {
    void refresh()
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [refresh])

  return (
    <div className="flex flex-col gap-3">
      {fetchStatus === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}

      <div className="rounded border border-neutral-800 px-3 py-2 flex flex-col gap-1.5 text-[11px]">
        <StatusRow
          name="order_book_snapshot"
          rows={status?.tables.order_book_snapshot ?? 0}
          writer={status?.writers.order_book ?? null}
        />
        <StatusRow
          name="news_item"
          rows={status?.tables.news_item ?? 0}
          writer={status?.writers.news ?? null}
        />
      </div>

      <div className="flex items-center gap-1">
        <Toggle active={table === 'order_books'} onClick={() => setTable('order_books')}>
          order_book_snapshot
        </Toggle>
        <Toggle active={table === 'news'} onClick={() => setTable('news')}>
          news_item
        </Toggle>
        <button
          type="button"
          onClick={() => void refresh()}
          className="ml-auto text-[10px] text-neutral-500 hover:text-neutral-300"
        >
          Refresh
        </button>
      </div>

      {table === 'order_books' ? (
        <OrderBookTable rows={orderBooks} />
      ) : (
        <NewsTable rows={news} />
      )}
    </div>
  )
}

function StatusRow({
  name,
  rows,
  writer,
}: {
  name: string
  rows: number
  writer: WriterStats | null
}) {
  return (
    <div>
      <div className="flex justify-between">
        <span className="font-mono text-neutral-400">{name}</span>
        <span className="text-neutral-200">{rows} rows</span>
      </div>
      <div className="font-mono text-[10px] text-neutral-600">{fmtWriter(writer)}</div>
    </div>
  )
}

function Toggle({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2 py-1 rounded text-[10px] font-mono transition-colors ${
        active
          ? 'bg-neutral-800 text-neutral-100'
          : 'text-neutral-500 hover:text-neutral-300'
      }`}
    >
      {children}
    </button>
  )
}

function EmptyHint({ text }: { text: string }) {
  return <div className="text-[11px] text-neutral-600">{text}</div>
}

function OrderBookTable({ rows }: { rows: OrderBookRow[] }) {
  if (rows.length === 0) {
    return <EmptyHint text="No order_book_snapshot rows yet." />
  }
  return (
    <ul className="flex flex-col">
      {rows.map((r) => (
        <li key={r.id} className="border-b border-neutral-800/60 py-1.5 last:border-b-0">
          <div className="flex justify-between font-mono text-[10px]">
            <span className="text-neutral-400 truncate" title={r.token_id}>
              {r.token_id.slice(0, 16)}…
            </span>
            <span className="text-neutral-600 shrink-0 pl-2">
              {fmtAgo(r.recorded_at)}
            </span>
          </div>
          <div className="mt-0.5 font-mono text-[10px] text-neutral-500">
            bid {r.bids.map(([p, s]) => `${p}×${s}`).join('  ')}
          </div>
          <div className="font-mono text-[10px] text-neutral-500">
            ask {r.asks.map(([p, s]) => `${p}×${s}`).join('  ')}
          </div>
        </li>
      ))}
    </ul>
  )
}

function NewsTable({ rows }: { rows: NewsRow[] }) {
  if (rows.length === 0) {
    return <EmptyHint text="No news_item rows yet." />
  }
  return (
    <ul className="flex flex-col">
      {rows.map((r) => (
        <li key={r.id} className="border-b border-neutral-800/60 py-1.5 last:border-b-0">
          <div className="text-xs text-neutral-200 leading-relaxed">{r.content}</div>
          <div className="mt-0.5 font-mono text-[10px] text-neutral-500">
            {fmtAgo(r.received_at)} · {r.urgency}
          </div>
        </li>
      ))}
    </ul>
  )
}
