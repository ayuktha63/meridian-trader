import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { fmtPrice, timeAgo } from '../../lib/format'
import { useStore } from '../../store/useStore'
import type { BrokerBook, BrokerOrder, BrokerPosition, ExecutionRecord, LiveTradingOverview, PaperState, PaperTrade } from '../../lib/types'
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
 *
 * In PAPER mode the same popup shows the demo account instead
 * (`/api/paper`) — never a mix of the two, so a demo trade can't be
 * mistaken for money at the broker or the other way round.
 */
const POLL_MS = 5_000

export default function LivePositionsPanel() {
  const open = useStore((s) => s.positionsOpen)
  const setOpen = useStore((s) => s.setPositionsOpen)
  const book = useStore((s) => s.brokerBook)
  const isLive = useStore((s) => s.tradingMode?.mode) === 'LIVE'
  const [data, setData] = useState<LiveTradingOverview | null>(null)
  const [paper, setPaper] = useState<PaperState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // A mode switch must never leave the other account's numbers on screen.
  useEffect(() => {
    setData(null)
    setPaper(null)
    setError(null)
  }, [isLive])

  const refresh = useCallback(async () => {
    try {
      if (isLive) setData(await api.liveOverview())
      else setPaper(await api.paperState())
      setError(null)
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message
        : isLive ? 'Could not reach the live account.' : 'Could not load the demo account.')
    } finally {
      setLoading(false)
    }
  }, [isLive])

  useEffect(() => {
    if (!open) return
    setLoading(true)
    void refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [open, refresh])

  const currency = isLive ? data?.currency : paper?.account.currency
  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${currency === 'INR' ? '₹' : '$'}${Math.abs(value)
      .toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  const account = paper?.account
  const demoOpen: PaperTrade[] = paper
    ? [paper.open_trade, ...paper.recent.filter((t) => t.status === 'open')]
        .filter((t, i, all): t is PaperTrade => !!t && all.findIndex((o) => o?.id === t.id) === i)
    : []
  const demoOpenRisk = demoOpen.reduce(
    (sum, t) => sum + Math.abs(t.entry - t.stop_loss) * t.quantity, 0)
  const hasData = isLive ? !!data : !!paper
  const label = isLive ? 'Live' : 'Demo'

  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}
              title={isLive
                ? 'What is actually open at Angel One right now, marked to the live price'
                : 'What the demo account has open right now, marked to the live price'}>
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"
             stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 3v18h18M7 14l4-4 3 3 5-6" />
        </svg>
        {label} Positions
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-[210] flex items-center justify-center bg-black/60 p-4 sm:px-6"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          >
            <motion.div
              className="w-full max-w-4xl max-h-[85vh] rounded-2xl border border-edge bg-panel
                         shadow-2xl overflow-hidden flex flex-col"
              initial={{ scale: 0.96, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-2 px-5 py-3 border-b border-hairline shrink-0">
                <h2 className="text-sm font-bold tracking-tight">{label} positions</h2>
                <span className="text-2xs text-faint">· {isLive ? 'Angel One' : 'Demo account'}</span>
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

              {loading && !hasData ? (
                <div className="flex-1 grid place-items-center">
                  <Spinner className="h-5 w-5" />
                </div>
              ) : error && !hasData ? (
                <div className="flex-1 grid place-items-center">
                  <EmptyState title={`Could not load ${label.toLowerCase()} positions`} hint={error} />
                </div>
              ) : (
                <>
                  {isLive ? (
                    <div className="shrink-0 grid grid-cols-4 gap-3 px-5 py-3 border-b border-hairline">
                      <Stat label="Wallet" value={money(data?.balance)}
                            tone={data?.balance_source === 'angel_one' ? 'text-bear' : 'text-ink'}
                            sub={data?.balance_source === 'angel_one' ? 'Angel One' : 'Settings'} />
                      <Stat label="Open risk" value={money(data?.open_risk)}
                            sub="if every stop hits" />
                      <Stat label="Realised today" value={money(book ? book.realised_today : data?.realised_today)}
                            tone={(book ? book.realised_today : data?.realised_today ?? 0) >= 0 ? 'text-bull' : 'text-bear'}
                            sub={book ? 'as Angel One reports it' : undefined} />
                      <Stat label="Orders filled today"
                            value={`${book ? book.orders.filter((o) => o.status === 'complete' && o.filled > 0).length : data?.trades_today ?? 0}`} />
                    </div>
                  ) : (
                    <div className="shrink-0 grid grid-cols-4 gap-3 px-5 py-3 border-b border-hairline">
                      <Stat label="Demo equity" value={money(account?.equity)}
                            sub={`started ${money(account?.starting_balance)}`} />
                      <Stat label="Open risk" value={money(demoOpenRisk)}
                            sub="if every stop hits" />
                      <Stat label="Open P&L" value={money(account?.unrealised_pnl)}
                            tone={(account?.unrealised_pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear'} />
                      <Stat label="Realised" value={money(account?.realised_pnl)}
                            tone={(account?.realised_pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear'}
                            sub={`${account?.total_trades ?? 0} trades`} />
                    </div>
                  )}

                  <div className="flex-1 min-h-0 overflow-y-auto px-5 py-3 space-y-2">
                    {isLive ? (
                      <LiveBook data={data} book={book} />
                    ) : !demoOpen.length ? (
                      <EmptyState title="No open demo positions"
                                  hint="Paper trades from the demo account show up here the moment they open." />
                    ) : (
                      demoOpen.map((trade) => (
                        <DemoPositionRow key={trade.id} trade={trade}
                                         pnl={demoOpen.length === 1 ? account?.unrealised_pnl : undefined}
                                         currency={paper!.account.currency} />
                      ))
                    )}
                  </div>

                  <p className="shrink-0 px-5 py-2.5 text-[10px] text-faint leading-relaxed border-t border-hairline">
                    {isLive
                      ? 'Refreshes every 5s while open. Unrealised P&L is an estimate — current price minus the same brokerage and statutory charges the daily loss limit is enforced on; the contract note from Angel One is the final figure.'
                      : 'Refreshes every 5s while open. Paper money only — nothing here was sent to a broker.'}
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

/**
 * Angel One's own books — positions and working orders, however they were
 * placed. Where they cannot be read, Meridian's own record of what it sent
 * stands in, and says so.
 */
function LiveBook({ data, book }: { data: LiveTradingOverview | null; book: BrokerBook | null }) {
  const currency = data?.currency ?? 'INR'
  const positions = book?.positions ?? data?.broker_positions ?? null
  const orders = (book?.orders ?? []).filter((o) => o.working)
  if (positions === null) {
    return (
      <>
        <p className="text-2xs text-warn">
          Could not read Angel One's position book just now, so this is only what Meridian itself
          sent. A trade taken in the Angel One app may be missing until the next refresh.
        </p>
        {data?.open_positions.map((row) => (
          <PositionRow key={row.id} row={row} currency={currency} />
        ))}
      </>
    )
  }
  if (!positions.length && !orders.length) {
    return (
      <EmptyState title="No open positions or orders at Angel One"
                  hint="Everything Angel One holds — however it was placed — shows up here and on the chart." />
    )
  }
  return (
    <>
      {positions.map((p) => (
        <BrokerPositionRow key={p.exchange + p.trading_symbol + p.product}
                           position={p} currency={currency} />
      ))}
      {orders.length > 0 && (
        <h4 className="pt-1 text-[9px] uppercase tracking-wider text-faint">Working orders</h4>
      )}
      {orders.map((o, i) => <BrokerOrderRow key={o.order_id || `oco-${i}`} order={o} currency={currency} />)}
    </>
  )
}

function BrokerOrderRow({ order, currency }: { order: BrokerOrder; currency: string }) {
  const sym = currency === 'INR' ? '₹' : '$'
  const price = order.trigger_price ?? order.price
  return (
    <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2">
      <div className="flex items-center gap-2 text-xs">
        <span className={`font-bold ${order.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>{order.side}</span>
        <span className="font-semibold text-ink">{order.symbol}</span>
        <Badge tone="neutral">{order.order_type}</Badge>
        <Badge tone="neutral">{order.status}</Badge>
        <span className="flex-1" />
        <span className="text-ink tnum">
          {order.quantity - order.filled} @ {price != null ? `${sym}${price}`
            : order.order_type.startsWith('OCO') ? 'levels set in Angel One' : 'market'}
        </span>
      </div>
    </div>
  )
}

function BrokerPositionRow({ position, currency }: { position: BrokerPosition; currency: string }) {
  const sym = currency === 'INR' ? '₹' : '$'
  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${sym}${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  const long = position.net_quantity > 0
  const entry = long ? position.buy_average : position.sell_average
  const up = (position.pnl ?? 0) >= 0

  return (
    <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-bold ${long ? 'text-bull' : 'text-bear'}`}>
          {long ? 'BUY' : 'SELL'}
        </span>
        <span className="text-xs font-semibold text-ink">{position.symbol}</span>
        <Badge tone="neutral">{position.product}</Badge>
        {position.managed && <Badge tone="accent">stop managed by Meridian</Badge>}
        <span className="flex-1" />
        {position.pnl != null && (
          <span className={`text-sm font-bold tnum ${up ? 'text-bull' : 'text-bear'}`}>
            {up ? '+' : '−'}{money(position.pnl)}
          </span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-3 text-2xs text-faint">
        <span>Qty <span className="text-ink tnum">{Math.abs(position.net_quantity)}</span></span>
        <span>Entry <span className="text-ink tnum">{fmtPrice(entry, 2)}</span></span>
        <span>Last <span className="text-ink tnum">{fmtPrice(position.last_price, 2)}</span></span>
      </div>
    </div>
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

function DemoPositionRow({ trade, pnl, currency }: {
  trade: PaperTrade; pnl?: number; currency: string
}) {
  const sym = currency === 'INR' ? '₹' : '$'
  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${sym}${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  const up = (pnl ?? 0) >= 0

  return (
    <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-bold ${trade.side === 'LONG' ? 'text-bull' : 'text-bear'}`}>
          {trade.side}
        </span>
        <span className="text-xs font-semibold text-ink">{trade.symbol}</span>
        <Badge tone="bull">OPEN</Badge>
        <Badge tone="neutral">demo</Badge>
        <span className="flex-1" />
        {pnl != null && (
          <span className={`text-sm font-bold tnum ${up ? 'text-bull' : 'text-bear'}`}>
            {up ? '+' : '−'}{money(pnl)}
          </span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-4 gap-3 text-2xs text-faint">
        <span>Qty <span className="text-ink tnum">{trade.quantity}</span></span>
        <span>Entry <span className="text-ink tnum">{fmtPrice(trade.entry, 2)}</span></span>
        <span>Stop <span className="text-ink tnum">{fmtPrice(trade.stop_loss, 2)}</span></span>
        <span>Target <span className="text-ink tnum">{fmtPrice(trade.take_profit, 2)}</span></span>
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-faint">
        <span>{trade.timeframe} · signal score {Math.round(trade.confidence)}</span>
        <span>opened {timeAgo(trade.entry_at)}</span>
      </div>
    </div>
  )
}
