import { motion } from 'framer-motion'
import { useState } from 'react'
import { api } from '../../lib/api'
import { fmtPips, fmtPrice, fmtSigned } from '../../lib/format'
import type { Position, ProtectionPlan, ProtectionState } from '../../lib/types'
import { useActiveInstrument, useStore } from '../../store/useStore'

/**
 * The risk ladder.
 *
 * One question, answered before anything else on screen: **if this reverses
 * right now, what does it cost me?** Everything here serves that — the state
 * banner, the rung meter, the cash figure.
 *
 * The broker-sync warning is the most important element and is styled to be
 * impossible to skim past. Meridian moves its own tracked stop automatically;
 * it has no connection to your broker and cannot move the real one. A trader
 * who reads "SAFE" here while the original stop is still resting in the market
 * is in a worse position than one with no tool at all, because they have
 * stopped watching.
 */
const STATE: Record<ProtectionState, {
  label: string; text: string; bg: string; border: string; bar: string
}> = {
  at_risk: {
    label: 'AT RISK', text: 'text-bear', bg: 'bg-bear/10',
    border: 'border-bear/40', bar: 'bg-bear',
  },
  armed: {
    label: 'PROTECTION READY', text: 'text-warn', bg: 'bg-warn/12',
    border: 'border-warn/50', bar: 'bg-warn',
  },
  safe: {
    label: 'SAFE', text: 'text-accent', bg: 'bg-accent/12',
    border: 'border-accent/50', bar: 'bg-accent',
  },
  locked: {
    label: 'PROFIT LOCKED', text: 'text-bull', bg: 'bg-bull/12',
    border: 'border-bull/50', bar: 'bg-bull',
  },
}

