import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { AngelStatus, TradingModeStatus } from '../../lib/types'

/**
 * The one control that answers "where am I?" — DEMO or LIVE — and nothing
 * else. Angel One is the only execution path that exists, so there is
 * deliberately no broker picker here: the only question a trader should
 * ever face is demo vs. real money, never which broker to route through.
 *
 * Polls rather than pushes: mode/connection state changes rarely enough
 * that a WS channel for it would be overkill, and a stale read here just
 * means the button briefly shows the wrong label — it can never let a
 * stale LIVE read from an old response act as authorization, because every
 * write goes back through the backend's own checks regardless of what this
 * component last saw.
 */
const POLL_MS = 15_000

export default function TradingModeControl() {
  const [mode, setMode] = useState<TradingModeStatus | null>(null)
  const [angel, setAngel] = useState<AngelStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [confirmingSwitch, setConfirmingSwitch] = useState<'LIVE' | 'PAPER' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    try {
      const [m, a] = await Promise.all([api.tradingMode(), api.angelStatus()])
      setMode(m)
      setAngel(a)
    } catch {
      /* transient — next poll retries; the button just stays in its last known state */
    }
  }

  useEffect(() => {
    void refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [])

  if (!mode) return null
  const isLive = mode.mode === 'LIVE'

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
      setConfirmingSwitch(null)
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not switch trading mode.')
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

  return (
    <div className="relative">
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
        {isLive && <span className="text-[9px] text-bear/80">REAL MONEY</span>}
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

            {!angel?.connected && (
              <button type="button" className="btn w-full !py-1" onClick={connect} disabled={busy}>
                Connect Angel One
              </button>
            )}

            {mode.kill_switch_active && (
              <p className="text-2xs text-warn bg-warn/10 rounded-lg px-2 py-1.5">
                🔴 New live trades blocked (kill switch)
              </p>
            )}

            {isLive && (
              <button type="button" className="btn w-full !py-1 !text-warn"
                      onClick={toggleKillSwitch} disabled={busy}>
                {mode.kill_switch_active ? 'Clear emergency stop' : 'Emergency stop'}
              </button>
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
