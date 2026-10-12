import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { LiveAlert } from '../../lib/types'
import { useStore } from '../../store/useStore'

/**
 * The one control that answers "where am I?" — DEMO or LIVE — and nothing
 * else. Angel One is the only execution path that exists, so there is
 * deliberately no broker picker here: the only question a trader should
 * ever face is demo vs. real money, never which broker to route through.
 *
 * Mode and Angel status live in the shared store (`useStore`), not local
 * state here — this is the component that *causes* a switch, and every
 * other reader (the wallet readout in `AutoTradeBar`) needs to see that the
 * instant this component does, not on its own next poll. The store's own
 * background poll is just a safety net for drift (a restart, a stale
 * session); it can never let a stale LIVE read act as authorization, because
 * every write goes back through the backend's own checks regardless of what
 * this component last saw.
 */
const POLL_MS = 15_000

export default function TradingModeControl() {
  const mode = useStore((s) => s.tradingMode)
  const angel = useStore((s) => s.angelStatus)
  const setMode = useStore((s) => s.setTradingMode)
  const setAngel = useStore((s) => s.setAngelStatus)
  const [open, setOpen] = useState(false)
  const [confirmingSwitch, setConfirmingSwitch] = useState<'LIVE' | 'PAPER' | null>(null)
  const [confirmingAutoLive, setConfirmingAutoLive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [critical, setCritical] = useState<LiveAlert | null>(null)
  const [openPositions, setOpenPositions] = useState(0)

  const refreshSupervision = async () => {
    try {
      // Supervision endpoints are newer than the mode switch; a backend
      // that predates them must not blank this control.
      const [alerts, live] = await Promise.all([
        api.alerts().catch(() => []), api.liveExecutions().catch(() => [])])
      setCritical([...alerts].reverse().find((x) => x.level === 'critical') ?? null)
      setOpenPositions(live.filter((x) =>
        x.filled_quantity > 0 && !['CLOSED', 'REJECTED', 'CANCELLED'].includes(x.status)).length)
    } catch {
      /* transient — next poll retries */
    }
  }

  useEffect(() => {
    void refreshSupervision()
    const id = window.setInterval(refreshSupervision, POLL_MS)
    return () => window.clearInterval(id)
  }, [])

  if (!mode) return null
  const isLive = mode.mode === 'LIVE'
  const wallet = isLive && angel?.available_funds != null
    ? `₹${angel.available_funds.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : null

  const connect = async () => {
    setBusy(true)
    setError(null)
    try {
      setAngel(await api.angelConnect())
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not connect to Angel One.')
    } finally {
      setBusy(false)
    }
  }

  const confirmSwitch = async () => {
    if (!confirmingSwitch) return
    setBusy(true)
    setError(null)
    try {
      const next = await api.setTradingMode(confirmingSwitch, true)
      setMode(next)
      // A fresh mode flip changes what the wallet readout should show
      // immediately — don't wait up to 15s for the background poll.
      setAngel(await api.angelStatus())
      setConfirmingSwitch(null)
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not switch trading mode.')
    } finally {
      setBusy(false)
    }
  }

  const disableAutoLive = async () => {
    setBusy(true)
    setError(null)
    try {
      setMode(await api.setAutoLive(false))
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not disable auto-live.')
    } finally {
      setBusy(false)
    }
  }

  const confirmAutoLive = async () => {
    setBusy(true)
    setError(null)
    try {
      setMode(await api.setAutoLive(true, true))
      setConfirmingAutoLive(false)
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not enable auto-live.')
    } finally {
      setBusy(false)
    }
  }

  const toggleKillSwitch = async () => {
    setBusy(true)
    try {
      setMode(await api.killSwitch(!mode.kill_switch_active))
    } finally {
      setBusy(false)
    }
  }

  const flattenAll = async () => {
    if (!window.confirm(
      `Close ${openPositions} live position(s) at market now? This also stops new live orders.`)) return
    setBusy(true)
    setError(null)
    try {
      setMode(await api.flattenAll())
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not flatten — check Angel One.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative">
      {critical && (
        <div role="alert"
             className="fixed inset-x-0 top-0 z-[220] bg-bear px-4 py-1.5 text-center text-2xs font-semibold text-white">
          {critical.message}
          {typeof critical.context.symbol === 'string' ? ` — ${critical.context.symbol}` : ''}
        </div>
      )}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 transition-colors ${
          isLive ? 'border-bear/60 bg-bear/10' : 'border-bull/40 bg-bull/[0.06]'}`}
        title={isLive ? 'LIVE — real orders can be submitted to Angel One'
                     : 'DEMO — simulated only, no real money'}
      >
        <span className={`h-2 w-2 rounded-full ${isLive ? 'bg-bear animate-pulse' : 'bg-bull'}`} />
        <span className={`text-2xs font-bold tracking-wide ${isLive ? 'text-bear' : 'text-bull'}`}>
          {isLive ? 'LIVE' : 'DEMO'}
        </span>
        {isLive && (
          <span className="text-[9px] text-bear/80" title="Available funds — Angel One (rmsLimit)">
            {wallet ?? 'REAL MONEY'}
          </span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.14 }}
            className="absolute right-0 top-full z-50 mt-1.5 w-[300px] rounded-xl p-3
                       bg-panel/[0.98] backdrop-blur-xl border border-edge
                       shadow-[0_24px_60px_-20px_rgb(var(--c-void)/0.95)] space-y-2.5"
          >
            <div className="flex items-center justify-between">
              <h3 className="panel-title">Trading mode</h3>
              <span className={`text-2xs font-bold ${isLive ? 'text-bear' : 'text-bull'}`}>
                {isLive ? '🔴 LIVE' : '🟢 DEMO'}
              </span>
            </div>

            <div className="flex items-center justify-between text-2xs">
              <span className="text-faint">Angel One</span>
              <span className={angel?.connected ? 'text-bull' : 'text-faint'}>
                {angel?.connected ? '● Connected' : '● Not connected'}
              </span>
            </div>

            {isLive && (
              <div className="flex items-center justify-between text-2xs">
                <span className="text-faint">Wallet</span>
                <span className={wallet ? 'font-semibold text-ink' : 'text-faint'}
                      title="Available funds reported by Angel One, not the Settings balance">
                  {wallet ?? 'Not available yet'}
                </span>
              </div>
            )}

            {!angel?.connected && (
              <button type="button" className="btn w-full !py-1" onClick={connect} disabled={busy}>
                Connect Angel One
              </button>
            )}

            {mode.kill_switch_active && (
              <p className="text-2xs text-warn bg-warn/10 rounded-lg px-2 py-1.5">
                🔴 New live trades blocked (kill switch)
                {mode.kill_switch_reason ? ` — ${mode.kill_switch_reason}` : ''}
              </p>
            )}

            {openPositions > 0 && (
              <button type="button" className="btn w-full !py-1 !border-bear/60 !text-bear"
                      onClick={flattenAll} disabled={busy}
                      title="Cancel protective orders and close every live position at market">
                Flatten {openPositions} live position{openPositions > 1 ? 's' : ''}
              </button>
            )}

            {(isLive || mode.kill_switch_active) && (
              <button type="button" className="btn w-full !py-1 !text-warn"
                      onClick={toggleKillSwitch} disabled={busy}>
                {mode.kill_switch_active ? 'Clear emergency stop' : 'Emergency stop'}
              </button>
            )}

            {isLive && (
              <div className={`flex items-center justify-between rounded-lg px-2 py-1.5 ${
                mode.auto_live_enabled ? 'bg-bear/10 border border-bear/40' : 'bg-tint/[0.03]'}`}>
                <span>
                  <span className={`block text-2xs font-semibold ${
                    mode.auto_live_enabled ? 'text-bear' : 'text-ink'}`}>
                    AUTO LIVE
                  </span>
                  <span className="block text-[9px] text-faint leading-relaxed">
                    {mode.auto_live_enabled
                      ? 'Armed — a signal with a measured edge places a real order with no click.'
                      : 'Off — every live order still needs your click.'}
                  </span>
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={mode.auto_live_enabled}
                  aria-label="Automatic live trading"
                  onClick={() => (mode.auto_live_enabled ? disableAutoLive() : setConfirmingAutoLive(true))}
                  disabled={busy}
                  className="shrink-0"
                >
                  <span className={`relative h-3.5 w-6 rounded-full transition-colors block ${
                    mode.auto_live_enabled ? 'bg-bear' : 'bg-tint/20'}`}>
                    <motion.span
                      className="absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white shadow"
                      animate={{ left: mode.auto_live_enabled ? 14 : 2 }}
                      transition={{ type: 'spring', stiffness: 500, damping: 32 }}
                    />
                  </span>
                </button>
              </div>
            )}

            <button
              type="button"
              className={`btn w-full ${isLive ? '' : '!border-bear/60 !text-bear'}`}
              onClick={() => setConfirmingSwitch(isLive ? 'PAPER' : 'LIVE')}
              disabled={busy || (!isLive && !angel?.connected)}
              title={!isLive && !angel?.connected ? 'Connect Angel One first' : undefined}
            >
              {isLive ? 'Switch to DEMO' : 'Switch to LIVE'}
            </button>

            {error && <p className="text-2xs text-bear">{error}</p>}

            <p className="text-[10px] text-faint leading-relaxed border-t border-hairline pt-2">
              {isLive
                ? 'Every live order still needs your explicit confirmation on the trade panel — switching modes here does not place anything by itself.'
                : 'Simulated fills at live prices. No orders are placed and no real money is involved until you switch to LIVE.'}
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {confirmingSwitch && (
          <motion.div
            className="fixed inset-0 z-[210] flex items-center justify-center bg-black/60 px-6"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          >
            <motion.div
              className={`w-full max-w-sm rounded-2xl border-2 bg-panel shadow-2xl overflow-hidden ${
                confirmingSwitch === 'LIVE' ? 'border-bear/60' : 'border-edge'}`}
              initial={{ scale: 0.94, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.96 }}
            >
              {confirmingSwitch === 'LIVE' ? (
                <>
                  <div className="bg-bear/15 border-b border-bear/40 px-5 py-3">
                    <h2 className="text-sm font-bold text-bear">⚠ Switch to Live Trading?</h2>
                  </div>
                  <div className="px-5 py-4 space-y-2 text-xs text-ink/85">
                    <p>You are about to enable real-money trading through Angel One.</p>
                    <p>New trades will be sent to your Angel One account.</p>
                    <div className="pt-2 space-y-1">
                      <Row label="Trading mode" value="LIVE" />
                      <Row label="Broker" value="Angel One" />
                    </div>
                  </div>
                </>
              ) : (
                <div className="px-5 py-4 space-y-2 text-xs text-ink/85">
                  <h2 className="text-sm font-bold text-ink">Switch back to DEMO?</h2>
                  <p>New real-money trades will be disabled.</p>
                  <p>Existing Angel One positions will continue to be monitored.</p>
                </div>
              )}
              {error && <p className="px-5 text-2xs text-bear">{error}</p>}
              <div className="flex gap-2 px-5 py-3 border-t border-hairline bg-tint/[0.02]">
                <button type="button" className="btn flex-1" onClick={() => setConfirmingSwitch(null)}
                        disabled={busy}>
                  Cancel
                </button>
                <button
                  type="button"
                  className={`btn btn-primary flex-1 ${
                    confirmingSwitch === 'LIVE' ? '!bg-bear !border-bear' : ''}`}
                  onClick={confirmSwitch} disabled={busy}
                >
                  {busy ? 'Switching…' : confirmingSwitch === 'LIVE' ? 'Enable LIVE' : 'Switch to DEMO'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {confirmingAutoLive && (
          <motion.div
            className="fixed inset-0 z-[210] flex items-center justify-center bg-black/60 px-6"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          >
            <motion.div
              className="w-full max-w-sm rounded-2xl border-2 border-bear/60 bg-panel shadow-2xl overflow-hidden"
              initial={{ scale: 0.94, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.96 }}
            >
              <div className="bg-bear/15 border-b border-bear/40 px-5 py-3">
                <h2 className="text-sm font-bold text-bear">⚠ Enable AUTO LIVE?</h2>
              </div>
              <div className="px-5 py-4 space-y-2 text-xs text-ink/85">
                <p>
                  A signal on whatever symbol is open will place a real Angel One
                  order with no click from you — the instant it clears the
                  measured-edge requirement and every other pre-trade check.
                </p>
                <p>
                  Nobody is watching each one. The daily loss limit, the funds
                  check and the kill switch still apply to every attempt, but
                  they stop a bad day — they do not undo an order already filled.
                </p>
                <div className="pt-2 space-y-1">
                  <Row label="Trading mode" value="LIVE" />
                  <Row label="Requires measured edge" value="Yes" />
                  <Row label="Stops on" value="Mode switch, kill switch, restart" />
                </div>
              </div>
              {error && <p className="px-5 text-2xs text-bear">{error}</p>}
              <div className="flex gap-2 px-5 py-3 border-t border-hairline bg-tint/[0.02]">
                <button type="button" className="btn flex-1"
                        onClick={() => setConfirmingAutoLive(false)} disabled={busy}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-primary flex-1 !bg-bear !border-bear"
                  onClick={confirmAutoLive} disabled={busy}
                >
                  {busy ? 'Enabling…' : 'Enable AUTO LIVE'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-2xs">
      <span className="text-faint">{label}</span>
      <span className="font-semibold">{value}</span>
    </div>
  )
}
