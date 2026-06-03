/**
 * lightweight-charts v5 custom series — a depth "band" series. Each data point
 * carries n order-book levels {bid, ask}; the renderer fills, per level, the
 * polygon between the bid edge and the ask edge across consecutive frames.
 * Deeper levels are drawn first with a fainter fill so the L1 spread band sits
 * on top. Frames missing a level break that level's polygon into segments.
 */
import {
  customSeriesDefaultOptions,
  type CustomData,
  type CustomSeriesOptions,
  type CustomSeriesPricePlotValues,
  type CustomSeriesWhitespaceData,
  type ICustomSeriesPaneRenderer,
  type ICustomSeriesPaneView,
  type PaneRendererCustomData,
  type PriceToCoordinateConverter,
  type Time,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'

export type BandLevel = { bid: number; ask: number }

export interface BandData extends CustomData<Time> {
  time: Time
  levels: BandLevel[] // index 0 = L1 (best bid/ask); deeper levels follow
}

export type BandSeriesOptions = CustomSeriesOptions

// L1 most opaque, deeper levels fainter.
const LEVEL_FILL = [
  'rgba(88,166,255,0.30)',
  'rgba(88,166,255,0.16)',
  'rgba(88,166,255,0.08)',
]

function hasLevels(d: BandData | CustomSeriesWhitespaceData<Time>): d is BandData {
  const levels = (d as BandData).levels
  return Array.isArray(levels) && levels.length > 0
}

class BandRenderer implements ICustomSeriesPaneRenderer {
  _data: PaneRendererCustomData<Time, BandData> | null = null

  setData(data: PaneRendererCustomData<Time, BandData>): void {
    this._data = data
  }

  draw(
    target: CanvasRenderingTarget2D,
    priceToY: PriceToCoordinateConverter,
  ): void {
    const data = this._data
    if (data === null || data.bars.length < 2) return
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context
      const bars = data.bars
      const maxLevels = bars.reduce(
        (m, b) => Math.max(m, b.originalData.levels?.length ?? 0),
        0,
      )
      // Deepest level first → L1 last (drawn on top).
      for (let lvl = maxLevels - 1; lvl >= 0; lvl--) {
        ctx.fillStyle = LEVEL_FILL[Math.min(lvl, LEVEL_FILL.length - 1)]
        let run: { x: number; bidY: number; askY: number }[] = []
        const flush = () => {
          if (run.length >= 2) {
            ctx.beginPath()
            ctx.moveTo(run[0].x, run[0].askY)
            for (let i = 1; i < run.length; i++) {
              ctx.lineTo(run[i].x, run[i].askY)
            }
            for (let i = run.length - 1; i >= 0; i--) {
              ctx.lineTo(run[i].x, run[i].bidY)
            }
            ctx.closePath()
            ctx.fill()
          }
          run = []
        }
        for (const bar of bars) {
          const lv = bar.originalData.levels?.[lvl]
          if (lv === undefined) {
            flush()
            continue
          }
          const bidY = priceToY(lv.bid)
          const askY = priceToY(lv.ask)
          if (bidY === null || askY === null) {
            flush()
            continue
          }
          run.push({ x: bar.x, bidY, askY })
        }
        flush()
      }
    })
  }
}

export class BandSeries
  implements ICustomSeriesPaneView<Time, BandData, BandSeriesOptions>
{
  _renderer = new BandRenderer()

  priceValueBuilder(plotRow: BandData): CustomSeriesPricePlotValues {
    if (plotRow.levels.length === 0) return [0, 0]
    const prices = plotRow.levels.flatMap((l) => [l.bid, l.ask])
    return [Math.max(...prices), Math.min(...prices)]
  }

  isWhitespace(
    d: BandData | CustomSeriesWhitespaceData<Time>,
  ): d is CustomSeriesWhitespaceData<Time> {
    return !hasLevels(d)
  }

  renderer(): ICustomSeriesPaneRenderer {
    return this._renderer
  }

  update(data: PaneRendererCustomData<Time, BandData>): void {
    this._renderer.setData(data)
  }

  defaultOptions(): BandSeriesOptions {
    return customSeriesDefaultOptions
  }
}
