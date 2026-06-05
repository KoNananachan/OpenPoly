/**
 * Demo mock server (M1 — routing kernel).
 *
 * A tiny in-browser stand-in for the FastAPI backend, used only by the
 * `VITE_DEMO` build. It owns no fixtures of its own: fixture modules (M2
 * canvas / M3 activity) register routes, and this kernel matches an incoming
 * `fetch('/api/...')` against them and synthesizes a real `Response`.
 *
 * Design notes:
 * - Consumers everywhere do `const r = await fetch(url); if (!r.ok) ...; await
 *   r.json()`. A native `Response` satisfies that contract for free, so a
 *   handler just returns a plain JS body and we wrap it.
 * - Routes are tagged `read` | `mutation`. Mutation hits (start/stop/close/
 *   save/test) fire `onMutation` so the UI can surface a "demo mode" hint
 *   (wired in M4); they still return a benign response so nothing throws.
 * - Unregistered `/api/*` paths fall back to `200 {}` plus a `console.warn`,
 *   so a missing fixture degrades to an empty panel instead of a red crash —
 *   and the warning tells us exactly which endpoint to add.
 */

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'

export type MockContext = {
  /** Request method, upper-cased. */
  method: HttpMethod
  /** URL pathname, e.g. `/api/inspect/news`. */
  path: string
  /** Parsed query string. */
  query: URLSearchParams
  /** Captured groups from the route `pattern`, if any. */
  params: RegExpExecArray
  /** Original fetch init, for handlers that need the request body. */
  init?: RequestInit
}

/**
 * A handler returns either a plain JS value (wrapped as `200 application/json`)
 * or a fully-formed `Response` when it needs a non-200 status (e.g. the canvas
 * template's `404 empty`).
 */
export type MockHandler = (ctx: MockContext) => unknown | Response

export type MockRoute = {
  /** Defaults to `GET`. */
  method?: HttpMethod
  /** Tested against the URL pathname only (query stripped). */
  pattern: RegExp
  /** Defaults to `read` for GET, `mutation` otherwise. */
  kind?: 'read' | 'mutation'
  handler: MockHandler
}

export type MockOptions = {
  /** Artificial latency so charts/polls show a brief "loading" beat. */
  delayMs?: number
  /** Fired when a `mutation` route matches — used to pop the demo-mode toast. */
  onMutation?: (ctx: MockContext) => void
  /** Real fetch to delegate non-`/api` requests to (fonts, etc.). */
  passthrough: typeof fetch
}

const DEFAULT_DELAY_MS = 150
const API_PREFIX = '/api'

function normalizeMethod(m?: string): HttpMethod {
  return (m ?? 'GET').toUpperCase() as HttpMethod
}

/** Extract method + path + query from any of fetch's input shapes. */
function describeRequest(
  input: RequestInfo | URL,
  init?: RequestInit,
): { method: HttpMethod; path: string; query: URLSearchParams } {
  let rawUrl: string
  let method = normalizeMethod(init?.method)
  if (typeof input === 'string') {
    rawUrl = input
  } else if (input instanceof URL) {
    rawUrl = input.toString()
  } else {
    // Request object: its own method wins unless init overrides it.
    rawUrl = input.url
    if (!init?.method) method = normalizeMethod(input.method)
  }
  // Resolve relative `/api/...` against a dummy origin so URL can parse it.
  const url = new URL(rawUrl, 'http://demo.local')
  return { method, path: url.pathname, query: url.searchParams }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function delay(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const t = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(t)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}

/**
 * Build a `fetch`-compatible function that serves `/api/*` from `routes` and
 * delegates everything else to `opts.passthrough`. The result is what
 * `install.ts` swaps onto `window.fetch`.
 */
export function createMockFetch(
  routes: MockRoute[],
  opts: MockOptions,
): typeof fetch {
  const delayMs = opts.delayMs ?? DEFAULT_DELAY_MS

  return async function mockFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const { method, path, query } = describeRequest(input, init)

    if (!path.startsWith(API_PREFIX)) {
      return opts.passthrough(input, init)
    }

    const signal =
      init?.signal ?? (input instanceof Request ? input.signal : undefined)

    for (const route of routes) {
      const routeMethod = normalizeMethod(route.method)
      if (routeMethod !== method) continue
      const params = route.pattern.exec(path)
      if (!params) continue

      const kind = route.kind ?? (routeMethod === 'GET' ? 'read' : 'mutation')
      const ctx: MockContext = { method, path, query, params, init }

      await delay(delayMs, signal)

      if (kind === 'mutation') opts.onMutation?.(ctx)

      const result = route.handler(ctx)
      return result instanceof Response ? result : jsonResponse(result)
    }

    // No fixture for this endpoint — degrade to an empty 200 and tell us which
    // one so it can be added, rather than throwing a red error in the panel.
    console.warn(`[demo] unhandled ${method} ${path} → empty 200`)
    await delay(delayMs, signal)
    return jsonResponse({})
  } as typeof fetch
}
