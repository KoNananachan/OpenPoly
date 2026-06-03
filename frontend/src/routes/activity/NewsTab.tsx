/**
 * Activity › News — single stream of NewsCards (v16).
 *
 * Filter chips at top (All / Filled / Skipped / Errored), default All
 * per D2; D6 semantics fall out of the card's terminal state derived in
 * newsClient — Filled/Skipped/Errored each show only `state === filter`,
 * so embedding-skipped news ends up under "Skipped" (where it belongs)
 * and is hidden from "Filled" / "Errored" buckets.
 *
 * Pagination via Load more (D5): we re-fetch with a higher `newsLimit`;
 * the section-log endpoints (embedding / analyzer / entry) are
 * size-200 rings server-side and always queried at their max, so they
 * don't need a paged limit. The next poll tick (≤3 s) picks up the new
 * news limit via the fetcher closure (usePoll re-reads via ref).
 *
 * 5-s polling re-uses the same `usePoll` everyone else here does;
 * `pending` cards are intentionally only visible under "All" — they're
 * transient and would clutter every other filter.
 */
import { useMemo, useState } from 'react'
import { DEFAULT_NEWS_LIMIT, fetchNewsPipeline } from './newsClient'
import { NewsCard } from './NewsCard'
import type { NewsPipelineCard } from './newsTypes'
import { usePoll } from './usePoll'

type Filter = 'all' | 'filled' | 'skipped' | 'errored'

const LOAD_MORE_STEP = 100

function FilterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1 rounded text-[11px] font-medium border transition-colors ${
        active
          ? 'bg-neutral-700 border-neutral-600 text-neutral-100'
          : 'bg-transparent border-neutral-800 text-neutral-400 hover:bg-neutral-900'
      }`}
    >
      {label} <span className="text-neutral-500">({count})</span>
    </button>
  )
}

export function NewsTab() {
  const [displayLimit, setDisplayLimit] = useState(DEFAULT_NEWS_LIMIT)
  const [filter, setFilter] = useState<Filter>('all')

  const { data: cards, status, error } = usePoll<NewsPipelineCard[]>(
    () => fetchNewsPipeline(displayLimit),
  )

  const counts = useMemo(() => {
    const r = { all: 0, filled: 0, skipped: 0, errored: 0, pending: 0 }
    if (!cards) return r
    r.all = cards.length
    for (const c of cards) {
      if (c.state === 'filled') r.filled++
      else if (c.state === 'skipped') r.skipped++
      else if (c.state === 'errored') r.errored++
      else r.pending++
    }
    return r
  }, [cards])

  const visible = useMemo(
    () =>
      (cards ?? []).filter((c) => filter === 'all' || c.state === filter),
    [cards, filter],
  )

  // Server hit our requested limit → there is likely more to fetch.
  const canLoadMore = cards !== null && cards.length >= displayLimit

  if (cards === null) {
    return (
      <div className="grid place-items-center p-10">
        <p className="text-sm text-neutral-400">
          {status === 'error' ? `Backend unreachable: ${error}` : 'Loading…'}
        </p>
      </div>
    )
  }

  return (
    <div className="px-6 pb-6 flex flex-col gap-4">
      {status === 'error' && (
        <div className="rounded border border-red-700/50 bg-red-900/20 px-3 py-2 text-[11px] text-red-200">
          Backend unreachable; data may be stale.
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <FilterChip
          label="All"
          count={counts.all}
          active={filter === 'all'}
          onClick={() => setFilter('all')}
        />
        <FilterChip
          label="Filled"
          count={counts.filled}
          active={filter === 'filled'}
          onClick={() => setFilter('filled')}
        />
        <FilterChip
          label="Skipped"
          count={counts.skipped}
          active={filter === 'skipped'}
          onClick={() => setFilter('skipped')}
        />
        <FilterChip
          label="Errored"
          count={counts.errored}
          active={filter === 'errored'}
          onClick={() => setFilter('errored')}
        />
        {counts.pending > 0 && filter !== 'all' && (
          <span className="ml-2 text-[10px] text-neutral-500 italic">
            ({counts.pending} pending — visible only under All)
          </span>
        )}
      </div>

      {visible.length === 0 ? (
        <div className="rounded border border-neutral-800 px-3 py-6 text-center text-[11px] text-neutral-500">
          {filter === 'all'
            ? 'No news yet — waiting for the upstream WS to push.'
            : `No "${filter}" news right now — try All to see everything.`}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {visible.map((c) => (
            <NewsCard key={c.news.id} card={c} />
          ))}
        </div>
      )}

      {canLoadMore && (
        <button
          type="button"
          onClick={() => setDisplayLimit((n) => n + LOAD_MORE_STEP)}
          className="self-center px-4 py-1.5 rounded border border-neutral-800 bg-transparent text-[11px] text-neutral-400 hover:bg-neutral-900"
        >
          Load more
        </button>
      )}
    </div>
  )
}
