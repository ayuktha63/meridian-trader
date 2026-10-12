import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useRef, useState } from 'react'
import { fmtPct, fmtPriceCcy } from '../../lib/format'
import { useActiveInstrument, useStore } from '../../store/useStore'
import LiveHistoryPanel from '../panels/LiveHistoryPanel'
import LivePositionsPanel from '../panels/LivePositionsPanel'
import { Spinner, Toggle } from '../ui'
import AutoStatus from './AutoStatus'
import AutoTradeBar from './AutoTradeBar'
import Screener from './Screener'
import TradingModeControl from './TradingModeControl'

const TIMEFRAME_ORDER = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w']

export default function TopBar({ onOpenSettings }: { onOpenSettings: () => void }) {
  const symbols = useStore((s) => s.symbols)
  const symbol = useStore((s) => s.symbol)
  const timeframe = useStore((s) => s.timeframe)
  const setSymbol = useStore((s) => s.setSymbol)
  const setTimeframe = useStore((s) => s.setTimeframe)
  const quote = useStore((s) => s.quote)
  const quotes = useStore((s) => s.quotes)
  const connection = useStore((s) => s.connection)
  const pending = useStore((s) => s.analysisPending)
  const autoAnalysis = useStore((s) => s.autoAnalysis)
  const toggleAutoAnalysis = useStore((s) => s.toggleAutoAnalysis)
  const showOverlays = useStore((s) => s.showOverlays)
  const toggleOverlays = useStore((s) => s.toggleOverlays)
  const requestAnalysis = useStore((s) => s.requestAnalysis)
  const theme = useStore((s) => s.theme)
  const toggleTheme = useStore((s) => s.toggleTheme)
  const aiEnabled = useStore((s) => s.aiEnabled)
  const aiModel = useStore((s) => s.aiModel)

  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const pickerRef = useRef<HTMLDivElement>(null)

  // Matches the ticker, the display name and the venue, so "gold", "MCX" and
  // "XAU" all find the same instrument.
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return symbols
    return symbols.filter((item) =>
      item.symbol.toLowerCase().includes(needle)
      || item.display.toLowerCase().includes(needle)
      || item.venue.toLowerCase().includes(needle)
      || item.asset_class.toLowerCase().includes(needle))
  }, [symbols, query])

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) {
        setPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const live = quotes[symbol] ?? quote
  const positive = (live?.change_pct ?? 0) >= 0

  return (
    // `relative z-50` is load-bearing. `.glass` applies backdrop-blur, and
    // backdrop-filter creates a stacking context — which traps the symbol
    // menu's own z-index inside this header. Without a positive z-index here
    // the header sits below <main> in paint order and the chart canvases
    // (lightweight-charts uses z-index 1 and 2) cover the open dropdown.
    <header className="glass rounded-xl relative z-50 flex items-center gap-3 px-3 py-2 shrink-0">
      {/* Brand */}
      <div className="flex items-center gap-2 pr-3 border-r border-hairline">
        <div className="h-6 w-6 rounded-md bg-gradient-to-br from-accent to-violet
                        grid place-items-center text-[10px] font-bold text-white">
          M
        </div>
        <div className="leading-none">
          <div className="text-xs font-semibold tracking-tight">Meridian</div>
          <div className="text-[9px] text-faint">analysis only</div>
        </div>
      </div>

      {/* Symbol picker */}
      <div className="relative" ref={pickerRef}>
        <button
          type="button"
          onClick={() => { setQuery(''); setPickerOpen((prev) => !prev) }}
          title="Change instrument — forex, crypto, NSE stocks, futures, commodities"
          aria-label="Select instrument"
          aria-expanded={pickerOpen}
          className="flex items-center gap-2 rounded-lg border border-hairline bg-tint/[0.03]
                     px-2.5 py-1.5 hover:bg-tint/[0.06] transition-colors"
        >
          <span className="text-sm font-semibold">{instrument?.display ?? symbol}</span>
          <svg viewBox="0 0 24 24" className="h-3 w-3 text-faint" fill="none"
               stroke="currentColor" strokeWidth={2}>
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>

        <AnimatePresence>
          {pickerOpen && (
            <motion.div
              initial={{ opacity: 0, y: -6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6, scale: 0.98 }}
              transition={{ duration: 0.13 }}
              // Nearly opaque rather than the usual glass: this menu floats over
              // a dense chart, and at 80% the candle and zone labels behind it
              // bled through the instrument rows.
              className="absolute left-0 top-full z-50 mt-1.5 w-80 rounded-xl
                         flex flex-col max-h-[70vh] bg-panel/[0.98] backdrop-blur-xl
                         border border-edge shadow-[0_24px_60px_-20px_rgb(var(--c-void)/0.95)]"
            >
              {/* With ~150 instruments the list is no longer scannable, so the
                  filter is the primary way in. */}
              <div className="p-1.5 pb-1 shrink-0">
                <input
                  autoFocus
                  className="input"
                  placeholder="Search — RELIANCE, NIFTY, BTC, gold…"
                  aria-label="Search instruments"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>

              <div className="scroll-y p-1.5 pt-0.5 min-h-0">
              {filtered.length === 0 && (
                <p className="px-2 py-3 text-2xs text-faint">
                  Nothing matches “{query}”.
                </p>
              )}
              {filtered.map((item) => {
                const itemQuote = quotes[item.symbol]
                const up = (itemQuote?.change_pct ?? 0) >= 0
                return (
                  <button
                    key={item.symbol}
                    type="button"
                    onClick={() => {
                      void setSymbol(item.symbol)
                      setPickerOpen(false)
                    }}
                    className={`w-full flex items-center gap-2 rounded-lg px-2 py-1.5
                                transition-colors text-left
                                ${item.symbol === symbol
                                  ? 'bg-accent/15' : 'hover:bg-tint/[0.05]'}`}
                  >
                    <span className="flex-1 min-w-0">
                      <span className="block text-2xs font-medium text-ink truncate">
                        {item.display}
                      </span>
                      <span className="flex items-center gap-1 mt-0.5">
                        <span className={`text-[9px] px-1 rounded ${item.live
                          ? 'bg-bull/15 text-bull' : 'bg-tint/[0.06] text-faint'}`}>
                          {item.live ? 'LIVE' : 'SIM'}
                        </span>
                        <span className="text-[9px] px-1 rounded bg-tint/[0.06] text-faint">
                          {item.venue === 'otc' ? 'OTC' : item.venue}
                        </span>
                        {!item.tradable_in_india && (
                          <span className="text-[9px] px-1 rounded bg-warn/15 text-warn"
                                title={item.note}>not for IN</span>
                        )}
                      </span>
                    </span>
                    {itemQuote && (
                      <span className="text-right shrink-0">
                        <span className="block text-2xs tnum text-ink">
                          {fmtPriceCcy(itemQuote.last, item.digits, item.quote_currency)}
                        </span>
                        <span className={`block text-[10px] tnum ${up ? 'text-bull' : 'text-bear'}`}>
                          {fmtPct(itemQuote.change_pct)}
                        </span>
                      </span>
                    )}
                  </button>
                )
              })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Live price */}
      <div className="flex items-baseline gap-2 min-w-[190px]">
        <AnimatePresence mode="popLayout">
          <motion.span
            key={live?.last ?? 0}
            initial={{ opacity: 0.4 }}
            animate={{ opacity: 1 }}
            className={`text-lg font-semibold tnum ${positive ? 'text-bull' : 'text-bear'}`}
          >
            {fmtPriceCcy(live?.last, digits, instrument?.quote_currency)}
          </motion.span>
        </AnimatePresence>
        <span className={`text-2xs tnum ${positive ? 'text-bull' : 'text-bear'}`}>
          {fmtPct(live?.change_pct)}
        </span>
        {live && (
          <span className="text-2xs text-faint tnum">
            {live.spread_pips.toFixed(1)}p spread
          </span>
        )}
        <span
          title={instrument?.live
            ? `Real market data from ${instrument.provider}`
            : 'Simulated data — realistic, but not a real market'}
          className={`text-[9px] px-1.5 py-0.5 rounded font-semibold tracking-wide
                      ${instrument?.live
                        ? 'bg-bull/15 text-bull' : 'bg-warn/15 text-warn'}`}
        >
          {instrument?.live ? 'LIVE' : 'SIM'}
        </span>
      </div>

      {/* Timeframes */}
      <div className="flex items-center gap-0.5 rounded-lg border border-hairline
                      bg-tint/[0.02] p-0.5">
        {TIMEFRAME_ORDER.map((code) => (
          <button
            key={code}
            type="button"
            onClick={() => void setTimeframe(code)}
              title={`Switch the chart to ${code} candles`}
              aria-pressed={timeframe === code}
            className={`relative px-2 py-1 rounded-md text-2xs font-medium transition-colors
                        ${code === timeframe ? 'text-ink' : 'text-faint hover:text-muted'}`}
          >
            {code === timeframe && (
              <motion.span
                layoutId="tf-active"
                className="absolute inset-0 rounded-md bg-accent/20 border border-accent/40"
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              />
            )}
            <span className="relative uppercase">{code}</span>
          </button>
        ))}
      </div>

      <div className="flex-1" />

      {/* Controls */}
      <div className="flex items-center gap-3">
        <Toggle checked={showOverlays} onChange={toggleOverlays}
                label="Overlays" hint="Draw the AI's levels on the chart" />
        <div className="flex flex-col items-start gap-0.5">
          <Toggle checked={autoAnalysis} onChange={toggleAutoAnalysis}
                  label="Auto" hint="Re-analyse automatically on every bar close and on a timer" />
          <AutoStatus />
        </div>

        <button type="button" className="btn btn-primary" onClick={requestAnalysis}
                disabled={pending}
                title="Run a full analysis now — indicators, structure, strategies and Claude">
          {pending ? <Spinner /> : (
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"
                 stroke="currentColor" strokeWidth={2} strokeLinecap="round">
              <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
            </svg>
          )}
          Analyse
        </button>

        <Screener />

        {/* Each is self-contained — its own trigger button and its own
            full-size popup (see LivePositionsPanel/LiveHistoryPanel) — not
            a tab in the right-rail strip, which already scrolls at 8 tabs
            in a 360px rail with its scrollbar hidden. Real money deserves a
            screen big enough to read at a glance, not a sliver. */}
        <LivePositionsPanel />
        <LiveHistoryPanel />

        {/* Grouped, not two disconnected floating badges — AutoTradeBar
            answers "is the demo auto-trader armed", TradingModeControl
            answers "which mode is the whole app in". Different questions,
            each keeps its own semantic color, but tight spacing + a divider
            reads as one status area instead of scattered chrome. */}
        <div className="flex items-center gap-1.5">
          <AutoTradeBar />
          <span className="h-4 w-px bg-hairline" />
          <TradingModeControl />
        </div>

        <button
          type="button"
          onClick={toggleTheme}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          title={theme === 'dark'
            ? 'Switch to light mode — easier in a bright room'
            : 'Switch to dark mode — easier on the eyes over a long session'}
          className="h-7 w-7 rounded-lg grid place-items-center text-faint
                     hover:text-ink hover:bg-tint/[0.06] transition-colors"
        >
          {theme === 'dark' ? (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
                 strokeWidth={1.7} strokeLinecap="round">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
                 strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
            </svg>
          )}
        </button>

        <button
          type="button"
          onClick={onOpenSettings}
          aria-label="Settings"
          title="Settings — Claude API key, live data provider, Zerodha, risk"
          className="h-7 w-7 rounded-lg grid place-items-center text-faint
                     hover:text-ink hover:bg-tint/[0.06] transition-colors"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
               strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>

        <div className="flex items-center gap-1.5 pl-2 border-l border-hairline"
             title={`${connection} · analyst: ${aiModel}`}>
          <span className="relative flex h-2 w-2">
            {connection === 'open' && (
              <span className="absolute inline-flex h-full w-full rounded-full
                               bg-bull opacity-70 animate-pulse-ring" />
            )}
            <span className={`relative inline-flex h-2 w-2 rounded-full ${
              connection === 'open' ? 'bg-bull'
                : connection === 'closed' ? 'bg-bear' : 'bg-warn'}`} />
          </span>
          <span className="text-[10px] text-faint">
            {aiEnabled ? 'Claude' : 'Rules'}
          </span>
        </div>
      </div>
    </header>
  )
}
