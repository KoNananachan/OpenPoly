import type { RuntimeCatalogEntry } from './types'

const ENDPOINT = '/api/sections/catalog'

export async function fetchCatalog(
  signal?: AbortSignal,
): Promise<RuntimeCatalogEntry[]> {
  const r = await fetch(ENDPOINT, { signal })
  if (!r.ok) throw new Error(`Catalog fetch failed: ${r.status}`)
  const body = (await r.json()) as { sections?: unknown }
  if (!Array.isArray(body.sections)) {
    throw new Error('Catalog response shape invalid')
  }
  return body.sections as RuntimeCatalogEntry[]
}
