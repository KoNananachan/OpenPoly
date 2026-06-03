import { CanvasFlow } from '../canvas/CanvasFlow'
import { CanvasTopBar } from '../canvas/CanvasTopBar'
import { SectionInspector } from '../canvas/SectionInspector'
import { StrategyStatusBar } from '../canvas/StrategyStatusBar'
import { usePageTitle } from '../lib/usePageTitle'

export function CanvasPage() {
  usePageTitle('Strategy')
  return (
    <div className="h-full flex flex-col">
      <CanvasTopBar />
      <StrategyStatusBar />
      <div className="relative flex-1 min-h-0">
        <CanvasFlow />
        <SectionInspector />
      </div>
    </div>
  )
}
