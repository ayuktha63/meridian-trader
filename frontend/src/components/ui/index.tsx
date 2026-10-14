import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import { clamp } from '../../lib/format'

export function Panel({
  title, action, children, className = '', dense = false,
}: {
  title?: string
  action?: ReactNode
  children: ReactNode
  className?: string
  dense?: boolean
}) {
  return (
    <section className={`glass rounded-xl flex flex-col min-h-0 ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-2 px-3 py-2 border-b border-hairline shrink-0">
          <h2 className="panel-title">{title}</h2>
          {action}
        </header>
      )}
      <div className={`flex-1 min-h-0 ${dense ? '' : 'p-3'}`}>{children}</div>
    </section>
  )
}

export function Badge({
  children, tone = 'neutral', className = '',
}: {
  children: ReactNode
  tone?: 'bull' | 'bear' | 'warn' | 'accent' | 'violet' | 'neutral'
  className?: string
}) {
  const tones = {
    bull: 'border-bull/40 bg-bull/10 text-bull',
    bear: 'border-bear/40 bg-bear/10 text-bear',
    warn: 'border-warn/40 bg-warn/10 text-warn',
    accent: 'border-accent/40 bg-accent/10 text-accent',
    violet: 'border-violet/40 bg-violet/10 text-violet',
    neutral: 'border-hairline bg-tint/[0.04] text-muted',
  }
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5
                      text-2xs font-medium ${tones[tone]} ${className}`}>
      {children}
    </span>
  )
}

export function Stat({
  label, value, sub, tone = '', mono = true,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: string
  mono?: boolean
}) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`text-sm font-semibold truncate ${mono ? 'tnum' : ''} ${tone || 'text-ink'}`}>
        {value}
      </div>
      {sub && <div className="text-2xs text-faint truncate">{sub}</div>}
    </div>
  )
}

/**
 * Signed meter: zero sits in the centre so a bearish component reads as a bar
 * growing left, not as a short bar. Direction is visible at a glance.
 */
export function SignedMeter({
  value, label, detail, weight,
}: {
  value: number
  label: string
  detail?: string
  weight?: number
}) {
  const pct = clamp(Math.abs(value) * 50, 0, 50)
  const positive = value >= 0
  return (
    <div className="group">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <span className="text-2xs font-medium text-muted capitalize">
          {label}
          {weight !== undefined && (
            <span className="ml-1 text-faint">{Math.round(weight * 100)}%</span>
          )}
        </span>
        <span className={`text-2xs tnum font-semibold ${positive ? 'text-bull' : 'text-bear'}`}>
          {value >= 0 ? '+' : ''}{value.toFixed(2)}
        </span>
      </div>
      <div className="relative h-1.5 rounded-full bg-tint/[0.05] overflow-hidden">
        <div className="absolute inset-y-0 left-1/2 w-px bg-tint/15" />
        <motion.div
          className={`absolute inset-y-0 ${positive ? 'bg-bull' : 'bg-bear'}`}
          initial={false}
          animate={{
            left: positive ? '50%' : `${50 - pct}%`,
            width: `${pct}%`,
          }}
          transition={{ type: 'spring', stiffness: 220, damping: 28 }}
        />
      </div>
      {detail && (
        <div className="mt-1 text-2xs text-faint leading-snug line-clamp-2">{detail}</div>
      )}
    </div>
  )
}

/** Radial gauge for the engine's signal score — the headline number on the
 *  trade panel. Labelled a score, not a confidence or a probability: it is a
 *  weighted count of agreeing indicators that has never been fitted to
 *  outcomes, and where it was tested it did not separate winners from losers. */
export function ConfidenceRing({
  value, decision, size = 108,
}: {
  value: number
  decision: string
  size?: number
}) {
  const stroke = 7
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const progress = clamp(value, 0, 100) / 100
  const color = decision === 'BUY' ? '#2FD3A0' : decision === 'SELL' ? '#FF4D6A' : '#F5C453'

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}
         title={'Signal score, 0–100: how many of the engine\'s indicators agree, weighted. '
           + 'A heuristic — not a probability of winning, and not calibrated against results.'}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={stroke}
        />
        <motion.circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={circumference}
          initial={false}
          animate={{ strokeDashoffset: circumference * (1 - progress) }}
          transition={{ type: 'spring', stiffness: 90, damping: 20 }}
          style={{ filter: `drop-shadow(0 0 6px ${color}66)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold tnum leading-none" style={{ color }}>
          {Math.round(value)}
        </span>
        <span className="text-2xs uppercase tracking-widest text-faint mt-1">
          signal score
        </span>
      </div>
    </div>
  )
}

export function Toggle({
  checked, onChange, label, hint,
}: {
  checked: boolean
  onChange: () => void
  label: string
  hint?: string
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      title={hint}
      aria-pressed={checked}
      className="flex items-center gap-2 group"
    >
      <span
        className={`relative h-4 w-7 rounded-full transition-colors duration-200
                    ${checked ? 'bg-accent/70' : 'bg-tint/10'}`}
      >
        <motion.span
          className="absolute top-0.5 h-3 w-3 rounded-full bg-white shadow"
          initial={false}
          animate={{ left: checked ? 14 : 2 }}
          transition={{ type: 'spring', stiffness: 500, damping: 32 }}
        />
      </span>
      <span className={`text-2xs transition-colors ${checked ? 'text-ink' : 'text-faint'}`}>
        {label}
      </span>
    </button>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      className={`inline-block h-3 w-3 rounded-full border-2 border-accent/30
                  border-t-accent animate-spin ${className}`}
    />
  )
}

export function EmptyState({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-6 py-10 gap-2">
      {icon && <div className="text-faint opacity-60">{icon}</div>}
      <p className="text-xs text-muted">{title}</p>
      {hint && <p className="text-2xs text-faint max-w-[26ch] leading-relaxed">{hint}</p>}
    </div>
  )
}

/** Minimal inline sparkline for equity curves and score history. */
export function Sparkline({
  values, width = 120, height = 32, color = '#4C8DFF',
}: {
  values: number[]
  width?: number
  height?: number
  color?: string
}) {
  if (values.length < 2) return <div style={{ width, height }} />
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const step = width / (values.length - 1)
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`)
    .join(' ')

  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
