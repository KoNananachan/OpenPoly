/**
 * Status dot + last-news text rendered on the news_source canvas card.
 * Polls via useNewsSourceStatusStore — refreshes on each 3s tick.
 */
import { useNewsSourceStatusStore, type SourceState } from './statusStore'
import { formatRelativeAgo, formatUTC } from './time'

const DOT_BY_STATE: Record<SourceState, string> = {
  connected: 'bg-emerald-500',
  connecting: 'bg-amber-400 animate-pulse',
  error: 'bg-red-500',
  stopped: 'bg-neutral-500',
}

export function NewsSourceStatusIndicator() {
  const snapshot = useNewsSourceStatusStore((s) => s.snapshot)
  const fetchStatus = useNewsSourceStatusStore((s) => s.status)

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
    text = 'Disconnected'
  } else if (snapshot.state === 'error') {
    text = snapshot.last_error ? `Error: ${snapshot.last_error}` : 'Error'
  } else if (snapshot.state === 'connecting') {
    text = 'Connecting…'
  } else if (snapshot.last_msg_at === null) {
    text = 'No news yet'
  } else {
    text = `Last news ${formatRelativeAgo(snapshot.last_msg_at)}`
    textTitle = formatUTC(snapshot.last_msg_at)
  }

  return (
    <>
      <span
        className={`absolute top-2 right-2 w-2 h-2 rounded-full ${DOT_BY_STATE[effectiveState]}`}
        title={effectiveState}
        aria-label={`news source ${effectiveState}`}
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
