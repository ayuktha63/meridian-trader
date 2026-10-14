import { useStore } from '../../store/useStore'
import type { BrokerOrder } from '../../lib/types'
import type { Transform } from './geometry'

interface Props {
  transform: Transform
  symbol: string
  digits: number
}

interface Row {
  key: string
  y: number
  color: string
  dashed: boolean
  text: string
  title: string
  /** A position with nothing protecting it. */
  warn?: boolean
  offset?: 'left-40'
}

/**
 * The broker's own markings on the chart, drawn the way a broker's terminal
 * draws them: a line at each open position's entry with its size and live
 * profit or loss, and a dashed line at every order still working — stop,
 * target, limit — tagged with what it is. All of it is read from Angel One,
 * so a trade taken in its app appears here and one closed there disappears.
 *
 * Tags can be hovered for the detail and clicked to open the positions
 * window. They only report; nothing here sends or changes an order.
 */
export default function BrokerLayer({ transform, symbol, digits }: Readonly<Props>) {
  const book = useStore((s) => s.brokerBook)
  const open = useStore((s) => s.setPositionsOpen)
  if (!book) return null

  const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: digits })
  const money = (n: number) => `₹${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  // A price off the visible scale has no place on the chart; the window lists it.
  const lineAt = (price: number) => {
    const y = transform.priceToY(price)
    return Number.isFinite(y) && y > 8 && y < transform.height - 8 ? y : null
  }

  const rows: Row[] = []
  const working = book.orders.filter((x) => x.working && x.chart_symbol === symbol)
  const claimed = new Set<string>()

  for (const p of book.positions.filter((x) => x.chart_symbol === symbol)) {
    const long = p.net_quantity > 0
    const entry = long ? p.buy_average : p.sell_average
    // The orders that close this position are the ones on its opposite side.
    // On a long, one below the entry is its stop and one above is its target.
    const closing = working.filter((o) => o.side === (long ? 'SELL' : 'BUY'))
    const priceOf = (o: BrokerOrder) => o.trigger_price ?? o.price ?? 0
    const stop = closing.find((o) => o.order_type.includes('STOPLOSS')
      || (long ? priceOf(o) < entry : priceOf(o) > entry))
    const target = closing.find((o) => o !== stop && (long ? priceOf(o) > entry : priceOf(o) < entry))
    if (stop) claimed.add(stop.order_id)
    if (target) claimed.add(target.order_id)

    const y = lineAt(entry)
    const pnl = p.pnl ?? 0
    if (y !== null) {
      rows.push({
        key: `pos-${p.exchange}-${p.trading_symbol}-${p.product}`, y, dashed: false,
        color: pnl >= 0 ? 'rgb(var(--c-bull))' : 'rgb(var(--c-bear))',
        text: `${long ? 'BUY' : 'SELL'} ${Math.abs(p.net_quantity)} @ ${fmt(entry)}  ${pnl >= 0 ? '+' : '−'}${money(pnl)}${stop ? '' : '  · NO STOP SET'}`,
        title: `${p.trading_symbol} · ${p.product} · entry ${fmt(entry)} · last ${p.last_price ?? '—'}`
          + (stop ? '' : '. No stop-loss order is resting at Angel One for this position.')
          + ' Click for positions.',
        warn: !stop,
      })
    }
    for (const [order, kind, color] of [
      [stop, 'SL', 'rgb(var(--c-bear))'], [target, 'TP', 'rgb(var(--c-bull))'],
    ] as const) {
      const price = order ? priceOf(order) : 0
      const level = order && price ? lineAt(price) : null
      if (!order || level === null) continue
      rows.push({
        key: `ord-${order.order_id}`, y: level, dashed: false, color,
        text: `${kind} SET @ ${fmt(price)}`,
        title: `${order.symbol} · ${order.order_type} · ${order.status}. Resting at Angel One. Click for positions.`,
        offset: 'left-40',
      })
    }
  }

  // Working orders that close nothing on this chart: entries still waiting.
  for (const o of working.filter((x) => !claimed.has(x.order_id))) {
    const price = o.trigger_price ?? o.price
    const y = price == null ? null : lineAt(price)
    if (price == null || y === null) continue
    let kind = o.order_type
    if (o.order_type.includes('STOPLOSS')) kind = 'SL'
    else if (o.order_type === 'LIMIT') kind = 'LMT'
    rows.push({
      key: `ord-${o.order_id}`, y, dashed: true, color: 'rgb(var(--c-accent))',
      text: `${kind} ${o.side} ${o.quantity - o.filled} @ ${fmt(price)}`,
      title: `${o.symbol} · ${o.order_type} · ${o.status}${o.message ? ` · ${o.message}` : ''}. Waiting at Angel One. Click for positions.`,
    })
  }

  return (
    <div className="absolute left-0 top-0 pointer-events-none overflow-hidden"
         style={{ width: transform.width, height: transform.height, zIndex: 4 }}>
      {rows.map((row) => (
        <div key={row.key} className="absolute left-0 right-0" style={{ top: row.y }}>
          <div className="absolute left-0 right-0"
               style={{ borderTop: `1px ${row.dashed ? 'dashed' : 'solid'} ${row.color}`, opacity: 0.85 }} />
          <button
            type="button"
            onClick={() => open(true)}
            title={row.title}
            className={`pointer-events-auto absolute -translate-y-1/2 rounded px-1.5 py-0.5
                       text-[10px] font-semibold tnum text-white shadow cursor-pointer
                       hover:brightness-110 ${row.offset ? 'left-40' : 'left-16'}
                       ${row.warn ? 'ring-2 ring-warn' : ''}`}
            style={{ background: row.color }}
          >
            {row.text}
          </button>
        </div>
      ))}
    </div>
  )
}
