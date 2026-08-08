import { motion } from 'framer-motion'
import { clamp } from '../../lib/format'
import { useStore } from '../../store/useStore'
import { Badge, EmptyState } from '../ui'

const LABEL: Record<string, string> = {
  '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
  '1h': '1H', '4h': '4H', '1d': 'D', '1w': 'W',
}

export default function MTFPanel() {
  const analysis = useStore((s) => s.analysis)
  if (!analysis) return <EmptyState title="No timeframe data yet" />

  // The backend already orders the ladder macro → micro (Daily first), which
  // is the order a trader reads it in: the higher timeframe sets the context
  // the lower ones are judged against.
  const { mtf } = analysis
  const views = mtf.views

  return (
    <div className="h-full scroll-y px-3 py-3 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-2xs uppercase tracking-wider text-faint">Fused bias</div>
          <div className={`text-lg font-semibold capitalize ${
            mtf.aligned_bias === 'bullish' ? 'text-bull'
              : mtf.aligned_bias === 'bearish' ? 'text-bear' : 'text-muted'}`}>
            {mtf.aligned_bias}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xs uppercase tracking-wider text-faint">Alignment</div>
          <div className="text-lg font-semibold tnum text-ink">
            {Math.round(mtf.alignment_score * 100)}%
          </div>
        </div>
      </div>

      <div className="space-y-1.5">
        {views.map((view) => {
          const positive = view.score >= 0
          const magnitude = clamp(Math.abs(view.score) * 100, 2, 100)
          return (
            <div key={view.timeframe} className="group">
              <div className="flex items-center gap-2">
                <span className="w-7 shrink-0 text-2xs font-semibold tnum text-muted">
                  {LABEL[view.timeframe] ?? view.timeframe}
                </span>
                <div className="relative flex-1 h-5 rounded-md bg-tint/[0.04] overflow-hidden">
                  <div className="absolute inset-y-0 left-1/2 w-px bg-tint/15" />
                  <motion.div
                    className={`absolute inset-y-0 ${positive ? 'bg-bull/25' : 'bg-bear/25'}`}
                    initial={false}
                    animate={{
                      left: positive ? '50%' : `${50 - magnitude / 2}%`,
                      width: `${magnitude / 2}%`,
                    }}
                    transition={{ type: 'spring', stiffness: 200, damping: 26 }}
                  />
                  <span className="absolute inset-0 flex items-center px-2 text-2xs text-ink/80">
                    {view.structure}
                    {view.note && (
                      <span className="ml-1.5 text-faint truncate">· {view.note}</span>
                    )}
                  </span>
                </div>
                <span className={`w-9 shrink-0 text-right text-2xs tnum font-semibold
                                  ${positive ? 'text-bull' : 'text-bear'}`}>
                  {view.score >= 0 ? '+' : ''}{view.score.toFixed(2)}
                </span>
              </div>
              <div className="ml-9 mt-0.5 flex gap-2 text-[10px] text-faint tnum">
                {view.rsi !== null && view.rsi !== undefined && <span>RSI {view.rsi.toFixed(0)}</span>}
                {view.adx !== null && view.adx !== undefined && <span>ADX {view.adx.toFixed(0)}</span>}
                {view.ema_stack && <span>EMA {view.ema_stack}</span>}
              </div>
            </div>
          )
        })}
      </div>

      {mtf.conflicts.length > 0 && (
        <div>
          <h3 className="panel-title mb-1.5">Conflicts</h3>
          <div className="flex flex-wrap gap-1">
            {mtf.conflicts.map((conflict) => (
              <Badge key={conflict} tone="warn">{conflict}</Badge>
            ))}
          </div>
        </div>
      )}

      <p className="text-2xs text-faint leading-relaxed">{mtf.narrative}</p>
    </div>
  )
}
