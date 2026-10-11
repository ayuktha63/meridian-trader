import type {
  AngelStatus, AnalysisResult, BacktestResult, Candle, Drawing, DrawingAnalytics,
  DrawingType, ExecutionPreview, ExecutionRecord, InstrumentCapability, LiveAlert, JournalEntry, JournalStats,
  MarketClock, Point, Quote, PaperState, Position, PositionSide, ScreenerRow,
  SettingsPayload, SymbolInfo, TimeframeInfo, TradingModeStatus,
} from './types'

/**
 * Empty in dev — Vite proxies /api to the backend, avoiding CORS entirely.
 * Under Electron the page is served from file://, where a relative path has
 * nothing to resolve against, so it needs an absolute origin.
 */
export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  (window.location.protocol === 'file:' ? 'http://localhost:8000' : '')

const BASE = API_BASE

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

const TOKEN_KEY = 'meridian.accessToken'

/**
 * The backend's APP_ACCESS_TOKEN, when it has one. Asked for once and kept in
 * this browser — never baked into the bundle, where anyone served the page
 * could read it. Local use needs none: the backend answers this machine
 * without a token.
 */
export function accessToken(): string | null {
  try { return window.localStorage.getItem(TOKEN_KEY) } catch { return null }
}

let asking: Promise<string | null> | null = null

function askForToken(): Promise<string | null> {
  // One prompt however many requests failed at once.
  asking ??= Promise.resolve().then(() => {
    const entered = window.prompt(
      'This Meridian server requires its access token (APP_ACCESS_TOKEN).')?.trim() || null
    try {
      if (entered) window.localStorage.setItem(TOKEN_KEY, entered)
      else window.localStorage.removeItem(TOKEN_KEY)
    } catch { /* storage unavailable — the token lasts for this request only */ }
    window.setTimeout(() => { asking = null }, 0)
    return entered
  })
  return asking
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const token = accessToken()
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  })

  if (response.status === 401 && !retried) {
    const entered = await askForToken()
    if (entered) return request<T>(path, init, true)
  }

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body — keep the status text */
    }
    throw new ApiError(response.status, detail)
  }

  return response.status === 204 ? (undefined as T) : response.json()
}

