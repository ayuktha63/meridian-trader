import {
  ColorType, CrosshairMode, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { chartTheme } from '../../lib/theme'
import type { Candle } from '../../lib/types'
import { useStore } from '../../store/useStore'
import type { Transform } from './geometry'

interface Props {
  candles: Candle[]
  digits: number
  showMovingAverages: boolean
  showVolume: boolean
  /** Rendered above the candles, given a live coordinate transform. */
  children?: (transform: Transform, container: HTMLDivElement) => ReactNode
  onCrosshair?: (candle: Candle | null) => void
}

function ema(values: number[], length: number): (number | undefined)[] {
  const k = 2 / (length + 1)
  const out: (number | undefined)[] = []
  let prev: number | undefined
  values.forEach((value, index) => {
    prev = index === 0 ? value : value * k + (prev as number) * (1 - k)
    out.push(index < length - 1 ? undefined : prev)
  })
  return out
}

const MA_CONFIG = [
  { length: 20, color: '#4C8DFF' },
  { length: 50, color: '#9B8CFF' },
  { length: 200, color: '#F5C453' },
]

export default function ChartCanvas({
  candles, digits, showMovingAverages, showVolume, children, onCrosshair,
}: Props) {
  const themeName = useStore((s) => s.theme)
  const theme = useMemo(() => chartTheme(), [themeName])
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const maSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const lastLoadedKeyRef = useRef<string>('')

  const [transform, setTransform] = useState<Transform | null>(null)
  const [container, setContainer] = useState<HTMLDivElement | null>(null)
  const versionRef = useRef(0)
  const recomputeRef = useRef<(() => void) | null>(null)
  const candlesRef = useRef<Candle[]>(candles)
  candlesRef.current = candles

  // Times are needed by the transform for logical-index lookups.
  const times = useMemo(() => candles.map((c) => c.time), [candles])
  const timesRef = useRef<number[]>(times)
  timesRef.current = times

  const barSeconds = useMemo(() => {
    if (times.length < 3) return 900
    // Median delta is robust against weekend gaps in FX data.
    const deltas: number[] = []
    for (let i = 1; i < Math.min(times.length, 60); i += 1) deltas.push(times[i] - times[i - 1])
    deltas.sort((a, b) => a - b)
    return deltas[Math.floor(deltas.length / 2)] || 900
  }, [times])
  const barSecondsRef = useRef(barSeconds)
  barSecondsRef.current = barSeconds

  // ── chart lifecycle ───────────────────────────────────────
  useLayoutEffect(() => {
    const element = containerRef.current
    if (!element) return

    const t = chartTheme()
    const chart = createChart(element, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: t.text,
        fontFamily: 'Inter, -apple-system, system-ui, sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: t.grid },
        horzLines: { color: t.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: t.crosshair, width: 1, style: LineStyle.Dashed,
          labelBackgroundColor: t.crosshairLabelBg,
        },
        horzLine: {
          color: t.crosshair, width: 1, style: LineStyle.Dashed,
          labelBackgroundColor: t.crosshairLabelBg,
        },
      },
      rightPriceScale: {
        borderColor: t.border,
        scaleMargins: { top: 0.08, bottom: 0.22 },
      },
      timeScale: {
        borderColor: t.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 8,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
      autoSize: false,
    })

    chartRef.current = chart

    candleSeriesRef.current = chart.addCandlestickSeries({
      upColor: t.bull,
      downColor: t.bear,
      borderUpColor: t.bull,
      borderDownColor: t.bear,
      wickUpColor: t.wickUp,
      wickDownColor: t.wickDown,
      priceLineVisible: true,
      priceLineColor: t.priceLine,
      priceLineStyle: LineStyle.Dotted,
      lastValueVisible: true,
    })

    volumeSeriesRef.current = chart.addHistogramSeries({
      priceScaleId: '',
      priceFormat: { type: 'volume' },
      color: t.volumeUp,
    })
    volumeSeriesRef.current.priceScale().applyOptions({
      scaleMargins: { top: 0.86, bottom: 0 },
    })

    maSeriesRef.current = MA_CONFIG.map(({ color }) =>
      chart.addLineSeries({
        color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      }))

    setContainer(element)

    // ── transform recomputation ─────────────────────────────
    const recompute = () => {
      const series = candleSeriesRef.current
      const currentTimes = timesRef.current
      if (!series || currentTimes.length < 2) return

      const priceScaleWidth = chart.priceScale('right').width()
      const timeScaleHeight = chart.timeScale().height()
      const width = Math.max(1, element.clientWidth - priceScaleWidth)
      const height = Math.max(1, element.clientHeight - timeScaleHeight)

      const range = chart.timeScale().getVisibleLogicalRange()
      const priceTop = series.coordinateToPrice(0)
      const priceBottom = series.coordinateToPrice(height)
      if (!range || priceTop === null || priceBottom === null) return

      const span = range.to - range.from
      if (!Number.isFinite(span) || span <= 0) return
      const pxPerBar = width / span
      const priceSpan = priceTop - priceBottom
      if (!Number.isFinite(priceSpan) || priceSpan === 0) return

      const bars = barSecondsRef.current
      const first = currentTimes[0]
      const last = currentTimes[currentTimes.length - 1]
      const lastIndex = currentTimes.length - 1

      const logicalOf = (time: number): number => {
        if (time <= first) return (time - first) / bars
        if (time >= last) return lastIndex + (time - last) / bars
        // Binary search for the bracketing bars, then interpolate.
        let lo = 0
        let hi = lastIndex
        while (hi - lo > 1) {
          const mid = (lo + hi) >> 1
          if (currentTimes[mid] <= time) lo = mid
          else hi = mid
        }
        const gap = currentTimes[hi] - currentTimes[lo] || bars
        return lo + (time - currentTimes[lo]) / gap
      }

      const timeOf = (logical: number): number => {
        if (logical <= 0) return first + logical * bars
        if (logical >= lastIndex) return last + (logical - lastIndex) * bars
        const lo = Math.floor(logical)
        const gap = (currentTimes[lo + 1] ?? currentTimes[lo] + bars) - currentTimes[lo]
        return currentTimes[lo] + (logical - lo) * gap
      }

      versionRef.current += 1
      setTransform({
        version: versionRef.current,
        width,
        height,
        timeToX: (time) => (logicalOf(time) - range.from) * pxPerBar,
        xToTime: (x) => timeOf(range.from + x / pxPerBar),
        priceToY: (price) => ((priceTop - price) / priceSpan) * height,
        yToPrice: (y) => priceTop - (y / height) * priceSpan,
      })
    }

    chart.timeScale().subscribeVisibleLogicalRangeChange(recompute)
    chart.subscribeCrosshairMove((param) => {
      if (!onCrosshair) return
      if (!param.time) {
        onCrosshair(null)
        return
      }
      const time = param.time as number
      const found = timesRef.current.findIndex((t) => t === time)
      onCrosshair(found >= 0 ? (candlesRef.current[found] ?? null) : null)
      // Price-scale width can change with the crosshair label, moving the pane.
      recompute()
    })

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({
        width: element.clientWidth,
        height: element.clientHeight,
      })
      recompute()
    })
    resizeObserver.observe(element)

    chart.applyOptions({ width: element.clientWidth, height: element.clientHeight })
    const raf = requestAnimationFrame(recompute)
    recomputeRef.current = recompute

    return () => {
      cancelAnimationFrame(raf)
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      maSeriesRef.current = []
      recomputeRef.current = null
    }
    // The chart instance is created once; data flows in through effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── data ──────────────────────────────────────────────────
  useEffect(() => {
    const series = candleSeriesRef.current
    const volume = volumeSeriesRef.current
    if (!series || !volume || candles.length === 0) return

    // Refit only when the dataset itself changed (symbol/timeframe switch),
    // never on a live tick — that would fight the trader's pan and zoom.
    const key = String(candles[0].time)
    const isNewDataset = key !== lastLoadedKeyRef.current

    const bars = candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open, high: c.high, low: c.low, close: c.close,
    }))
    series.setData(bars)

    volume.setData(candles.map((c) => ({
      time: c.time as UTCTimestamp,
      value: c.volume,
      color: c.close >= c.open ? theme.volumeUp : theme.volumeDown,
    })))

    lastLoadedKeyRef.current = key
    if (isNewDataset) chartRef.current?.timeScale().fitContent()
    recomputeRef.current?.()
  }, [candles, theme])

  // Canvas cannot read CSS variables, so the theme swap has to be pushed into
  // lightweight-charts by hand. Applying options keeps the existing viewport;
  // recreating the chart would reset pan and zoom on every toggle.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.applyOptions({
      layout: { textColor: theme.text },
      grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
      crosshair: {
        vertLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshairLabelBg },
        horzLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshairLabelBg },
      },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border },
    })
    candleSeriesRef.current?.applyOptions({
      upColor: theme.bull, downColor: theme.bear,
      borderUpColor: theme.bull, borderDownColor: theme.bear,
      wickUpColor: theme.wickUp, wickDownColor: theme.wickDown,
      priceLineColor: theme.priceLine,
    })
  }, [theme])

  useEffect(() => {
    candleSeriesRef.current?.applyOptions({
      priceFormat: { type: 'price', precision: digits, minMove: 1 / 10 ** digits },
    })
  }, [digits])

  useEffect(() => {
    volumeSeriesRef.current?.applyOptions({ visible: showVolume })
  }, [showVolume])

  useEffect(() => {
    const closes = candles.map((c) => c.close)
    maSeriesRef.current.forEach((series, index) => {
      const { length } = MA_CONFIG[index]
      if (!showMovingAverages || closes.length < length) {
        series.setData([])
        return
      }
      const values = ema(closes, length)
      series.setData(
        candles
          .map((candle, i) => ({ time: candle.time as UTCTimestamp, value: values[i] }))
          .filter((point): point is { time: UTCTimestamp; value: number } =>
            point.value !== undefined),
      )
    })
  }, [candles, showMovingAverages])

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      {transform && container && children?.(transform, container)}
    </div>
  )
}
