import { useEffect, useState } from 'react'
import { useStore } from '../../store/useStore'

const STALE_SECONDS = 20

/**
 * Today's profit or loss at Angel One, large and live.
 *
 * Booked profit and the open positions' profit are read from the broker's
 * own books (refreshed every few seconds); the open part is then carried
 * forward to the live quote between refreshes, so the figure moves with the
 * price rather than in steps. Charges are the cost model's estimate of what
 * today's filled orders have cost so far — a position still open has not
 * paid its exit yet — and the contract note from Angel One is the record.
 */
export default function TodayPnl() {
  const book = useStore((s) => s.brokerBook)
  const quotes = useStore((s) => s.quotes)
  const connected = useStore((s) => s.angelStatus?.connected)
  const [now, setNow] = useState(() => Date.now() / 1000)

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(id)
  }, [])

  const shell = 'shrink-0 rounded-xl border border-hairline bg-panel/70 px-4 py-3'
  if (!connected || !book) {
    return (
      <div className={shell}>
        <div className="text-[10px] uppercase tracking-wider text-faint">Today's P&amp;L · Angel One</div>
        <div className="mt-1 text-2xl font-bold text-faint tnum">—</div>
        <div className="text-2xs text-faint">
          {connected ? 'Reading Angel One…' : 'Connect Angel One to see it live.'}
        </div>
      </div>
    )
  }

  // Carry each open position from the broker's last price to the live quote.
  let open = 0
  for (const p of book.positions) {
    const booked = p.unrealised ?? p.pnl ?? 0
    const live = p.chart_symbol ? quotes[p.chart_symbol]?.last : undefined
    const moved = live != null && p.last_price != null ? (live - p.last_price) * p.net_quantity : 0
    open += booked + moved
  }
  const charges = book.charges_today ?? 0
  const gross = book.realised_today + open
  const net = gross - charges
  const age = Math.max(0, Math.round(now - book.as_of))
  const stale = age > STALE_SECONDS
  const up = net >= 0
  const rupees = (n: number, digits = 2) =>
    `${n >= 0 ? '+' : '−'}₹${Math.abs(n).toLocaleString(undefined, {
      minimumFractionDigits: digits, maximumFractionDigits: digits })}`

  return (
    <div className={`${shell} ${up ? 'border-bull/40' : 'border-bear/40'}`}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-faint">
        <span className={`h-1.5 w-1.5 rounded-full ${stale ? 'bg-warn' : 'bg-bull animate-pulse'}`} />
        Today's P&amp;L · Angel One
        <span className="flex-1" />
        <span className={stale ? 'text-warn' : ''}>
          {stale ? `stale — ${age}s old` : age < 2 ? 'live' : `${age}s ago`}
        </span>
      </div>
      <div className={`mt-1 text-4xl font-bold tnum leading-none ${up ? 'text-bull' : 'text-bear'}`}
           title="After the charges paid so far">
        {rupees(net)}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-2xs text-faint tnum">
        <span>Booked <span className={book.realised_today >= 0 ? 'text-bull' : 'text-bear'}>
          {rupees(book.realised_today)}</span></span>
        <span>Open <span className={open >= 0 ? 'text-bull' : 'text-bear'}>{rupees(open)}</span></span>
        <span>Charges <span className="text-warn">−₹{charges.toLocaleString(undefined, {
          minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></span>
      </div>
      <div className="mt-1 text-[10px] text-faint">
        Before charges {rupees(gross)} · {book.positions.length} open · estimate, not the contract note
      </div>
    </div>
  )
}
