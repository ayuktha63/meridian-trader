import type { TradePlan } from '../../lib/types'
import { fmtPrice } from '../../lib/format'
import type { Transform } from './geometry'

/** Whether price has already gone through the stop or the target since this
 * plan was generated — the one thing this overlay used to have no way to
 * say, so a dead setup looked exactly as "live" as a fresh one until the
 * next re-analysis happened to run. See docs/RISK_CONTROL_AUDIT.md's UX
 * findings. */
export type SetupStatus = 'stopped' | 'reached' | null

interface Props {
  plan: TradePlan
  /** Open time of the last completed bar the plan was made from. */
  barTime: number
  transform: Transform
  digits: number
  invalidated?: SetupStatus
}

/**
 * The trade and nothing else: where to get in, where to take profit, where
 * the stop is. A green block from entry to target, a red block from entry to
 * stop. It draws only what the engine's plan says; on WAIT there is no plan
 * and it says so instead of drawing one.
 */
export default function TradeSetupLayer({ plan, barTime, transform, digits, invalidated }: Props) {
  const { entry, stop_loss: stop, take_profit: target } = plan
  const long = plan.decision === 'BUY'
  if (plan.decision === 'WAIT' || !entry || !stop || !target) {
    return (
      <div className="absolute left-3 top-3 pointer-events-none rounded-md px-2 py-1 text-2xs
                      text-muted bg-panel/80 border border-hairline" style={{ zIndex: 4 }}>
        No trade setup right now — the engine says WAIT.
      </div>
    )
  }
  const left = Math.max(0, Math.min(transform.timeToX(barTime), transform.width - 60))
  const width = transform.width - left
  const yEntry = transform.priceToY(entry)
  const yStop = transform.priceToY(stop)
  const yTarget = transform.priceToY(target)
  const block = (a: number, b: number) => ({ top: Math.min(a, b), height: Math.abs(a - b) })
  const tag = (y: number, text: string, color: string) => (
    <div className="absolute right-1 rounded px-1.5 py-0.5 text-[10px] font-semibold tnum
                    bg-panel/90 border" style={{ top: y - 9, color, borderColor: color }}>
      {text}
    </div>
  )
  const reward = Math.abs(target - entry), risk = Math.abs(entry - stop)
  // Dead rather than live: price already went through one of the two levels
  // since this plan was made. The lines stay — losing that context would be
  // worse — but desaturated, with no mistaking it for an actionable setup.
  const dead = invalidated != null
  const opacity = dead ? 0.35 : 1
  return (
    <div className="absolute left-0 top-0 pointer-events-none overflow-hidden"
         style={{ width: transform.width, height: transform.height, zIndex: 4, opacity }}>
      <div className="absolute bg-bull/20" style={{ left, width, ...block(yEntry, yTarget) }} />
      <div className="absolute bg-bear/20" style={{ left, width, ...block(yEntry, yStop) }} />
      {[yEntry, yTarget, yStop].map((y, i) => (
        <div key={i} className="absolute h-px" style={{
          left, width, top: y,
          background: i === 0 ? 'rgb(var(--c-ink, 200 200 200))' : i === 1 ? '#2FD3A0' : '#FF4D6A' }} />
      ))}
      {tag(yEntry, `${long ? 'BUY' : 'SELL'} ${fmtPrice(entry, digits)}`, '#9AA4B2')}
      {tag(yTarget, `TP ${fmtPrice(target, digits)}`, '#2FD3A0')}
      {tag(yStop, `SL ${fmtPrice(stop, digits)}`, '#FF4D6A')}
      <div className="absolute left-3 top-3 rounded-md px-2 py-1 text-2xs text-muted
                      bg-panel/80 border border-hairline" style={{ opacity: 1 }}>
        {dead
          ? (invalidated === 'stopped'
            ? '⚠ Setup invalidated — price already hit the stop since this read. Re-analyse for a fresh one.'
            : '✓ Setup already reached target since this read. Re-analyse for a fresh one.')
          : <>Reward : risk {risk > 0 ? (reward / risk).toFixed(2) : '—'} · a heuristic reading, not a promise</>}
      </div>
    </div>
  )
}
