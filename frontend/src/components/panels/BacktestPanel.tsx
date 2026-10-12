import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { fmtNumber, fmtSigned } from '../../lib/format'
import type { BacktestResult, Eligibility } from '../../lib/types'
import { useStore } from '../../store/useStore'
import { Sparkline, Spinner, Stat } from '../ui'

/**
 * `engine` is the live verdict — the call on the trade panel, and the only
 * thing Execute Live sends. The rest are the named strategies from the Plays
 * tab. All six are the real code replayed bar by bar, not a faster imitation.
 */
const STRATEGIES = [
  { id: 'engine', label: 'Engine',
    hint: 'The live verdict: the same call the trade panel shows and Execute Live sends' },
  { id: 'trend_continuation', label: 'Trend',
    hint: 'Trend Continuation, from the Plays tab' },
  { id: 'mean_reversion', label: 'Reversion',
    hint: 'Mean Reversion, from the Plays tab' },
  { id: 'breakout', label: 'Breakout',
    hint: 'Range Breakout, from the Plays tab' },
  { id: 'smart_money', label: 'Order block',
    hint: 'Order Block Retest, from the Plays tab' },
  { id: 'momentum', label: 'Momentum',
    hint: 'Momentum Expansion, from the Plays tab' },
]

/** Rough cost of replaying the engine: one full analysis per bar. */
const SECONDS_PER_BAR = 0.045
const WALK_FORWARD_BARS = 4000

