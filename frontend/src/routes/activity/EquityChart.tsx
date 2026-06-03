/**
 * Equity curve — a lightweight-charts baseline series with the baseline at 0
 * (green above, red below). The TradingView attributionLogo is left on, which
 * satisfies the library's Apache-2.0 attribution requirement.
 *
 * lightweight-charts needs strictly-increasing whole-second timestamps, so the
 * points are de-duplicated by Math.floor(ts), keeping the last in each second.
 *
 * v5 API: chart.addSeries(BaselineSeries, …). If a v4 build is installed,
 * swap to chart.addBaselineSeries(…) and remove the BaselineSeries import.
 */
import { useEffect, useRef } from 'react'
import {
  BaselineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { EquityPoint } from './equityClient'

function formatUsd(n: number): string {
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toFixed(2)}`
}

export function EquityChart({ points }: { points: EquityPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const bySecondRef = useRef<Map<number, EquityPoint>>(new Map())

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
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#3fb950',
      topFillColor1: 'rgba(63,185,80,0.28)',
      topFillColor2: 'rgba(63,185,80,0.04)',
      bottomLineColor: '#f85149',
      bottomFillColor1: 'rgba(248,81,73,0.04)',
      bottomFillColor2: 'rgba(248,81,73,0.28)',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    chart.subscribeCrosshairMove((param: MouseEventParams) => {
      const tip = tooltipRef.current
      if (!tip) return
      if (param.time === undefined || param.point === undefined) {
        tip.style.display = 'none'
        return
      }
      const pt = bySecondRef.current.get(param.time as number)
      if (!pt) {
        tip.style.display = 'none'
        return
      }
      tip.style.display = 'block'
      tip.style.left = `${param.point.x + 12}px`
      tip.style.top = `${param.point.y + 12}px`
      tip.innerHTML =
        `<div>Equity <b>${formatUsd(pt.equity)}</b></div>` +
        `<div style="color:#8b949e">realized ${formatUsd(pt.realized)} · ` +
        `unrealized ${formatUsd(pt.unrealized)}</div>`
    })
    chartRef.current = chart
    seriesRef.current = series
    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  // Push data whenever points change.
  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    const bySecond = new Map<number, EquityPoint>()
    for (const p of points) {
      bySecond.set(Math.floor(p.ts), p)
    }
    bySecondRef.current = bySecond
    const data = [...bySecond.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([time, p]) => ({ time: time as UTCTimestamp, value: p.equity }))
    series.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [points])

  return (
    <div className="relative h-64 w-full">
      <div ref={containerRef} className="h-full w-full" />
      <div
        ref={tooltipRef}
        className="pointer-events-none absolute z-10 hidden rounded border border-neutral-700 bg-neutral-900/95 px-2 py-1 text-[11px] text-neutral-200"
      />
    </div>
  )
}
