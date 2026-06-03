/**
 * Time formatters for the market_source Live tab. Mirrors news_source/time.ts.
 */

export function formatRelativeAgo(epochSeconds: number, nowMs = Date.now()): string {
  const delta = Math.max(0, nowMs / 1000 - epochSeconds)
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return `${Math.floor(delta / 86400)}d ago`
}

export function formatUTC(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString().replace('.000Z', 'Z')
}
