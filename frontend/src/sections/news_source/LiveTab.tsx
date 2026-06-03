/**
 * Live status tab for news_source nodes. Combines:
 *   - Dirty banner (N8): canvas config vs backend running_config diff
 *   - Summary card: state pill + counters + last error
 *   - Start / Stop buttons (immediately disabled on click; per plan OD#3)
 *   - Event timeline (newest first; "show more" reveals up to 200 ring entries)
 *   - Message tail (newest first; id / urgency / time + 80-char content)
 */
import { useState } from 'react'

import type { SectionNodeType } from '../../canvas/store'
import {
  useNewsSourceStatusStore,
  type LogEvent,
  type RecentMessage,
  type Snapshot,
  type SourceState,
  type StartConfig,
} from './statusStore'
import { formatRelativeAgo, formatUTC } from './time'

const STATE_PILL: Record<SourceState, { label: string; cls: string }> = {
  stopped: { label: 'Stopped', cls: 'bg-neutral-800 text-neutral-300' },
  connecting: { label: 'Connecting', cls: 'bg-amber-500/15 text-amber-300' },
  connected: { label: 'Connected', cls: 'bg-emerald-500/15 text-emerald-300' },
  error: { label: 'Error', cls: 'bg-red-500/15 text-red-300' },
}

const KIND_COLOR: Record<string, string> = {
  connecting: 'text-amber-300',
  connected: 'text-emerald-300',
  disconnected: 'text-neutral-400',
  auth_fail: 'text-red-300',
  parse_error: 'text-amber-200',
  first_message: 'text-sky-300',
  start_failed: 'text-red-300',
  stopped: 'text-neutral-500',
}

/**
 * Subset-match: running_config (backend) determines the key set; if any
 * key's value disagrees with the canvas config, the user has unsaved
 * changes that won't apply until the source is restarted.
 * Avoids JSON.stringify key-order false positives (plan risk 6).
 */
function configMatches(
  canvas: Record<string, unknown>,
  running: Record<string, unknown>,
): boolean {
  for (const k of Object.keys(running)) {
    if (canvas[k] !== running[k]) return false
  }
  return true
}

export function NewsSourceLiveTab({ node }: { node: SectionNodeType }) {
  const snapshot = useNewsSourceStatusStore((s) => s.snapshot)
  const fetchStatus = useNewsSourceStatusStore((s) => s.status)
  const start = useNewsSourceStatusStore((s) => s.start)
  const stop = useNewsSourceStatusStore((s) => s.stop)

  const [busy, setBusy] = useState<'starting' | 'stopping' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [showAllEvents, setShowAllEvents] = useState(false)

  const state: SourceState = snapshot?.state ?? 'stopped'
  const isRunning = state === 'connecting' || state === 'connected'

  const dirty =
    isRunning &&
    snapshot?.running_config != null &&
    !configMatches(
      node.data.config as Record<string, unknown>,
      snapshot.running_config,
    )

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
      {dirty && (
        <div className="rounded border border-amber-700/50 bg-amber-900/20 px-3 py-2 text-[11px] text-amber-200 leading-snug">
          Config differs from running instance. Stop and Start again to apply.
        </div>
      )}

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

      <MessageTail messages={snapshot?.recent_messages ?? []} />
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
        <dt className="text-neutral-500">Last news</dt>
        <dd
          className="text-neutral-200 truncate"
          title={snapshot.last_msg_at ? formatUTC(snapshot.last_msg_at) : undefined}
        >
          {snapshot.last_msg_at ? formatRelativeAgo(snapshot.last_msg_at) : '—'}
        </dd>
        <dt className="text-neutral-500">Total received</dt>
        <dd className="text-neutral-200">{snapshot.total_recv}</dd>
        <dt className="text-neutral-500">Buffer size</dt>
        <dd className="text-neutral-200">{snapshot.buffer_size}</dd>
        <dt className="text-neutral-500">Reconnects</dt>
        <dd className="text-neutral-200">{snapshot.reconnect_attempts}</dd>
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
            {showAll ? `Show last 20` : `Show all ${events.length}`}
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
                className={`shrink-0 w-[100px] ${
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

function MessageTail({ messages }: { messages: RecentMessage[] }) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500 mb-1">
        Recent messages
      </h3>
      {messages.length === 0 ? (
        <div className="text-[11px] text-neutral-600">No messages yet.</div>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {[...messages].reverse().map((m) => (
            <li key={m.id} className="text-[10px] leading-snug">
              <div className="flex items-center gap-2 font-mono">
                <span className="text-neutral-500 truncate" title={m.id}>
                  {m.id.slice(0, 18)}
                </span>
                <span className="text-neutral-600">{m.urgency}</span>
                <span
                  className="text-neutral-600 ml-auto"
                  title={formatUTC(m.published_at)}
                >
                  {formatRelativeAgo(m.published_at)}
                </span>
              </div>
              {m.content && (
                <div className="text-neutral-400 truncate" title={m.content}>
                  {m.content.slice(0, 80)}
                  {m.content.length > 80 ? '…' : ''}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
