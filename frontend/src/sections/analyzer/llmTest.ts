export type LLMTestResult = {
  ok: boolean
  error: string | null
  latency_ms: number | null
}

const ENDPOINT = '/api/analyzer/test'

/**
 * Asks the backend to make one minimal forced tool call with the analyzer's
 * LLM config — verifies key resolution, base_url routing, model id, and
 * tool-call support in a single round trip. Server-side so a third-party
 * gateway's CORS rules can't block the check.
 */
export async function testLLMConnection(args: {
  llm_model: string
  api_key_ref: string
  base_url: string
  temperature: number
}): Promise<LLMTestResult> {
  let resp: Response
  try {
    resp = await fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
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
  return (await resp.json()) as LLMTestResult
}
