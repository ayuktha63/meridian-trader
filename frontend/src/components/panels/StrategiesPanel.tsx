import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { fmtPrice } from '../../lib/format'
import type { StrategyFamily, StrategyResult } from '../../lib/types'
import { useActiveInstrument, useStore } from '../../store/useStore'
import { Badge } from '../ui'

/**
 * Five strategies, side by side, with every precondition shown.
 *
 * The point of this panel is that a single blended verdict hides the shape of
 * the market. Reversion and breakout are mutually exclusive readings of the
 * same chart — seeing which one the conditions actually favour, and which
 * conditions are still missing, is more useful than one number.
 */
const FAMILY: Record<StrategyFamily, { label: string; dot: string }> = {
  trend: { label: 'Trend', dot: 'bg-[#4C8DFF]' },
  reversion: { label: 'Reversion', dot: 'bg-[#F5C453]' },
  breakout: { label: 'Breakout', dot: 'bg-[#2FD3A0]' },
  smc: { label: 'Smart money', dot: 'bg-[#9B8CFF]' },
  momentum: { label: 'Momentum', dot: 'bg-[#FF7A59]' },
}

export default function StrategiesPanel() {
  const analysis = useStore((s) => s.analysis)
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5
  const [open, setOpen] = useState<string | null>(null)

  const report = analysis?.strategies
  if (!report || report.results.length === 0) {
    return (
      <div className="p-4 text-2xs text-faint leading-relaxed">
        Strategies appear with the next analysis. Each one is scored on its own
        preconditions, independently of the headline verdict.
      </div>
    )
  }

  const engaged = report.results.filter((r) => r.stance !== 'flat')

  return (
    <div className="h-full scroll-y">
      {/* Consensus header */}
      <div className="px-3 py-2.5 border-b border-hairline space-y-1.5">
        <div className="flex items-center gap-2">
          <h3 className="panel-title">Strategy consensus</h3>
          <span className="flex-1" />
          <Badge tone={report.consensus_stance === 'long' ? 'bull'
                     : report.consensus_stance === 'short' ? 'bear' : 'neutral'}>
            {report.consensus_stance === 'flat'
              ? 'no consensus' : report.consensus_stance}
          </Badge>
        </div>

        <div className="flex items-center gap-2 text-2xs">
          <span className="text-faint">Engaged</span>
          <span className="tnum text-ink font-semibold">
            {engaged.length}/{report.results.length}
          </span>
          <span className="text-faint ml-2">Agreement</span>
          <span className="tnum text-ink font-semibold">
            {(report.agreement * 100).toFixed(0)}%
          </span>
        </div>

        {/* Bias bar: short weight on the left, long weight on the right. */}
        <div className="h-1.5 rounded-full bg-tint/[0.06] overflow-hidden flex"
             title="Conviction-weighted balance between long and short strategies">
          <div className="h-full bg-bear transition-all"
               style={{ width: `${Math.max(0, -report.consensus_score) * 100}%` }} />
          <span className="flex-1" />
          <div className="h-full bg-bull transition-all"
               style={{ width: `${Math.max(0, report.consensus_score) * 100}%` }} />
        </div>

        <p className="text-2xs text-muted leading-relaxed">{report.summary}</p>
      </div>

      {/* Each strategy */}
      <div className="divide-y divide-hairline">
        {report.results.map((strategy) => (
          <StrategyRow
            key={strategy.key}
            strategy={strategy}
            digits={digits}
            expanded={open === strategy.key}
            onToggle={() => setOpen(open === strategy.key ? null : strategy.key)}
          />
        ))}
      </div>

      <p className="px-3 py-2.5 text-[10px] leading-relaxed text-faint/80 border-t
                    border-hairline">
        {report.disclaimer}
      </p>
    </div>
  )
}

