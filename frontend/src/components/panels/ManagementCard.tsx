import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { api } from '../../lib/api'
import { fmtPips, fmtPrice, fmtSigned } from '../../lib/format'
import type { ManagementAction, ManagementSignal, Position } from '../../lib/types'
import { useActiveInstrument, useStore } from '../../store/useStore'
import { Badge, Spinner } from '../ui'

/**
 * The card that answers "hold or sell". It replaces the entry panel as the
 * top-of-rail item whenever a position is open, because once you are in a
 * trade the entry signal is no longer the decision in front of you.
 */
const TONE: Record<ManagementAction, {
  label: string; text: string; bg: string; border: string
}> = {
  HOLD: { label: 'HOLD', text: 'text-accent', bg: 'bg-accent/10', border: 'border-accent/40' },
  TRAIL_STOP: { label: 'MOVE STOP', text: 'text-bull', bg: 'bg-bull/10', border: 'border-bull/40' },
  TAKE_PARTIAL: { label: 'TAKE PARTIAL', text: 'text-warn', bg: 'bg-warn/10', border: 'border-warn/40' },
  EXIT: { label: 'EXIT NOW', text: 'text-bear', bg: 'bg-bear/12', border: 'border-bear/50' },
  STOPPED: { label: 'STOPPED OUT', text: 'text-bear', bg: 'bg-bear/12', border: 'border-bear/50' },
  TARGET_HIT: { label: 'TARGET HIT', text: 'text-bull', bg: 'bg-bull/12', border: 'border-bull/50' },
}

export default function ManagementCard({
  signal, position, onChanged,
}: {
  signal: ManagementSignal
  position: Position
  onChanged: () => void
}) {
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5
  const requestAnalysis = useStore((s) => s.requestAnalysis)
  const liveTick = useStore((s) => s.liveTick)
  const [busy, setBusy] = useState(false)

  const tone = TONE[signal.action]
  // The action and its reasoning come from the structural pass; the numbers
  // come from the one-second tier so P&L is never stale on screen.
  const live = liveTick?.position_id === position.id ? liveTick : null
  const r = live?.r_multiple ?? signal.r_multiple
  const pips = live?.unrealised_pips ?? signal.unrealised_pips
  const toStop = live?.distance_to_stop_pips ?? signal.distance_to_stop_pips
  const toTarget = live?.distance_to_target_pips ?? signal.distance_to_target_pips
  const profitable = r >= 0

  const tightenStop = async () => {
    if (!signal.suggested_stop) return
    setBusy(true)
    try {
      await api.updatePosition(position.id, { stop_loss: signal.suggested_stop })
      onChanged()
      requestAnalysis()
    } finally {
      setBusy(false)
    }
  }

  const close = async () => {
    setBusy(true)
    try {
      await api.closePosition(position.id, signal.current_price)
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
          <AnimatePresence mode="wait">
            <motion.span
              key={signal.action}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              className={`text-lg font-bold tracking-tight ${tone.text}`}
            >
              {tone.label}
            </motion.span>
          </AnimatePresence>
          {signal.urgency !== 'normal' && (
            <Badge tone={signal.urgency === 'immediate' ? 'bear' : 'warn'}>
              {signal.urgency}
            </Badge>
          )}
          <span className="flex-1" />
          <span className={`text-lg font-bold tnum ${profitable ? 'text-bull' : 'text-bear'}`}
                title="Updates every tick, not only on re-analysis">
            {fmtSigned(r, 2)}R
          </span>
        </div>
        <p className="text-2xs text-ink/80 mt-0.5">{signal.headline}</p>
      </div>

      {/* Position facts */}
      <div className="grid grid-cols-4 gap-2 px-4 py-2.5 border-b border-hairline">
        <Cell label={position.side === 'LONG' ? 'Long from' : 'Short from'}
              value={fmtPrice(position.entry, digits)} />
        <Cell label="Stop" value={fmtPrice(position.stop_loss, digits)}
              sub={`${fmtPips(toStop)} away`} tone="text-bear" />
        <Cell label="Target"
              value={position.take_profit ? fmtPrice(position.take_profit, digits) : '—'}
              sub={position.take_profit ? `${fmtPips(toTarget)} away` : undefined}
              tone="text-bull" />
        <Cell label="Open" value={`${signal.bars_held} bars`}
              sub={`${fmtSigned(pips, 1)}p`} />
      </div>

      {/* Why */}
      <div className="px-4 py-2.5 space-y-1.5 max-h-[190px] scroll-y">
        {signal.reasons.map((reason, index) => (
          <p key={index} className="text-2xs text-ink/85 leading-relaxed flex gap-1.5">
            <span className={`${tone.text} shrink-0`}>·</span>
            <span>{reason}</span>
          </p>
        ))}
        {signal.cautions.map((caution, index) => (
          <p key={`c${index}`} className="text-2xs text-warn/90 leading-relaxed flex gap-1.5">
            <span className="shrink-0">!</span>
            <span>{caution}</span>
          </p>
        ))}
        {signal.max_favourable_r > r + 0.3 && (
          <p className="text-2xs text-faint leading-relaxed">
            Best so far {fmtSigned(signal.max_favourable_r, 2)}R.
          </p>
        )}
        {signal.invalidation && (
          <p className="text-2xs text-faint leading-relaxed pt-1">
            <span className="uppercase tracking-wider mr-1">Invalidation</span>
            {signal.invalidation}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-t border-hairline bg-tint/[0.015]">
        {signal.suggested_stop && (
          <button type="button" className="btn btn-primary flex-1" onClick={tightenStop}
                  disabled={busy}
                  title={signal.suggested_stop_reason ?? undefined}>
            {busy ? <Spinner /> : null}
            Stop → {fmtPrice(signal.suggested_stop, digits)}
          </button>
        )}
        <button type="button" className="btn flex-1" onClick={close} disabled={busy}
                title="Stop tracking this position. Close it in your broker first.">
          {busy ? <Spinner /> : null}
          I've closed it
        </button>
      </div>

      <p className="px-4 pb-2.5 text-[10px] leading-snug text-faint/70">
        Meridian cannot see your broker. Adjusting the stop here only updates this
        tracker — move it in Kite too, or the protection is imaginary.
      </p>
    </div>
  )
}

function Cell({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: string
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`text-2xs font-semibold tnum truncate ${tone ?? 'text-ink'}`}>{value}</div>
      {sub && <div className="text-[10px] text-faint tnum truncate">{sub}</div>}
    </div>
  )
}
