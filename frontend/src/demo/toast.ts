/**
 * Demo-mode toast (M4).
 *
 * A single, self-contained DOM pill injected at the document level — no React,
 * no component-tree changes. Fired from install.ts's `onMutation` hook when a
 * side-effect endpoint (start/stop/close/save/test) is intercepted, so the
 * operator learns the click was a no-op without the UI faking success.
 *
 * One reused element: rapid mutations (e.g. Pause stops both sources → two
 * hits) refresh the same toast's timer instead of stacking duplicates.
 */

const VISIBLE_MS = 2500
const DEFAULT_MESSAGE = 'Demo mode — this action isn’t real'

let el: HTMLDivElement | null = null
let hideTimer: ReturnType<typeof setTimeout> | null = null

function ensureElement(): HTMLDivElement {
  if (el) return el
  const node = document.createElement('div')
  node.setAttribute('role', 'status')
  Object.assign(node.style, {
    position: 'fixed',
    bottom: '24px',
    left: '50%',
    transform: 'translate(-50%, 8px)',
    zIndex: '99999',
    maxWidth: '90vw',
    padding: '8px 14px',
    borderRadius: '8px',
    background: '#262626', // neutral-800, matches the app chrome
    border: '1px solid #404040', // neutral-700
    color: '#e5e5e5', // neutral-200
    font: '12px/1.4 ui-sans-serif, system-ui, sans-serif',
    boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
    opacity: '0',
    transition: 'opacity 160ms ease, transform 160ms ease',
    pointerEvents: 'none',
  } satisfies Partial<CSSStyleDeclaration>)
  document.body.appendChild(node)
  el = node
  return node
}

export function showDemoToast(message: string = DEFAULT_MESSAGE): void {
  if (typeof document === 'undefined') return
  const node = ensureElement()
  node.textContent = message
  // Bump onto the next frame so the transition runs even on the first show.
  requestAnimationFrame(() => {
    node.style.opacity = '1'
    node.style.transform = 'translate(-50%, 0)'
  })
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = setTimeout(() => {
    node.style.opacity = '0'
    node.style.transform = 'translate(-50%, 8px)'
  }, VISIBLE_MS)
}
