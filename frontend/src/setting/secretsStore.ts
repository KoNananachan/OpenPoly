/**
 * Zustand store for the local secret list (S4).
 *
 * Names + created_at only — values are write-only from the UI's perspective.
 * Self-mounts an initial fetch on import; refresh on demand after add/delete.
 */
import { create } from 'zustand'

import {
  createKey,
  deleteKey,
  listKeys,
  type StoredKey,
} from './secretsClient'

export type FetchStatus = 'idle' | 'loading' | 'ready' | 'error'

type StoreState = {
  keys: StoredKey[]
  status: FetchStatus
  error: string | null
  refresh: () => Promise<void>
  add: (name: string, value: string) => Promise<void>
  remove: (name: string) => Promise<void>
}

function sortByName(arr: StoredKey[]): StoredKey[] {
  return [...arr].sort((a, b) => a.name.localeCompare(b.name))
}

export const useSecretsStore = create<StoreState>((set, get) => ({
  keys: [],
  status: 'idle',
  error: null,
  refresh: async () => {
    if (get().status === 'loading') return
    set({ status: 'loading' })
    try {
      const keys = await listKeys()
      set({ keys: sortByName(keys), status: 'ready', error: null })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      set({ status: 'error', error: msg })
    }
  },
  add: async (name, value) => {
    const entry = await createKey(name, value)
    set((s) => ({
      keys: sortByName([...s.keys.filter((k) => k.name !== entry.name), entry]),
      status: 'ready',
      error: null,
    }))
  },
  remove: async (name) => {
    await deleteKey(name)
    set((s) => ({ keys: s.keys.filter((k) => k.name !== name) }))
  },
}))

if (typeof window !== 'undefined') {
  void useSecretsStore.getState().refresh()
}
