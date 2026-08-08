import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import { fmtPrice, fmtSigned, timeAgo } from '../../lib/format'
import type { Position, PositionSide } from '../../lib/types'
import { useActiveInstrument, useStore } from '../../store/useStore'
import { Badge, EmptyState, Spinner } from '../ui'

export default function PositionPanel() {
  const symbol = useStore((s) => s.symbol)
  const timeframe = useStore((s) => s.timeframe)
  const analysis = useStore((s) => s.analysis)
  const requestAnalysis = useStore((s) => s.requestAnalysis)
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5

  const [open, setOpen] = useState<Position[]>([])
  const [closed, setClosed] = useState<Position[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<{
    side: PositionSide; entry: string; stop: string; target: string; lots: string
  } | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [live, done] = await Promise.all([
        api.positions(undefined, 'open'),
        api.positions(undefined, 'closed'),
      ])
      setOpen(live)
      setClosed(done.slice(0, 12))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh, analysis?.id])

  const prefill = () => {
    const plan = analysis?.plan
    const price = analysis?.price ?? 0
    const directional = plan?.decision === 'BUY' || plan?.decision === 'SELL'
    setForm({
      side: plan?.decision === 'SELL' ? 'SHORT' : 'LONG',
      entry: String(directional && plan?.entry ? plan.entry : price),
      stop: String(directional && plan?.stop_loss ? plan.stop_loss : ''),
      target: String(directional && plan?.take_profit ? plan.take_profit : ''),
      lots: String(plan?.suggested_lots ?? 1),
    })
    setError(null)
  }

  const submit = async () => {
    if (!form) return
    setError(null)
    try {
      await api.openPosition({
        symbol,
        timeframe,
        side: form.side,
        entry: Number(form.entry),
        stop_loss: Number(form.stop),
        take_profit: form.target ? Number(form.target) : null,
        lots: Number(form.lots) || 1,
        analysis_id: analysis?.id,
      })
      setForm(null)
      await refresh()
      requestAnalysis()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not record the trade')
    }
  }

  const close = async (id: string) => {
    await api.closePosition(id)
    await refresh()
    requestAnalysis()
  }

  // Live risk maths for the entry form. A stop the wrong side of entry is the
  // single most common typo here, and it would fire the exit alert instantly.
  const entryNum = Number(form?.entry)
  const stopNum = Number(form?.stop)
  const targetNum = Number(form?.target)
  const valid = form !== null && Number.isFinite(entryNum) && Number.isFinite(stopNum)
    && entryNum > 0 && stopNum > 0
  const stopInvalid = valid
    && ((form!.side === 'LONG' && stopNum >= entryNum)
      || (form!.side === 'SHORT' && stopNum <= entryNum))

  const pipSize = instrument?.pip_size ?? 0.0001
  const riskPips = valid && !stopInvalid ? Math.abs(entryNum - stopNum) / pipSize : null
  const rr = riskPips && Number.isFinite(targetNum) && targetNum > 0
    ? Math.abs(targetNum - entryNum) / Math.abs(entryNum - stopNum) : null
  // Tick value per lot is pip size x contract size — the same identity the
  // backend's sizing uses, kept here so the readout updates as you type.
  const cashRisk = riskPips !== null && instrument
    ? riskPips * instrument.pip_size * instrument.contract_size
      * (Number(form?.lots) || 1)
    : null

  if (loading) {
    return <div className="h-full grid place-items-center"><Spinner className="h-5 w-5" /></div>
  }

  return (
    <div className="h-full scroll-y px-3 py-3 space-y-3">
      {/* Record a trade */}
      {form ? (
        <div className="rounded-lg border border-accent/40 bg-accent/[0.06] p-3 space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="panel-title">Enter your trade</h3>
            <span className="flex-1" />
            <span className="text-[10px] text-faint">{symbol} · {timeframe}</span>
          </div>

          <div className="flex gap-1" role="group" aria-label="Trade direction">
            {(['LONG', 'SHORT'] as PositionSide[]).map((side) => (
              <button
                key={side}
                type="button"
                onClick={() => setForm({ ...form, side })}
                aria-pressed={form.side === side}
                title={side === 'LONG'
                  ? 'You bought — you profit as price rises'
                  : 'You sold — you profit as price falls'}
                className={`chip flex-1 justify-center ${form.side === side ? 'chip-active' : ''}`}
              >
                {side}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Labelled label="Entry price"
                      hint="The price you were actually filled at, not the price you wanted">
              <input className="input tnum" inputMode="decimal" value={form.entry}
                     aria-label="Entry price"
                     title="The price your broker filled you at"
                     onChange={(e) => setForm({ ...form, entry: e.target.value })} />
            </Labelled>
            <Labelled label="Stop loss" required
                      hint="Required. Without a stop there is no risk unit, so no R, no sizing and no exit alert.">
              <input className={`input tnum ${stopInvalid ? 'border-bear/60' : ''}`}
                     inputMode="decimal" value={form.stop} placeholder="must be set"
                     aria-label="Stop loss price"
                     title="Where you are wrong. This drives the exit alert."
                     onChange={(e) => setForm({ ...form, stop: e.target.value })} />
            </Labelled>
            <Labelled label="Take profit"
                      hint="Optional. Leave blank to manage the exit by structure instead.">
              <input className="input tnum" inputMode="decimal" value={form.target}
                     aria-label="Take profit price"
                     title="Optional target — triggers the target-reached alert"
                     onChange={(e) => setForm({ ...form, target: e.target.value })} />
            </Labelled>
            <Labelled label="Lots / quantity"
                      hint="Contracts or lots you actually hold, used for cash risk">
              <input className="input tnum" inputMode="decimal" value={form.lots}
                     aria-label="Lots or quantity"
                     title="How many lots you hold"
                     onChange={(e) => setForm({ ...form, lots: e.target.value })} />
            </Labelled>
          </div>

          {/* Live risk maths, so the size is a decision rather than a guess. */}
          {riskPips !== null && (
            <div className="grid grid-cols-3 gap-2 rounded-lg bg-tint/[0.04] px-2.5 py-2">
              <Metric label="Risk" value={`${riskPips.toFixed(1)}p`} tone="text-bear"
                      hint="Distance from entry to stop, in ticks" />
              <Metric label="Cash at risk"
                      value={cashRisk !== null ? cashRisk.toLocaleString(undefined, {
                        maximumFractionDigits: 0 }) : '—'}
                      tone="text-bear"
                      hint="Ticks × tick value × lots. What a stop-out costs you." />
              <Metric label="R:R" value={rr !== null ? rr.toFixed(2) : '—'}
                      tone={rr !== null && rr >= 1.5 ? 'text-bull' : 'text-muted'}
                      hint="Reward divided by risk. Below 1.5 the win rate has to carry the trade." />
            </div>
          )}

          {stopInvalid && (
            <p className="text-2xs text-bear leading-relaxed">
              A {form.side.toLowerCase()} stop must be{' '}
              {form.side === 'LONG' ? 'below' : 'above'} the entry — otherwise it is
              a target, and the exit alert would fire immediately.
            </p>
          )}
          {error && <p className="text-2xs text-bear leading-relaxed">{error}</p>}

          <div className="flex gap-2">
            <button type="button" className="btn flex-1" onClick={() => setForm(null)}
                    title="Discard this entry — nothing is recorded">
              Cancel
            </button>
            <button type="button" className="btn btn-primary flex-1" onClick={submit}
                    disabled={!form.entry || !form.stop || stopInvalid}
                    title="Start tracking — enables live R, hold/exit guidance and the exit alert">
              Track this trade
            </button>
          </div>
          <p className="text-[10px] text-faint leading-relaxed">
            This records what you already did in your broker. It places nothing.
          </p>
        </div>
      ) : (
        <button type="button" className="btn btn-primary w-full" onClick={prefill}
                title="Record a position you have already opened, so Meridian can track it tick by tick and alert you on the exit">
          + I'm in a trade on {symbol}
        </button>
      )}

      {/* Open */}
      {open.length > 0 && (
        <section>
          <h3 className="panel-title mb-1.5">Open</h3>
          <div className="space-y-1.5">
            {open.map((position) => (
              <div key={position.id}
                   className="rounded-lg border border-hairline bg-tint/[0.02] px-2.5 py-2">
                <div className="flex items-center gap-2">
                  <Badge tone={position.side === 'LONG' ? 'bull' : 'bear'}>
                    {position.side}
                  </Badge>
                  <span className="text-2xs text-muted">{position.symbol}</span>
                  <span className="text-2xs tnum text-faint">
                    {fmtPrice(position.entry, digits)}
                  </span>
                  <span className="flex-1" />
                  <button type="button" className="chip hover:text-ink"
                          onClick={() => close(position.id)}>
                    close
                  </button>
                </div>
                <div className="mt-1 flex gap-2 text-[10px] text-faint tnum">
                  <span>SL {fmtPrice(position.stop_loss, digits)}</span>
                  {position.take_profit && <span>TP {fmtPrice(position.take_profit, digits)}</span>}
                  <span>{position.lots} lot{position.lots === 1 ? '' : 's'}</span>
                  <span>{timeAgo(position.opened_at)}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {open.length === 0 && !form && (
        <EmptyState
          title="No open position tracked"
          hint="Once you take a trade in your broker, record it here. The panel above then switches from entry analysis to live hold/exit guidance."
        />
      )}

      {/* Closed */}
      {closed.length > 0 && (
        <section>
          <h3 className="panel-title mb-1.5">Recently closed</h3>
          <div className="space-y-1">
            {closed.map((position) => (
              <div key={position.id} className="flex items-center gap-2 text-[10px] tnum">
                <span className={position.side === 'LONG' ? 'text-bull' : 'text-bear'}>
                  {position.side}
                </span>
                <span className="text-muted">{position.symbol}</span>
                <span className="text-faint">{fmtPrice(position.entry, digits)}</span>
                <span className="flex-1" />
                <span className={(position.realised_r ?? 0) >= 0 ? 'text-bull' : 'text-bear'}>
                  {fmtSigned(position.realised_r ?? 0, 2)}R
                </span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-faint leading-relaxed">
            Closed trades here are a tracker, not the journal. Log the recommendation
            itself from the trade panel to build the calibration table.
          </p>
        </section>
      )}
    </div>
  )
}

function Labelled({ label, children, hint, required }: {
  label: string; children: React.ReactNode; hint?: string; required?: boolean
}) {
  return (
    <label className="block space-y-0.5" title={hint}>
      <span className="text-[10px] text-faint">
        {label}
        {required && <span className="text-bear ml-0.5" aria-hidden>*</span>}
      </span>
      {children}
    </label>
  )
}

function Metric({ label, value, tone, hint }: {
  label: string; value: string; tone?: string; hint?: string
}) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[10px] uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`text-2xs font-semibold tnum truncate ${tone ?? 'text-ink'}`}>{value}</div>
    </div>
  )
}
