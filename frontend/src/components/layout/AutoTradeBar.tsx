import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { fmtSigned, timeAgo } from '../../lib/format'
import type { ExecutionRecord, LiveTradingOverview, PaperAccount, PaperState } from '../../lib/types'
import { useStore } from '../../store/useStore'

/**
 * The AUTO switch, and — behind the wallet readout — the running result.
 *
 * LIVE and DEMO show two genuinely different accounts, never the same panel
 * relabelled: LIVE reads `/api/trades/live/overview`, the same ledger the
 * pre-trade risk gate enforces against, so this can never show a number the
 * gate itself disagrees with. DEMO is the honest paper test of whether the
 * engine has an edge: live prices, real spreads, real commission, no real
 * money. The wording is deliberate throughout — **DEMO**, never "profit so
 * far" as if it were banked. A paper result is evidence, not income, and a
 * UI that blurs those is how someone ends up funding an account on the
 * strength of forty simulated trades.
 */
const LIVE_POLL_MS = 5_000

function computeLiveStats(history: ExecutionRecord[]) {
  const closed = history.filter((r) => r.net_pnl != null && r.closed_at != null)
    .sort((a, b) => (b.closed_at ?? 0) - (a.closed_at ?? 0))
  const wins = closed.filter((r) => (r.net_pnl ?? 0) > 0)
  const losses = closed.filter((r) => (r.net_pnl ?? 0) <= 0)
  const grossWin = wins.reduce((sum, r) => sum + (r.net_pnl ?? 0), 0)
  const grossLoss = Math.abs(losses.reduce((sum, r) => sum + (r.net_pnl ?? 0), 0))
  const rOf = (r: ExecutionRecord) =>
    r.risk_amount && r.risk_amount > 0 ? (r.net_pnl ?? 0) / r.risk_amount : null
  const withR = closed.map(rOf).filter((r): r is number => r != null)
  const winsWithR = wins.map(rOf).filter((r): r is number => r != null)
  const lossesWithR = losses.map(rOf).filter((r): r is number => r != null)

  // Peak-to-trough on the cumulative realised P&L, oldest first.
  let running = 0, peak = 0, maxDrawdown = 0
  for (const r of [...closed].reverse()) {
    running += r.net_pnl ?? 0
    peak = Math.max(peak, running)
    maxDrawdown = Math.max(maxDrawdown, peak - running)
  }

  return {
    closed,
    totalTrades: closed.length,
    wins: wins.length,
    losses: losses.length,
    winRate: closed.length ? (wins.length / closed.length) * 100 : 0,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : 0,
    totalCosts: closed.reduce((sum, r) => sum + (r.charges ?? 0), 0),
    expectancyR: withR.length ? withR.reduce((a, b) => a + b, 0) / withR.length : null,
    avgWinR: winsWithR.length ? winsWithR.reduce((a, b) => a + b, 0) / winsWithR.length : null,
    avgLossR: lossesWithR.length ? lossesWithR.reduce((a, b) => a + b, 0) / lossesWithR.length : null,
    maxDrawdown,
  }
}

