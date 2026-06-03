import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type XYPosition,
} from '@xyflow/react'
import { create } from 'zustand'
import { defaultConfigForType } from '../sections/catalog'
import { useCatalogStore } from '../sections/catalogStore'
import type { ConfigValues, SectionType } from '../sections/types'
import { isValidConnection } from './edgeRules'
import { SEED_TEMPLATE } from './seedTemplate'
import {
  fetchTemplateFromBackend,
  loadFromStorage,
  pushTemplateToBackend,
  saveToStorage,
  TEMPLATE_VERSION,
  type Template,
} from './templateIO'

export type SectionNodeData = {
  sectionType: SectionType
  config: ConfigValues
} & Record<string, unknown>

export type SectionNodeType = Node<SectionNodeData, 'section'>

type ConfigValue = string | number | boolean

export type SaveStatus = 'saved' | 'saving' | 'offline'

/** Pending conflict surfaced by autosave PUT — operator must explicitly
 * resolve via ConflictDialog before any further autosave fires. */
export type CanvasConflict = {
  mine: Template
  theirs: Template
  theirsRev: string
}

type CanvasState = {
  templateName: string
  nodes: SectionNodeType[]
  edges: Edge[]
  selectedNodeId: string | null
  startupFlash: string | null
  saveStatus: SaveStatus
  // canvas-sync v2: rev the backend confirmed on the last successful
  // GET/PUT. Sent back as If-Match on every autosave PUT. `null` =
  // backend has no template on disk yet (first-write window).
  serverRev: string | null
  // True between a successful backend reach and the next network error.
  // When false, autosave only writes localStorage and we show a banner.
  isOnline: boolean
  // Set when a PUT returned 409 — the live form is the operator's
  // (`mine`), and we show ConflictDialog over the canvas with `theirs`.
  conflict: CanvasConflict | null

  onNodesChange: (changes: NodeChange<SectionNodeType>[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onConnect: (connection: Connection) => void
  addSectionNode: (type: SectionType, position: XYPosition) => void
  setSelectedNodeId: (id: string | null) => void
  updateNodeConfig: (id: string, key: string, value: ConfigValue) => void
  updateNodeConfigBulk: (id: string, config: ConfigValues) => void
  setTemplateName: (name: string) => void
  serialize: () => Template
  loadTemplate: (template: Template) => void
  resetToSeed: () => void
  consumeStartupFlash: () => string | null
  // canvas-sync v2: called once on app mount. ALWAYS pulls server first
  // (regardless of whether localStorage has a draft). If server has a
  // canvas → adopt. If server returns 404 → keep current state (which
  // came from localStorage if present, else SEED). If server unreachable
  // → mark offline, keep current state.
  bootstrapFromBackend: () => Promise<void>
  // ConflictDialog handlers: caller picks which side wins.
  // `keepMine` → force-overwrite backend with the in-memory template
  //   (force = If-Match: *). Operator has presumably already eye-balled
  //   the diff; a confirm dialog wraps this in the UI layer.
  // `takeTheirs` → adopt the server template, discarding the local
  //   conflict-state draft.
  // `dismissConflict` → leave the conflict in place for inspection
  //   (clears the modal but keeps state stuck until resolved).
  resolveConflict: (choice: 'keep_mine' | 'take_theirs' | 'dismiss') => Promise<void>
}

let nextNumericId = 1
const newId = () => `n${nextNumericId++}`

function bumpIdCounterFrom(nodes: SectionNodeType[]): void {
  for (const n of nodes) {
    const m = /^n(\d+)$/.exec(n.id)
    if (m) {
      const v = parseInt(m[1], 10)
      if (v >= nextNumericId) nextNumericId = v + 1
    }
  }
}

function templateToCanvas(t: Template): {
  nodes: SectionNodeType[]
  edges: Edge[]
} {
  const nodes: SectionNodeType[] = t.nodes.map((n) => ({
    id: n.id,
    type: 'section',
    position: n.position,
    data: { sectionType: n.sectionType, config: n.config },
  }))
  const edges: Edge[] = t.edges.map((e, i) => ({
    id: `e-${e.source}-${e.target}-${i}`,
    source: e.source,
    target: e.target,
  }))
  return { nodes, edges }
}

const loaded = loadFromStorage()
const startupTemplate =
  loaded?.status === 'ok' ? loaded.template : SEED_TEMPLATE
const startup = templateToCanvas(startupTemplate)
bumpIdCounterFrom(startup.nodes)
const initialStartupFlash =
  loaded?.status === 'ok' && loaded.migrated
    ? 'Migrated draft to v3: linked Embedding → Database'
    : loaded?.status === 'incompatible'
      ? 'Saved draft used a removed section type — reset to seed'
      : null

// Debounce window for autosave. Long enough that a node drag or a burst of
// keystrokes collapses into a single write; short enough to feel instant.
const AUTOSAVE_DEBOUNCE_MS = 500

export const useCanvasStore = create<CanvasState>((set, get) => {
  let saveTimer: ReturnType<typeof setTimeout> | null = null

  // Suppress autosave while bootstrap is in flight — adopting the server
  // template via loadTemplate would otherwise immediately schedule a
  // round-trip PUT of the same content (wasteful + race against rev).
  let suppressAutosave = false

  const scheduleAutosave = () => {
    if (suppressAutosave) return
    // Don't autosave while a conflict is pending — the operator must
    // resolve first. Otherwise the next push would 409 again immediately.
    if (get().conflict !== null) {
      return
    }
    set({ saveStatus: 'saving' })
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveTimer = null
      void doSave()
    }, AUTOSAVE_DEBOUNCE_MS)
  }

  const doSave = async (): Promise<void> => {
    const tpl = get().serialize()
    saveToStorage(tpl)
    // canvas-sync v2: server is canonical. Push with the rev we last
    // observed; backend rejects mismatched revs with 409.
    const result = await pushTemplateToBackend(tpl, get().serverRev)
    if (result.status === 'ok') {
      set({ saveStatus: 'saved', serverRev: result.rev, isOnline: true })
    } else if (result.status === 'conflict') {
      // Backend has moved since our last sync. Park the local draft as
      // `mine`, show theirs, force operator to choose.
      set({
        saveStatus: 'saved',
        conflict: {
          mine: tpl,
          theirs: result.current_template,
          theirsRev: result.current_rev,
        },
      })
    } else if (result.status === 'network_error') {
      // localStorage is still authoritative for the live working copy.
      // Mark offline so the UI surfaces a banner; reconnect will retry.
      set({ saveStatus: 'offline', isOnline: false })
    } else if (result.status === 'bad_request') {
      // 400 from server (e.g. malformed). Loud-log; not autosave-retryable.
      console.error('canvas PUT 400:', result.error)
      set({ saveStatus: 'saved' })
    }
  }

  // Guard so bootstrap is idempotent — protects against React StrictMode
  // double-mounting the canvas in dev.
  let bootstrapRan = false

  return {
    templateName: startupTemplate.name,
    nodes: startup.nodes,
    edges: startup.edges,
    selectedNodeId: null,
    startupFlash: initialStartupFlash,
    saveStatus: 'saved',
    serverRev: null,
    isOnline: true,
    conflict: null,

    onNodesChange: (changes) => {
      set({ nodes: applyNodeChanges(changes, get().nodes) as SectionNodeType[] })
      if (changes.some((c) => c.type !== 'select' && c.type !== 'dimensions')) {
        scheduleAutosave()
      }
    },
    onEdgesChange: (changes) => {
      set({ edges: applyEdgeChanges(changes, get().edges) })
      if (changes.some((c) => c.type !== 'select')) scheduleAutosave()
    },
    onConnect: (connection) => {
      const { nodes, edges } = get()
      if (!isValidConnection(connection, nodes, edges)) return
      set({ edges: addEdge(connection, edges) })
      scheduleAutosave()
    },
    addSectionNode: (type, position) => {
      const s = get()
      if (s.nodes.some((n) => n.data.sectionType === type)) return
      const entries = useCatalogStore.getState().entries
      const id = newId()
      set({
        nodes: [
          ...s.nodes,
          {
            id,
            type: 'section',
            position,
            data: {
              sectionType: type,
              config: defaultConfigForType(type, entries),
            },
          },
        ],
        selectedNodeId: id,
      })
      scheduleAutosave()
    },
    setSelectedNodeId: (id) => set({ selectedNodeId: id }),
    updateNodeConfig: (id, key, value) => {
      set((s) => ({
        nodes: s.nodes.map((n) =>
          n.id === id
            ? {
                ...n,
                data: { ...n.data, config: { ...n.data.config, [key]: value } },
              }
            : n,
        ),
      }))
      scheduleAutosave()
    },
    updateNodeConfigBulk: (id, config) => {
      set((s) => ({
        nodes: s.nodes.map((n) =>
          n.id === id ? { ...n, data: { ...n.data, config } } : n,
        ),
      }))
      scheduleAutosave()
    },
    setTemplateName: (name) => {
      set({ templateName: name })
      scheduleAutosave()
    },

    serialize: () => {
      const s = get()
      return {
        version: TEMPLATE_VERSION,
        name: s.templateName,
        nodes: s.nodes.map((n) => ({
          id: n.id,
          sectionType: n.data.sectionType,
          position: n.position,
          config: n.data.config,
        })),
        edges: s.edges.map((e) => ({ source: e.source, target: e.target })),
      }
    },
    loadTemplate: (template) => {
      const next = templateToCanvas(template)
      bumpIdCounterFrom(next.nodes)
      set({
        templateName: template.name,
        nodes: next.nodes,
        edges: next.edges,
        selectedNodeId: null,
      })
      scheduleAutosave()
    },
    resetToSeed: () => {
      get().loadTemplate(SEED_TEMPLATE)
    },
    consumeStartupFlash: () => {
      const msg = get().startupFlash
      if (msg) set({ startupFlash: null })
      return msg
    },

    bootstrapFromBackend: async () => {
      if (bootstrapRan) return
      bootstrapRan = true
      const result = await fetchTemplateFromBackend()
      if (result.status === 'ok') {
        // Adopt server canvas as the authoritative view. Suppress the
        // autosave that loadTemplate would otherwise trigger — the just-
        // fetched template IS the server state, no need to round-trip.
        suppressAutosave = true
        try {
          const next = templateToCanvas(result.template)
          bumpIdCounterFrom(next.nodes)
          set({
            templateName: result.template.name,
            nodes: next.nodes,
            edges: next.edges,
            selectedNodeId: null,
            serverRev: result.rev,
            isOnline: true,
            startupFlash: result.migrated
              ? 'Loaded backend canvas (migrated to v3)'
              : 'Loaded canvas from backend',
          })
          // Also mirror to localStorage so a network drop leaves a sane
          // offline draft.
          saveToStorage(result.template)
        } finally {
          suppressAutosave = false
        }
        return
      }
      if (result.status === 'empty') {
        // Backend has nothing — operator's localStorage draft (if any) or
        // SEED is the seed. Will become the first PUT after any edit.
        set({ serverRev: null, isOnline: true })
        return
      }
      if (result.status === 'incompatible') {
        set({
          startupFlash:
            'Backend canvas uses a removed section type — using local draft',
          isOnline: true,
        })
        return
      }
      // network_error → keep current state (from localStorage or SEED)
      // and surface the offline state.
      set({
        isOnline: false,
        saveStatus: 'offline',
        startupFlash: `Backend unreachable — working from local draft`,
      })
    },

    resolveConflict: async (choice) => {
      const c = get().conflict
      if (c === null) return
      if (choice === 'dismiss') {
        set({ conflict: null })
        return
      }
      if (choice === 'take_theirs') {
        suppressAutosave = true
        try {
          const next = templateToCanvas(c.theirs)
          bumpIdCounterFrom(next.nodes)
          set({
            templateName: c.theirs.name,
            nodes: next.nodes,
            edges: next.edges,
            selectedNodeId: null,
            serverRev: c.theirsRev,
            conflict: null,
            saveStatus: 'saved',
          })
          saveToStorage(c.theirs)
        } finally {
          suppressAutosave = false
        }
        return
      }
      // keep_mine — force-overwrite backend with current local state.
      // ConflictDialog should have shown a 2nd confirm before this call.
      set({ saveStatus: 'saving' })
      const result = await pushTemplateToBackend(get().serialize(), '*')
      if (result.status === 'ok') {
        set({
          saveStatus: 'saved',
          serverRev: result.rev,
          conflict: null,
          isOnline: true,
        })
      } else {
        // Force-overwrite failed (network or shape) — leave conflict in
        // place so operator can retry.
        set({ saveStatus: 'saved' })
        console.error('Force overwrite failed:', result)
      }
    },
  }
})
