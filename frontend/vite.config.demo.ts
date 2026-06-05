import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

// Demo build: produces a single double-clickable `dist-demo/index.html` that
// renders the real frontend against the in-browser mock server (src/demo).
//
// - `__DEMO__: true` flips on the fetch mock (main.tsx) + HashRouter (App.tsx).
//   The normal vite.config.ts pins it to `false`, so all of src/demo is
//   tree-shaken out of `yarn build`.
// - `base: './'` makes every asset reference relative so the file opens from
//   `file://` with no server.
// - viteSingleFile inlines JS/CSS into the HTML — inline module scripts run
//   under file:// (external module fetches would be CORS-blocked), which is
//   what makes "double-click to open" work.
export default defineConfig({
  base: './',
  define: { __DEMO__: 'true' },
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: {
    outDir: 'dist-demo',
    // Keep the bundle as one chunk; singlefile inlines it regardless.
    chunkSizeWarningLimit: 5000,
  },
})
