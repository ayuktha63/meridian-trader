import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { fmtSigned } from '../lib/format'
import { useStore } from '../store/useStore'
import type { PaperTrade } from '../lib/types'

/**
 * The demo account's own receipt: the auto-trader just did something, here it
 * is. Distinct from ExitAlert (which asks the trader to act on a real
 * position) — this one is purely informational, always self-dismisses, and
 * never blocks the chart. A full-screen flicker rides alongside it because a
 * corner toast is easy to miss if you are not looking at the chart when a
 * trade fires — the flicker registers in peripheral vision.
 */
const AUTO_DISMISS_MS = 6_000
const FLASH_MS = 700

type FlashKind = 'open' | 'win' | 'loss'

const FLASH_COLOR: Record<FlashKind, string> = {
  open: 'rgba(76,141,255,0.35)',
  win: 'rgba(47,211,160,0.4)',
  loss: 'rgba(255,77,106,0.4)',
}

const TONE: Record<FlashKind, { ring: string; text: string; bg: string }> = {
  open: { ring: 'border-accent', text: 'text-accent', bg: 'from-accent/20' },
  win: { ring: 'border-bull', text: 'text-bull', bg: 'from-bull/20' },
  loss: { ring: 'border-bear', text: 'text-bear', bg: 'from-bear/20' },
}

function beep(frequency: number, times = 2) {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as {
      webkitAudioContext: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    for (let i = 0; i < times; i += 1) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = frequency
      const start = ctx.currentTime + i * 0.16
      gain.gain.setValueAtTime(0.0001, start)
      gain.gain.exponentialRampToValueAtTime(0.14, start + 0.015)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16)
      osc.connect(gain).connect(ctx.destination)
      osc.start(start)
      osc.stop(start + 0.18)
    }
    window.setTimeout(() => void ctx.close(), times * 200 + 300)
  } catch {
    /* audio is a bonus; the toast itself is the contract */
  }
}

function kindOf(trade: PaperTrade): FlashKind {
  if (trade.status === 'open') return 'open'
  return (trade.net_pnl ?? 0) >= 0 ? 'win' : 'loss'
}

export default function PaperFillToast() {
  const fill = useStore((s) => s.paperFill)
  const dismiss = useStore((s) => s.dismissPaperFill)
  const soundOn = useStore((s) => s.alertSound)
  const seenId = useRef<string | null>(null)
  const [flash, setFlash] = useState<FlashKind | null>(null)

  const trade = fill?.trade

  useEffect(() => {
    if (!trade) return
    const key = `${trade.id}:${trade.status}`
    if (seenId.current === key) return
    seenId.current = key

    const kind = kindOf(trade)
    if (soundOn) {
      if (kind === 'open') beep(520, 2)
      else beep(kind === 'win' ? 660 : 300, kind === 'win' ? 3 : 2)
    }

    setFlash(kind)
    const clearFlash = window.setTimeout(() => setFlash(null), FLASH_MS)
    const timer = window.setTimeout(() => dismiss(), AUTO_DISMISS_MS)
    return () => {
      window.clearTimeout(clearFlash)
      window.clearTimeout(timer)
    }
  }, [trade, soundOn, dismiss])

  const kind = trade ? kindOf(trade) : null
  const tone = kind ? TONE[kind] : TONE.open
  const opened = kind === 'open'

  return (
    <>
      {/* Full-screen flicker — noticeable even if you are not looking at the
          AUTO badge. Two quick pulses, never blocks input. */}
      <AnimatePresence>
        {flash && (
          <motion.div
            key={`${trade?.id}:${trade?.status}`}
            className="fixed inset-0 z-[300] pointer-events-none"
            style={{ backgroundColor: FLASH_COLOR[flash] }}
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0.15, 0.85, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: FLASH_MS / 1000, times: [0, 0.15, 0.4, 0.55, 1] }}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {trade && (
          <motion.div
            className="fixed top-20 right-4 z-[190] pointer-events-none flex justify-end"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <motion.div
              className={`pointer-events-auto w-80 rounded-xl border-2 ${tone.ring}
                          bg-panel/[0.97] backdrop-blur-xl overflow-hidden shadow-2xl`}
              initial={{ x: 40, opacity: 0, scale: 0.95 }}
              animate={{ x: 0, opacity: 1, scale: 1 }}
              exit={{ x: 40, opacity: 0, scale: 0.95 }}
              transition={{ type: 'spring', stiffness: 340, damping: 26 }}
            >
              <div className={`bg-gradient-to-b ${tone.bg} to-transparent px-4 py-3`}>
                <div className="flex items-center justify-between">
                  <span className={`text-2xs font-bold uppercase tracking-wider ${tone.text}`}>
                    {opened ? 'Demo auto-trade opened' : 'Demo auto-trade closed'}
                  </span>
                  <button type="button" className="text-faint hover:text-ink text-2xs"
                          onClick={dismiss} aria-label="Dismiss">
                    ✕
                  </button>
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <span className="text-lg font-bold tnum">
                    {trade.side} {trade.symbol}
                  </span>
                  {opened ? (
                    <span className="text-xs text-faint tnum">@ {trade.entry}</span>
                  ) : (
                    <span className={`text-lg font-bold tnum ${
                      kind === 'win' ? 'text-bull' : 'text-bear'}`}>
                      {fmtSigned(trade.net_pnl, 2)}
                    </span>
                  )}
                </div>
                {!opened && trade.realised_r != null && (
                  <div className="text-2xs text-faint tnum mt-0.5">
                    {fmtSigned(trade.realised_r, 2)}R · {trade.exit_reason}
                  </div>
                )}
                <p className="text-2xs text-ink/80 mt-1.5 leading-relaxed">{fill?.reason}</p>
                <p className="text-2xs text-faint mt-1 italic">DEMO — no real money moved.</p>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
