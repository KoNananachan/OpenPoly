/**
 * Activity › Positions — single stream of PositionCards (v15).
 *
 * Filter chips (Open / Closed / All) at the top, default Open. Sort: when
 * "All", open positions come first (opened_at desc) followed by closed
 * (closed_at desc); single-status filters use the obvious key. Fills are
 * still rendered, but tucked behind a collapsible <details> so the operator
 * can pull up fee / order_id / tx_hash when auditing without cluttering the
 * day-to-day view.
 */
import { useMemo, useState } from 'react'
import { formatRelativeAgo, formatUTC } from '../../sections/news_source/time'
import { PositionCard } from './PositionCard'
import type { Fill, PositionRecord } from './portfolioTypes'
import { usePoll } from './usePoll'

type PositionsData = { positions: PositionRecord[]; fills: Fill[] }
type Filter = 'open' | 'closed' | 'all'

async function fetchJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as T
}

async function fetchPositionsData(): Promise<PositionsData> {
  const [p, f] = await Promise.all([
    fetchJSON<{ positions: PositionRecord[] }>('/api/positions'),
    fetchJSON<{ fills: Fill[] }>('/api/fills'),
  ])
  return { positions: p.positions, fills: f.fills }
}

function sortPositions(rows: PositionRecord[], filter: Filter): PositionRecord[] {
  // Returns a new array; never mutates input. Sort keys per D1:
  //   open → opened_at desc
  //   closed → closed_at desc (closed_at is non-null for closed rows)
  //   all → open first (opened_at desc), then closed (closed_at desc)
  const openRows = rows
    .filter((p) => p.status === 'open')
    .slice()
    .sort((a, b) => b.opened_at - a.opened_at)
  const closedRows = rows
    .filter((p) => p.status === 'closed')
    .slice()
    .sort((a, b) => (b.closed_at ?? 0) - (a.closed_at ?? 0))
  if (filter === 'open') return openRows
  if (filter === 'closed') return closedRows
  return [...openRows, ...closedRows]
}

function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1 rounded text-[11px] font-medium border transition-colors ${
        active
          ? 'bg-neutral-700 border-neutral-600 text-neutral-100'
          : 'bg-transparent border-neutral-800 text-neutral-400 hover:bg-neutral-900'
      }`}
    >
      {label} <span className="text-neutral-500">({count})</span>
    </button>
  )
}

export function PositionsTab() {
  const { data, status, error } = usePoll<PositionsData>(fetchPositionsData)
  const [filter, setFilter] = useState<Filter>('open')

  const counts = useMemo(() => {
    if (data === null) return { open: 0, closed: 0, all: 0 }
    const open = data.positions.filter((p) => p.status === 'open').length
    return { open, closed: data.positions.length - open, all: data.positions.length }
  }, [data])

  const visible = useMemo(
    () => (data === null ? [] : sortPositions(data.positions, filter)),
    [data, filter],
  )

  if (data === null) {
    return (
      <div className="grid place-items-center p-10">
        <p className="text-sm text-neutral-400">
          {status === 'error' ? `Backend unreachable: ${error}` : 'Loading…'}
        </p>
      </div>
    )
  }

  return (
    <div className="px-6 pb-6 flex flex-col gap-4">
      {status === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}

      <div className="flex items-center gap-2">
        <FilterChip
          label="Open"
          count={counts.open}
          active={filter === 'open'}
          onClick={() => setFilter('open')}
        />
        <FilterChip
          label="Closed"
          count={counts.closed}
          active={filter === 'closed'}
          onClick={() => setFilter('closed')}
        />
        <FilterChip
          label="All"
          count={counts.all}
          active={filter === 'all'}
          onClick={() => setFilter('all')}
        />
      </div>

      {visible.length === 0 ? (
        <div className="rounded border border-neutral-800 px-3 py-6 text-center text-[11px] text-neutral-500">
          {filter === 'open'
            ? 'No open positions.'
            : filter === 'closed'
              ? 'No closed positions yet.'
              : 'No positions yet — entry has not filled anything.'}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {visible.map((p) => (
            <PositionCard key={p.id} p={p} />
          ))}
        </div>
      )}

      {/* Fills ledger — hidden by default, expanded when auditing.
          Keeps fee / order_id / tx_hash reachable now that the per-fill
          row is no longer the primary surface. */}
      <details className="rounded border border-neutral-800 mt-2">
        <summary className="px-3 py-2 text-[11px] text-neutral-400 cursor-pointer hover:bg-neutral-900 select-none">
          Raw fills ({data.fills.length}) — debug / audit
        </summary>
        <FillsTable fills={data.fills} />
      </details>
    </div>
  )
}

function FillsTable({ fills }: { fills: Fill[] }) {
  if (fills.length === 0) {
    return (
      <div className="px-3 py-3 text-[11px] text-neutral-500 border-t border-neutral-800">
        No fills yet — the ledger is empty.
      </div>
    )
  }
  return (
    <div className="border-t border-neutral-800 overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-neutral-500 border-b border-neutral-800">
            <Th>#</Th>
            <Th>Time</Th>
            <Th>Market</Th>
            <Th>Action</Th>
            <Th>Side</Th>
            <Th right>Price</Th>
            <Th right>Qty</Th>
            <Th right>Fee</Th>
            <Th right>Pos</Th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {fills.map((f) => (
            <tr key={f.id} className="border-b border-neutral-900 last:border-0">
              <Td tone="text-neutral-500">{f.id}</Td>
              <Td tone="text-neutral-500" title={formatUTC(f.ts)}>
                {formatRelativeAgo(f.ts)}
              </Td>
              <Td title={f.market_id}>{f.market_id.slice(0, 16)}</Td>
              <Td tone={f.action === 'buy' ? 'text-emerald-300' : 'text-amber-300'}>
                {f.action}
              </Td>
              <Td tone={f.side === 'yes' ? 'text-emerald-300' : 'text-sky-300'}>
                {f.side.toUpperCase()}
              </Td>
              <Td right>{f.price.toFixed(3)}</Td>
              <Td right>{f.qty.toFixed(2)}</Td>
              <Td right tone="text-neutral-500">
                {f.fee.toFixed(3)}
              </Td>
              <Td right tone="text-neutral-500">
                {f.position_id}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`px-3 py-1.5 font-medium ${right ? 'text-right' : ''}`}>
      {children}
    </th>
  )
}

function Td({
  children,
  right,
  tone,
  title,
}: {
  children: React.ReactNode
  right?: boolean
  tone?: string
  title?: string
}) {
  return (
    <td
      className={`px-3 py-1.5 ${right ? 'text-right' : ''} ${tone ?? 'text-neutral-300'}`}
      title={title}
    >
      {children}
    </td>
  )
}

