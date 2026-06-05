/**
 * `__DEMO__` is a compile-time constant injected via Vite `define`:
 *   - vite.config.ts       → false (normal dev/build; src/demo tree-shaken)
 *   - vite.config.demo.ts  → true  (demo single-file build)
 * Statically foldable, so `if (__DEMO__)` branches dead-code-eliminate.
 */
declare const __DEMO__: boolean
