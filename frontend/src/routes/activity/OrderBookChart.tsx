/**
 * Order book chart — mid line + n nested bid/ask spread bands over a token's
 * holding window. Native lightweight-charts Line series for mid; the custom
 * BandSeries (./bandSeries) for the depth bands. Entry/exit markers sit on the
 * mid series. A depth selector (1-3) controls how many bands render. Hovering
 * shows that frame's full depth ladder (price + size).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { BandSeries, type BandData } from './bandSeries'
import type { OrderBookSnapshot } from './orderBookClient'

export type OrderBookChartProps = {
  snapshots: OrderBookSnapshot[]
  entry: { ts: number; price: number } | null
  exit: { ts: number; price: number } | null
}

type MidPoint = { time: UTCTimestamp; value: number }

type Derived = {
  mid: MidPoint[]
  band: BandData[]
  bySecond: Map<number, OrderBookSnapshot>
}

function derive(snapshots: OrderBookSnapshot[], depth: number): Derived {
  // Dedupe by whole second (lightweight-charts needs strictly-increasing
  // integer time); keep the last snapshot in each second.
  const bySecond = new Map<number, OrderBookSnapshot>()
  for (const s of snapshots) bySecond.set(Math.floor(s.recorded_at), s)
  const ordered = [...bySecond.entries()].sort((a, b) => a[0] - b[0])
  const mid: MidPoint[] = []
  const band: BandData[] = []
  for (const [sec, s] of ordered) {
    const bestBid = s.bids[0]?.[0]
    const bestAsk = s.asks[0]?.[0]
    if (bestBid === undefined || bestAsk === undefined) continue
    const time = sec as UTCTimestamp
    mid.push({ time, value: (bestBid + bestAsk) / 2 })
    const levels = []
    for (let i = 0; i < depth; i++) {
      const b = s.bids[i]?.[0]
      const a = s.asks[i]?.[0]
      if (b === undefined || a === undefined) break
      levels.push({ bid: b, ask: a })
    }
    band.push({ time, levels })
  }
  return { mid, band, bySecond }
}

function formatLadder(s: OrderBookSnapshot): string {
  const row = (side: string, lv: [number, number] | undefined, i: number) =>
    lv ? `${side}${i + 1} ${lv[0].toFixed(3)} × ${lv[1].toFixed(0)}` : ''
  const asks = [2, 1, 0].map((i) => row('ask', s.asks[i], i)).filter(Boolean)
  const bids = [0, 1, 2].map((i) => row('bid', s.bids[i], i)).filter(Boolean)
  return [...asks, ...bids].join('\n')
}

export function OrderBookChart({ snapshots, entry, exit }: OrderBookChartProps) {
  const [depth, setDepth] = useState(1)
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const midRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bandRef = useRef<ISeriesApi<'Custom'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const bySecondRef = useRef<Map<number, OrderBookSnapshot>>(new Map())

  const { mid, band, bySecond } = useMemo(
    () => derive(snapshots, depth),
    [snapshots, depth],
  )

  // Create the chart once.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8b949e',
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: '#1f242c' },
        horzLines: { color: '#1f242c' },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: '#30363d' },
    })
    const bandSeries = chart.addCustomSeries(new BandSeries(), {})
    const midSeries = chart.addSeries(LineSeries, {
      color: '#58a6ff',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
    })
    const markers = createSeriesMarkers(midSeries, [])
    chart.subscribeCrosshairMove((param: MouseEventParams) => {
      const tip = tooltipRef.current
      if (!tip) return
      if (param.time === undefined || param.point === undefined) {
        tip.style.display = 'none'
        return
      }
      const snap = bySecondRef.current.get(param.time as number)
      if (!snap) {
        tip.style.display = 'none'
        return
      }
      tip.style.display = 'block'
      tip.style.left = `${param.point.x + 14}px`
      tip.style.top = `${param.point.y + 14}px`
      tip.textContent = formatLadder(snap)
    })
    chartRef.current = chart
    midRef.current = midSeries
    bandRef.current = bandSeries as ISeriesApi<'Custom'>
    markersRef.current = markers
    return () => {
      chart.remove()
      chartRef.current = null
      midRef.current = null
      bandRef.current = null
      markersRef.current = null
    }
  }, [])

  // Push data whenever the derived series change.
  useEffect(() => {
    bySecondRef.current = bySecond
    if (!midRef.current || !bandRef.current) return
    bandRef.current.setData(band as BandData[])
    midRef.current.setData(mid)
    const markers: SeriesMarker<Time>[] = []
    if (entry) {
      markers.push({
        time: Math.floor(entry.ts) as UTCTimestamp,
        position: 'belowBar',
        color: '#3fb950',
        shape: 'arrowUp',
        text: `entry ${entry.price.toFixed(3)}`,
      })
    }
    if (exit) {
      markers.push({
        time: Math.floor(exit.ts) as UTCTimestamp,
        position: 'aboveBar',
        color: '#f85149',
        shape: 'arrowDown',
        text: `exit ${exit.price.toFixed(3)}`,
      })
    }
    markersRef.current?.setMarkers(markers)
    chartRef.current?.timeScale().fitContent()
  }, [mid, band, bySecond, entry, exit])

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wide text-neutral-500">
          Order book · mid + spread band
        </span>
        <div className="flex items-center gap-1 text-[10px] text-neutral-500">
          <span>depth</span>
          {[1, 2, 3].map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDepth(d)}
              className={`rounded px-1.5 py-0.5 ${
                depth === d
                  ? 'bg-blue-600 text-white'
                  : 'border border-neutral-700 text-neutral-400'
              }`}
            >
              {d}
            </button>
          ))}
        </div>
      </div>
      <div className="relative h-72 w-full">
        <div ref={containerRef} className="h-full w-full" />
        <div
          ref={tooltipRef}
          className="pointer-events-none absolute z-10 hidden whitespace-pre rounded border border-neutral-700 bg-neutral-900/95 px-2 py-1 font-mono text-[10px] text-neutral-200"
        />
      </div>
    </div>
  )
}
