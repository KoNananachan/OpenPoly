import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { SECTION_ORDER, TYPE_DISPLAY } from '../sections/catalog'
import type { SectionType } from '../sections/types'

type Anchor = { x: number; y: number }

type Props = {
  open: boolean
  anchor: Anchor | null
  disabledTypes?: ReadonlySet<SectionType>
  onPick: (type: SectionType) => void
  onClose: () => void
}

const MENU_WIDTH = 220
const VIEWPORT_PADDING = 8

export function SectionPicker({
  open,
  anchor,
  disabledTypes,
  onPick,
  onClose,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    if (!open || !anchor || !ref.current) {
      setPosition(null)
      return
    }
    const rect = ref.current.getBoundingClientRect()
    const w = rect.width || MENU_WIDTH
    const h = rect.height
    const vw = window.innerWidth
    const vh = window.innerHeight
    let left = anchor.x
    let top = anchor.y
    if (left + w + VIEWPORT_PADDING > vw) left = vw - w - VIEWPORT_PADDING
    // Flip above the anchor when it would overflow bottom.
    if (top + h + VIEWPORT_PADDING > vh) top = anchor.y - h
    if (left < VIEWPORT_PADDING) left = VIEWPORT_PADDING
    if (top < VIEWPORT_PADDING) top = VIEWPORT_PADDING
    setPosition({ left, top })
  }, [open, anchor])

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      const el = ref.current
      if (el && e.target instanceof Node && el.contains(e.target)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    // Use capture so React Flow's pane mousedown (which stops propagation
    // for its pan gesture) doesn't swallow our outside-click close.
    document.addEventListener('mousedown', onMouseDown, true)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onMouseDown, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  if (!open || !anchor) return null

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="Add section"
      style={{
        position: 'fixed',
        left: position?.left ?? anchor.x,
        top: position?.top ?? anchor.y,
        width: MENU_WIDTH,
        // Pre-measure: hide first paint to avoid flicker; reveal after clamp.
        visibility: position ? 'visible' : 'hidden',
      }}
      className="z-30 rounded-md border border-neutral-800 bg-neutral-950 shadow-xl py-1"
    >
      <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-neutral-500">
        Add section
      </div>
      {SECTION_ORDER.map((type) => {
        const display = TYPE_DISPLAY[type]
        const disabled = disabledTypes?.has(type) ?? false
        return (
          <button
            key={type}
            type="button"
            role="menuitem"
            disabled={disabled}
            title={disabled ? 'Already on canvas (one per type)' : display.description}
            onClick={() => {
              if (disabled) return
              onPick(type)
              onClose()
            }}
            className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-left text-sm transition-colors ${
              disabled
                ? 'text-neutral-600 cursor-not-allowed'
                : 'text-neutral-200 cursor-pointer hover:bg-neutral-800/80'
            }`}
          >
            <span className="flex flex-col min-w-0">
              <span className="leading-tight">{display.label}</span>
              <code className="text-[10px] text-neutral-500">{type}</code>
            </span>
            {disabled && (
              <span className="text-[10px] text-neutral-500 shrink-0">
                Already placed
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
