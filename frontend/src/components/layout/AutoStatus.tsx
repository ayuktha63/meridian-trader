import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { useStore } from '../../store/useStore'

/**
 * Makes the automatic re-analysis visible.
 *
 * The loop always ran — on every bar close and on a timer — but with no
 * indicator it was indistinguishable from a stale panel, so the natural
 * reaction was to keep pressing Analyse. This shows the countdown to the next
 * run and the moment one is in flight.
 */
export default function AutoStatus() {
  const auto = useStore((s) => s.autoAnalysis)
  const pending = useStore((s) => s.analysisPending)
  const lastAt = useStore((s) => s.lastAnalysisAt)
  const interval = useStore((s) => s.autoIntervalSeconds)
  const trigger = useStore((s) => s.lastAnalysisTrigger)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  if (!auto) {
    return (
      <span className="text-[10px] text-faint tabular-nums" title="Auto-analysis is off">
        auto off
      </span>
    )
  }

  if (pending) {
    return (
      <span className="flex items-center gap-1.5 text-[10px] text-accent">
        <motion.span
          className="h-1.5 w-1.5 rounded-full bg-accent"
          animate={{ opacity: [1, 0.25, 1] }}
          transition={{ duration: 1.1, repeat: Infinity }}
        />
        analysing…
      </span>
    )
  }

  const elapsed = lastAt ? Math.floor((now - lastAt) / 1000) : null
  const remaining = elapsed === null ? null : Math.max(0, interval - elapsed)

  return (
    <span
      className="flex items-center gap-1.5 text-[10px] text-faint tabular-nums"
      title={`Re-analyses automatically every ${interval}s and on every bar close`
             + (trigger ? ` · last run: ${trigger.replace('_', ' ')}` : '')}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-bull/70" />
      {remaining === null
        ? 'auto on'
        : remaining > 0
          ? `next in ${remaining}s`
          : 'due now'}
    </span>
  )
}
