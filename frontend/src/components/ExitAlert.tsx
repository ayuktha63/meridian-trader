import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { fmtPips, fmtSigned } from '../lib/format'
import type { LiveTick } from '../lib/types'
import { useStore } from '../store/useStore'

/**
 * The alert you cannot miss.
 *
 * It fires from the one-second price tier, not from the analysis loop — a stop
 * being hit is arithmetic, and waiting for the next structural pass to notice
 * would be exactly the wrong time to be slow. Terminal alerts (stop or target
 * reached) require acknowledgement; proximity warnings clear themselves.
 */
const TONE = {
  stop_hit: {
    ring: 'border-bear', glow: 'shadow-[0_0_120px_-10px_rgba(255,77,106,0.85)]',
    text: 'text-bear', bg: 'from-bear/25', beep: 220,
  },
  target_hit: {
    ring: 'border-bull', glow: 'shadow-[0_0_120px_-10px_rgba(47,211,160,0.85)]',
    text: 'text-bull', bg: 'from-bull/25', beep: 660,
  },
  approaching_stop: {
    ring: 'border-warn', glow: 'shadow-[0_0_90px_-20px_rgba(245,196,83,0.7)]',
    text: 'text-warn', bg: 'from-warn/20', beep: 330,
  },
  approaching_target: {
    ring: 'border-warn', glow: 'shadow-[0_0_90px_-20px_rgba(245,196,83,0.7)]',
    text: 'text-warn', bg: 'from-warn/20', beep: 520,
  },
  // A rung of the protection ladder arming. Good news, so it reads calmer than
  // the exit alerts — but it still interrupts, because taking protection late
  // is the same as not taking it.
  protect: {
    ring: 'border-accent', glow: 'shadow-[0_0_90px_-20px_rgba(76,141,255,0.7)]',
    text: 'text-accent', bg: 'from-accent/18', beep: 780,
  },
} as const

type AlertKind = keyof typeof TONE

const COUNTDOWN_SECONDS = 5
const PROXIMITY_AUTO_DISMISS_MS = 14_000

function beep(frequency: number, times = 3) {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as {
      webkitAudioContext: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    for (let i = 0; i < times; i += 1) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'square'
      osc.frequency.value = frequency
      const start = ctx.currentTime + i * 0.28
      gain.gain.setValueAtTime(0.0001, start)
      gain.gain.exponentialRampToValueAtTime(0.16, start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.2)
      osc.connect(gain).connect(ctx.destination)
      osc.start(start)
      osc.stop(start + 0.22)
    }
    window.setTimeout(() => void ctx.close(), times * 300 + 400)
  } catch {
    /* audio is a bonus; the visual alert is the contract */
  }
}

export default function ExitAlert() {
  const alert = useStore((s) => s.exitAlert)
  const dismiss = useStore((s) => s.dismissExitAlert)
  const soundOn = useStore((s) => s.alertSound)
  const requestAnalysis = useStore((s) => s.requestAnalysis)
  const [countdown, setCountdown] = useState(COUNTDOWN_SECONDS)
  const [closing, setClosing] = useState(false)
  const firedFor = useRef<string | null>(null)

  const kind = (alert?.alert ?? 'approaching_stop') as AlertKind
  const tone = TONE[kind] ?? TONE.approaching_stop
  const terminal = kind === 'stop_hit' || kind === 'target_hit'
  const protect = kind === 'protect'

  useEffect(() => {
    if (!alert) return
    const key = `${alert.position_id}:${alert.alert}`
    if (firedFor.current === key) return
    firedFor.current = key

    setCountdown(COUNTDOWN_SECONDS)
    if (soundOn) beep(tone.beep, terminal ? 4 : 2)

    const ticker = window.setInterval(
      () => setCountdown((value) => Math.max(0, value - 1)), 1000)
    const auto = terminal
      ? undefined
      : window.setTimeout(() => dismiss(), PROXIMITY_AUTO_DISMISS_MS)

    return () => {
      window.clearInterval(ticker)
      if (auto) window.clearTimeout(auto)
    }
  }, [alert, soundOn, terminal, tone.beep, dismiss])

  useEffect(() => {
    if (!alert) firedFor.current = null
  }, [alert])

  const markClosed = async () => {
    if (!alert) return
    setClosing(true)
    try {
      await api.closePosition(alert.position_id, alert.price)
      dismiss()
      requestAnalysis()
    } finally {
      setClosing(false)
    }
  }

  return (
    <AnimatePresence>
      {alert && (
        <motion.div
          className="fixed inset-0 z-[200] flex items-start justify-center pt-24 px-6
                     pointer-events-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          {/* A wash across the whole screen so it registers in peripheral vision. */}
          <motion.div
            className={`absolute inset-0 bg-gradient-to-b ${tone.bg} to-transparent`}
            animate={{ opacity: terminal ? [0.85, 0.25, 0.85] : [0.5, 0.2, 0.5] }}
            transition={{ duration: 1.1, repeat: Infinity }}
          />

          <motion.div
            className={`relative pointer-events-auto w-full max-w-2xl rounded-2xl border-2
                        ${tone.ring} ${tone.glow} bg-panel/[0.97] backdrop-blur-xl
                        overflow-hidden`}
            initial={{ scale: 0.92, y: -20 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.95, y: -12 }}
            transition={{ type: 'spring', stiffness: 320, damping: 24 }}
          >
            <div className="flex items-center gap-4 px-6 py-5">
              <motion.div
                className={`h-14 w-14 shrink-0 rounded-full border-2 ${tone.ring}
                            grid place-items-center`}
                animate={{ scale: [1, 1.08, 1] }}
                transition={{ duration: 0.9, repeat: Infinity }}
              >
                <span className={`text-2xl font-bold tnum ${tone.text}`}>
                  {terminal ? countdown : protect ? '✓' : '!'}
                </span>
              </motion.div>

              <div className="flex-1 min-w-0">
                <h2 className={`text-2xl font-bold tracking-tight ${tone.text}`}>
                  {alert.headline}
                </h2>
                <p className="text-xs text-ink/85 leading-relaxed mt-1">{alert.detail}</p>
              </div>

              <div className="text-right shrink-0">
                <div className={`text-2xl font-bold tnum ${
                  alert.r_multiple >= 0 ? 'text-bull' : 'text-bear'}`}>
                  {fmtSigned(alert.r_multiple, 2)}R
                </div>
                <div className="text-2xs text-faint tnum">
                  {fmtSigned(alert.unrealised_pips, 1)} pips
                </div>
                <div className="text-2xs text-faint tnum">@ {alert.price}</div>
              </div>
            </div>

            <div className="flex items-center gap-2 px-6 py-3 border-t border-hairline
                            bg-tint/[0.02]">
              <span className="text-2xs text-faint flex-1">
                {terminal
                  ? 'Act in your broker first. This panel only tracks the trade.'
                  : protect
                    ? `Move the stop to ${alert.suggested_stop} in your broker. Until you do, this protection exists only on screen.`
                    : `Stop ${fmtPips(alert.distance_to_stop_pips)} · target ${fmtPips(alert.distance_to_target_pips)}`}
              </span>
              {terminal && (
                <button type="button" className="btn btn-primary" onClick={markClosed}
                        disabled={closing}
                        title="Mark this position closed here. Do it in your broker first.">
                  I've exited
                </button>
              )}
              <button type="button" className="btn" onClick={dismiss}
                      title="Hide this alert. The position stays open and tracked.">
                Dismiss
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export type { LiveTick }
