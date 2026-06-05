import {
  BrowserRouter,
  HashRouter,
  Navigate,
  Route,
  Routes,
} from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { CanvasPage } from './routes/CanvasPage'
import { ActivityPage } from './routes/ActivityPage'
import { OverviewTab } from './routes/activity/OverviewTab'
import { PositionsTab } from './routes/activity/PositionsTab'
import { NewsTab } from './routes/activity/NewsTab'
import { PositionDetail } from './routes/activity/PositionDetail'

// Demo opens from file:// where path-based routing has no server to fall back
// on — HashRouter keeps every route resolvable (index.html#/strategy). Normal
// builds pin __DEMO__ to false, so this folds to BrowserRouter.
const Router = __DEMO__ ? HashRouter : BrowserRouter

function App() {
  return (
    <Router>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/strategy" replace />} />
          <Route path="strategy" element={<CanvasPage />} />
          {/* "canvas" was the earlier name for this page; redirect stale links. */}
          <Route path="canvas" element={<Navigate to="/strategy" replace />} />
          <Route path="activity" element={<ActivityPage />}>
            <Route index element={<Navigate to="/activity/overview" replace />} />
            <Route path="overview" element={<OverviewTab />} />
            <Route path="positions" element={<PositionsTab />} />
            <Route path="positions/:positionId" element={<PositionDetail />} />
            <Route path="news" element={<NewsTab />} />
          </Route>
          {/* "runs" / "portfolio" were earlier names for this tab; redirect
              any stale link. */}
          <Route path="runs" element={<Navigate to="/activity/overview" replace />} />
          <Route path="portfolio" element={<Navigate to="/activity/overview" replace />} />
          {/* Setting page removed (v9 / SK2) — secrets are managed from the
              Strategy page "Keys" drawer. Any stale link falls back there. */}
          <Route path="*" element={<Navigate to="/strategy" replace />} />
        </Route>
      </Routes>
    </Router>
  )
}

export default App
