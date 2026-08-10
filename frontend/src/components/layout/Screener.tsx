import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { fmtPriceCcy } from '../../lib/format'
import type { ScreenerRow } from '../../lib/types'
import { useStore } from '../../store/useStore'
import { Spinner } from '../ui'

/**
 * "What's actually worth trading right now" — one click, every instrument,
 * sorted by confidence, instead of opening each chart in turn. This is the
 * same rule-engine verdict the Trade panel would show for that symbol, just
 * computed for all of them at once (no Claude, no patterns — that's what
 * keeps ~30 symbols under 5 seconds instead of minutes).
 */
export default function Screener() {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<ScreenerRow[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timeframe = useStore((s) => s.timeframe)
  const setSymbol = useStore((s) => s.setSymbol)
  const setTimeframe = useStore((s) => s.setTimeframe)

  const run = async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await api.screener(timeframe))
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Screener request failed.')
    } finally {
      setLoading(false)
    }
  }

  const openFor = async (row: ScreenerRow) => {
    await setTimeframe(row.timeframe)
    await setSymbol(row.symbol)
    setOpen(false)
  }

  const launch = () => {
    setOpen(true)
    if (!rows && !loading) void run()
  }

  const buys = (rows ?? []).filter((r) => r.decision === 'BUY')
  const sells = (rows ?? []).filter((r) => r.decision === 'SELL')

  return (
    <>
      <button type="button" className="btn" onClick={launch}
              title="Scan every instrument for a BUY/SELL signal right now — no need to check each chart by hand">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"
             stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 17l6-6 4 4 8-8M15 7h6v6" />
        </svg>
        Best trades
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-[210] flex items-start justify-center bg-black/60 px-6 pt-20"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          >
            <motion.div
              className="w-full max-w-2xl max-h-[75vh] rounded-2xl border border-edge bg-panel
                         shadow-2xl overflow-hidden flex flex-col"
              initial={{ scale: 0.96, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-2 px-5 py-3 border-b border-hairline">
                <h2 className="text-sm font-bold tracking-tight">Best trades right now</h2>
                <span className="text-2xs text-faint">· {timeframe}</span>
                <span className="flex-1" />
                <button type="button" className="chip !py-0.5" onClick={run} disabled={loading}
                        title="Re-scan">
                  {loading ? <Spinner className="h-3 w-3" /> : 'Refresh'}
                </button>
                <button type="button" className="text-faint hover:text-ink text-sm px-1"
                        onClick={() => setOpen(false)} aria-label="Close">
                  ✕
                </button>
              </div>

              <div className="overflow-y-auto flex-1 px-2 py-2">
                {loading && !rows && (
                  <div className="flex flex-col items-center justify-center gap-2 py-16">
                    <Spinner className="h-5 w-5" />
                    <p className="text-xs text-muted">
                      Scanning every instrument — a few seconds…
                    </p>
                    <p className="text-2xs text-faint">
                      First scan of the session can take up to ~30s while
                      Angel One's contract list downloads. After that it's fast.
                    </p>
                  </div>
                )}

                {error && (
                  <p className="text-xs text-bear text-center py-8">{error}</p>
                )}

                {rows && rows.length === 0 && !loading && (
                  <p className="text-xs text-muted text-center py-16">
                    Nothing clears the bar right now — every instrument is WAIT
                    on {timeframe}. That's a real result, not an error; try a
                    different timeframe or check back later.
                  </p>
                )}

                {rows && rows.length > 0 && (
                  <div className="space-y-4 py-1">
                    {buys.length > 0 && (
                      <RowGroup title="BUY" rows={buys} onSelect={openFor} tone="bull" />
                    )}
                    {sells.length > 0 && (
                      <RowGroup title="SELL" rows={sells} onSelect={openFor} tone="bear" />
                    )}
                  </div>
                )}
              </div>

              <p className="px-5 py-2.5 text-[10px] text-faint border-t border-hairline">
                Deterministic rule-engine read, no AI — the same fast check used to
                cut down which charts are worth opening. Analysis only; nothing here places an order.
              </p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function RowGroup({ title, rows, onSelect, tone }: {
  title: string; rows: ScreenerRow[]
  onSelect: (row: ScreenerRow) => void; tone: 'bull' | 'bear'
}) {
  const toneText = tone === 'bull' ? 'text-bull' : 'text-bear'
  return (
    <div>
      <h3 className={`text-2xs font-bold uppercase tracking-wider px-3 mb-1 ${toneText}`}>
        {title} · {rows.length}
      </h3>
      <div className="space-y-0.5">
        {rows.map((row) => (
          <button
            key={row.symbol}
            type="button"
            onClick={() => void onSelect(row)}
            className="w-full flex items-center gap-3 rounded-lg px-3 py-2 text-left
                       hover:bg-tint/[0.05] transition-colors"
          >
            <span className="w-24 shrink-0 font-semibold text-xs truncate">{row.display}</span>
            <span className={`w-10 shrink-0 text-2xs font-bold ${toneText}`}>{row.decision}</span>
            <span className="w-16 shrink-0 text-2xs tnum text-faint">
              {row.confidence.toFixed(0)}%
            </span>
            <span className="flex-1 text-2xs text-ink/70 truncate">{row.summary}</span>
            <span className="shrink-0 text-2xs tnum text-faint">
              {row.risk_reward != null ? `${row.risk_reward.toFixed(2)}R:R` : ''}
            </span>
            <span className="shrink-0 text-2xs tnum">
              {fmtPriceCcy(row.entry, 5, row.quote_currency)}
            </span>
            {row.broker && (
              <span className="shrink-0 flex items-center gap-1 rounded px-1.5 py-0.5
                               bg-tint/[0.06] border border-hairline"
                    title={row.exchange
                      ? `Trade this on ${row.broker}, ${row.exchange} — several stocks are also `
                        + `listed on other exchanges, this is the one to pick`
                      : `Trade this on ${row.broker} — search for exactly this name`}>
                <span className="text-2xs font-mono font-semibold">{row.broker_symbol}</span>
                <span className="text-[9px] text-faint">
                  {row.broker}{row.exchange ? ` · ${row.exchange}` : ''}
                </span>
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
