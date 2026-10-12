import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { fmtPrice, fmtSigned, timeAgo } from '../../lib/format'
import type { ExecutionRecord, LiveTradingOverview } from '../../lib/types'
import { Badge, EmptyState, Spinner } from '../ui'

/**
 * Every live order that's run its course — filled and closed, or refused
 * by the pre-trade gate before it ever reached Angel One. A full-size
 * popup, same as Live Positions: real-money history deserves more than a
 * sliver in the tabbed side panel. Same source as that screen
 * (`/api/trades/live/overview`), just the other half of the split —
 * anything no longer open.
 */
const POLL_MS = 15_000

export default function LiveHistoryPanel() {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<LiveTradingOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setData(await api.liveOverview())
      setError(null)
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not reach the live account.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    setLoading(true)
    void refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [open, refresh])

  const history = data?.history ?? []
  const closedWins = history.filter((r) => (r.net_pnl ?? 0) > 0).length
  const closedLosses = history.filter((r) => (r.net_pnl ?? 0) < 0 && r.status === 'CLOSED').length
  const refused = history.filter((r) => r.status === 'REJECTED').length

  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}
              title="Every closed, cancelled or refused live order">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"
             stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5" />
          <path d="M12 7v5l4 2" />
        </svg>
        Live History
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-[210] flex items-center justify-center bg-black/60 px-6"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          >
            <motion.div
              className="w-full max-w-4xl h-[82vh] rounded-2xl border border-edge bg-panel
                         shadow-2xl overflow-hidden flex flex-col"
              initial={{ scale: 0.96, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-2 px-5 py-3 border-b border-hairline shrink-0">
                <h2 className="text-sm font-bold tracking-tight">Live history</h2>
                <span className="text-2xs text-faint">· Angel One</span>
                <span className="flex-1" />
                <button type="button" className="chip !py-0.5" onClick={refresh} disabled={loading}
                        title="Refresh now">
                  {loading ? <Spinner className="h-3 w-3" /> : 'Refresh'}
                </button>
                <button type="button" className="text-faint hover:text-ink text-sm px-1"
                        onClick={() => setOpen(false)} aria-label="Close">
                  ✕
                </button>
              </div>

              {loading && !data ? (
                <div className="flex-1 grid place-items-center">
                  <Spinner className="h-5 w-5" />
                </div>
              ) : error && !data ? (
                <div className="flex-1 grid place-items-center">
                  <EmptyState title="Could not load live history" hint={error} />
                </div>
              ) : (
                <>
                  <div className="shrink-0 flex items-center gap-4 px-5 py-3 border-b border-hairline text-xs">
                    <span className="text-faint">{history.length} total</span>
                    <span className="text-bull font-semibold">{closedWins}W</span>
                    <span className="text-bear font-semibold">{closedLosses}L</span>
                    <span className="text-warn font-semibold">{refused} refused</span>
                  </div>

                  <div className="flex-1 min-h-0 overflow-y-auto px-5 py-3 space-y-2">
                    {history.length === 0 ? (
                      <EmptyState title="No live history yet"
                                  hint="Every closed, cancelled or refused live order will show up here." />
                    ) : (
                      history.map((row) => (
                        <HistoryRow key={row.id} row={row} currency={data?.currency ?? 'USD'} />
                      ))
                    )}
                  </div>
                </>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function HistoryRow({ row, currency }: { row: ExecutionRecord; currency: string }) {
  const sym = currency === 'INR' ? '₹' : '$'
  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${sym}${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  const rejected = row.status === 'REJECTED'
  const cancelled = row.status === 'CANCELLED'
  const netWin = (row.net_pnl ?? 0) >= 0

  let badgeTone: 'bull' | 'bear' | 'warn' | 'neutral' = 'neutral'
  if (rejected) badgeTone = 'warn'
  else if (cancelled) badgeTone = 'neutral'
  else badgeTone = netWin ? 'bull' : 'bear'

  return (
    <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-bold ${row.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>
          {row.side}
        </span>
        <span className="text-xs font-semibold text-ink">{row.symbol}</span>
        <Badge tone={badgeTone}>{row.status}</Badge>
        <span className="flex-1" />
        {!rejected && !cancelled && row.net_pnl != null && (
          <span className={`text-sm font-bold tnum ${netWin ? 'text-bull' : 'text-bear'}`}>
            {netWin ? '+' : '−'}{money(row.net_pnl)}
          </span>
        )}
      </div>

      {rejected || cancelled ? (
        <p className="mt-1.5 text-2xs text-faint leading-relaxed">
          {row.error_message || 'No reason recorded.'}
        </p>
      ) : (
        <div className="mt-2 grid grid-cols-4 gap-3 text-2xs text-faint">
          <span>Qty <span className="text-ink tnum">{row.filled_quantity}</span></span>
          <span>Entry <span className="text-ink tnum">{fmtPrice(row.average_fill_price, 2)}</span></span>
          <span>Exit <span className="text-ink tnum">{fmtPrice(row.exit_price, 2)}</span></span>
          <span>Charges <span className="text-ink tnum">{money(row.charges)}</span></span>
        </div>
      )}

      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-faint">
        <span>{row.exchange} · {row.trading_symbol}</span>
        <span>{row.closed_at ? timeAgo(row.closed_at) : timeAgo(row.created_at)}</span>
        {row.exit_reason && <span>{row.exit_reason}</span>}
        {row.gross_pnl != null && row.charges != null && (
          <span>{fmtSigned(row.gross_pnl, 2)} gross − {money(row.charges)} charges</span>
        )}
      </div>
    </div>
  )
}
