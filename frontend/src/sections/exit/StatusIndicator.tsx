/**
 * Status dot + tick-heartbeat text rendered on the exit canvas card.
 *
 * Reuses useExitLogStore (polls /api/exit/log every 3s) — no extra fetch. The
 * exit monitor is position-driven, not queue-driven, so there is no "busy" /
 * queue_depth state. Liveness is the tick heartbeat (`last_tick_at` +
 * `open_positions`); the red dot comes from the *latest* entry's verdict
 * (errors are logged as entries — exit_monitor.state itself only toggles
 * stopped/running). Mirrors AnalyzerStatusIndicator.
 */
import { formatRelativeAgo, formatUTC } from '../news_source/time'
import { useExitLogStore } from './logStore'

type DotState = 'running' | 'error' | 'stopped'

const DOT_BY_STATE: Record<DotState, string> = {
  running: 'bg-emerald-500',
  error: 'bg-red-500',
  stopped: 'bg-neutral-500',
}

export function ExitStatusIndicator() {
  const data = useExitLogStore((s) => s.data)
  const fetchStatus = useExitLogStore((s) => s.status)

  // Priority: unreachable / stopped → latest-entry error → running. A historical
  // error counter does NOT keep the dot red — only the *latest* entry's verdict.
  let dotState: DotState = 'stopped'
  let text: string
  let textTitle: string | undefined

  if (fetchStatus === 'error') {
    text = 'Backend unreachable'
  } else if (data === null) {
    text = 'Loading…'
  } else if (data.state === 'stopped') {
    text = 'Monitor stopped'
  } else {
    const latest =
      data.entries.length > 0 ? data.entries[data.entries.length - 1] : null
    const open = data.open_positions ?? 0
    const blocked = data.blocked ?? 0
    if (latest?.verdict === 'error') {
      dotState = 'error'
      text = latest.error ? `Error: ${latest.error}` : 'Last close errored'
    } else if (open > 0) {
      dotState = 'running'
      const tick =
        data.last_tick_at !== null
          ? ` · tick ${formatRelativeAgo(data.last_tick_at)}`
          : ''
      const blockedSuffix = blocked > 0 ? ` · ${blocked} blocked` : ''
      text = `Monitoring ${open}${tick}${blockedSuffix}`
      textTitle =
        data.last_tick_at !== null ? formatUTC(data.last_tick_at) : undefined
    } else {
      dotState = 'running'
      text = 'Idle — no open positions'
    }
  }

  return (
    <>
      <span
        className={`absolute top-2 right-2 w-2 h-2 rounded-full ${DOT_BY_STATE[dotState]}`}
        title={dotState}
        aria-label={`exit ${dotState}`}
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
