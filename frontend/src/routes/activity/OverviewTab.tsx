/**
 * Activity › Overview — P&L stat cards + the equity curve. Phase 1 of the
 * Activity redesign.
 */
import { EquityChart } from './EquityChart'
import { fetchEquity, type EquityResponse } from './equityClient'
import { formatPnl, pnlClass } from './format'
import { usePoll } from './usePoll'

export function OverviewTab() {
  const { data, status, error } = usePoll<EquityResponse>(fetchEquity)

  if (data === null) {
    return (
      <div className="grid place-items-center p-10">
        <p className="text-sm text-neutral-400">
          {status === 'error' ? `Backend unreachable: ${error}` : 'Loading…'}
        </p>
      </div>
    )
  }

  const s = data.summary
  return (
    <div className="px-6 pb-6 flex flex-col gap-4">
      {status === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Realized P&L" value={formatPnl(s.realized)} tone={pnlClass(s.realized)} />
        <StatCard label="Unrealized P&L" value={formatPnl(s.unrealized)} tone={pnlClass(s.unrealized)} />
        <StatCard label="Total P&L" value={formatPnl(s.total)} tone={pnlClass(s.total)} />
        <StatCard label="Open positions" value={String(s.open_positions)} tone="text-neutral-100" />
      </div>
      <div className="rounded border border-neutral-800 p-3">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
          Equity curve · realized + unrealized (mark @ bid)
        </div>
        {data.points.length === 0 ? (
          <div className="h-64 grid place-items-center text-[11px] text-neutral-500">
            No trades yet.
          </div>
        ) : (
          <EquityChart points={data.points} />
        )}
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: string
}) {
  return (
    <div className="rounded border border-neutral-800 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className={`mt-1 text-xl font-mono font-semibold ${tone}`}>
        {value}
      </div>
    </div>
  )
}
