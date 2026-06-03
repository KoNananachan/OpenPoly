/**
 * AnalyzerRationaleBlock — shows the LLM's stated reason(s) for opening a
 * position. Same rendering across PositionDetail (single position page) and
 * PositionCard (list view).
 *
 * Dedupe rule: groups by exact rationale text so the LLM saying the same
 * thing N times collapses to one row with a ×N count. Newest group is
 * expanded by default; older groups hide behind "Show N earlier attempts".
 *
 * Empty list (decisions.length === 0) → "unavailable" fallback that tells
 * the operator this is a data-retention gap (ring buffer evicted the call),
 * not a system bug.
 */
import { useMemo, useState } from 'react'
import type { AnalyzerDecision } from './portfolioTypes'

type DecisionGroup = {
  rationale: string
  members: AnalyzerDecision[]   // newest-first within group
  newest: AnalyzerDecision
}

function groupDecisions(decisions: AnalyzerDecision[]): DecisionGroup[] {
  // Decisions arrive newest-first from the backend. We preserve that order
  // for the groups (first-seen-rationale gets the slot), and within each
  // group keep members in arrival order so .newest stays correct.
  const byKey = new Map<string, DecisionGroup>()
  for (const d of decisions) {
    const key = (d.rationale ?? '').trim()
    const existing = byKey.get(key)
    if (existing) {
      existing.members.push(d)
    } else {
      byKey.set(key, { rationale: key, members: [d], newest: d })
    }
  }
  for (const g of byKey.values()) g.newest = g.members[0]
  return [...byKey.values()]
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    month: 'short', day: 'numeric',
  })
}

function DecisionMeta({ d }: { d: AnalyzerDecision }) {
  return (
    <span className="text-[10px] text-neutral-500 font-mono">
      {fmtTs(d.ts)}
      {d.p_model !== null && (
        <span className="ml-2 text-neutral-400">p={d.p_model.toFixed(2)}</span>
      )}
      {d.confidence !== null && (
        <span
          className={`ml-2 ${
            d.confidence === 'high'
              ? 'text-emerald-300'
              : d.confidence === 'medium'
                ? 'text-amber-300'
                : 'text-neutral-400'
          }`}
        >
          {d.confidence}
        </span>
      )}
    </span>
  )
}

export function AnalyzerRationaleBlock({
  decisions,
}: {
  decisions: AnalyzerDecision[]
}) {
  const groups = useMemo(() => groupDecisions(decisions), [decisions])
  const [showOlder, setShowOlder] = useState(false)
  const [expandedDupes, setExpandedDupes] = useState<Set<number>>(new Set())

  if (groups.length === 0) {
    return (
      <div className="rounded border border-neutral-800/70 bg-neutral-950 p-3 text-[11px] text-neutral-500 leading-relaxed">
        <div className="text-neutral-400 mb-0.5">Analyzer rationale</div>
        <div>
          Unavailable — the LLM call that opened this position has been evicted
          from the in-memory analyzer log (retains only the most recent ~200
          analyzer calls; persist-to-DB is a planned follow-up).
        </div>
      </div>
    )
  }

  const newestGroup = groups[0]
  const olderGroups = groups.slice(1)

  return (
    <div className="rounded border border-neutral-800 bg-neutral-950 p-3 flex flex-col gap-2">
      <div className="flex items-baseline gap-3 text-[11px]">
        <span className="text-neutral-400">Analyzer rationale</span>
        {decisions.length > 1 && (
          <span className="text-neutral-600">
            ({decisions.length} total
            {olderGroups.length > 0
              ? `, ${groups.length} distinct`
              : groups.length === 1 && newestGroup.members.length > 1
                ? `, ×${newestGroup.members.length} same text`
                : ''}
            )
          </span>
        )}
      </div>

      <RationaleGroupRow
        group={newestGroup}
        expandedDupes={expandedDupes.has(0)}
        onToggleDupes={() =>
          setExpandedDupes((s) => {
            const next = new Set(s)
            if (next.has(0)) next.delete(0)
            else next.add(0)
            return next
          })
        }
      />

      {olderGroups.length > 0 && !showOlder && (
        <button
          type="button"
          onClick={() => setShowOlder(true)}
          className="self-start text-[10px] text-blue-400 hover:text-blue-300 underline"
        >
          Show {olderGroups.length} earlier attempt
          {olderGroups.length > 1 ? 's' : ''}
        </button>
      )}

      {showOlder &&
        olderGroups.map((g, i) => (
          <RationaleGroupRow
            key={`${g.rationale}-${i + 1}`}
            group={g}
            expandedDupes={expandedDupes.has(i + 1)}
            onToggleDupes={() =>
              setExpandedDupes((s) => {
                const next = new Set(s)
                if (next.has(i + 1)) next.delete(i + 1)
                else next.add(i + 1)
                return next
              })
            }
          />
        ))}
    </div>
  )
}

function RationaleGroupRow({
  group,
  expandedDupes,
  onToggleDupes,
}: {
  group: DecisionGroup
  expandedDupes: boolean
  onToggleDupes: () => void
}) {
  const dupeCount = group.members.length
  return (
    <div className="rounded bg-neutral-900/60 p-2.5 flex flex-col gap-1">
      <div className="flex items-baseline gap-2">
        <DecisionMeta d={group.newest} />
        {dupeCount > 1 && (
          <button
            type="button"
            onClick={onToggleDupes}
            className="text-[10px] text-blue-400 hover:text-blue-300"
            title="Same rationale appeared this many times"
          >
            ×{dupeCount} {expandedDupes ? '▾' : '▸'}
          </button>
        )}
      </div>
      <div className="text-[12px] text-neutral-200 leading-relaxed whitespace-pre-wrap break-words">
        {group.rationale.length > 0 ? (
          group.rationale
        ) : (
          <span className="text-neutral-500 italic">(empty rationale)</span>
        )}
      </div>
      {dupeCount > 1 && expandedDupes && (
        <div className="pl-3 border-l border-neutral-700/50 flex flex-col gap-0.5 text-[10px] text-neutral-500 font-mono">
          {group.members.slice(1).map((d, j) => (
            <DecisionMeta key={j} d={d} />
          ))}
        </div>
      )}
    </div>
  )
}
