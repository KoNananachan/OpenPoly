/**
 * HTTP client for /api/secrets/local/* (S3).
 *
 * Encoding note: backend uses ``{name:path}`` so literal ``/`` in name maps
 * naturally. We percent-encode each segment in case future names contain
 * non-URL-safe characters, but never encode the slash separator itself.
 */

export type StoredKey = {
  name: string
  created_at: number
}

type ListResponse = { entries: StoredKey[] }
type CreateResponse = { ok: boolean; entry: StoredKey }

const BASE = '/api/secrets/local'

function encodePath(name: string): string {
  return name
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
}

export async function listKeys(prefix?: string): Promise<StoredKey[]> {
  const url = prefix ? `${BASE}?prefix=${encodeURIComponent(prefix)}` : BASE
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const body = (await r.json()) as ListResponse
  return body.entries
}

async function readErrorDetail(r: Response): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: string }
    return body.detail ?? ''
  } catch {
    return await r.text()
  }
}

export async function createKey(name: string, value: string): Promise<StoredKey> {
  const r = await fetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, value }),
  })
  if (!r.ok) {
    const detail = await readErrorDetail(r)
    throw new Error(detail || `HTTP ${r.status}`)
  }
  const body = (await r.json()) as CreateResponse
  return body.entry
}

export async function deleteKey(name: string): Promise<void> {
  const r = await fetch(`${BASE}/${encodePath(name)}`, { method: 'DELETE' })
  // 204 No Content on success; 404 if missing.
  if (!r.ok && r.status !== 204) {
    throw new Error(`HTTP ${r.status}`)
  }
}
