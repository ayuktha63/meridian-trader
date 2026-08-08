import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { SettingsPayload } from '../lib/types'
import { useStore } from '../store/useStore'
import { Badge, Spinner } from './ui'

type Tab = 'analyst' | 'data' | 'broker' | 'risk'

const TABS: { id: Tab; label: string }[] = [
  { id: 'analyst', label: 'Analyst' },
  { id: 'data', label: 'Market data' },
  { id: 'broker', label: 'Zerodha' },
  { id: 'risk', label: 'Risk' },
]

export default function SettingsModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('analyst')
  const [payload, setPayload] = useState<SettingsPayload | null>(null)
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const bootstrap = useStore((s) => s.bootstrap)

  const load = useCallback(async () => {
    try {
      setPayload(await api.settings())
    } catch (error) {
      setMessage({ tone: 'error', text: error instanceof Error ? error.message : 'Load failed' })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const set = (key: string, value: unknown) => setDraft((prev) => ({ ...prev, [key]: value }))
  const value = <T,>(key: string, fallback: T): T => {
    if (key in draft) return draft[key] as T
    const saved = payload?.settings as unknown as Record<string, unknown> | undefined
    return (saved?.[key] as T) ?? fallback
  }

  const save = async () => {
    if (Object.keys(draft).length === 0) return
    setSaving(true)
    setMessage(null)
    try {
      setPayload(await api.saveSettings(draft))
      setDraft({})
      setMessage({ tone: 'ok', text: 'Saved. Applied immediately — no restart needed.' })
      // Provider changes alter which symbols are live, so refresh the app.
      void bootstrap()
    } catch (error) {
      setMessage({
        tone: 'error',
        text: error instanceof ApiError ? error.message : 'Could not save settings',
      })
    } finally {
      setSaving(false)
    }
  }

  const dirty = Object.keys(draft).length > 0

  return (
    <AnimatePresence>
      <motion.div
        // Above the top bar, which now sits at z-50 so its dropdown clears the chart.
        className="fixed inset-0 z-[100] grid place-items-center p-6 bg-void/70 backdrop-blur-sm"
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
      >
        <motion.div
          className="glass-raised rounded-2xl w-full max-w-3xl max-h-[86vh] flex flex-col overflow-hidden"
          initial={{ opacity: 0, y: 14, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 10, scale: 0.98 }}
          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        >
          <header className="flex items-center gap-3 px-4 py-3 border-b border-hairline shrink-0">
            <h2 className="text-sm font-semibold">Settings</h2>
            <nav className="flex gap-0.5 ml-2">
              {TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  className={`relative px-2.5 py-1 rounded-md text-2xs font-medium transition-colors
                              ${tab === item.id ? 'text-ink' : 'text-faint hover:text-muted'}`}
                >
                  {tab === item.id && (
                    <motion.span layoutId="settings-tab"
                      className="absolute inset-0 rounded-md bg-tint/[0.08]" />
                  )}
                  <span className="relative">{item.label}</span>
                </button>
              ))}
            </nav>
            <span className="flex-1" />
            <button type="button" onClick={onClose}
                    className="text-faint hover:text-ink text-sm px-2">✕</button>
          </header>

          <div className="flex-1 min-h-0 scroll-y px-5 py-4">
            {!payload ? (
              <div className="h-40 grid place-items-center"><Spinner className="h-5 w-5" /></div>
            ) : tab === 'analyst' ? (
              <AnalystTab payload={payload} value={value} set={set} onReload={load} />
            ) : tab === 'data' ? (
              <DataTab payload={payload} value={value} set={set} />
            ) : tab === 'broker' ? (
              <BrokerTab payload={payload} value={value} set={set} onReload={load} />
            ) : (
              <RiskTab value={value} set={set} />
            )}
          </div>

          <footer className="flex items-center gap-3 px-5 py-3 border-t border-hairline shrink-0">
            {message && (
              <span className={`text-2xs ${message.tone === 'ok' ? 'text-bull' : 'text-bear'}`}>
                {message.text}
              </span>
            )}
            <span className="flex-1" />
            <button type="button" className="btn" onClick={onClose}>Close</button>
            <button type="button" className="btn btn-primary" onClick={save}
                    disabled={!dirty || saving}>
              {saving ? <Spinner /> : null}
              {dirty ? 'Save changes' : 'Saved'}
            </button>
          </footer>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}

// ── tabs ──────────────────────────────────────────────────────

function AnalystTab({ payload, value, set, onReload }: {
  payload: SettingsPayload
  value: <T,>(key: string, fallback: T) => T
  set: (key: string, value: unknown) => void
  onReload: () => void
}) {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      const response = await api.testClaude()
      setResult(`✓ ${response.model} replied "${response.reply}" · ${response.input_tokens} in / ${response.output_tokens} out`)
      onReload()
    } catch (error) {
      setResult(`✗ ${error instanceof Error ? error.message : 'Test failed'}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-5">
      <Section
        title="Anthropic API key"
        hint="Create one at console.anthropic.com → API keys. It is stored in backend/data/settings.json on this machine with 0600 permissions and never leaves it except to call Anthropic."
      >
        <Field label="API key">
          <input
            type="password"
            defaultValue={payload.settings.anthropic_api_key ?? ''}
            placeholder="sk-ant-api03-…"
            onChange={(event) => set('anthropic_api_key', event.target.value)}
            className="input font-mono"
          />
        </Field>
        <div className="flex items-center gap-2">
          <button type="button" className="btn" onClick={test}
                  disabled={testing || !payload.settings.anthropic_api_key_set}>
            {testing ? <Spinner /> : null} Test connection
          </button>
          <Badge tone={payload.analyst.enabled ? 'bull' : 'warn'}>
            {payload.analyst.enabled ? 'Claude active' : 'Rule engine (no key)'}
          </Badge>
        </div>
        {result && (
          <p className={`text-2xs ${result.startsWith('✓') ? 'text-bull' : 'text-bear'}`}>
            {result}
          </p>
        )}
        <p className="text-2xs text-faint leading-relaxed">
          Save the key first, then test. Without a key the platform runs its deterministic
          confluence engine — same JSON contract, same risk framing, no reasoning prose.
        </p>
      </Section>

      <Section title="Model" hint="Effort controls reasoning depth and cost per analysis.">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Model">
            <select className="input" value={value('claude_model', 'claude-opus-5')}
                    onChange={(event) => set('claude_model', event.target.value)}>
              <option value="claude-opus-5">Claude Opus 5 — best reasoning</option>
              <option value="claude-sonnet-5">Claude Sonnet 5 — faster, cheaper</option>
              <option value="claude-haiku-4-5">Claude Haiku 4.5 — cheapest</option>
            </select>
          </Field>
          <Field label="Effort">
            <select className="input" value={value('claude_effort', 'medium')}
                    onChange={(event) => set('claude_effort', event.target.value)}>
              <option value="low">low — fastest</option>
              <option value="medium">medium — balanced</option>
              <option value="high">high — deeper</option>
              <option value="xhigh">xhigh</option>
              <option value="max">max — slowest, most thorough</option>
            </select>
          </Field>
        </div>
        <Field label={`Auto re-analysis every ${value('ai_auto_analysis_interval', 45)}s`}>
          <input type="range" min={15} max={300} step={15}
                 value={value('ai_auto_analysis_interval', 45)}
                 onChange={(event) => set('ai_auto_analysis_interval', Number(event.target.value))}
                 className="w-full accent-accent" />
        </Field>
        <p className="text-2xs text-faint leading-relaxed">
          Analysis also runs on every bar close. On a 1-minute chart with a 15s interval
          you will make roughly 240 Claude calls an hour — set the interval to match how
          fast you actually trade.
        </p>
      </Section>
    </div>
  )
}

function DataTab({ payload, value, set }: {
  payload: SettingsPayload
  value: <T,>(key: string, fallback: T) => T
  set: (key: string, value: unknown) => void
}) {
  return (
    <div className="space-y-5">
      <Section
        title="Where prices come from"
        hint="A symbol is served by the best available source. Zerodha wins for Indian instruments, Binance for crypto, and the simulator only fills the gaps."
      >
        <div className="space-y-2">
          {payload.providers.map((provider) => (
            <label
              key={provider.id}
              className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 cursor-pointer
                          transition-colors
                          ${value('market_provider', 'synthetic') === provider.id
                            ? 'border-accent/50 bg-accent/10'
                            : 'border-hairline bg-tint/[0.02] hover:bg-tint/[0.05]'}`}
            >
              <input
                type="radio"
                name="provider"
                className="mt-1 accent-accent"
                checked={value('market_provider', 'synthetic') === provider.id}
                onChange={() => set('market_provider', provider.id)}
              />
              <span className="flex-1 min-w-0">
                <span className="flex items-center gap-2">
                  <span className="text-2xs font-medium text-ink">{provider.label}</span>
                  {provider.live && <Badge tone="bull">live</Badge>}
                  {provider.active && <Badge tone="accent">connected</Badge>}
                  {!provider.ready && <Badge tone="warn">needs setup</Badge>}
                </span>
                <span className="block text-2xs text-faint leading-relaxed mt-0.5">
                  {provider.covers}
                </span>
              </span>
            </label>
          ))}
        </div>

        <label className="flex items-center gap-2 pt-1">
          <input type="checkbox" className="accent-accent"
                 checked={value('hybrid_crypto_live', true)}
                 onChange={(event) => set('hybrid_crypto_live', event.target.checked)} />
          <span className="text-2xs text-muted">
            Always stream BTC/ETH live from Binance, whatever else is selected
          </span>
        </label>
      </Section>

      <Section title="Twelve Data (optional)"
               hint="Only needed for live spot FX and gold quotes. Free tier at twelvedata.com.">
        <Field label="API key">
          <input type="password" className="input font-mono"
                 defaultValue={payload.settings.twelvedata_api_key ?? ''}
                 placeholder="your twelvedata key"
                 onChange={(event) => set('twelvedata_api_key', event.target.value)} />
        </Field>
      </Section>

      <Section title="What each symbol is using right now">
        <div className="rounded-lg border border-hairline overflow-hidden">
          {payload.instruments.map((instrument) => (
            <div key={instrument.symbol}
                 className="flex items-center gap-2 px-2.5 py-1.5 border-b border-hairline
                            last:border-0 text-2xs">
              <span className="w-24 shrink-0 text-muted truncate">{instrument.display}</span>
              <Badge tone={instrument.live ? 'bull' : 'neutral'}>
                {instrument.live ? instrument.provider : 'simulated'}
              </Badge>
              {instrument.tradable_in_india
                ? <Badge tone="accent">{instrument.venue}</Badge>
                : <Badge tone="warn">not for IN residents</Badge>}
              <span className="flex-1" />
              <span className="text-faint tnum">
                lot {instrument.lot_size} · tick {instrument.tick_size}
              </span>
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}

function BrokerTab({ payload, value, set, onReload }: {
  payload: SettingsPayload
  value: <T,>(key: string, fallback: T) => T
  set: (key: string, value: unknown) => void
  onReload: () => void
}) {
  const [loginUrl, setLoginUrl] = useState<string | null>(null)
  const [requestToken, setRequestToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const connected = payload.settings.zerodha_access_token_set
  const issued = payload.settings.zerodha_token_issued_at

  const getLoginUrl = async () => {
    setNote(null)
    try {
      const response = await api.zerodhaLoginUrl()
      setLoginUrl(response.url)
      window.open(response.url, '_blank', 'noopener')
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not build the login URL')
    }
  }

  const exchange = async () => {
    setBusy(true)
    setNote(null)
    try {
      const response = await api.zerodhaExchange(requestToken)
      setNote(`Connected as ${response.user_name ?? 'your account'}. ${response.note}`)
      setRequestToken('')
      onReload()
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Exchange failed')
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async () => {
    await api.zerodhaDisconnect()
    setNote('Disconnected.')
    onReload()
  }

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-warn/30 bg-warn/[0.07] px-3 py-2.5">
        <p className="text-2xs text-warn leading-relaxed">
          Zerodha does not offer spot EURUSD, GBPUSD or XAUUSD, and Indian residents may
          not trade spot forex offshore under FEMA. What you <em>can</em> trade here:
          USDINR / EURINR / GBPINR / JPYINR currency futures on NSE-CDS, NIFTY and
          BANKNIFTY on NFO, and GOLD / SILVER / CRUDEOIL on MCX.
        </p>
      </div>

      <Section
        title="Kite Connect credentials"
        hint="Create an app at developers.kite.trade. Market data requires the paid Kite Connect subscription; the free personal tier can place orders but returns no quotes or candles."
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="API key">
            <input className="input font-mono"
                   defaultValue={value('zerodha_api_key', '')}
                   placeholder="your kite api_key"
                   onChange={(event) => set('zerodha_api_key', event.target.value)} />
          </Field>
          <Field label="API secret">
            <input type="password" className="input font-mono"
                   defaultValue={payload.settings.zerodha_api_secret ?? ''}
                   placeholder="your kite api_secret"
                   onChange={(event) => set('zerodha_api_secret', event.target.value)} />
          </Field>
        </div>
        <p className="text-2xs text-faint leading-relaxed">
          Set the app's redirect URL to <code className="text-ink">http://localhost:5173</code>.
          Kite sends you back there with <code className="text-ink">?request_token=…</code> in
          the address bar — that is the value you paste below.
        </p>
      </Section>

      <Section
        title="Daily login"
        hint="Kite access tokens expire every morning around 06:00 IST. This is a broker rule, not a limitation of this app — repeat these two steps each trading day."
      >
        <div className="flex items-center gap-2 flex-wrap">
          <Badge tone={connected ? 'bull' : 'warn'}>
            {connected ? 'session active' : 'not connected'}
          </Badge>
          {issued && (
            <span className="text-2xs text-faint">
              token issued {new Date(issued * 1000).toLocaleString()}
            </span>
          )}
        </div>

        <div className="flex gap-2">
          <button type="button" className="btn" onClick={getLoginUrl}>
            1 · Open Kite login
          </button>
          {connected && (
            <button type="button" className="btn" onClick={disconnect}>Disconnect</button>
          )}
        </div>
        {loginUrl && (
          <p className="text-2xs text-faint break-all">
            If the tab did not open: <span className="text-accent">{loginUrl}</span>
          </p>
        )}

        <Field label="2 · Paste the request_token from the redirect URL">
          <div className="flex gap-2">
            <input className="input font-mono flex-1"
                   value={requestToken}
                   placeholder="e.g. Xy7aB2c…"
                   onChange={(event) => setRequestToken(event.target.value)} />
            <button type="button" className="btn btn-primary shrink-0"
                    onClick={exchange} disabled={busy || requestToken.trim().length < 6}>
              {busy ? <Spinner /> : null} Connect
            </button>
          </div>
        </Field>
        {note && <p className="text-2xs text-muted leading-relaxed">{note}</p>}
      </Section>
    </div>
  )
}

function RiskTab({ value, set }: {
  value: <T,>(key: string, fallback: T) => T
  set: (key: string, value: unknown) => void
}) {
  const balance = value('account_balance', 10000)
  const riskPct = value('risk_per_trade_pct', 1)
  const currency = value('account_currency', 'USD')

  return (
    <div className="space-y-5">
      <Section
        title="Position sizing"
        hint="Every suggested lot size comes from these two numbers and the distance to the stop. Nothing else in the platform decides how much you risk."
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Account balance">
            <input type="number" className="input tnum" value={balance}
                   onChange={(event) => set('account_balance', Number(event.target.value))} />
          </Field>
          <Field label="Currency">
            <select className="input" value={currency}
                    onChange={(event) => set('account_currency', event.target.value)}>
              <option value="INR">INR</option>
              <option value="USD">USD</option>
              <option value="EUR">EUR</option>
              <option value="GBP">GBP</option>
            </select>
          </Field>
        </div>

        <Field label={`Risk per trade — ${riskPct}% (${currency} ${(balance * riskPct / 100).toFixed(0)})`}>
          <input type="range" min={0.25} max={5} step={0.25} value={riskPct}
                 onChange={(event) => set('risk_per_trade_pct', Number(event.target.value))}
                 className="w-full accent-accent" />
        </Field>

        <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2.5 space-y-1">
          <p className="text-2xs text-muted leading-relaxed">
            At {riskPct}% per trade, ten consecutive losses costs you about{' '}
            <span className="text-bear tnum">
              {(100 * (1 - Math.pow(1 - riskPct / 100, 10))).toFixed(1)}%
            </span>{' '}
            of the account. Losing streaks of that length happen to strategies that win
            more than half the time.
          </p>
          <p className="text-2xs text-faint leading-relaxed">
            1–2% is the conventional professional range. Above 3%, a normal drawdown
            becomes an account-ending one.
          </p>
        </div>
      </Section>

      <Section
        title="Automatic protection"
        hint="As a trade runs in your favour the stop climbs a ladder behind it: breakeven first, then locking half a risk unit, then a full one."
      >
        <label className="flex items-start gap-2">
          <input type="checkbox" className="accent-accent mt-0.5"
                 aria-label="Move the tracked stop automatically"
                 title="Ratchet the tracked stop up the protection ladder without waiting for you to click"
                 checked={value('auto_protect', true)}
                 onChange={(event) => set('auto_protect', event.target.checked)} />
          <span className="text-2xs text-muted leading-relaxed">
            Move the tracked stop up the ladder automatically as the trade runs.
            <span className="block text-faint mt-0.5">
              With this off, each rung is offered as a one-click action instead.
            </span>
          </span>
        </label>

        <div className="rounded-lg border border-bear/40 bg-bear/[0.07] px-3 py-2.5 space-y-1">
          <p className="text-2xs text-ink/90 leading-relaxed">
            <span className="text-bear font-semibold">This never touches your broker.</span>{' '}
            Meridian has no order connection and will not pretend otherwise. It moves
            its own tracked stop and then tells you, loudly, to mirror it in Kite or
            CoinDCX.
          </p>
          <p className="text-2xs text-faint leading-relaxed">
            Until you confirm you have moved it, the position is flagged and the panel
            shows the protection you actually have — not the protection it planned.
            A screen that says SAFE while the original stop is still resting in the
            market is more dangerous than no tool at all.
          </p>
        </div>

        <div className="rounded-lg border border-hairline bg-tint/[0.02] px-3 py-2.5">
          <p className="text-2xs text-faint leading-relaxed">
            Rungs are gated on ATR as well as R, so a stop never moves into the
            instrument's own noise band. Moving to breakeven too early does not make a
            trade safer — it converts winners into scratches, which costs more over a
            year than the losses it avoids.
          </p>
        </div>
      </Section>
    </div>
  )
}

// ── primitives ────────────────────────────────────────────────

function Section({ title, hint, children }: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="space-y-2.5">
      <div>
        <h3 className="text-2xs font-semibold uppercase tracking-[0.12em] text-muted">{title}</h3>
        {hint && <p className="mt-1 text-2xs text-faint leading-relaxed">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-2xs text-faint">{label}</span>
      {children}
    </label>
  )
}
