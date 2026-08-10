/** Number and time formatting. Prices always render at instrument precision. */

export function fmtPrice(value: number | null | undefined, digits = 5): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

/** ₹ for INR-quoted instruments, $ for USD/USDT — the only two on this
 * platform. A bare number with no currency mark is how a trader ends up
 * misreading a rupee price as dollars or vice versa; this is deliberately
 * called out everywhere a price is the main thing on screen. */
export function currencySymbol(quoteCurrency?: string | null): string {
  return quoteCurrency === 'INR' ? '₹' : '$'
}

/** Price with its currency symbol prefixed — use this instead of bare
 * `fmtPrice` anywhere the number is a primary, standalone price (chart
 * header, entry/stop/target, screener rows). Tables where every row is
 * already the same instrument's own precision column can keep `fmtPrice`. */
export function fmtPriceCcy(value: number | null | undefined, digits = 5,
                            quoteCurrency?: string | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${currencySymbol(quoteCurrency)}${fmtPrice(value, digits)}`
}

export function fmtNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtSigned(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

export function fmtPips(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(1)}p`
}

export function fmtTime(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function fmtDateTime(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function timeAgo(unixSeconds: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - unixSeconds))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function biasColor(bias: string): string {
  if (bias === 'bullish' || bias === 'BUY') return 'text-bull'
  if (bias === 'bearish' || bias === 'SELL') return 'text-bear'
  return 'text-muted'
}

export function decisionTone(decision: string): {
  text: string
  bg: string
  border: string
  glow: string
} {
  switch (decision) {
    case 'BUY':
      return {
        text: 'text-bull',
        bg: 'bg-bull/10',
        border: 'border-bull/40',
        glow: 'shadow-[0_0_40px_-12px_rgba(47,211,160,0.7)]',
      }
    case 'SELL':
      return {
        text: 'text-bear',
        bg: 'bg-bear/10',
        border: 'border-bear/40',
        glow: 'shadow-[0_0_40px_-12px_rgba(255,77,106,0.7)]',
      }
    default:
      return {
        text: 'text-warn',
        bg: 'bg-warn/10',
        border: 'border-warn/30',
        glow: 'shadow-[0_0_40px_-16px_rgba(245,196,83,0.5)]',
      }
  }
}

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}
