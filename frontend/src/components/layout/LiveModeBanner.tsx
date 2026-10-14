import { useStore } from '../../store/useStore'

/**
 * TRADING.md promises that LIVE mode replaces the quiet default with
 * something unmissable. The segmented control in `TradingModeControl` makes
 * the *switch* unmissable; this makes the *state* unmissable for as long as
 * it lasts — a fixed, full-width strip above everything else, present on
 * every screen of the app, not a toast that times out or a badge someone
 * can scroll past.
 */
export default function LiveModeBanner() {
  const mode = useStore((s) => s.tradingMode)
  if (mode?.mode !== 'LIVE') return null

  return (
    <div
      role="alert"
      className="shrink-0 rounded-xl bg-bear px-4 py-1.5 flex items-center justify-center
                 gap-2 text-xs font-bold tracking-wide text-white shadow-[0_8px_24px_-8px_rgb(var(--c-bear)/0.6)]"
    >
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full rounded-full bg-white opacity-70
                         animate-pulse-ring" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-white" />
      </span>
      LIVE TRADING · REAL MONEY
      <span className="font-normal text-white/80">
        — every order needs your confirmation, but Angel One is armed to send real orders
      </span>
    </div>
  )
}
