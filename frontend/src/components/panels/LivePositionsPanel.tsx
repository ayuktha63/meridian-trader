import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { fmtPrice, timeAgo } from '../../lib/format'
import type { ExecutionRecord, LiveTradingOverview } from '../../lib/types'
import { Badge, EmptyState, Spinner, Stat } from '../ui'

/**
 * What's actually open at Angel One right now, marked to the live price.
 *
 * A full-size popup, not a sliver in the tabbed side panel — this is real
 * money and deserves to be read at a glance, not squinted at. Reads the
 * same ledger the pre-trade risk gate enforces against
 * (`/api/trades/live/overview` — `broker/live_limits.py`'s `LiveLedger` plus
 * a fresh mark-to-market on each open fill) rather than a second,
 * independently-computed number — so "what the gate just refused a new
 * trade over" and "what this screen shows" can never quietly disagree.
 */
const POLL_MS = 5_000

export default function LivePositionsPanel() {
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

  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${data?.currency === 'INR' ? '₹' : '$'}${Math.abs(value)
      .toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}
              title="What is actually open at Angel One right now, marked to the live price">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"
             stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 3v18h18M7 14l4-4 3 3 5-6" />
        </svg>
        Live Positions
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
                <h2 className="text-sm font-bold tracking-tight">Live positions</h2>
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
                  <EmptyState title="Could not load live positions" hint={error} />
                </div>
              ) : (
                <>
                  <div className="shrink-0 grid grid-cols-4 gap-3 px-5 py-3 border-b border-hairline">
                    <Stat label="Wallet" value={money(data?.balance)}
                          tone={data?.balance_source === 'angel_one' ? 'text-bear' : 'text-ink'}
                          sub={data?.balance_source === 'angel_one' ? 'Angel One' : 'Settings'} />
                    <Stat label="Open risk" value={money(data?.open_risk)}
                          sub="if every stop hits" />
                    <Stat label="Realised today" value={money(data?.realised_today)}
                          tone={(data?.realised_today ?? 0) >= 0 ? 'text-bull' : 'text-bear'} />
                    <Stat label="Trades today" value={`${data?.trades_today ?? 0}`} />
                  </div>

                  <div className="flex-1 min-h-0 overflow-y-auto px-5 py-3 space-y-2">
                    {!data?.open_positions.length ? (
                      <EmptyState title="No open live positions"
                                  hint="Every position you hold at Angel One, marked to the live price, will show up here the instant it fills." />
                    ) : (
                      data.open_positions.map((row) => (
                        <PositionRow key={row.id} row={row} currency={data.currency} />
                      ))
                    )}
                  </div>

                  <p className="shrink-0 px-5 py-2.5 text-[10px] text-faint leading-relaxed border-t border-hairline">
                    Refreshes every 5s while open. Unrealised P&amp;L is an estimate — current price
                    minus the same brokerage and statutory charges the daily loss limit is enforced
                    on; the contract note from Angel One is the final figure.
                  </p>
                </>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function PositionRow({ row, currency }: { row: ExecutionRecord; currency: string }) {
  const sym = currency === 'INR' ? '₹' : '$'
  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${sym}${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  const pnl = row.unrealised_pnl ?? null
  const up = (pnl ?? 0) >= 0

  return (
    <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-bold ${row.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>
          {row.side}
        </span>
        <span className="text-xs font-semibold text-ink">{row.symbol}</span>
        <Badge tone={row.status === 'FILLED' || row.status === 'OPEN' ? 'bull' : 'neutral'}>
          {row.status}
        </Badge>
        {row.protection_status === 'PROTECTED' && <Badge tone="accent">stop at broker</Badge>}
        {row.protection_status === 'FAILED' && <Badge tone="warn">stop not confirmed</Badge>}
        <span className="flex-1" />
        {pnl != null && (
          <span className={`text-sm font-bold tnum ${up ? 'text-bull' : 'text-bear'}`}>
            {up ? '+' : '−'}{money(pnl)}
          </span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-4 gap-3 text-2xs text-faint">
        <span>Qty <span className="text-ink tnum">{row.filled_quantity || row.requested_quantity}</span></span>
        <span>Entry <span className="text-ink tnum">{fmtPrice(row.average_fill_price ?? row.requested_price, 2)}</span></span>
        <span>Stop <span className="text-ink tnum">{fmtPrice(row.stop_price, 2)}</span></span>
        <span>Last <span className="text-ink tnum">{fmtPrice(row.last_price, 2)}</span></span>
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-faint">
        <span>{row.exchange} · {row.trading_symbol}</span>
        <span>opened {timeAgo(row.created_at)}</span>
        {row.broker_order_id && <span className="font-mono">#{row.broker_order_id}</span>}
      </div>
    </div>
  )
}
