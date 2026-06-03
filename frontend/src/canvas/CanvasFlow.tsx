import {
  Background,
  ConnectionLineType,
  ControlButton,
  Controls,
  MarkerType,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  useCallback,
  useMemo,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import type { SectionType } from '../sections/types'
import { isValidConnection } from './edgeRules'
import { SectionNode } from './SectionNode'
import { SectionPicker } from './SectionPicker'
import { useCanvasStore } from './store'

const nodeTypes = { section: SectionNode }

// Edge palette. Every edge — pipeline flow and write-to-DB alike — is the same
// thin, dimmed step line so connections recede behind the nodes; selection
// echoes the indigo node-selected border.
const EDGE_STROKE_PIPELINE = '#52525b' // neutral-600 — dimmed, low visual weight
const EDGE_STROKE_SELECTED = '#818cf8' // indigo-400
const EDGE_STROKE_WIDTH = 1.5

type PickerTrigger = {
  anchor: { x: number; y: number }
  spawn: { x: number; y: number }
}

function FlowInner() {
  const nodes = useCanvasStore((s) => s.nodes)
  const edges = useCanvasStore((s) => s.edges)
  const onNodesChange = useCanvasStore((s) => s.onNodesChange)
  const onEdgesChange = useCanvasStore((s) => s.onEdgesChange)
  const onConnect = useCanvasStore((s) => s.onConnect)
  const addSectionNode = useCanvasStore((s) => s.addSectionNode)
  const setSelectedNodeId = useCanvasStore((s) => s.setSelectedNodeId)
  const { screenToFlowPosition } = useReactFlow()
  const [trigger, setTrigger] = useState<PickerTrigger | null>(null)

  const disabledTypes = useMemo(
    () => new Set(nodes.map((n) => n.data.sectionType)),
    [nodes],
  )

  const validate = useCallback(
    (conn: Connection | Edge) => isValidConnection(conn, nodes, edges),
    [nodes, edges],
  )

  // Decorate edges for readability — derived, never serialized (store edges
  // stay {source,target}). Every edge, write-to-DB included, is the same thin
  // dimmed step (smoothstep) line with a small arrowhead so direction is
  // explicit but the wiring recedes; a selected edge picks up the same indigo
  // as a selected node.
  const decoratedEdges = useMemo(() => {
    return edges.map((e) => {
      const stroke = e.selected ? EDGE_STROKE_SELECTED : EDGE_STROKE_PIPELINE
      return {
        ...e,
        type: 'smoothstep',
        style: { stroke, strokeWidth: EDGE_STROKE_WIDTH },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 12,
          height: 12,
          color: stroke,
        },
      }
    })
  }, [edges])

  const openPickerFromButton = useCallback(
    (e: ReactMouseEvent<HTMLButtonElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      setTrigger({
        anchor: { x: rect.left, y: rect.top },
        spawn: { x: window.innerWidth / 2, y: window.innerHeight / 2 },
      })
    },
    [],
  )

  const onPaneContextMenu = useCallback(
    (e: ReactMouseEvent | MouseEvent) => {
      e.preventDefault()
      const point = { x: e.clientX, y: e.clientY }
      setTrigger({ anchor: point, spawn: point })
    },
    [],
  )

  const onPick = useCallback(
    (type: SectionType) => {
      if (!trigger) return
      addSectionNode(type, screenToFlowPosition(trigger.spawn))
      setTrigger(null)
    },
    [trigger, screenToFlowPosition, addSectionNode],
  )

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={decoratedEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={validate}
        onNodeClick={(_, n) => setSelectedNodeId(n.id)}
        onPaneClick={() => setSelectedNodeId(null)}
        onPaneContextMenu={onPaneContextMenu}
        colorMode="dark"
        connectionLineType={ConnectionLineType.SmoothStep}
        connectionLineStyle={{
          stroke: EDGE_STROKE_PIPELINE,
          strokeWidth: EDGE_STROKE_WIDTH,
        }}
        fitView
        fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} />
        <Controls>
          <ControlButton
            onClick={openPickerFromButton}
            title="Add section"
            aria-label="Add section"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M8 3v10M3 8h10"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </ControlButton>
        </Controls>
        {nodes.length === 0 && (
          <Panel position="top-center" className="!pointer-events-none">
            <div className="mt-12 rounded border border-dashed border-neutral-700 bg-neutral-900/60 px-4 py-3 text-center text-sm text-neutral-400">
              Right-click anywhere or use + to add a section.
            </div>
          </Panel>
        )}
      </ReactFlow>
      <SectionPicker
        open={!!trigger}
        anchor={trigger?.anchor ?? null}
        disabledTypes={disabledTypes}
        onPick={onPick}
        onClose={() => setTrigger(null)}
      />
    </div>
  )
}

export function CanvasFlow() {
  return (
    <ReactFlowProvider>
      <FlowInner />
    </ReactFlowProvider>
  )
}
