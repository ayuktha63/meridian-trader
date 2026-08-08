import { useState } from 'react'
import type { OptionLeg } from '../../lib/types'
import { useStore } from '../../store/useStore'
import { Badge } from '../ui'

/**
 * "How do I actually place this?"
 *
 * The signal panel gives a level on the underlying. This panel turns it into a
 * contract: the future, or a strike and an expiry — with the two numbers that
 * catch people out made unmissable. The stop is quoted in *premium*, because
 * that is what the broker fills; and the daily theta is quoted as a percentage
 * of premium, because that is the rent the trade pays for being open.
 */
const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
})

export default function DerivativesPanel() {
  const analysis = useStore((s) => s.analysis)
  const derivatives = analysis?.derivatives
  const [mode, setMode] = useState<'options' | 'futures'>('options')

  if (!derivatives) {
    return (
      <div className="p-4 text-2xs text-faint leading-relaxed space-y-2">
        <p>
          No listed derivative for this instrument, or no directional call to
          express yet.
        </p>
        <p>
          Futures and options appear here for NIFTY, BANK NIFTY, the NSE F&amp;O
          stocks, currency futures and MCX commodities — whenever the engine
          returns a BUY or SELL with a stop.
        </p>
      </div>
    )
  }

  const long = derivatives.direction === 'long'
  const fromVerdict = derivatives.source === 'verdict'

  return (
    <div className="h-full scroll-y">
      <div className="px-3 py-2.5 border-b border-hairline space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="panel-title">
            {fromVerdict ? 'Place this trade' : 'If this setup triggers'}
          </h3>
          <Badge tone={long ? 'bull' : 'bear'}>
            {long ? 'bullish' : 'bearish'} · {long ? 'CE' : 'PE'}
          </Badge>
          <span className="flex-1" />
          <span className="text-2xs text-faint tnum"
                title="Annualised volatility inferred from recent price range, not quoted from the option chain">
            IV≈{derivatives.implied_vol_estimate.toFixed(0)}%
          </span>
        </div>

        {!fromVerdict && (
          <p className="text-[10px] text-warn/90 leading-relaxed">
            The engine has not issued a call. These levels come from the{' '}
            <span className="text-ink">{derivatives.source}</span> strategy, priced
            so you know the contract and its decay before the setup triggers —
            not as a recommendation to take it now.
          </p>
        )}

        <div className="flex gap-1">
          {(['options', 'futures'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setMode(tab)}
              className={`chip !py-0.5 capitalize ${mode === tab ? 'chip-active' : ''}`}
              title={tab === 'options'
                ? 'Defined maximum loss, but decay works against you every day'
                : 'Tracks the underlying one-for-one, with uncapped loss'}
            >
              {tab}
            </button>
          ))}
          <span className="flex-1" />
          <span className="text-[10px] text-faint tnum self-center"
                title="Contract lot size — verify against your broker, the exchange revises it">
            lot {derivatives.lot_size}
          </span>
        </div>
      </div>

      {mode === 'options' ? (
        <div className="divide-y divide-hairline">
          {derivatives.options.map((leg) => (
            <OptionRow key={leg.contract} leg={leg}
                       recommended={leg.contract === derivatives.recommended} />
          ))}

          <div className="px-3 py-2.5 space-y-1.5">
            {derivatives.notes.map((note, index) => (
              <p key={index} className="text-2xs text-ink/80 leading-relaxed flex gap-1.5">
                <span className="text-accent shrink-0">·</span>
                <span>{note}</span>
              </p>
            ))}
          </div>
        </div>
      ) : (
        <div className="px-3 py-2.5 space-y-2.5">
          <div className="rounded-lg border border-hairline bg-tint/[0.03] px-3 py-2.5">
            <div className="flex items-center gap-2">
              <span className="text-2xs font-semibold text-ink">
                {derivatives.futures_symbol}
              </span>
              <Badge tone="neutral">{long ? 'BUY' : 'SELL'}</Badge>
              <span className="flex-1" />
              <span className="text-[10px] text-faint">
                expiry {derivatives.futures_expiry}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2 mt-2">
              <Stat label="Risk / lot"
                    value={INR.format(derivatives.futures_risk_per_lot)}
                    tone="text-bear"
                    hint="Cash lost per lot if the stop fills exactly" />
              <Stat label="Reward / lot"
                    value={derivatives.futures_reward_per_lot
                      ? INR.format(derivatives.futures_reward_per_lot) : '—'}
                    tone="text-bull"
                    hint="Cash gained per lot if the target fills" />
              <Stat label="Lots"
                    value={derivatives.futures_lots?.toString() ?? '—'}
                    hint="Whole lots your per-trade risk budget affords" />
            </div>
          </div>

          {derivatives.futures_notes.map((note, index) => (
            <p key={index} className="text-2xs text-ink/80 leading-relaxed flex gap-1.5">
              <span className="text-warn shrink-0">·</span>
              <span>{note}</span>
            </p>
          ))}
        </div>
      )}

      {/* Expiry ladder */}
      <div className="px-3 py-2.5 border-t border-hairline space-y-1.5">
        <div className="text-[10px] uppercase tracking-wider text-faint">
          Expiries available
        </div>
        {derivatives.expiries.map((expiry) => (
          <div key={expiry.date} className="flex gap-2 items-start">
            <span className="text-2xs tnum text-ink w-[86px] shrink-0">
              {expiry.date}
            </span>
            <span className="text-[10px] text-faint w-[52px] shrink-0 tnum">
              {expiry.days_to_expiry}d
            </span>
            <span className="text-[10px] text-faint leading-snug">{expiry.note}</span>
          </div>
        ))}
      </div>

      <p className="px-3 py-2.5 text-[10px] leading-relaxed text-warn/85 border-t
                    border-hairline">
        {derivatives.disclaimer}
      </p>
    </div>
  )
}

