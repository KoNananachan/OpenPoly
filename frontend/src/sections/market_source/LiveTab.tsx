/**
 * Live status tab for market_source nodes:
 *   - Summary card: state pill + poll counters + last error
 *   - Start / Stop buttons (immediately disabled on click)
 *   - Event timeline (newest first; "show all" up to 200 ring entries)
 *   - Last-poll reject histogram (N fetched -> M kept, by reason)
 */
import { useState } from 'react'

import type { SectionNodeType } from '../../canvas/store'
import {
  useMarketSourceStatusStore,
  type LastPoll,
  type LogEvent,
  type Snapshot,
  type SourceState,
  type StartConfig,
} from './statusStore'
import { formatRelativeAgo, formatUTC } from './time'

const STATE_PILL: Record<SourceState, { label: string; cls: string }> = {
  stopped: { label: 'Stopped', cls: 'bg-neutral-800 text-neutral-300' },
  running: { label: 'Running', cls: 'bg-emerald-500/15 text-emerald-300' },
  error: { label: 'Error', cls: 'bg-red-500/15 text-red-300' },
}

const KIND_COLOR: Record<string, string> = {
  started: 'text-emerald-300',
  stopped: 'text-neutral-500',
  poll_ok: 'text-sky-300',
  poll_error: 'text-red-300',
}

export function MarketSourceLiveTab({ node }: { node: SectionNodeType }) {
  const snapshot = useMarketSourceStatusStore((s) => s.snapshot)
  const fetchStatus = useMarketSourceStatusStore((s) => s.status)
  const start = useMarketSourceStatusStore((s) => s.start)
  const stop = useMarketSourceStatusStore((s) => s.stop)

  const [busy, setBusy] = useState<'starting' | 'stopping' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [showAllEvents, setShowAllEvents] = useState(false)

  const state: SourceState = snapshot?.state ?? 'stopped'
  // 'error' still means the polling loop is alive — Stop, not Start.
  const isRunning = state === 'running' || state === 'error'

  async function handleStart() {
    setBusy('starting')
    setActionError(null)
    try {
      const resp = await start(node.data.config as unknown as StartConfig)
      if (!resp.ok) setActionError(resp.error ?? 'Unknown error')
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function handleStop() {
    setBusy('stopping')
    setActionError(null)
    try {
      await stop()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {fetchStatus === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; status may be stale.
        </div>
      )}

      <SummaryCard snapshot={snapshot} />

      <div className="flex gap-2">
        <button
          type="button"
          onClick={handleStart}
          disabled={busy !== null || isRunning}
          className="flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors bg-indigo-500/90 hover:bg-indigo-500 disabled:bg-neutral-800 disabled:text-neutral-500 disabled:cursor-not-allowed text-white"
        >
          {busy === 'starting' ? 'Starting…' : 'Start'}
        </button>
        <button
          type="button"
          onClick={handleStop}
          disabled={busy !== null || !isRunning}
          className="flex-1 px-3 py-1.5 rounded text-xs font-medium transition-colors bg-neutral-800 hover:bg-neutral-700 disabled:bg-neutral-900 disabled:text-neutral-600 disabled:cursor-not-allowed text-neutral-100"
        >
          {busy === 'stopping' ? 'Stopping…' : 'Stop'}
        </button>
      </div>

      {actionError && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-xs text-red-200 break-words">
          {actionError}
        </div>
      )}

      <EventTimeline
        events={snapshot?.events ?? []}
        showAll={showAllEvents}
        onToggleAll={() => setShowAllEvents((v) => !v)}
      />

      <PollHistogram lastPoll={snapshot?.last_poll ?? null} />
    </div>
  )
}

function SummaryCard({ snapshot }: { snapshot: Snapshot | null }) {
  if (!snapshot) {
    return (
      <div className="rounded border border-neutral-800 px-3 py-2 text-xs text-neutral-500">
        Loading…
      </div>
    )
  }
  const pill = STATE_PILL[snapshot.state]
  return (
    <div className="rounded border border-neutral-800 px-3 py-2 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className={`px-2 py-0.5 text-[10px] rounded font-medium ${pill.cls}`}>
          {pill.label}
        </span>
        <span className="text-[10px] text-neutral-500">
          {snapshot.started_at
            ? `Started ${formatRelativeAgo(snapshot.started_at)}`
            : '—'}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <dt className="text-neutral-500">Last poll</dt>
        <dd
          className="text-neutral-200 truncate"
          title={snapshot.last_poll_at ? formatUTC(snapshot.last_poll_at) : undefined}
        >
          {snapshot.last_poll_at ? formatRelativeAgo(snapshot.last_poll_at) : '—'}
        </dd>
        <dt className="text-neutral-500">Poll count</dt>
        <dd className="text-neutral-200">{snapshot.poll_count}</dd>
        <dt className="text-neutral-500">Catalog size</dt>
        <dd className="text-neutral-200">{snapshot.catalog_size}</dd>
      </dl>
      {snapshot.last_error && (
        <div className="text-[11px] text-red-300 break-words">
          Last error: {snapshot.last_error}
        </div>
      )}
    </div>
  )
}

function EventTimeline({
  events,
  showAll,
  onToggleAll,
}: {
  events: LogEvent[]
  showAll: boolean
  onToggleAll: () => void
}) {
  const sliced = showAll ? events : events.slice(-20)
  const ordered = [...sliced].reverse() // newest first
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
          Events
        </h3>
        {events.length > 20 && (
          <button
            type="button"
            onClick={onToggleAll}
            className="text-[10px] text-neutral-500 hover:text-neutral-300"
          >
            {showAll ? 'Show last 20' : `Show all ${events.length}`}
          </button>
        )}
      </div>
      {ordered.length === 0 ? (
        <div className="text-[11px] text-neutral-600">No events yet.</div>
      ) : (
        <ol className="flex flex-col gap-0.5 font-mono text-[10px]">
          {ordered.map((e, i) => (
            <li key={`${e.ts}-${i}`} className="flex gap-2">
              <span
                className="text-neutral-600 shrink-0 w-[60px]"
                title={formatUTC(e.ts)}
              >
                {formatRelativeAgo(e.ts)}
              </span>
              <span
                className={`shrink-0 w-[90px] ${
                  KIND_COLOR[e.kind] ?? 'text-neutral-300'
                }`}
              >
                {e.kind}
              </span>
              <span className="text-neutral-400 truncate">{e.detail ?? ''}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function PollHistogram({ lastPoll }: { lastPoll: LastPoll | null }) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500 mb-1">
        Last poll
      </h3>
      {!lastPoll ? (
        <div className="text-[11px] text-neutral-600">No poll yet.</div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div className="text-[11px] text-neutral-300">
            <span className="font-medium text-neutral-100">{lastPoll.fetched}</span>
            {' fetched → '}
            <span className="font-medium text-emerald-300">{lastPoll.kept}</span>
            {' kept'}
          </div>
          {Object.keys(lastPoll.reason_counts).length > 0 && (
            <ol className="flex flex-col gap-0.5 font-mono text-[10px]">
              {Object.entries(lastPoll.reason_counts)
                .sort((a, b) => b[1] - a[1])
                .map(([reason, n]) => (
                  <li key={reason} className="flex gap-2">
                    <span className="text-neutral-500 shrink-0 w-[150px] truncate">
                      {reason}
                    </span>
                    <span className="text-neutral-300">{n}</span>
                  </li>
                ))}
            </ol>
          )}
        </div>
      )}
    </div>
  )
}
