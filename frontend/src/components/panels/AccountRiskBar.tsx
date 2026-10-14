import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import type { AccountRisk, GuardrailStatus } from '../../lib/types'

/**
 * The account-level circuit breaker.
 *
 * Per-trade risk management stops one trade from being a disaster. This exists
 * because that is not what kills accounts — a sequence is. Three losses, then
 * a bigger position to win it back, then a bigger one still.
 *
 * On HALT it collapses the trade panel and says why. It cannot stop you from
 * trading, and it does not pretend to; it refuses to help, which is the only
 * honest lever a tool has here.
 */
const TONE: Record<GuardrailStatus, {
  text: string; bg: string; border: string; dot: string
}> = {
  clear: {
    text: 'text-muted', bg: 'bg-tint/[0.03]',
    border: 'border-hairline', dot: 'bg-bull',
  },
  caution: {
    text: 'text-warn', bg: 'bg-warn/10', border: 'border-warn/40', dot: 'bg-warn',
  },
  halt: {
    text: 'text-bear', bg: 'bg-bear/12', border: 'border-bear/50', dot: 'bg-bear',
  },
}

export default function AccountRiskBar({ risk }: { risk: AccountRisk }) {
  const [open, setOpen] = useState(risk.status !== 'clear')
  const tone = TONE[risk.status] ?? TONE.clear
  const money = (value: number) =>
    `${risk.currency} ${Math.abs(value).toLocaleString(undefined, {
      maximumFractionDigits: 0 })}`

  return (
    <div className={`rounded-xl border ${tone.border} ${tone.bg} overflow-hidden`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title="Account-level risk limits — daily loss, losing streak, open exposure and trade count"
        className="w-full px-3 py-2 text-left hover:bg-tint/[0.03] transition-colors"
      >
        <div className="flex items-center gap-2">
          {risk.status === 'halt' ? (
            <motion.span className={`h-1.5 w-1.5 rounded-full shrink-0 ${tone.dot}`}
                         animate={{ opacity: [1, 0.3, 1] }}
                         transition={{ duration: 1.1, repeat: Infinity }} />
          ) : (
            <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${tone.dot}`} />
          )}
          <span className={`text-2xs font-semibold ${tone.text}`}>{risk.headline}</span>
          <span className="flex-1" />
          <span className="text-[10px] tnum text-faint"
                title="Realised today across all closed trades">
            {risk.realised_today_r >= 0 ? '+' : ''}{risk.realised_today_r.toFixed(1)}R today
          </span>
          <span className="text-[10px] tnum text-faint"
                title="Total cash at stake across every open position">
            {risk.open_risk_r.toFixed(1)}R open
          </span>
        </div>
        {risk.status !== 'clear' && !open && (
          <p className="text-[10px] text-ink/85 leading-relaxed mt-1">{risk.reason}</p>
        )}
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-2.5 space-y-2">
              {risk.guardrails.map((rail) => {
                const railTone = TONE[rail.status] ?? TONE.clear
                const pct = rail.limit > 0
                  ? Math.min(100, (rail.value / rail.limit) * 100) : 0
                return (
                  <div key={rail.key} title={rail.detail}>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] ${
                        rail.status === 'clear' ? 'text-faint' : railTone.text}`}>
                        {rail.label}
                      </span>
                      <span className="flex-1" />
                      <span className="text-[10px] tnum text-muted">
                        {rail.value.toFixed(rail.unit === 'count' ? 0 : 1)}
                        {' / '}
                        {rail.limit.toFixed(0)}
                        {rail.unit === 'R' ? 'R' : ''}
                      </span>
                    </div>
                    <div className="mt-0.5 h-1 rounded-full bg-tint/[0.07] overflow-hidden">
                      <div className={`h-full transition-all ${railTone.dot}`}
                           style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                )
              })}

              <div className="flex gap-3 pt-0.5 text-[10px] text-faint tnum">
                <span title={risk.balance_source === 'angel_one'
                  ? 'Live available funds from Angel One (rmsLimit)'
                  : 'Account balance from Settings → Risk — not your live Angel One wallet'}>
                  Balance {money(risk.balance)}
                  {risk.balance_source === 'angel_one' ? ' 🔴' : ''}
                </span>
                <span title="Risk budget for a single trade">
                  1R = {money(risk.balance * risk.risk_per_trade_pct / 100)}
                </span>
                <span>{risk.trades_today} trades today</span>
              </div>

              {risk.notes.map((note, index) => (
                <p key={index} className={`text-[10px] leading-relaxed ${
                  risk.status === 'halt' ? 'text-bear' : 'text-faint'}`}>
                  {note}
                </p>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
