import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type {
  AnalysisResult, ExecutionPreview, ExecutionRecord, InstrumentCapability,
} from '../../lib/types'

/**
 * The one button that can place a real order. Everything about the trade —
 * symbol, side, entry, stop, quantity — is decided server-side from the
 * stored recommendation; this component only ever sends a confirmation flag.
 * It stays disabled, with the exact reason shown, until every precondition
 * (LIVE mode, Angel One connected, kill switch off, instrument supported)
 * actually holds — never a generic "unavailable".
 *
 * The confirmation dialog shows the server's own preview of the order: the
 * real quantity, the tick-rounded prices, what it costs in margin, and the
 * pre-trade gate's verdict. What is confirmed is what is sent; nothing in
 * the dialog is computed here.
 */
export default function ExecuteLiveButton({ analysis }: { analysis: AnalysisResult }) {
  const [mode, setMode] = useState<'PAPER' | 'LIVE' | null>(null)
  const [killSwitch, setKillSwitch] = useState(false)
  const [angelConnected, setAngelConnected] = useState(false)
  const [capability, setCapability] = useState<InstrumentCapability | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [preview, setPreview] = useState<ExecutionPreview | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<ExecutionRecord | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [modeStatus, angel, cap] = await Promise.all([
          api.tradingMode(), api.angelStatus(),
          api.instrumentCapability(analysis.symbol),
        ])
        if (cancelled) return
        setMode(modeStatus.mode)
        setKillSwitch(modeStatus.kill_switch_active)
        setAngelConnected(angel.connected)
        setCapability(cap)
      } catch {
        if (!cancelled) setCapability({ supported: false, reason: 'Could not reach the broker service.' })
      }
    }
    void load()
    return () => { cancelled = true }
  }, [analysis.symbol])

  const isActionable = analysis.verdict.decision !== 'WAIT'

  const blockedReason = !isActionable
    ? 'No BUY/SELL signal on this analysis.'
    : mode !== 'LIVE'
      ? 'Live trading mode is off — enable it in Settings → Broker.'
      : killSwitch
        ? 'The kill switch is active.'
        : !angelConnected
          ? 'Angel One is not connected.'
          : !capability?.supported
            ? (capability?.reason ?? 'Checking instrument support…')
            : null

  const canExecute = isActionable && !blockedReason

  const openConfirmation = async () => {
    setConfirming(true)
    setPreview(null)
    setError(null)
    try {
      setPreview(await api.previewLive(analysis.id))
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not preview the order.')
    }
  }

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const record = await api.executeLive(analysis.id, true)
      setResult(record)
      setConfirming(false)
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Execution request failed.')
    } finally {
      setSubmitting(false)
    }
  }

  if (result) {
    return <ExecutionResultCard record={result} onDismiss={() => setResult(null)} />
  }

  return (
    <>
      <button
        type="button"
        className={`btn flex-1 ${canExecute ? 'border-bear/60 text-bear hover:bg-bear/10' : ''}`}
        disabled={!canExecute}
        title={blockedReason ?? 'Submit a real order to Angel One'}
        onClick={openConfirmation}
      >
        Execute Live
      </button>

      <AnimatePresence>
        {confirming && (
          <motion.div
            className="fixed inset-0 z-[210] flex items-center justify-center bg-black/60 px-6"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          >
            <motion.div
              className="w-full max-w-md rounded-2xl border-2 border-bear/60 bg-panel shadow-2xl overflow-hidden"
              initial={{ scale: 0.94, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.96 }}
            >
              <div className="bg-bear/15 border-b border-bear/40 px-5 py-3">
                <h2 className="text-sm font-bold text-bear tracking-wide">
                  ANGEL ONE — LIVE ORDER
                </h2>
              </div>
              <div className="px-5 py-4 space-y-2 text-xs">
                <Row label="Symbol"
                     value={preview?.trading_symbol
                       ? `${preview.trading_symbol} · ${preview.exchange}` : analysis.symbol} />
                <Row label="Side" value={analysis.verdict.decision} />
                {!preview && !error && (
                  <p className="text-2xs text-faint">Checking the order with the server…</p>
                )}
                {preview && (
                  <>
                    <Row label="Quantity" value={num(preview.quantity, 0)} />
                    <Row label="Entry (limit)" value={num(preview.entry_price)} />
                    <Row label="Stop" value={num(preview.stop_price)} />
                    <Row label="Target" value={num(preview.target_price)} />
                    <Row label="Order value" value={num(preview.order_value)} />
                    <Row label="Loss if stopped" value={num(preview.risk)} />
                    <Row label={preview.margin_required != null ? 'Margin blocked' : 'Available funds'}
                         value={num(preview.margin_required ?? preview.available_funds)} />
                    <Row label="R:R" value={`${analysis.plan.risk_reward ?? '—'}`} />
                    {preview.allowed ? (
                      <p className="text-2xs text-faint leading-relaxed">
                        On a fill, a stop order for this quantity is placed at Angel One
                        at once. If that fails the position is closed at market.
                      </p>
                    ) : (
                      <p className="text-2xs text-warn bg-warn/10 rounded-lg px-2 py-1.5 leading-relaxed">
                        <span className="font-bold">Refused by the pre-trade check.</span>{' '}
                        {preview.reason}
                      </p>
                    )}
                  </>
                )}
                {preview?.allowed && (
                  <p className="text-2xs text-bear font-semibold pt-2 border-t border-hairline mt-2">
                    This will place a REAL order on your Angel One account.
                  </p>
                )}
                {error && <p className="text-2xs text-bear">{error}</p>}
              </div>
              <div className="flex gap-2 px-5 py-3 border-t border-hairline bg-tint/[0.02]">
                <button type="button" className="btn flex-1" onClick={() => setConfirming(false)}
                        disabled={submitting}>
                  Cancel
                </button>
                <button type="button" className="btn btn-primary flex-1 !bg-bear !border-bear"
                        onClick={submit} disabled={submitting || !preview?.allowed}
                        title={preview && !preview.allowed ? preview.reason ?? undefined : undefined}>
                  {submitting ? 'Submitting…' : 'Confirm & Execute'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function num(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : value.toLocaleString('en-IN', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-faint">{label}</span>
      <span className="font-semibold tnum">{value}</span>
    </div>
  )
}

function ExecutionResultCard({ record, onDismiss }: {
  record: ExecutionRecord; onDismiss: () => void
}) {
  const filled = record.status === 'FILLED'
  const rejected = record.status === 'REJECTED'
  const unknown = record.status === 'UNKNOWN'
  const tone = filled ? 'text-bull border-bull/50 bg-bull/10'
    : rejected ? 'text-bear border-bear/50 bg-bear/10'
      : unknown ? 'text-warn border-warn/50 bg-warn/10'
        : 'text-accent border-accent/50 bg-accent/10'

  return (
    <div className={`rounded-lg border px-3 py-2 text-2xs space-y-1 ${tone}`}>
      <div className="flex items-center justify-between">
        <span className="font-bold uppercase tracking-wide">
          {filled ? 'Live position open' : rejected ? 'Order rejected'
            : unknown ? 'Status unknown — check Angel One manually'
              : `Live order ${record.status.toLowerCase()}`}
        </span>
        <button type="button" onClick={onDismiss} className="text-faint hover:text-ink">✕</button>
      </div>
      {record.broker_order_id && <div>Order ID: {record.broker_order_id}</div>}
      {filled && (
        <>
          <div>Filled: {record.filled_quantity}</div>
          <div>Average price: {record.average_fill_price}</div>
        </>
      )}
      {filled && (
        <div className={record.protection_status === 'PROTECTED' ? '' : 'text-bear font-bold'}>
          {record.protection_status === 'PROTECTED'
            ? `Stop resting at Angel One: ${record.stop_price}`
            : 'NO STOP AT THE BROKER YET — check Angel One'}
        </div>
      )}
      {record.error_message && <div className="text-ink/80">{record.error_message}</div>}
    </div>
  )
}
