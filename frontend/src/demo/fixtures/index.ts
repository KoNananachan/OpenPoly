/**
 * Demo route registry.
 *
 * The aggregate list of mock routes the demo server serves. M1 ships it empty;
 * fixture modules add their routes here:
 *   - M2 (canvas)   → ./canvas
 *   - M3 (activity) → ./activity
 * Each module exports a `MockRoute[]` that gets spread below.
 */
import type { MockRoute } from '../mockServer'
import { canvasRoutes } from './canvas'
import { activityRoutes } from './activity'

export const routes: MockRoute[] = [...canvasRoutes, ...activityRoutes]