function StrategyRow({ strategy, digits, expanded, onToggle }: {
  strategy: StrategyResult
  digits: number
  expanded: boolean
  onToggle: () => void
}) {
  const family = FAMILY[strategy.family] ?? { label: strategy.family, dot: 'bg-tint/30' }
  const engaged = strategy.stance !== 'flat'
  const tone = strategy.stance === 'long' ? 'text-bull'
    : strategy.stance === 'short' ? 'text-bear' : 'text-faint'

  return (
    <div className={engaged ? 'bg-tint/[0.02]' : ''}>
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-3 py-2 hover:bg-tint/[0.03] transition-colors"
        title={strategy.description}
      >
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${family.dot}`} />
          <span className="text-2xs font-semibold text-ink truncate">{strategy.name}</span>
          <span className={`text-[10px] uppercase tracking-wider ${tone}`}>
            {engaged ? strategy.stance : 'stand aside'}
          </span>
          <span className="flex-1" />
          <span className="text-2xs tnum text-muted">
            {strategy.conditions_met}/{strategy.conditions_total}
          </span>
          <span className={`text-2xs tnum font-semibold ${
            strategy.conviction >= 60 ? 'text-ink' : 'text-faint'}`}>
            {strategy.conviction.toFixed(0)}%
          </span>
        </div>

        {/* Conviction meter — the 60% line is where a strategy engages. */}
        <div className="mt-1 h-1 rounded-full bg-tint/[0.06] overflow-hidden relative">
          <div className={`h-full transition-all ${
            engaged ? (strategy.stance === 'long' ? 'bg-bull' : 'bg-bear') : 'bg-tint/25'}`}
               style={{ width: `${strategy.conviction}%` }} />
          <span className="absolute inset-y-0 w-px bg-tint/30" style={{ left: '60%' }} />
        </div>

        {!expanded && strategy.waiting_for.length > 0 && (
          <p className="mt-1 text-[10px] text-faint truncate">
            Waiting on: {strategy.waiting_for.join(' · ')}
          </p>
        )}
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 space-y-2.5">
              <p className="text-[10px] text-muted leading-relaxed">
                {strategy.description}
              </p>

              {/* Levels */}
              {strategy.entry !== null && strategy.entry !== undefined && (
                <div className="grid grid-cols-4 gap-2 rounded-lg bg-tint/[0.03] px-2.5 py-2">
                  <Level label="Entry" value={fmtPrice(strategy.entry, digits)} />
                  <Level label="Stop" tone="text-bear"
                         value={fmtPrice(strategy.stop_loss ?? 0, digits)} />
                  <Level label="Target" tone="text-bull"
                         value={fmtPrice(strategy.take_profit ?? 0, digits)} />
                  <Level label="R:R"
                         value={strategy.risk_reward ? `${strategy.risk_reward.toFixed(2)}` : '—'}
                         sub={`${strategy.horizon_bars} bars`} />
                </div>
              )}

              {/* Conditions — the detail the panel exists for */}
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wider text-faint">
                  Conditions
                </div>
                {strategy.conditions.map((condition) => (
                  <div key={condition.name} className="flex gap-1.5 items-start">
                    <span className={`text-2xs shrink-0 mt-px ${
                      condition.met ? 'text-bull' : 'text-bear/70'}`}>
                      {condition.met ? '✓' : '✕'}
                    </span>
                    <div className="min-w-0">
                      <div className={`text-2xs leading-snug ${
                        condition.met ? 'text-ink/90' : 'text-faint'}`}>
                        {condition.name}
                        {condition.weight > 1 && (
                          <span className="text-[10px] text-faint ml-1"
                                title="Weighted more heavily than the others">
                            ×{condition.weight}
                          </span>
                        )}
                      </div>
                      {condition.detail && (
                        <div className="text-[10px] text-faint leading-snug">
                          {condition.detail}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Why it trades the way it does */}
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wider text-faint">
                  How this strategy behaves
                </div>
                {strategy.rationale.map((line, index) => (
                  <p key={index} className="text-2xs text-ink/80 leading-relaxed flex gap-1.5">
                    <span className="text-faint shrink-0">·</span>
                    <span>{line}</span>
                  </p>
                ))}
              </div>

              {strategy.note && (
                <p className="text-[10px] text-warn/90 leading-relaxed">{strategy.note}</p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Level({ label, value, sub, tone }: {
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
