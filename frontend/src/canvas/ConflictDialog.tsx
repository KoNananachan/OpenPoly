/**
 * Conflict dialog — pops when the autosave PUT to /api/canvas/template
 * returned 409 (operator's draft is based on a stale server rev). The
 * operator must explicitly choose Keep Mine (force-overwrite server,
 * with a 2nd confirm since it's destructive) or Take Theirs (discard
 * local edits, adopt server). Dismiss leaves the conflict pending; any
 * further canvas autosave is suppressed until resolved.
 */
import { useMemo, useState } from 'react'
import { useCanvasStore } from './store'
import type { Template, TemplateNode } from './templateIO'

type DiffField = {
  path: string         // e.g. "entry.heat_cap_usd"
  mine: unknown
  theirs: unknown
}

function diffTemplates(mine: Template, theirs: Template): DiffField[] {
  const diffs: DiffField[] = []
  if (mine.name !== theirs.name) {
    diffs.push({ path: 'name', mine: mine.name, theirs: theirs.name })
  }
  const myNodes = new Map<string, TemplateNode>(
    mine.nodes.map((n) => [n.sectionType, n]),
  )
  const theirNodes = new Map<string, TemplateNode>(
    theirs.nodes.map((n) => [n.sectionType, n]),
  )
  const allTypes = new Set<string>([...myNodes.keys(), ...theirNodes.keys()])
  for (const t of allTypes) {
    const a = myNodes.get(t)
    const b = theirNodes.get(t)
    if (!a) {
      diffs.push({ path: `${t}.<exists>`, mine: false, theirs: true })
      continue
    }
    if (!b) {
      diffs.push({ path: `${t}.<exists>`, mine: true, theirs: false })
      continue
    }
    const allKeys = new Set([
      ...Object.keys(a.config ?? {}),
      ...Object.keys(b.config ?? {}),
    ])
    for (const k of allKeys) {
      const av = (a.config ?? {})[k]
      const bv = (b.config ?? {})[k]
      if (JSON.stringify(av) !== JSON.stringify(bv)) {
        diffs.push({ path: `${t}.${k}`, mine: av, theirs: bv })
      }
    }
  }
  // Edges (ID-set diff). Less common but show count if it changed.
  const myEdges = new Set(mine.edges.map((e) => `${e.source}→${e.target}`))
  const theirEdges = new Set(theirs.edges.map((e) => `${e.source}→${e.target}`))
  const onlyMine = [...myEdges].filter((e) => !theirEdges.has(e))
  const onlyTheirs = [...theirEdges].filter((e) => !myEdges.has(e))
  if (onlyMine.length || onlyTheirs.length) {
    diffs.push({
      path: 'edges',
      mine: onlyMine.length ? `+${onlyMine.length} only here` : '(same)',
      theirs: onlyTheirs.length ? `+${onlyTheirs.length} only theirs` : '(same)',
    })
  }
  return diffs
}

function fmt(v: unknown): string {
  if (v === undefined) return '—'
  if (v === null) return 'null'
  if (typeof v === 'string') return v.length > 40 ? v.slice(0, 40) + '…' : v
  return JSON.stringify(v)
}

export function ConflictDialog() {
  const conflict = useCanvasStore((s) => s.conflict)
  const resolveConflict = useCanvasStore((s) => s.resolveConflict)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const diffs = useMemo(() => {
    if (!conflict) return []
    return diffTemplates(conflict.mine, conflict.theirs)
  }, [conflict])

  if (!conflict) return null

  const onKeepMineClick = () => {
    if (!confirming) {
      setConfirming(true)
      return
    }
    setBusy(true)
    void resolveConflict('keep_mine').finally(() => {
      setBusy(false)
      setConfirming(false)
    })
  }
  const onTakeTheirsClick = () => {
    setBusy(true)
    void resolveConflict('take_theirs').finally(() => setBusy(false))
  }
  const onDismiss = () => {
    if (busy) return
    setConfirming(false)
    void resolveConflict('dismiss')
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-sm"
      onClick={onDismiss}
    >
      <div
        className="w-[600px] max-w-[calc(100vw-2rem)] max-h-[80vh] rounded-lg border border-amber-700/50 bg-neutral-950 shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-neutral-800">
          <h3 className="text-sm font-medium text-amber-200">
            Canvas conflict — server has different content
          </h3>
          <p className="mt-1 text-[11px] text-neutral-400 leading-relaxed">
            Your draft was based on an older version of the canvas. Someone
            (or an out-of-band script) saved a different canvas to the
            backend in the meantime. Choose whose version wins.
          </p>
        </div>

        <div className="px-5 py-3 flex-1 overflow-y-auto">
          {diffs.length === 0 ? (
            <p className="text-[12px] text-neutral-500">
              No field-level diff detected (revs differ but content matches —
              likely a stale rev, try Take Theirs).
            </p>
          ) : (
            <div className="grid grid-cols-[1fr_auto_1fr] gap-x-3 gap-y-1.5 text-[11px] font-mono">
              <div className="text-neutral-500 text-[10px] uppercase tracking-wide">
                Path / Mine
              </div>
              <div></div>
              <div className="text-neutral-500 text-[10px] uppercase tracking-wide">
                Theirs (server)
              </div>
              {diffs.map((d) => (
                <DiffRow key={d.path} d={d} />
              ))}
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-neutral-800 flex items-center justify-between gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onDismiss}
            className="px-3 py-1 text-xs rounded border border-neutral-700 hover:border-neutral-600 hover:bg-neutral-900 text-neutral-300 disabled:opacity-50"
          >
            Dismiss
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={onTakeTheirsClick}
              className="px-3 py-1 text-xs rounded bg-sky-700 hover:bg-sky-600 text-sky-50 disabled:opacity-50"
            >
              {busy ? '…' : 'Take Theirs (adopt server)'}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onKeepMineClick}
              className={`px-3 py-1 text-xs rounded text-red-50 disabled:opacity-50 ${
                confirming
                  ? 'bg-red-600 hover:bg-red-500'
                  : 'bg-red-800 hover:bg-red-700'
              }`}
            >
              {busy
                ? '…'
                : confirming
                  ? 'Click again to confirm overwrite'
                  : 'Keep Mine (overwrite server)'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function DiffRow({ d }: { d: DiffField }) {
  return (
    <>
      <div className="text-neutral-400 truncate" title={d.path}>
        <span className="text-neutral-600">{d.path}</span>
        <div className="text-red-300/90 break-all">{fmt(d.mine)}</div>
      </div>
      <div className="text-neutral-600 self-center">→</div>
      <div className="text-emerald-300/90 break-all">{fmt(d.theirs)}</div>
    </>
  )
}
