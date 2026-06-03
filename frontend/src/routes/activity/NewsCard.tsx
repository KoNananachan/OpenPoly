/**
 * NewsCard — one News item rendered as a vertical timeline of the four
 * pipeline stages it passed (or didn't pass) through.
 *
 * Layout (D1: fully expanded inline — no toggle interaction):
 *   Header        | #id · urgency badge · state badge · ts                 |
 *                 | full news content (no truncation, D3)                  |
 *   ① News        | published / received tsago, sentiment                  |
 *   ② Embedding   | verdict badge + candidate/catalog + top market+score   |
 *   ③ Analyzer    | verdict badge + p/confidence/market_id +               |
 *                 | <AnalyzerRationaleBlock> (reused from v15)             |
 *   ④ Entry       | verdict + decided BUY + fill status + → #pos N link    |
 *
 * Border tone: terminal state (filled green / skipped neutral /
 *  errored red / pending amber). high urgency overrides → red border per
 *  D7 (operator must spot it regardless of pipeline state).
 *
 * Every stage falls back independently to an "evicted" line when its
 * backend ring lost the news_id — plan §Risks #3 forbids cascading
 * failure (older news ② may exist while ③④ are gone).
 */
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { formatRelativeAgo, formatUTC } from '../../sections/news_source/time'
import { AnalyzerRationaleBlock } from './AnalyzerRationale'
import type { AnalyzerDecision } from './portfolioTypes'
import type { CardState, NewsPipelineCard, Verdict } from './newsTypes'

const STATE_BORDER: Record<CardState, string> = {
  filled: 'border-emerald-700/50',
  skipped: 'border-neutral-700/50',
  errored: 'border-red-700/50',
  pending: 'border-amber-700/50',
}

const STATE_BADGE: Record<CardState, string> = {
  filled: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
  skipped: 'bg-neutral-800 text-neutral-300 border-neutral-700/50',
  errored: 'bg-red-900/40 text-red-300 border-red-700/50',
  pending: 'bg-amber-900/40 text-amber-300 border-amber-700/50',
}

const URGENCY_BADGE: Record<string, string> = {
  high: 'bg-red-900/40 text-red-300 border-red-700/50',
  medium: 'bg-amber-900/40 text-amber-300 border-amber-700/50',
  low: 'bg-neutral-800 text-neutral-400 border-neutral-700/50',
  regular: 'bg-neutral-800 text-neutral-400 border-neutral-700/50',
}

const VERDICT_BADGE: Record<Verdict, string> = {
  ok: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/50',
  skip: 'bg-neutral-800 text-neutral-400 border-neutral-700/50',
  fail_open: 'bg-amber-900/40 text-amber-300 border-amber-700/50',
  error: 'bg-red-900/40 text-red-300 border-red-700/50',
}

function Badge({ tone, label }: { tone: string; label: string }) {
  return (
    <span
      className={`px-1.5 py-0.5 text-[10px] uppercase font-mono rounded border ${tone}`}
    >
      {label}
    </span>
  )
}

function StageRow({
  label,
  children,
  collapsible = false,
  collapsed = false,
  onToggle,
}: {
  label: string
  children: ReactNode
  collapsible?: boolean
  collapsed?: boolean
  onToggle?: () => void
}) {
  // Collapsed: single-line affordance — chevron + label + neutral hint.
  // Only used when the stage has no data (null), per plan D1: skipped /
  // errored stages stay expanded because their verdict is meaningful.
  if (collapsible && collapsed) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="w-full px-4 py-1.5 flex items-center gap-3 text-[12px] hover:bg-neutral-900/40 transition-colors text-left"
      >
        <span className="w-28 shrink-0 text-neutral-500 font-mono text-[11px]">
          {label}
        </span>
        <span className="text-neutral-600 text-[11px]">
          ▸ not available
        </span>
      </button>
    )
  }
  return (
    <div className="px-4 py-2 flex items-start gap-3 text-[12px]">
      <span className="w-28 shrink-0 text-neutral-500 font-mono text-[11px] pt-0.5 flex items-center gap-1">
        {label}
        {collapsible && (
          <button
            type="button"
            onClick={onToggle}
            className="text-neutral-600 hover:text-neutral-400 text-[10px]"
            title="Collapse"
          >
            ▾
          </button>
        )}
      </span>
      <div className="flex-1 min-w-0 flex flex-col gap-1.5">{children}</div>
    </div>
  )
}

