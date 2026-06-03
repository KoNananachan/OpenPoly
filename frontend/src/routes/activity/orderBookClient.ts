/** Client + types for GET /api/inspect/order-books/{token_id}. */

export type OrderBookSnapshot = {
  recorded_at: number
  bids: [number, number][]
  asks: [number, number][]
}

export type OrderBookHistory = {
  token_id: string
  count: number
  snapshots: OrderBookSnapshot[]
}

export async function fetchOrderBookHistory(
  tokenId: string,
  since: number,
  until: number | null,
): Promise<OrderBookHistory> {
  const params = new URLSearchParams({ since: String(since) })
  if (until !== null) params.set('until', String(until))
  const r = await fetch(
    `/api/inspect/order-books/${encodeURIComponent(tokenId)}?${params}`,
  )
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as OrderBookHistory
}
