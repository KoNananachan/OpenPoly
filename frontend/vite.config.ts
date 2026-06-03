import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Allow overriding the backend the dev server proxies /api to — used when
// running the local UI against a remote backend (e.g. a remote VPS via SSH
// tunnel). Default keeps the original local-only behavior.
//   VITE_API_PROXY_TARGET=http://127.0.0.1:18000 yarn dev
const API_PROXY_TARGET =
  process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Pin the port: localStorage (where the canvas template lives) is keyed by
    // origin, so a drifting port silently orphans the saved draft. strictPort
    // fails loudly on a stale instance instead of bumping to 5174/5175/...
    port: 5173,
    strictPort: true,
    proxy: {
      // Use IPv4 explicitly: `localhost` may resolve to IPv6 first and collide
      // with other services (e.g. Docker Desktop binding * on :8000).
      '/api': API_PROXY_TARGET,
    },
  },
})