function EvictedFallback() {
  return (
    <span className="text-neutral-500 italic text-[11px]">
      log evicted — ring buffer overflowed
    </span>
  )
}

export function NewsCard({ card }: { card: NewsPipelineCard }) {
  const { news, embedding, analyzer, entry, state } = card
  const urgencyKey = (news.urgency ?? 'regular').toLowerCase()
  const urgencyTone = URGENCY_BADGE[urgencyKey] ?? URGENCY_BADGE.regular
  // D7: high urgency overrides state border (still keep state badge for clarity).
  const borderClass =
    urgencyKey === 'high' ? 'border-red-700/50' : STATE_BORDER[state]

  // Per-card collapse state for the three downstream stages. v17 default:
  // null-data stages collapse to a one-liner; toggling re-expands them so
  // the operator can still inspect the fallback text. Populated stages
  // pass collapsible=false below and ignore this state entirely.
  const [collapsed, setCollapsed] = useState({
    embedding: true,
    analyzer: true,
    entry: true,
  })
  const toggle = (stage: 'embedding' | 'analyzer' | 'entry') => () =>
    setCollapsed((c) => ({ ...c, [stage]: !c[stage] }))

  return (
    <article
      className={`rounded border bg-neutral-950 overflow-hidden ${borderClass}`}
    >
      {/* Header */}
      <div className="px-4 py-3 flex flex-col gap-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-neutral-500 font-mono text-[11px]">
            #{news.id}
          </span>
          <Badge tone={urgencyTone} label={news.urgency || 'regular'} />
          <Badge tone={STATE_BADGE[state]} label={state} />
          <span
            className="ml-auto text-[10px] text-neutral-500"
            title={formatUTC(news.received_at)}
          >
            {formatRelativeAgo(news.received_at)}
          </span>
        </div>
        {/* Full content, no truncation (D3) */}
        <div className="text-[13px] text-neutral-100 leading-relaxed whitespace-pre-wrap break-words">
          {news.content}
        </div>
      </div>

      <hr className="border-neutral-800" />

      {/* ① News received */}
      <StageRow label="① News">
        <div className="font-mono text-[11px] text-neutral-400">
          <span title={formatUTC(news.published_at)}>
            published {formatRelativeAgo(news.published_at)}
          </span>
          {' · '}
          <span title={formatUTC(news.received_at)}>
            received {formatRelativeAgo(news.received_at)}
          </span>
          {news.sentiment !== null && (
            <span className="ml-2 text-neutral-500">
              sentiment {news.sentiment.toFixed(2)}
            </span>
          )}
        </div>
      </StageRow>

      <hr className="border-neutral-800" />

      {/* ② Embedding filter */}
      <StageRow
        label="② Embedding"
        collapsible={embedding === null}
        collapsed={collapsed.embedding}
        onToggle={toggle('embedding')}
      >
        {embedding ? (
          <>
            <div className="flex items-baseline gap-2 flex-wrap font-mono text-[11px]">
              <Badge
                tone={VERDICT_BADGE[embedding.verdict]}
                label={embedding.verdict}
              />
              <span className="text-neutral-400">
                {embedding.candidate_count} candidate
                {embedding.candidate_count === 1 ? '' : 's'} / catalog{' '}
                {embedding.catalog_size}
              </span>
              <span className="ml-auto text-neutral-600 text-[10px]">
                {embedding.latency_ms}ms
              </span>
            </div>
            {embedding.top_market_id !== null && embedding.top_score !== null && (
              <div className="font-mono text-[11px] text-neutral-500">
                top market{' '}
                <span className="text-neutral-300">
                  {embedding.top_market_id.slice(0, 20)}
                  {embedding.top_market_id.length > 20 ? '…' : ''}
                </span>{' '}
                score={' '}
                <span className="text-neutral-300">
                  {embedding.top_score.toFixed(3)}
                </span>
              </div>
            )}
            {embedding.error && (
              <div className="text-[11px] text-red-400 font-mono break-all">
                {embedding.error}
              </div>
            )}
          </>
        ) : (
          <EvictedFallback />
        )}
      </StageRow>

      <hr className="border-neutral-800" />

      {/* ③ Analyzer verdict */}
      <StageRow
        label="③ Analyzer"
        collapsible={analyzer === null}
        collapsed={collapsed.analyzer}
        onToggle={toggle('analyzer')}
      >
        {analyzer ? (
          <>
            <div className="flex items-baseline gap-2 flex-wrap font-mono text-[11px]">
              <Badge
                tone={VERDICT_BADGE[analyzer.verdict]}
                label={analyzer.verdict}
              />
              {analyzer.p_model !== null && (
                <span className="text-neutral-400">
                  p={analyzer.p_model.toFixed(2)}
                </span>
              )}
              {analyzer.confidence !== null && (
                <span
                  className={
                    analyzer.confidence === 'high'
                      ? 'text-emerald-300'
                      : analyzer.confidence === 'medium'
                        ? 'text-amber-300'
                        : 'text-neutral-400'
                  }
                >
                  {analyzer.confidence}
                </span>
              )}
              {analyzer.market_id !== null && (
                <span className="text-neutral-500">
                  → market{' '}
                  <span className="text-neutral-300">
                    {analyzer.market_id.slice(0, 16)}
                    {analyzer.market_id.length > 16 ? '…' : ''}
                  </span>
                </span>
              )}
              <span className="ml-auto text-neutral-600 text-[10px]">
                {analyzer.latency_ms}ms
              </span>
            </div>
            {analyzer.error && (
              <div className="text-[11px] text-red-400 font-mono break-all">
                {analyzer.error}
              </div>
            )}
            {/* AnalyzerDecision is a structural subset of AnalyzerCallEntry —
                rationale / p_model / confidence / ts line up directly. */}
            <AnalyzerRationaleBlock
              decisions={[
                {
                  rationale: analyzer.rationale,
                  p_model: analyzer.p_model,
                  confidence: analyzer.confidence,
                  ts: analyzer.ts,
                } satisfies AnalyzerDecision,
              ]}
            />
          </>
        ) : (
          <EvictedFallback />
        )}
      </StageRow>

      <hr className="border-neutral-800" />

      {/* ④ Entry decision */}
      <StageRow
        label="④ Entry"
        collapsible={entry === null}
        collapsed={collapsed.entry}
        onToggle={toggle('entry')}
      >
        {entry ? (
          <>
            <div className="flex items-baseline gap-2 flex-wrap font-mono text-[11px]">
              <Badge
                tone={VERDICT_BADGE[entry.verdict]}
                label={entry.verdict}
              />
              {entry.side !== null &&
                entry.qty !== null &&
                entry.price !== null && (
                  <span className="text-neutral-300">
                    BUY {entry.side.toUpperCase()} {entry.qty.toFixed(2)} @{' '}
                    {entry.price.toFixed(3)}
                  </span>
                )}
              {entry.fill_status && (
                <Badge
                  tone={
                    entry.fill_status === 'filled'
                      ? VERDICT_BADGE.ok
                      : entry.fill_status === 'error'
                        ? VERDICT_BADGE.error
                        : VERDICT_BADGE.skip
                  }
                  label={`fill:${entry.fill_status}`}
                />
              )}
              {entry.fill_status === 'filled' &&
                entry.fill_qty !== null &&
                entry.fill_price !== null && (
                  <span className="text-emerald-300 text-[10px]">
                    @ {entry.fill_price.toFixed(3)} × {entry.fill_qty.toFixed(2)}
                  </span>
                )}
              {entry.fill_status === 'filled' && entry.position_id !== null && (
                <Link
                  to={`/activity/positions/${entry.position_id}`}
                  className="text-blue-400 hover:text-blue-300 underline"
                >
                  → #pos {entry.position_id}
                </Link>
              )}
              <span className="ml-auto text-neutral-600 text-[10px]">
                {entry.latency_ms}ms
              </span>
            </div>
            {entry.reason && (
              <div className="text-[11px] text-neutral-500 font-mono break-words">
                reason: {entry.reason}
              </div>
            )}
            {entry.error && (
              <div className="text-[11px] text-red-400 font-mono break-all">
                {entry.error}
              </div>
            )}
          </>
        ) : (
          <EvictedFallback />
        )}
      </StageRow>
    </article>
  )
}
