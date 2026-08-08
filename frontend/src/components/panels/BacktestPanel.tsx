import { useState } from 'react'
import { api } from '../../lib/api'
import { fmtNumber, fmtSigned } from '../../lib/format'
import type { BacktestResult } from '../../lib/types'
import { useStore } from '../../store/useStore'
import { Sparkline, Spinner, Stat } from '../ui'

const STRATEGIES = [
  { id: 'confluence', label: 'Confluence',
    hint: 'The full weighted engine — trend, momentum, volume, structure and patterns' },
  { id: 'structure', label: 'Structure',
    hint: 'Break of structure and order-block entries only' },
  { id: 'trend_pullback', label: 'Pullback',
    hint: 'Buy dips in an uptrend, sell rallies in a downtrend' },
]

export default function BacktestPanel() {
  const symbol = useStore((s) => s.symbol)
  const timeframe = useStore((s) => s.timeframe)
  const [strategy, setStrategy] = useState('confluence')
  const [minConfidence, setMinConfidence] = useState(55)
  const [riskReward, setRiskReward] = useState(2)
  const [bars, setBars] = useState(1500)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const metrics = result?.metrics

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="shrink-0 px-3 py-2.5 border-b border-hairline space-y-2">
        <div className="flex gap-1">
          {STRATEGIES.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setStrategy(item.id)}
              title={item.hint}
              aria-pressed={strategy === item.id}
              className={`chip flex-1 justify-center ${strategy === item.id ? 'chip-active' : ''}`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <Slider label="Min confidence" value={minConfidence} min={30} max={85} step={5}
                onChange={setMinConfidence} suffix="%" />
        <Slider label="Reward : risk" value={riskReward} min={1} max={4} step={0.5}
                onChange={setRiskReward} suffix="R" />
        <Slider label="Bars" value={bars} min={500} max={3000} step={250}
                onChange={setBars} />

        <button type="button" className="btn btn-primary w-full" onClick={run} disabled={running}
              title="Replay the rules over this symbol's history and score the result">
          {running ? <Spinner /> : null}
          Replay {symbol} {timeframe}
        </button>
        {error && <p className="text-2xs text-bear">{error}</p>}
      </div>

      <div className="flex-1 min-h-0 scroll-y px-3 py-3">
        {!result ? (
          <p className="text-2xs text-faint leading-relaxed">
            Replays the deterministic confluence engine bar by bar over history.
            Entries fill at the signal bar's close; when a bar spans both stop and
            target the stop is assumed to fill first, so results lean pessimistic
            rather than flattering.
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
                    <span className="text-faint">{trade.confidence.toFixed(0)}%</span>
                    <span className={trade.r_multiple >= 0 ? 'text-bull' : 'text-bear'}>
                      {fmtSigned(trade.r_multiple, 2)}R
                    </span>
                  </div>
                ))}
              </div>
            </div>

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