export default function AutoTradeBar() {
  const paper = useStore((s) => s.paper)
  const setPaper = useStore((s) => s.setPaper)
  // Shared with `TradingModeControl` — reading from the store rather than
  // polling independently means a switch there is reflected here the
  // instant it resolves, with no second or two of lag from a separate poll.
  const mode = useStore((s) => s.tradingMode)
  const angel = useStore((s) => s.angelStatus)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [live, setLive] = useState<LiveTradingOverview | null>(null)
  const isLive = mode?.mode === 'LIVE'

  // Only polled while the panel showing it is actually open, and only in
  // LIVE — there is nothing here to poll in DEMO, which already comes
  // straight off the shared store.
  useEffect(() => {
    if (!open || !isLive) return
    let cancelled = false
    const refresh = () => { api.liveOverview().then((data) => { if (!cancelled) setLive(data) }).catch(() => {}) }
    refresh()
    const id = window.setInterval(refresh, LIVE_POLL_MS)
    return () => { cancelled = true; window.clearInterval(id) }
  }, [open, isLive])

  if (!paper) return null
  const { account, open_trade: trade } = paper
  const pnl = account.equity - account.starting_balance
  const up = pnl >= 0
  const wallet = angel?.available_funds ?? null
  const liveStats = live ? computeLiveStats(live.history) : null

  const money = (value: number) =>
    `${account.currency === 'INR' ? '₹' : '$'}${Math.abs(value)
      .toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  const walletMoney = (value: number) =>
    `₹${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

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

        {/* LIVE replaces this readout with the real wallet, and the
            click-to-expand panel behind it switches with it — the live
            account's own ledger, not a relabelled demo one (see module
            docstring). */}
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                title={isLive
                  ? 'Available funds — Angel One (rmsLimit). Click for your live account\'s performance.'
                  : 'Demo account — click for the full performance breakdown'}
                className="flex items-center gap-2 text-left">
          {isLive ? (
            <span>
              <span className="block text-[9px] uppercase tracking-wider text-bear/80 leading-none">
                Wallet (real)
              </span>
              <span className="block text-2xs font-semibold tnum text-bear leading-tight">
                {wallet != null ? walletMoney(wallet) : '—'}
              </span>
            </span>
          ) : (
            <>
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
            </>
          )}
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
            {isLive ? (
              <LivePanel wallet={wallet} walletMoney={walletMoney}
                         live={live} stats={liveStats} />
            ) : (
              <DemoPanel account={account} paper={paper} money={money}
                         busy={busy} onReset={reset} />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function LivePanel({ wallet, walletMoney, live, stats }: {
  wallet: number | null
  walletMoney: (value: number) => string
  live: LiveTradingOverview | null
  stats: ReturnType<typeof computeLiveStats> | null
}) {
  const money = (value: number | null | undefined) =>
    value == null ? '—' : `${live?.currency === 'INR' ? '₹' : '$'}${Math.abs(value)
      .toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  const rOrDash = (value: number | null) => value == null ? '—' : `${fmtSigned(value, 2)}R`

  return (
    <>
      <div className="flex items-center gap-2">
        <h3 className="panel-title">Live account</h3>
        <span className="flex-1" />
        <span className="text-2xs font-bold text-bear">🔴 REAL MONEY</span>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Stat label="Wallet" value={wallet != null ? walletMoney(wallet) : '—'}
              tone="text-bear" hint="Available funds — Angel One (rmsLimit)" />
        <Stat label="Open risk" value={money(live?.open_risk)}
              tone="text-warn" hint="What the open book loses if every stop is hit, charges included" />
        <Stat label="Realised today" value={money(live?.realised_today)}
              tone={(live?.realised_today ?? 0) >= 0 ? 'text-bull' : 'text-bear'}
              hint="Today's closed P&L, net of estimated charges" />
      </div>

      {stats && (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Trades" value={`${stats.totalTrades}`}
                  sub={`${stats.wins}W / ${stats.losses}L`}
                  hint="Closed live executions" />
            <Stat label="Win rate" value={`${stats.winRate.toFixed(0)}%`}
                  hint="Share of closed trades that finished net positive" />
            <Stat label="Expectancy" value={rOrDash(stats.expectancyR)}
                  tone={(stats.expectancyR ?? 0) >= 0 ? 'text-bull' : 'text-bear'}
                  hint="Average R per trade after charges, where the risk at entry was recorded" />
          </div>

          <div className="grid grid-cols-3 gap-2">
            <Stat label="Profit factor" value={stats.profitFactor.toFixed(2)}
                  hint="Gross wins ÷ gross losses. Below 1.0 the strategy loses." />
            <Stat label="Max drawdown" value={money(stats.maxDrawdown)}
                  tone="text-bear" hint="Largest peak-to-trough fall in realised P&L" />
            <Stat label="Avg win / loss"
                  value={`${rOrDash(stats.avgWinR)} / ${rOrDash(stats.avgLossR)}`}
                  hint="Average R on winners and on losers" />
          </div>
        </>
      )}

      <div className="border-t border-hairline pt-2">
        <h4 className="text-[9px] uppercase tracking-wider text-faint mb-1.5">
          Trade history {live?.open_positions.length ? `(${live.open_positions.length} open)` : ''}
        </h4>
        <div className="max-h-52 overflow-y-auto space-y-1 pr-0.5">
          {live?.open_positions.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5
                                        bg-accent/10 border border-accent/30">
              <div className="min-w-0">
                <span className={`text-2xs font-semibold ${
                  p.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>
                  {p.side}
                </span>
                <span className="text-2xs font-semibold ml-1">{p.symbol}</span>
                <span className="text-[9px] text-faint ml-1.5">
                  @ {p.average_fill_price ?? p.requested_price} · {timeAgo(p.created_at)}
                </span>
              </div>
              <span className="text-[9px] font-semibold text-accent shrink-0">OPEN</span>
            </div>
          ))}
          {stats?.closed.length ? stats.closed.slice(0, 10).map((t) => (
            <div key={t.id}
                 className={`flex items-center justify-between gap-2 rounded-lg px-2 py-1.5
                             ${(t.net_pnl ?? 0) >= 0 ? 'bg-bull/[0.06]' : 'bg-bear/[0.06]'}`}>
              <div className="min-w-0">
                <span className={`text-2xs font-semibold ${
                  t.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>
                  {t.side}
                </span>
                <span className="text-2xs font-semibold ml-1">{t.symbol}</span>
                <span className="text-[9px] text-faint ml-1.5">
                  {t.closed_at ? timeAgo(t.closed_at) : ''} · {t.exit_reason ?? ''}
                </span>
              </div>
              <span className={`text-2xs font-semibold tnum shrink-0 ${
                (t.net_pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
                {fmtSigned(t.net_pnl ?? 0, 2)}
              </span>
            </div>
          )) : !live?.open_positions.length && (
            <p className="text-[10px] text-faint italic px-2 py-1">
              {live ? 'No live trades yet.' : 'Loading…'}
            </p>
          )}
        </div>
      </div>

      <p className="text-[10px] text-faint leading-relaxed border-t border-hairline pt-2">
        Real orders at Angel One. Net P&amp;L is after estimated brokerage and
        statutory charges — the contract note from Angel One is the final figure.
      </p>
    </>
  )
}

function DemoPanel({ account, paper, money, busy, onReset }: {
  account: PaperAccount
  paper: PaperState
  money: (value: number) => string
  busy: boolean
  onReset: () => void
}) {
  return (
    <>
      <div className="flex items-center gap-2">
        <h3 className="panel-title">Demo account</h3>
        <span className="flex-1" />
        <button type="button" className="chip !py-0.5" onClick={onReset} disabled={busy}
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
    </>
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
