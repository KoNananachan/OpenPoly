import { create } from 'zustand'

import { MOCK_RUNTIME_CATALOG } from './catalog'
import { fetchCatalog } from './catalogClient'
import type { RuntimeCatalogEntry } from './types'

export type CatalogStatus = 'loading' | 'ready' | 'error'

type CatalogState = {
  entries: RuntimeCatalogEntry[]
  source: 'runtime' | 'mock'
  status: CatalogStatus
  error: string | null
  reload: () => Promise<void>
}

let inflight: Promise<void> | null = null

export const useCatalogStore = create<CatalogState>((set) => ({
  entries: MOCK_RUNTIME_CATALOG,
  source: 'mock',
  status: 'loading',
  error: null,
  reload: async () => {
    if (inflight) return inflight
    inflight = (async () => {
      try {
        const entries = await fetchCatalog()
        set({ entries, source: 'runtime', status: 'ready', error: null })
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        set({
          entries: MOCK_RUNTIME_CATALOG,
          source: 'mock',
          status: 'error',
          error: msg,
        })
      } finally {
        inflight = null
      }
    })()
    return inflight
  },
}))

// Kick off initial fetch as soon as the module is imported.
useCatalogStore.getState().reload()
