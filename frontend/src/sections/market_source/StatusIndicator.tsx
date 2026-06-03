/**
 * Status dot + catalog summary rendered on the market_source canvas card.
 * Polls via useMarketSourceStatusStore — refreshes on each 3s tick.
 */
import { useMarketSourceStatusStore, type SourceState } from './statusStore'
import { formatRelativeAgo, formatUTC } from './time'

const DOT_BY_STATE: Record<SourceState, string> = {
  running: 'bg-emerald-500',
  error: 'bg-red-500',
  stopped: 'bg-neutral-500',
}

export function MarketSourceStatusIndicator() {
  const snapshot = useMarketSourceStatusStore((s) => s.snapshot)
  const fetchStatus = useMarketSourceStatusStore((s) => s.status)

  // Backend unreachable / pre-first-fetch both render as a neutral dot.
  const effectiveState: SourceState =
    fetchStatus === 'error' || snapshot === null ? 'stopped' : snapshot.state

  let text: string
  let textTitle: string | undefined

  if (fetchStatus === 'error') {
    text = 'Backend unreachable'
  } else if (snapshot === null) {
    text = 'Loading…'
  } else if (snapshot.state === 'stopped') {
    text = 'Stopped'
  } else if (snapshot.state === 'error') {
    text = snapshot.last_error ? `Error: ${snapshot.last_error}` : 'Error'
  } else if (snapshot.last_poll_at === null) {
    text = 'Polling…'
  } else {
    text = `${snapshot.catalog_size} markets · ${formatRelativeAgo(snapshot.last_poll_at)}`
    textTitle = formatUTC(snapshot.last_poll_at)
  }

  return (
    <>
      <span
        className={`absolute top-2 right-2 w-2 h-2 rounded-full ${DOT_BY_STATE[effectiveState]}`}
        title={effectiveState}
        aria-label={`market source ${effectiveState}`}
      />
      <div
        className="text-[10px] text-neutral-500 mt-1.5 truncate"
        title={textTitle}
      >
        {text}
      </div>
    </>
  )
}
