import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { api } from '../../lib/api'
import { fmtSigned, timeAgo } from '../../lib/format'
import { useStore } from '../../store/useStore'

/**
 * The AUTO switch and the demo account's running result.
 *
 * This is the honest test of whether the engine has an edge: live prices, real
 * spreads, real commission, no real money. The equity figure is balance plus
 * the open trade marked to the live bid/ask, so it moves every second.
 *
 * The wording is deliberate throughout — **DEMO**, never "profit so far" as if
 * it were banked. A paper result is evidence, not income, and a UI that blurs
 * those is how someone ends up funding an account on the strength of forty
 * simulated trades.
 */
export default function AutoTradeBar() {
  const paper = useStore((s) => s.paper)
  const setPaper = useStore((s) => s.setPaper)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)

  if (!paper) return null
  const { account, open_trade: trade } = paper
  const pnl = account.equity - account.starting_balance
  const up = pnl >= 0

  const money = (value: number) =>
    `${account.currency === 'INR' ? '₹' : '$'}${Math.abs(value)
      .toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  const toggle = async () => {
    setBusy(true)
    try {
      setPaper(await api.paperConfig({ enabled: !account.enabled }))
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    if (!window.confirm('Reset the demo to its starting balance? Every paper trade is deleted.')) return
    setBusy(true)
    try {
      setPaper(await api.paperReset(account.starting_balance))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative">
      <div className={`flex items-center gap-2.5 rounded-lg border px-2.5 py-1
                       transition-colors ${account.enabled
                        ? 'border-bull/50 bg-bull/10' : 'border-hairline bg-tint/[0.03]'}`}>
        {/* The switch */}
        <button
          type="button"
          onClick={toggle}
          disabled={busy}
          role="switch"
          aria-checked={account.enabled}
          aria-label="Automatic paper trading"
          title={account.enabled
            ? 'Auto-trading is ON — the demo account opens and closes trades from the engine\'s signals. No real money is involved.'
            : 'Turn on auto-trading. The demo account will take the engine\'s signals at live prices with real spreads and commission — no real money.'}
          className="flex items-center gap-1.5"
        >
          <span className={`relative h-3.5 w-6 rounded-full transition-colors
                            ${account.enabled ? 'bg-bull' : 'bg-tint/20'}`}>
            <motion.span
              className="absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white shadow"
              animate={{ left: account.enabled ? 14 : 2 }}
              transition={{ type: 'spring', stiffness: 500, damping: 32 }}
            />
          </span>
          <span className={`text-2xs font-semibold tracking-wide ${
            account.enabled ? 'text-bull' : 'text-faint'}`}>
            AUTO
          </span>
        </button>

        <span className="h-4 w-px bg-tint/10" />

        {/* Equity + P&L */}
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                title="Demo account — click for the full performance breakdown"
                className="flex items-center gap-2 text-left">
          <span>
            <span className="block text-[9px] uppercase tracking-wider text-faint leading-none">
              Demo equity
            </span>
            <span className="block text-2xs font-semibold tnum text-ink leading-tight">
              {money(account.equity)}
            </span>
          </span>
          <span className={`text-2xs font-semibold tnum ${up ? 'text-bull' : 'text-bear'}`}>
            {up ? '+' : '−'}{money(pnl)}
            <span className="text-[9px] ml-1 opacity-80">
              {fmtSigned(account.return_pct, 2)}%
            </span>
          </span>
        </button>

        {trade && (
          <span className={`text-[9px] px-1.5 py-0.5 rounded ${
            trade.side === 'LONG' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'}`}
                title={`Open paper ${trade.side} on ${trade.symbol} from ${trade.entry}`}>
            {trade.side} {trade.symbol}
          </span>
        )}
      </div>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.14 }}
            className="absolute right-0 top-full z-50 mt-1.5 w-[340px] rounded-xl p-3
                       bg-panel/[0.98] backdrop-blur-xl border border-edge
                       shadow-[0_24px_60px_-20px_rgb(var(--c-void)/0.95)] space-y-2.5"
          >
            <div className="flex items-center gap-2">
              <h3 className="panel-title">Demo account</h3>
              <span className="flex-1" />
              <button type="button" className="chip !py-0.5" onClick={reset} disabled={busy}
                      title="Delete every paper trade and start again from the opening balance">
                Reset
              </button>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <Stat label="Balance" value={money(account.balance)}
                    hint="Cash. Only moves when a trade closes." />
              <Stat label="Open P&L" value={fmtSigned(account.unrealised_pnl, 2)}
                    tone={account.unrealised_pnl >= 0 ? 'text-bull' : 'text-bear'}
                    hint="The open trade marked to the live price" />
              <Stat label="Costs paid" value={money(account.total_costs)}
                    tone="text-warn"
                    hint="Spread crossed plus commission, across every closed trade" />
            </div>

            <div className="grid grid-cols-3 gap-2">
              <Stat label="Trades" value={`${account.total_trades}`}
                    sub={`${account.wins}W / ${account.losses}L`}
                    hint="Closed paper trades" />
              <Stat label="Win rate" value={`${account.win_rate.toFixed(0)}%`}
                    hint="Share of closed trades that finished net positive" />
              <Stat label="Expectancy"
                    value={`${fmtSigned(account.expectancy_r, 2)}R`}
                    tone={account.expectancy_r >= 0 ? 'text-bull' : 'text-bear'}
                    hint="Average R per trade after costs. This is the number that matters — a positive win rate with negative expectancy still loses money." />
            </div>

            <div className="grid grid-cols-3 gap-2">
              <Stat label="Profit factor" value={account.profit_factor.toFixed(2)}
                    hint="Gross wins ÷ gross losses. Below 1.0 the strategy loses." />
              <Stat label="Max drawdown" value={`${account.max_drawdown.toFixed(1)}%`}
                    tone="text-bear"
                    hint="Largest peak-to-trough fall in balance" />
              <Stat label="Avg win / loss"
                    value={`${account.avg_win_r.toFixed(1)} / ${account.avg_loss_r.toFixed(1)}R`}
                    hint="Average R on winners and on losers" />
            </div>

            <div className="border-t border-hairline pt-2">
              <h4 className="text-[9px] uppercase tracking-wider text-faint mb-1.5">
                Trade history {paper?.open_trade ? '(1 open)' : ''}
              </h4>
              <div className="max-h-52 overflow-y-auto space-y-1 pr-0.5">
                {paper?.open_trade && (
                  <div className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5
                                  bg-accent/10 border border-accent/30">
                    <div className="min-w-0">
                      <span className={`text-2xs font-semibold ${
                        paper.open_trade.side === 'LONG' ? 'text-bull' : 'text-bear'}`}>
                        {paper.open_trade.side}
                      </span>
                      <span className="text-2xs font-semibold ml-1">{paper.open_trade.symbol}</span>
                      <span className="text-[9px] text-faint ml-1.5">
                        @ {paper.open_trade.entry} · {timeAgo(paper.open_trade.entry_at)}
                      </span>
                    </div>
                    <span className="text-[9px] font-semibold text-accent shrink-0">OPEN</span>
                  </div>
                )}
                {paper?.recent.length ? paper.recent.map((t) => (
                  <div key={t.id}
                       className={`flex items-center justify-between gap-2 rounded-lg px-2 py-1.5
                                   ${(t.net_pnl ?? 0) >= 0 ? 'bg-bull/[0.06]' : 'bg-bear/[0.06]'}`}>
                    <div className="min-w-0">
                      <span className={`text-2xs font-semibold ${
                        t.side === 'LONG' ? 'text-bull' : 'text-bear'}`}>
                        {t.side}
                      </span>
                      <span className="text-2xs font-semibold ml-1">{t.symbol}</span>
                      <span className="text-[9px] text-faint ml-1.5">
                        {t.exit_at ? timeAgo(t.exit_at) : ''} · {t.exit_reason}
                      </span>
                    </div>
                    <span className={`text-2xs font-semibold tnum shrink-0 ${
                      (t.net_pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {fmtSigned(t.net_pnl, 2)}
                    </span>
                  </div>
                )) : !paper?.open_trade && (
                  <p className="text-[10px] text-faint italic px-2 py-1">
                    No trades yet — waiting for a signal that clears every gate.
                  </p>
                )}
              </div>
            </div>

            {account.total_trades < 30 && (
              <p className="text-[10px] text-warn/90 leading-relaxed">
                {account.total_trades} closed {account.total_trades === 1 ? 'trade' : 'trades'}.
                Nothing here is a result yet — a run needs 30–40 before the win
                rate and expectancy mean anything at all, and a good first ten
                is the single most common way to be fooled.
              </p>
            )}

            <p className="text-[10px] text-faint leading-relaxed border-t border-hairline pt-2">
              Simulated fills at live prices: entries cross the spread, stops fill
              slightly through the level, and commission is charged both sides.
              No orders are placed and no real money is involved.
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Stat({ label, value, sub, tone, hint }: {
  label: string; value: string; sub?: string; tone?: string; hint?: string
}) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[9px] uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`text-2xs font-semibold tnum truncate ${tone ?? 'text-ink'}`}>{value}</div>
      {sub && <div className="text-[9px] text-faint tnum truncate">{sub}</div>}
    </div>
  )
}