function OptionRow({ leg, recommended }: { leg: OptionLeg; recommended: boolean }) {
  const [open, setOpen] = useState(recommended)
  // Above ~3%/day the decay is the dominant term in the trade, not the view.
  const heavyDecay = leg.theta_pct_per_day >= 3

  return (
    <div className={recommended ? 'bg-accent/[0.06]' : ''}>
      <button type="button" onClick={() => setOpen(!open)}
              title="Expand for the premium stop, break-even, decay and sizing"
              aria-expanded={open}
              className="w-full text-left px-3 py-2 hover:bg-tint/[0.03] transition-colors">
        <div className="flex items-center gap-2">
          <span className={`text-[10px] px-1 rounded ${
            leg.moneyness === 'ATM' ? 'bg-accent/20 text-accent'
              : leg.moneyness === 'ITM' ? 'bg-bull/15 text-bull'
              : 'bg-tint/[0.06] text-faint'}`}>
            {leg.moneyness}
          </span>
          <span className="text-2xs font-semibold text-ink truncate">{leg.contract}</span>
          <span className="flex-1" />
          {recommended && <Badge tone="accent">pick</Badge>}
          <span className="text-2xs tnum text-ink font-semibold">
            ₹{leg.estimated_premium.toFixed(2)}
          </span>
        </div>

        <div className="flex gap-3 mt-1 text-[10px] tnum text-faint">
          <span title="How much of the underlying's move the premium captures">
            Δ {Math.abs(leg.delta).toFixed(2)}
          </span>
          <span className={heavyDecay ? 'text-warn' : undefined}
                title="Premium lost per day to time decay, as a share of the premium">
            θ {leg.theta_pct_per_day.toFixed(1)}%/day
          </span>
          <span title="Cash outlay for one lot — also the maximum loss">
            {INR.format(leg.cost_per_lot)}/lot
          </span>
        </div>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2">
          <div className="grid grid-cols-3 gap-2 rounded-lg bg-tint/[0.03] px-2.5 py-2">
            <Stat label="Stop (premium)" value={`₹${leg.premium_stop.toFixed(2)}`}
                  tone="text-bear"
                  hint="Your underlying stop converted through delta. This is the
                        number to put in the broker — not the underlying level." />
            <Stat label="Target (premium)"
                  value={leg.premium_target ? `₹${leg.premium_target.toFixed(2)}` : '—'}
                  tone="text-bull"
                  hint="Premium if the underlying reaches the target" />
            <Stat label="Break-even" value={leg.breakeven.toFixed(2)}
                  hint="Underlying level at expiry where you get your premium back" />
          </div>

          <div className="grid grid-cols-3 gap-2 text-[10px]">
            <Stat label="Max loss" value={INR.format(leg.max_loss_per_lot)}
                  tone="text-bear"
                  hint="A bought option can go to zero — this is the whole premium" />
            <Stat label="Lots" value={leg.suggested_lots?.toString() ?? '—'}
                  hint="Sized on the premium risk to your stop, not the full premium" />
            <Stat label="Days left" value={`${leg.days_to_expiry}`}
                  hint="Calendar days to expiry" />
          </div>

          <p className="text-[10px] text-faint leading-relaxed">{leg.note}</p>

          {heavyDecay && (
            <p className="text-[10px] text-warn leading-relaxed">
              Decay is {leg.theta_pct_per_day.toFixed(1)}% of the premium per day.
              Held flat for a week this loses about
              {' '}{(leg.theta_pct_per_day * 7).toFixed(0)}% before the market has
              done anything at all.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, tone, hint }: {
  label: string; value: string; tone?: string; hint?: string
}) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[10px] uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`text-2xs font-semibold tnum truncate ${tone ?? 'text-ink'}`}>{value}</div>
    </div>
  )
}
