/**
 * Activity › Overview — P&L stat cards + the equity curve. Phase 1 of the
 * Activity redesign. Wallet cards (cash + total value) read the on-chain
 * wallet via /api/wallet/balance — ledger P&L on the left, on-chain truth
 * on the right, so a divergence between the two is visible at a glance.
 */
import { EquityChart } from './EquityChart'
import { fetchEquity, type EquityResponse } from './equityClient'
import { formatPnl, pnlClass } from './format'
import { usePoll } from './usePoll'
import { fetchWalletBalance, type WalletBalance } from './walletClient'

function fmtUsd(n: number | null): string {
  return n === null ? '—' : `$${n.toFixed(2)}`
}

export function OverviewTab() {
  const { data, status, error } = usePoll<EquityResponse>(fetchEquity)
  // Backend caches for 30s — no point polling faster. A failed wallet fetch
  // must not take down the P&L cards, so it gets its own poll + null guards.
  const { data: wallet } = usePoll<WalletBalance>(fetchWalletBalance, 30000)

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
  const noWallet = wallet !== null && !wallet.configured
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
        <StatCard
          label="Wallet cash"
          value={noWallet ? 'No wallet' : fmtUsd(wallet?.usdc ?? null)}
          tone={noWallet ? 'text-neutral-500' : 'text-neutral-100'}
        />
        <StatCard
          label="Wallet value"
          value={noWallet ? 'No wallet' : fmtUsd(wallet?.total ?? null)}
          tone={noWallet ? 'text-neutral-500' : 'text-neutral-100'}
          sub={
            noWallet || wallet?.positions_value == null
              ? undefined
              : `cash ${fmtUsd(wallet?.usdc ?? null)} + positions ${fmtUsd(wallet.positions_value)}`
          }
        />
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
  sub,
}: {
  label: string
  value: string
  tone: string
  sub?: string
}) {
  return (
    <div className="rounded border border-neutral-800 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className={`mt-1 text-xl font-mono font-semibold ${tone}`}>
        {value}
      </div>
      {sub !== undefined && (
        <div className="mt-0.5 text-[10px] font-mono text-neutral-500">
          {sub}
        </div>
      )}
    </div>
  )
}
