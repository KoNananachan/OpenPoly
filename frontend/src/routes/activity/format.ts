/** Shared P&L formatting for the Activity tabs. */

export function formatPnl(n: number): string {
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toFixed(2)}`
}

export function pnlClass(n: number | null): string {
  if (n === null || n === 0) return 'text-neutral-400'
  return n > 0 ? 'text-emerald-300' : 'text-red-300'
}
