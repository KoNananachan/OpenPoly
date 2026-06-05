/**
 * Demo server installer (M1).
 *
 * Swaps `window.fetch` for the mock dispatcher. Called once from `main.tsx`
 * behind the `import.meta.env.VITE_DEMO` guard, so this whole module — and the
 * fixtures it pulls in — is tree-shaken out of the normal `yarn build`.
 *
 * Idempotent: a double call (e.g. across a Vite HMR boundary) is a no-op.
 */
import { createMockFetch } from './mockServer'
import { routes } from './fixtures'
import { showDemoToast } from './toast'
import { mountDemoBadge } from './badge'

let installed = false

/**
 * A side-effect endpoint (start/stop/close/save/test) was intercepted — tell
 * the user the click was a no-op instead of letting the UI fake success.
 */
function onMutation(): void {
  showDemoToast()
}

export function installDemoServer(): void {
  if (installed || typeof window === 'undefined') return
  installed = true

  const realFetch = window.fetch.bind(window)
  window.fetch = createMockFetch(routes, {
    passthrough: realFetch,
    onMutation,
  })

  mountDemoBadge()

  console.info(
    `[demo] mock server installed — ${routes.length} route(s); /api/* is served from fixtures`,
  )
}