export default function ProtectionCard({ plan, position, onChanged }: {
  plan: ProtectionPlan
  position: Position
  onChanged: () => void
}) {
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5
  const currency = useStore((s) => s.analysis?.account_risk?.currency ?? '')
  const liveTick = useStore((s) => s.liveTick)
  const requestAnalysis = useStore((s) => s.requestAnalysis)
  const [busy, setBusy] = useState(false)

  // The state and cash figures come from the structural pass; the R multiple
  // comes from the tick tier so it never looks frozen.
  const live = liveTick?.position_id === position.id ? liveTick : null
  const state = (live?.protection_state as ProtectionState) ?? plan.state
  const tone = STATE[state] ?? STATE.at_risk
  const r = live?.r_multiple ?? plan.r_multiple
  const lockedR = live?.locked_r ?? plan.locked_r

  const takeProtection = async () => {
    if (!plan.suggested_stop) return
    setBusy(true)
    try {
      await api.updatePosition(position.id, { stop_loss: plan.suggested_stop })
      onChanged()
      requestAnalysis()
    } finally {
      setBusy(false)
    }
  }

  const confirmBroker = async () => {
    setBusy(true)
    try {
      await api.updatePosition(position.id, { broker_stop: position.stop_loss })
      onChanged()
      requestAnalysis()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`glass rounded-xl overflow-hidden border ${tone.border}`}>
      <div className={`px-4 py-2.5 ${tone.bg} border-b ${tone.border}`}>
        <div className="flex items-center gap-2">
          <span className={`text-sm font-bold tracking-tight ${tone.text}`}>
            {tone.label}
          </span>
          <span className="flex-1" />
          <span className="text-2xs text-faint" title="Current open profit in risk units">
            {fmtSigned(r, 2)}R open
          </span>
        </div>
        <p className="text-2xs text-ink/85 mt-0.5 leading-relaxed">{plan.detail}</p>
      </div>

      {/* The ladder */}
      <div className="px-4 py-3 border-b border-hairline">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[10px] uppercase tracking-wider text-faint">
            Protection ladder
          </span>
          <span className="flex-1" />
          <span className={`text-2xs tnum font-semibold ${
            lockedR >= 0 ? 'text-bull' : 'text-bear'}`}
                title="What a stop-out books right now. Negative means you would still take a loss.">
            {fmtSigned(lockedR, 2)}R secured
          </span>
        </div>

        <div className="space-y-1">
          {plan.tiers.map((tier) => (
            <div key={tier.index}
                 className={`flex items-center gap-2 rounded-md px-2 py-1 transition-colors
                             ${tier.active ? tone.bg : ''}`}
                 title={tier.note}>
              <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${
                tier.reached ? tone.bar : 'bg-tint/20'}`} />
              <span className={`text-2xs ${
                tier.reached ? 'text-ink' : 'text-faint'}`}>
                {tier.name}
              </span>
              <span className="text-[10px] text-faint">at +{tier.trigger_r}R</span>
              <span className="flex-1" />
              <span className={`text-[10px] tnum ${
                tier.reached ? 'text-muted' : 'text-faint/60'}`}>
                stop {fmtPrice(tier.stop, digits)}
              </span>
            </div>
          ))}
        </div>

        {plan.next_tier && plan.to_next_tier_pips !== null && (
          <p className="mt-2 text-[10px] text-faint leading-relaxed">
            {plan.next_tier.name} arms in {fmtPips(plan.to_next_tier_pips ?? 0)} —
            {' '}{plan.next_tier.locked_r > 0
              ? `it would bank +${plan.next_tier.locked_r}R.`
              : 'it would make the trade free.'}
          </p>
        )}
      </div>

      {/* What it costs right now */}
      <div className="grid grid-cols-3 gap-2 px-4 py-2.5 border-b border-hairline">
        <Cell label="At stake"
              value={plan.risk_amount > 0
                ? `${currency} ${plan.risk_amount.toLocaleString(undefined, {
                    maximumFractionDigits: 0 })}`
                : 'nothing'}
              tone={plan.risk_amount > 0 ? 'text-bear' : 'text-bull'}
              hint="Cash you lose if the stop is hit, measured from your entry" />
        <Cell label="Peak" value={`${fmtSigned(plan.peak_r, 2)}R`}
              hint="Best this trade has been, from ticks not bar closes" />
        <Cell label="Worst" value={`${fmtSigned(plan.trough_r, 2)}R`}
              hint="Deepest it went against you before now" />
      </div>

      {/* The unmirrored-stop warning. Deliberately the loudest thing here. */}
      {!plan.broker_synced && (
        <motion.div
          className="px-4 py-3 bg-bear/15 border-b border-bear/40"
          animate={{ opacity: [1, 0.72, 1] }}
          transition={{ duration: 1.6, repeat: Infinity }}
        >
          <div className="flex items-center gap-2">
            <span className="text-2xs font-bold text-bear">
              ⚠ YOUR BROKER DOES NOT HAVE THIS STOP
            </span>
          </div>
          <p className="text-2xs text-ink/90 leading-relaxed mt-1">
            {plan.broker_warning}
          </p>
          <button type="button" className="btn btn-primary w-full mt-2" onClick={confirmBroker}
                  disabled={busy}
                  title="Only press this after you have actually moved the stop in your broker">
            I've moved it to {fmtPrice(position.stop_loss, digits)} in my broker
          </button>
        </motion.div>
      )}

      {plan.notes.map((note, index) => (
        <p key={index} className="px-4 py-1.5 text-[10px] text-warn/90 leading-relaxed">
          {note}
        </p>
      ))}

      {plan.action_available && plan.suggested_stop && (
        <div className="px-4 py-2.5 border-t border-hairline">
          <button type="button" className="btn btn-primary w-full" onClick={takeProtection}
                  disabled={busy}
                  title={plan.suggested_reason}>
            Take protection — stop to {fmtPrice(plan.suggested_stop, digits)}
          </button>
        </div>
      )}
    </div>
  )
}

function Cell({ label, value, tone, hint }: {
  label: string; value: string; tone?: string; hint?: string
}) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[10px] uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`text-2xs font-semibold tnum truncate ${tone ?? 'text-ink'}`}>{value}</div>
    </div>
  )
}