export const api = {
  health: () => request<Record<string, unknown>>('/api/health'),

  // ── market ────────────────────────────────────────────────
  symbols: () => request<SymbolInfo[]>('/api/market/symbols'),
  timeframes: () => request<TimeframeInfo[]>('/api/market/timeframes'),
  candles: (symbol: string, timeframe: string, limit = 600) =>
    request<{ symbol: string; timeframe: string; candles: Candle[]; provider: string }>(
      `/api/market/candles?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`),
  quotes: (symbols?: string[]) =>
    request<Record<string, Quote>>(
      `/api/market/quotes${symbols?.length ? `?symbols=${symbols.join(',')}` : ''}`),
  clock: () => request<MarketClock>('/api/market/clock'),

  // ── drawings ──────────────────────────────────────────────
  listDrawings: (symbol: string, timeframe?: string) =>
    request<Drawing[]>(
      `/api/drawings?symbol=${symbol}${timeframe ? `&timeframe=${timeframe}` : ''}`),
  createDrawing: (payload: {
    symbol: string
    timeframe: string
    type: DrawingType
    points: Point[]
    style?: Partial<Drawing['style']>
    label?: string
    text?: string
  }) => request<Drawing>('/api/drawings', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateDrawing: (id: string, payload: Partial<Pick<Drawing, 'points' | 'label' | 'text' | 'visible' | 'locked'>> & { style?: Partial<Drawing['style']> }) =>
    request<Drawing>(`/api/drawings/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteDrawing: (id: string) =>
    request<void>(`/api/drawings/${id}`, { method: 'DELETE' }),
  clearDrawings: (symbol: string, timeframe?: string) =>
    request<{ removed: number }>(
      `/api/drawings?symbol=${symbol}${timeframe ? `&timeframe=${timeframe}` : ''}`,
      { method: 'DELETE' }),
  drawingAnalytics: (symbol: string, timeframe: string) =>
    request<DrawingAnalytics[]>(
      `/api/drawings/analytics?symbol=${symbol}&timeframe=${timeframe}`),

  // ── analysis ──────────────────────────────────────────────
  analyse: (symbol: string, timeframe: string, opts?: {
    includeAi?: boolean
    force?: boolean
    question?: string
  }) => request<AnalysisResult>('/api/analysis/run', {
    method: 'POST',
    body: JSON.stringify({
      symbol,
      timeframe,
      include_ai: opts?.includeAi ?? true,
      force_refresh: opts?.force ?? false,
      question: opts?.question ?? null,
    }),
  }),
  ask: (symbol: string, timeframe: string, question: string) =>
    request<AnalysisResult>('/api/analysis/ask', {
      method: 'POST',
      body: JSON.stringify({ symbol, timeframe, question, include_ai: true }),
    }),
  watchlist: (timeframe: string, symbols?: string[]) =>
    request<{ symbol: string; bias: string; score: number; rsi?: number; adx?: number; structure?: string; note?: string }[]>(
      `/api/analysis/watchlist?timeframe=${timeframe}${symbols?.length ? `&symbols=${symbols.join(',')}` : ''}`),
  aiStatus: () => request<{ enabled: boolean; model: string; effort: string; fallback: string }>(
    '/api/analysis/ai-status'),
  screener: (timeframe: string, minConfidence = 0, symbols?: string[]) =>
    request<ScreenerRow[]>(
      `/api/analysis/screener?timeframe=${timeframe}&min_confidence=${minConfidence}`
      + (symbols?.length ? `&symbols=${symbols.join(',')}` : '')),

  // ── journal ───────────────────────────────────────────────
  journal: (symbol?: string) =>
    request<JournalEntry[]>(`/api/journal${symbol ? `?symbol=${symbol}` : ''}`),
  logAnalysis: (analysis: AnalysisResult, snapshot?: string, notes?: string) =>
    request<JournalEntry>('/api/journal/from-analysis', {
      method: 'POST',
      body: JSON.stringify({ analysis, snapshot, notes }),
    }),
  setOutcome: (id: string, payload: {
    outcome: string
    exit_price?: number
    r_multiple?: number
    notes?: string
  }) => request<JournalEntry>(`/api/journal/${id}/outcome`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  deleteJournalEntry: (id: string) =>
    request<void>(`/api/journal/${id}`, { method: 'DELETE' }),
  journalStats: () => request<JournalStats>('/api/journal/stats'),

  // ── positions ─────────────────────────────────────────────
  positions: (symbol?: string, status = 'open') =>
    request<Position[]>(
      `/api/positions?status=${status}${symbol ? `&symbol=${symbol}` : ''}`),
  openPosition: (payload: {
    symbol: string
    timeframe: string
    side: PositionSide
    entry: number
    stop_loss: number
    take_profit?: number | null
    take_profit_2?: number | null
    lots?: number
    notes?: string
    analysis_id?: string
  }) => request<Position>('/api/positions', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updatePosition: (id: string, payload: {
    stop_loss?: number; take_profit?: number; lots?: number; notes?: string
    /** Confirm the stop now actually resting at your broker. */
    broker_stop?: number
  }) =>
    request<Position>(`/api/positions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  closePosition: (id: string, exitPrice?: number) =>
    request<Position>(`/api/positions/${id}/close`, {
      method: 'POST',
      body: JSON.stringify({ exit_price: exitPrice ?? null }),
    }),

  // ── paper trading (the demo account) ──────────────────────
  paperState: () => request<PaperState>('/api/paper'),
  paperConfig: (payload: {
    enabled?: boolean; risk_per_trade_pct?: number
    min_confidence?: number; symbols?: string[]; timeframes?: string[]
  }) => request<PaperState>('/api/paper/config', {
    method: 'PATCH', body: JSON.stringify(payload),
  }),
  paperReset: (startingBalance = 10000) =>
    request<PaperState>('/api/paper/reset', {
      method: 'POST', body: JSON.stringify({ starting_balance: startingBalance }),
    }),
  paperClose: (id: string) =>
    request<PaperState>(`/api/paper/close/${id}`, { method: 'POST' }),

  // ── live trading (Angel One) ───────────────────────────────
  tradingMode: () => request<TradingModeStatus>('/api/trading/mode'),
  setTradingMode: (mode: 'PAPER' | 'LIVE', confirmed = false) =>
    request<TradingModeStatus>('/api/trading/mode', {
      method: 'POST', body: JSON.stringify({ mode, confirmed }),
    }),
  killSwitch: (activate: boolean) =>
    request<TradingModeStatus>(`/api/trading/kill-switch?activate=${activate}`, {
      method: 'POST',
    }),
  flattenAll: () =>
    request<TradingModeStatus & { closing: number }>('/api/trading/flatten-all', {
      method: 'POST', body: JSON.stringify({ confirmed: true }),
    }),
  liveExecutions: () => request<ExecutionRecord[]>('/api/trades/live'),
  alerts: (since = 0) => request<LiveAlert[]>(`/api/alerts?since=${since}`),
  angelStatus: () => request<AngelStatus>('/api/broker/angel/status'),
  angelConnect: () => request<AngelStatus>('/api/broker/angel/connect', { method: 'POST' }),
  angelDisconnect: () =>
    request<AngelStatus>('/api/broker/angel/disconnect', { method: 'POST' }),
  instrumentCapability: (symbol: string) =>
    request<InstrumentCapability>(`/api/broker/angel/instrument/${symbol}/capability`),
  executeLive: (recommendationId: string, confirmed: boolean) =>
    request<ExecutionRecord>(`/api/trades/${recommendationId}/execute`, {
      method: 'POST', body: JSON.stringify({ confirmed }),
    }),
  /** Read-only: the exact order and whether the pre-trade gate would allow it. */
  previewLive: (recommendationId: string) =>
    request<ExecutionPreview>(`/api/trades/${recommendationId}/preview`),
  getExecution: (recommendationId: string) =>
    request<ExecutionRecord>(`/api/trades/${recommendationId}/execution`),

  // ── settings ──────────────────────────────────────────────
  settings: () => request<SettingsPayload>('/api/settings'),
  saveSettings: (patch: Record<string, unknown>) =>
    request<SettingsPayload>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  testClaude: () => request<{ ok: boolean; model: string; reply: string; input_tokens: number; output_tokens: number }>(
    '/api/settings/test-claude', { method: 'POST' }),
  zerodhaLoginUrl: () =>
    request<{ url: string; instructions: string }>('/api/settings/zerodha/login-url'),
  zerodhaExchange: (requestToken: string) =>
    request<{ ok: boolean; user_name?: string; note: string }>(
      '/api/settings/zerodha/exchange', {
        method: 'POST',
        body: JSON.stringify({ request_token: requestToken }),
      }),
  zerodhaDisconnect: () =>
    request<{ ok: boolean }>('/api/settings/zerodha/disconnect', { method: 'POST' }),
  zerodhaContracts: () =>
    request<{ symbol: string; display: string; resolved: string | null; exchange: string
              expiry: string | null; lot_size: number; error: string | null }[]>(
      '/api/settings/zerodha/contracts'),

  // ── backtest ──────────────────────────────────────────────
  backtest: (payload: {
    symbol: string
    timeframe: string
    bars?: number
    strategy?: string
    min_confidence?: number
    risk_reward?: number
    starting_balance?: number
    risk_pct?: number
  }) => request<BacktestResult>('/api/backtest/run', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  /** The test the live gate reads: parameters fitted on one stretch, traded on the next. */
  walkForward: (payload: {
    symbol: string; timeframe: string; bars?: number; strategy?: string
    min_confidence?: number; risk_reward?: number
  }) =>
    request<BacktestResult>('/api/backtest/walk-forward', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
