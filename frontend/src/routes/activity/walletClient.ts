/** Client + types for GET /api/wallet/balance. */

export type WalletBalance = {
  configured: boolean
  usdc: number | null
  positions_value: number | null
  total: number | null
  ts: number | null
}

export async function fetchWalletBalance(): Promise<WalletBalance> {
  const r = await fetch('/api/wallet/balance')
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as WalletBalance
}
