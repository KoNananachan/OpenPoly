export type TestConnectionResult = {
  ok: boolean
  error: string | null
  latency_ms: number | null
}

const ENDPOINT = '/api/news/test'

/**
 * Asks the backend to open a short-lived WebSocket against (endpoint, ref)
 * with the resolved secret in `X-API-Key`. Server-side so we avoid the
 * browser CORS wall.
 */
export async function testNewsConnection(args: {
  endpoint: string
  /** *_ref string (e.g. local:… / env:…); backend resolves it to a secret. */
  api_ref: string
}): Promise<TestConnectionResult> {
  let resp: Response
  try {
    resp = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        endpoint: args.endpoint,
        // Backend uses `api_key_ref` naming (matches openpoly.news.secrets).
        api_key_ref: args.api_ref,
      }),
    })
  } catch (e) {
    return {
      ok: false,
      error: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}`,
      latency_ms: null,
    }
  }
  if (!resp.ok) {
    return { ok: false, error: `HTTP ${resp.status}`, latency_ms: null }
  }
  return (await resp.json()) as TestConnectionResult
}
