/**
 * Strategy status bar — shows at a glance whether the pipeline is running, the
 * health of each source, and (when not ready) what still needs configuring
 * before Run is enabled. Sits directly under the top bar.
 */
import { useRuntime, type Blocker } from './useRuntime'
import { useCanvasUiStore } from './uiStore'

function ago(ts: number | null): string {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
}

function Dot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${pulse ? 'animate-pulse' : ''}`}
      style={{ background: color }}
    />
  )
}

function BlockerChip({ b }: { b: Blocker }) {
  const setKeysOpen = useCanvasUiStore((s) => s.setKeysOpen)
  return (
    <span className="inline-flex items-center gap-1 rounded border border-amber-800/60 bg-amber-900/40 px-2 py-0.5 text-[11px] text-amber-200">
      <span className="text-amber-400">✗</span>
      <span className="font-medium">{b.label}</span>: {b.msg}
      {b.action === 'keys' && (
        <button
          type="button"
          onClick={() => setKeysOpen(true)}
          className="ml-1 underline decoration-amber-500/50 hover:text-amber-100"
        >
          Open Keys
        </button>
      )}
    </span>
  )
}

export function StrategyStatusBar() {
  const rt = useRuntime()

  if (!rt.ready) {
    return (
      <div className="border-b border-amber-800/60 bg-amber-950/40">
        <div className="flex h-10 items-center gap-3 px-4 text-sm">
          <span className="flex items-center gap-2 font-medium text-amber-300">
            <Dot color="#f59e0b" /> Not ready — {rt.blockers.length} blocker
            {rt.blockers.length === 1 ? '' : 's'}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            {rt.blockers.map((b, i) => (
              <BlockerChip key={i} b={b} />
            ))}
          </div>
          <span className="ml-auto text-[11px] text-neutral-400">
            Configure the items above to enable Run
          </span>
        </div>
      </div>
    )
  }

  const running = rt.overall === 'running'
  const partial = rt.overall === 'partial'
  const activeColor = running ? '#34d399' : partial ? '#f59e0b' : '#9ca3af'
  const lead = running ? 'Running' : partial ? 'Partial' : 'Paused'
  const leadText = running
    ? 'text-emerald-300'
    : partial
      ? 'text-amber-300'
      : 'text-neutral-300'

  const srcDot = (active: boolean) =>
    active ? (running ? '#34d399' : '#f59e0b') : '#6b7280'

  return (
    <div className="border-b border-neutral-800 bg-neutral-900/60">
      <div className="flex h-10 items-center gap-4 px-4 text-sm">
        <span className="flex items-center gap-2">
          <Dot color={activeColor} pulse={running} />
          <span className={`font-medium ${leadText}`}>{lead}</span>
        </span>
        <span className="text-neutral-700">·</span>
        <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-300">
          <Dot color={srcDot(rt.newsState === 'connected' || rt.newsState === 'connecting')} />
          <span className="text-neutral-400">news</span>
          {rt.newsState === 'connected'
            ? <>connected · last msg <span className="text-neutral-200">{ago(rt.newsLastMsgAt)}</span> ago</>
            : rt.newsState ?? 'stopped'}
        </span>
        <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-300">
          <Dot color={srcDot(rt.marketState === 'running')} />
          <span className="text-neutral-400">market</span>
          {rt.marketState === 'running'
            ? <>polling · <span className="text-neutral-200">{rt.marketCatalogSize ?? '—'}</span> markets · poll <span className="text-neutral-200">{ago(rt.marketLastPollAt)}</span> ago</>
            : rt.marketState ?? 'stopped'}
        </span>
        <span className="ml-auto text-[11px] text-neutral-500">
          {running ? 'pipeline live' : partial ? 'one source down' : 'pipeline idle'}
        </span>
      </div>
    </div>
  )
}