export default function BacktestPanel() {
  const symbol = useStore((s) => s.symbol)
  const timeframe = useStore((s) => s.timeframe)
  const [strategy, setStrategy] = useState('engine')
  const [minConfidence, setMinConfidence] = useState(55)
  const [riskReward, setRiskReward] = useState(2)
  const [bars, setBars] = useState(750)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [eligibility, setEligibility] = useState<Eligibility | null>(null)

  // Where the live engine stands on this market — re-read after every run,
  // since a run is what changes it. A server too old to have the endpoint
  // simply shows nothing.
  useEffect(() => {
    let current = true
    api.eligibility(symbol, timeframe)
      .then((state) => { if (current) setEligibility(state) })
      .catch(() => { if (current) setEligibility(null) })
    return () => { current = false }
  }, [symbol, timeframe, result])

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      const payload = await api.backtest({
        symbol, timeframe, bars, strategy,
        min_confidence: minConfidence,
        risk_reward: riskReward,
      })
      setResult(payload)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Backtest failed')
    } finally {
      setRunning(false)
    }
  }

  const runWalkForward = async () => {
    setRunning(true)
    setError(null)
    try {
      // A verdict needs 30 trades on bars it was not fitted to, and four of
      // every five bars are the tested ones: fewer than this rarely concludes.
      setResult(await api.walkForward({
        symbol, timeframe, bars: Math.max(bars, WALK_FORWARD_BARS), strategy,
      }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Walk-forward failed')
    } finally {
      setRunning(false)
    }
  }

  const metrics = result?.metrics
  const edge = result?.edge
  const forLive = strategy === 'engine'
  const estimate = Math.max(5, Math.round(bars * SECONDS_PER_BAR / 5) * 5)

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="shrink-0 px-3 py-2.5 border-b border-hairline space-y-2">
        <div className="grid grid-cols-3 gap-1">
          {STRATEGIES.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setStrategy(item.id)}
              title={item.hint}
              aria-pressed={strategy === item.id}
              className={`chip justify-center ${strategy === item.id ? 'chip-active' : ''}`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <Slider label="Min confidence" value={minConfidence} min={30} max={85} step={5}
                onChange={setMinConfidence} suffix="%" />
        <Slider label="Min reward : risk" value={riskReward} min={1} max={4} step={0.5}
                onChange={setRiskReward} suffix="R" />
        <Slider label="Bars" value={bars} min={250} max={3000} step={250}
                onChange={setBars} />

        <button type="button" className="btn btn-primary w-full" onClick={run} disabled={running}
              title="Replay the rules over this symbol's history and score the result">
          {running ? <Spinner /> : null}
          Replay {symbol} {timeframe}
        </button>
        <button type="button" className="btn w-full" onClick={runWalkForward} disabled={running}
                title="Fit the thresholds on one stretch of history, trade them on the next, repeat. Only the stretches it did not fit on are scored.">
          {forLive ? 'Walk-forward test (required for live)' : 'Walk-forward test'}
        </button>
        <p className="text-[10px] text-faint leading-relaxed">
          {running
            ? 'Replaying the live engine bar by bar — this is the slow, honest way.'
            : `A replay takes about ${estimate}s the first time on a market, a walk-forward about `
              + `${Math.round(WALK_FORWARD_BARS * SECONDS_PER_BAR / 60)} minutes; other strategies and re-runs reuse it.`}
        </p>
        {error && <p className="text-2xs text-bear">{error}</p>}
      </div>

      <div className="flex-1 min-h-0 scroll-y px-3 py-3">
        {!result ? (
          <p className="text-2xs text-faint leading-relaxed">
            Replays the live engine itself over history, one full analysis per bar,
            and trades it the way a live order trades: a limit at the signal price
            that is missed if the market does not come back, a stop, a target, and
            every cost. When a bar spans both stop and target the stop fills first.
            Results are net, and lean pessimistic rather than flattering.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2">
              <Stat label="Trades" value={metrics!.trades}
                    sub={`${metrics!.wins}W / ${metrics!.losses}L`} />
              <Stat label="Win rate" value={`${metrics!.win_rate.toFixed(1)}%`}
                    tone={metrics!.win_rate >= 50 ? 'text-bull' : 'text-bear'} />
              <Stat label="Profit factor"
                    value={metrics!.profit_factor >= 999 ? '∞' : fmtNumber(metrics!.profit_factor, 2)}
                    tone={metrics!.profit_factor >= 1.5 ? 'text-bull' : 'text-warn'} />
              <Stat label="Avg R" value={fmtSigned(metrics!.average_r, 2)}
                    tone={metrics!.average_r >= 0 ? 'text-bull' : 'text-bear'} />
              <Stat label="Expectancy" value={`${fmtSigned(metrics!.expectancy, 2)}R`} />
              <Stat label="Sharpe" value={fmtNumber(metrics!.sharpe_ratio, 2)}
                    sub={`sortino ${fmtNumber(metrics!.sortino_ratio, 2)}`} />
              <Stat label="Max DD" value={`${metrics!.max_drawdown_pct.toFixed(1)}%`}
                    tone="text-bear" sub={`$${fmtNumber(metrics!.max_drawdown, 0)}`} />
              <Stat label="Return" value={`${fmtSigned(metrics!.return_pct, 1)}%`}
                    tone={metrics!.return_pct >= 0 ? 'text-bull' : 'text-bear'}
                    sub={`$${fmtNumber(metrics!.ending_balance, 0)}`} />
              <Stat label="Streaks"
                    value={`${metrics!.longest_win_streak}W`}
                    sub={`${metrics!.longest_loss_streak}L worst`} />
            </div>

            {result.equity_curve.length > 2 && (
              <div>
                <h3 className="panel-title mb-1.5">Equity curve</h3>
                <div className="rounded-lg border border-hairline bg-tint/[0.02] p-2">
                  <Sparkline
                    values={result.equity_curve.map((point) => point.equity)}
                    width={260}
                    height={54}
                    color={metrics!.return_pct >= 0 ? '#2FD3A0' : '#FF4D6A'}
                  />
                </div>
              </div>
            )}

            {result.in_sample && result.out_of_sample && edge?.method !== 'walk_forward' && (
              <div className="grid grid-cols-2 gap-2">
                <Stat label="First 70%" value={`${fmtSigned(result.in_sample.expectancy, 2)}R`}
                      sub={`${result.in_sample.trades} trades`} />
                <Stat label="Last 30% (held out)"
                      value={`${fmtSigned(result.out_of_sample.expectancy, 2)}R`}
                      tone={result.out_of_sample.expectancy >= 0 ? 'text-bull' : 'text-bear'}
                      sub={`${result.out_of_sample.trades} trades`} />
              </div>
            )}

            {(result.folds?.length ?? 0) > 0 && (
              <div>
                <h3 className="panel-title mb-1.5">Walk-forward folds</h3>
                <div className="space-y-1">
                  {result.folds!.map((fold) => (
                    <div key={fold.fold} className="flex items-center gap-2 text-[10px] tnum">
                      <span className="text-faint">#{fold.fold}</span>
                      <span className="text-muted">
                        {fold.min_confidence ? `≥${fold.min_confidence}% · ≥${fold.risk_reward}R` : 'nothing qualified'}
                      </span>
                      <span className="flex-1" />
                      <span className="text-faint">{fold.test_trades} trades</span>
                      <span className={fold.test_expectancy >= 0 ? 'text-bull' : 'text-bear'}>
                        {fmtSigned(fold.test_expectancy, 2)}R
                      </span>
                    </div>
                  ))}
                </div>
                {result.next_parameters?.min_confidence != null && (
                  <p className="text-[10px] text-faint leading-relaxed mt-1.5">
                    A live order has to clear what the latest stretch fitted:
                    confidence ≥ {result.next_parameters.min_confidence}% and
                    reward:risk ≥ {result.next_parameters.min_risk_reward}.
                  </p>
                )}
              </div>
            )}

            <div>
              <h3 className="panel-title mb-1.5">Last trades</h3>
              <div className="space-y-1">
                {result.trades.slice(-8).reverse().map((trade) => (
                  <div key={trade.index}
                       className="flex items-center gap-2 text-[10px] tnum">
                    <span className={trade.direction === 'BUY' ? 'text-bull' : 'text-bear'}>
                      {trade.direction}
                    </span>
                    <span className="text-muted">{trade.entry}</span>
                    <span className="flex-1" />
                    <span className="text-faint">{trade.exit_reason || trade.outcome}</span>
                    <span className="text-faint">{trade.confidence.toFixed(0)}%</span>
                    <span className={trade.r_multiple >= 0 ? 'text-bull' : 'text-bear'}>
                      {fmtSigned(trade.r_multiple, 2)}R
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {result.data_source === 'synthetic' && (
              <div className="rounded-lg border border-bear/50 bg-bear/10 px-2.5 py-2 text-2xs
                              leading-relaxed text-bear">
                <span className="font-bold uppercase tracking-wide">Simulated data.</span>{' '}
                These bars are generated, not traded. The numbers below test the software,
                not a market, and count for nothing.
              </div>
            )}

            {edge && (
              <div className={`rounded-lg border px-2.5 py-2 text-2xs leading-relaxed ${
                edge.passed ? 'border-bull/50 bg-bull/10 text-bull'
                  : 'border-warn/50 bg-warn/10 text-warn'}`}>
                <div className="font-bold uppercase tracking-wide">
                  {edge.passed ? 'Measured edge' : 'No measured edge'}
                  <span className="font-normal normal-case text-faint">
                    {' '}· {edge.method === 'walk_forward' ? 'walk-forward' : 'holdout only'}
                  </span>
                </div>
                <div className="text-ink/85">{edge.reason}</div>
                {(edge.method !== 'walk_forward' || result.strategy !== 'engine') && (
                  <div className="text-faint">
                    Execute Live needs a passing walk-forward test of the Engine, not this one.
                  </div>
                )}
              </div>
            )}

            {eligibility && (
              <div className="rounded-lg border border-hairline px-2.5 py-2 text-2xs leading-relaxed">
                <div className="font-bold uppercase tracking-wide text-ink/85">
                  Live eligibility · {symbol} {timeframe}
                </div>
                <div className={eligibility.live_allowed ? 'text-bull font-semibold'
                  : 'text-warn font-semibold'}>
                  {eligibility.state}{eligibility.approved ? ' · approved' : ''}
                </div>
                {eligibility.reasons.slice(0, 4).map((reason) => (
                  <p key={reason} className="text-faint">· {reason}</p>
                ))}
                <p className="text-faint">
                  No state trades by itself: a live order also needs a person's approval
                  on record and every pre-trade check.
                  {eligibility.experiment_id ? ` Evidence: ${eligibility.experiment_id}.` : ''}
                </p>
              </div>
            )}

            <div className="space-y-1">
              {result.notes.map((note) => (
                <p key={note} className="text-[10px] text-faint leading-relaxed">· {note}</p>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Slider({
  label, value, min, max, step, onChange, suffix = '',
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
  suffix?: string
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between">
        <span className="text-2xs text-muted">{label}</span>
        <span className="text-2xs tnum text-ink">{value}{suffix}</span>
      </div>
      <input
        type="range"
        min={min} max={max} step={step} value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full h-1 mt-1 appearance-none rounded-full bg-tint/10
                   accent-accent cursor-pointer"
      />
    </label>
  )
}
