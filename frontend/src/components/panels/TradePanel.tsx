import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { api } from '../../lib/api'
import { decisionTone, fmtNumber, fmtPips, fmtPriceCcy, timeAgo } from '../../lib/format'
import { useActiveInstrument, useStore } from '../../store/useStore'
import { Badge, ConfidenceRing, Spinner, Stat } from '../ui'
import ExecuteLiveButton from './ExecuteLiveButton'

export default function TradePanel() {
  const analysis = useStore((s) => s.analysis)
  const pending = useStore((s) => s.analysisPending)
  const error = useStore((s) => s.analysisError)
  const requestAnalysis = useStore((s) => s.requestAnalysis)
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5
  const [logged, setLogged] = useState<string | null>(null)
  const [logging, setLogging] = useState(false)

  if (!analysis) {
    return (
      <div className="glass rounded-xl p-5 flex flex-col items-center justify-center gap-3 min-h-[220px]">
        {pending ? (
          <>
            <Spinner className="h-5 w-5" />
            <p className="text-xs text-muted">Reading the market…</p>
          </>
        ) : (
          <>
            <p className="text-xs text-muted text-center">
              {error ?? 'No analysis yet for this chart.'}
            </p>
            <button type="button" className="btn btn-primary" onClick={requestAnalysis}
                title="Run the analysis for this symbol and timeframe">
              Analyse now
            </button>
          </>
        )}
      </div>
    )
  }

  const { verdict, plan, confidence } = analysis

  // The account circuit breaker outranks the signal. When the day's limits are
  // breached the correct answer to 'what should I trade' is 'nothing', and
  // showing an entry underneath that message would undercut it entirely.
  const halted = analysis.account_risk?.status === 'halt'
  if (halted) {
    const risk = analysis.account_risk!
    return (
      <div className="glass rounded-xl overflow-hidden border border-bear/50">
        <div className="px-4 py-3 bg-bear/12 border-b border-bear/40">
          <h2 className="text-sm font-bold text-bear tracking-tight">
            {risk.headline}
          </h2>
          <p className="text-2xs text-ink/85 leading-relaxed mt-1">{risk.reason}</p>
        </div>
        <div className="px-4 py-3 space-y-2">
          <p className="text-2xs text-muted leading-relaxed">
            The signal for this chart is still being computed and is visible in
            the Analysis tab. Meridian will not pre-fill an entry while a
            guardrail is breached.
          </p>
          {/* The rail above already prints `risk.notes`; repeating them here
              reads as padding and dilutes the one line that matters. */}
          <p className="text-[10px] text-faint leading-relaxed pt-1">
            Limits reset at 00:00 UTC, or when a winning trade breaks the streak.
          </p>
        </div>
      </div>
    )
  }

  const tone = decisionTone(verdict.decision)
  const isActionable = verdict.decision !== 'WAIT'

  const logTrade = async () => {
    setLogging(true)
    try {
      const entry = await api.logAnalysis(analysis)
      setLogged(entry.id)
      window.setTimeout(() => setLogged(null), 3000)
    } finally {
      setLogging(false)
    }
  }

  return (
    <div className={`glass rounded-xl overflow-hidden ${tone.glow}`}>
      {/* Decision header */}
      <div className={`relative px-4 py-3 border-b ${tone.border} ${tone.bg}`}>
        <div className="flex items-center gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <AnimatePresence mode="wait">
                <motion.span
                  key={verdict.decision}
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 6 }}
                  transition={{ duration: 0.2 }}
                  className={`text-3xl font-bold tracking-tight ${tone.text}`}
                >
                  {verdict.decision}
                </motion.span>
              </AnimatePresence>
              {pending && <Spinner />}
            </div>
            <p className="text-2xs text-faint mt-0.5">
              {verdict.probability.toFixed(0)}% est. hit probability ·{' '}
              {analysis.symbol} {analysis.timeframe} · {timeAgo(analysis.generated_at)}
            </p>
            <div className="flex flex-wrap gap-1 mt-2">
              <Badge tone={verdict.source === 'claude' ? 'violet' : 'neutral'}>
                {verdict.source === 'claude' ? verdict.model ?? 'Claude' : 'Rule engine'}
              </Badge>
              <Badge tone={confidence.conflict ? 'warn' : 'accent'}>
                {Math.round(confidence.agreement * 100)}% aligned
              </Badge>
              {verdict.latency_ms ? (
                <Badge>{(verdict.latency_ms / 1000).toFixed(1)}s</Badge>
              ) : null}
            </div>
          </div>
          <ConfidenceRing value={confidence.final_confidence} decision={verdict.decision} />
        </div>
      </div>

      {/* Levels */}
      {isActionable ? (
        <div className="grid grid-cols-3 divide-x divide-hairline border-b border-hairline">
          <LevelCell label="Entry" value={fmtPriceCcy(plan.entry, digits, instrument?.quote_currency)} tone="text-accent" />
          <LevelCell
            label="Stop"
            value={fmtPriceCcy(plan.stop_loss, digits, instrument?.quote_currency)}
            tone="text-bear"
            sub={fmtPips(plan.risk_pips)}
          />
          <LevelCell
            label="Target"
            value={fmtPriceCcy(plan.take_profit, digits, instrument?.quote_currency)}
            tone="text-bull"
            sub={fmtPips(plan.reward_pips)}
          />
        </div>
      ) : (
        <div className="px-4 py-2.5 border-b border-hairline">
          <p className="text-2xs text-warn leading-relaxed">
            {confidence.reason}
          </p>
        </div>
      )}

      {/* Metrics */}
      <div className="grid grid-cols-4 gap-3 px-4 py-3 border-b border-hairline">
        <Stat
          label="R:R"
          value={plan.risk_reward ? `${plan.risk_reward.toFixed(2)}` : '—'}
          tone={
            plan.risk_reward && plan.risk_reward >= 2 ? 'text-bull'
              : plan.risk_reward && plan.risk_reward < 1.5 ? 'text-warn' : ''
          }
        />
        <Stat label="ATR" value={fmtPips(plan.atr_pips)} />
        <Stat
          label="Spread"
          value={fmtPips(plan.spread_pips)}
          tone={
            plan.spread_pips && plan.risk_pips && plan.spread_pips > plan.risk_pips * 0.12
              ? 'text-warn' : ''
          }
        />
        <Stat
          label="Lots"
          value={plan.suggested_lots ? fmtNumber(plan.suggested_lots, 2) : '—'}
          sub={plan.risk_amount ? `$${fmtNumber(plan.risk_amount, 0)} risk` : undefined}
        />
      </div>

      {/* Summary. Capped so a long AI narrative cannot push the panels below
          it off screen — the reasoning scrolls in place instead. */}
      <div className="px-4 py-3 max-h-[132px] scroll-y">
        <p className="text-xs leading-relaxed text-ink/90">{verdict.summary}</p>
        {verdict.invalidation && (
          <p className="mt-2 text-2xs text-muted leading-relaxed">
            <span className="text-faint uppercase tracking-wider mr-1">Invalidation</span>
            {verdict.invalidation}
          </p>
        )}
        {verdict.uncertainty && (
          <p className="mt-2 text-2xs text-warn/90 leading-relaxed">
            <span className="text-warn/60 uppercase tracking-wider mr-1">Uncertainty</span>
            {verdict.uncertainty}
          </p>
        )}
      </div>

      {analysis.warnings.length > 0 && (
        <div className="px-4 pb-3 space-y-1">
          {analysis.warnings.map((warning) => (
            <p key={warning} className="text-2xs text-warn/80 leading-snug">⚠ {warning}</p>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 px-4 py-2.5 border-t border-hairline bg-tint/[0.015]">
        <button type="button" className="btn flex-1" onClick={requestAnalysis} disabled={pending}
                title="Re-run the analysis against the latest price">
          {pending ? <Spinner /> : null}
          Re-analyse
        </button>
        <button
          type="button"
          className="btn flex-1"
          onClick={logTrade}
          disabled={logging}
          title="Save this recommendation with its full evidence snapshot"
        >
          {logged ? '✓ Logged' : logging ? <Spinner /> : 'Log to journal'}
        </button>
        {isActionable && <ExecuteLiveButton analysis={analysis} />}
      </div>

      <p className="px-4 pb-2.5 text-[10px] leading-snug text-faint/70">
        Analysis is estimates, not guarantees. Orders are only ever placed when
        you explicitly confirm Execute Live with Angel One connected — nothing
        here trades on its own.
      </p>
    </div>
  )
}

function LevelCell({
  label, value, tone, sub,
}: { label: string; value: string; tone: string; sub?: string }) {
  return (
    <div className="px-3 py-2.5">
      <div className="text-2xs uppercase tracking-wider text-faint">{label}</div>
      <div className={`text-sm font-semibold tnum ${tone}`}>{value}</div>
      {sub && <div className="text-2xs text-faint tnum">{sub}</div>}
    </div>
  )
}
