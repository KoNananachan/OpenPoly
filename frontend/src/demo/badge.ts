/**
 * Persistent "DEMO" marker (demo build only).
 *
 * Now that the demo is publicly reachable, a visitor needs to know at a glance
 * that the running/green pipeline and the P&L numbers are mock data, not a live
 * deployment. A small fixed pill in the (empty) center of the top nav row,
 * injected at the document level like the toast — no React, no component-tree
 * changes, gated by `__DEMO__` so it never ships in a normal build.
 */

export function mountDemoBadge(): void {
  if (typeof document === 'undefined') return

  const mount = () => {
    if (document.getElementById('demo-badge')) return
    const el = document.createElement('div')
    el.id = 'demo-badge'
    el.textContent = 'DEMO · mock data — not live'
    Object.assign(el.style, {
      position: 'fixed',
      top: '7px',
      left: '50%',
      transform: 'translateX(-50%)',
      zIndex: '99998', // below the toast (99999), above the app
      padding: '2px 10px',
      borderRadius: '9999px',
      background: 'rgba(120, 53, 15, 0.55)', // amber-900 tint
      border: '1px solid #b45309', // amber-700
      color: '#fcd34d', // amber-300
      font: '11px/1.4 ui-sans-serif, system-ui, sans-serif',
      letterSpacing: '0.02em',
      whiteSpace: 'nowrap',
      pointerEvents: 'none',
    } satisfies Partial<CSSStyleDeclaration>)
    document.body.appendChild(el)
  }

  if (document.body) mount()
  else document.addEventListener('DOMContentLoaded', mount, { once: true })
}
