/**
 * Mount-scoped polling hook for the Activity tabs.
 *
 * The initial fetch runs unconditionally; only the interval is gated on tab
 * visibility, so a background tab does not keep hitting the API but also never
 * hangs forever on "Loading…". The fetcher is held in a ref so passing an
 * inline arrow does not re-subscribe the effect.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

export type PollStatus = 'loading' | 'ready' | 'error'

export type PollResult<T> = {
  data: T | null
  status: PollStatus
  error: string | null
}

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs = 3000,
): PollResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [status, setStatus] = useState<PollStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const fetcherRef = useRef(fetcher)
  useLayoutEffect(() => {
    fetcherRef.current = fetcher
  })

  useEffect(() => {
    let cancelled = false
    async function refresh() {
      try {
        const result = await fetcherRef.current()
        if (cancelled) return
        setData(result)
        setStatus('ready')
        setError(null)
      } catch (e) {
        if (cancelled) return
        setStatus('error')
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    void refresh()
    const maybeRefresh = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    const timer = setInterval(maybeRefresh, intervalMs)
    document.addEventListener('visibilitychange', maybeRefresh)
    return () => {
      cancelled = true
      clearInterval(timer)
      document.removeEventListener('visibilitychange', maybeRefresh)
    }
  }, [intervalMs])

  return { data, status, error }
}
