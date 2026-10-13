import { create } from 'zustand'
import { api } from '../lib/api'
import { applyTheme, readStoredTheme, type Theme } from '../lib/theme'
import { stream, type ConnectionStatus } from '../lib/ws'
import type {
  AngelStatus, AnalysisResult, Candle, Drawing, DrawingType, LiveTick, MarketClock, Point,
  PaperState, PaperTrade, Quote, SymbolInfo, TimeframeInfo, TradingModeStatus,
} from '../lib/types'

//: Mode and Angel connection state change rarely — this is a background
//: safety net, not how a switch is meant to reach the UI. The components that
//: cause a change (TradingModeControl's switch, kill switch, connect) write
//: the fresh value straight into the store themselves, so every subscriber
//: — the mode badge, the wallet readout — updates the instant the request
//: resolves. This poll only catches drift: a restart, a session going stale,
//: a second tab doing the switching.
const TRADING_STATUS_POLL_MS = 15_000

export type ToolId = DrawingType | 'cursor'

interface State {
  // reference data
  symbols: SymbolInfo[]
  timeframes: TimeframeInfo[]
  clock: MarketClock | null
  aiEnabled: boolean
  aiModel: string

  // active view
  symbol: string
  timeframe: string
  candles: Candle[]
  quote: Quote | null
  quotes: Record<string, Quote>
  loadingCandles: boolean
  /** Why the active chart has no bars, when it has none. */
  noData: string | null

  // drawings
  drawings: Drawing[]
  activeTool: ToolId
  selectedDrawingId: string | null
  showDrawings: boolean

  // analysis
  analysis: AnalysisResult | null
  analysisPending: boolean
  analysisError: string | null
  lastAnalysisTrigger: string | null
  lastAnalysisAt: number | null
  autoIntervalSeconds: number

  // live position tier (updates every tick, not every analysis)
  liveTick: LiveTick | null
  paper: PaperState | null
  paperFill: { trade: PaperTrade; reason: string } | null
  exitAlert: LiveTick | null
  alertSound: boolean
  showOverlays: boolean
  autoAnalysis: boolean
  includeAi: boolean

  // broker / trading mode — shared so every reader (the mode badge, the
  // wallet readout) sees a switch the instant it happens, not on its own poll
  tradingMode: TradingModeStatus | null
  angelStatus: AngelStatus | null

  // connection
  connection: ConnectionStatus
  bootError: string | null

  // ui
  theme: Theme
  activePanel: 'analysis' | 'position' | 'strategies' | 'derivatives'
    | 'structure' | 'drawings' | 'journal' | 'backtest'

  // actions
  bootstrap: () => Promise<void>
  setSymbol: (symbol: string) => Promise<void>
  setTimeframe: (timeframe: string) => Promise<void>
  loadCandles: (quiet?: boolean) => Promise<void>
  refreshMarket: () => Promise<void>
  loadDrawings: () => Promise<void>
  addDrawing: (type: DrawingType, points: Point[], extra?: { text?: string; label?: string }) => Promise<void>
  moveDrawing: (id: string, points: Point[]) => Promise<void>
  removeDrawing: (id: string) => Promise<void>
  clearDrawings: () => Promise<void>
  setActiveTool: (tool: ToolId) => void
  selectDrawing: (id: string | null) => void
  requestAnalysis: () => void
  askQuestion: (question: string) => Promise<void>
  toggleOverlays: () => void
  toggleDrawingsVisible: () => void
  toggleAutoAnalysis: () => void
  toggleIncludeAi: () => void
  setActivePanel: (panel: State['activePanel']) => void
  setPaper: (paper: PaperState) => void
  dismissPaperFill: () => void
  setTradingMode: (mode: TradingModeStatus) => void
  setAngelStatus: (status: AngelStatus) => void
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  dismissExitAlert: () => void
  toggleAlertSound: () => void
}

const CANDLE_LIMIT = 700

export const useStore = create<State>((set, get) => ({
  symbols: [],
  timeframes: [],
  clock: null,
  aiEnabled: false,
  aiModel: '',

  // NIFTY is listed whether or not Angel One is connected; the stocks only
  // exist once it is, so a first run cannot open on one of those.
  symbol: 'NIFTY',
  timeframe: '15m',
  candles: [],
  quote: null,
  quotes: {},
  loadingCandles: false,
  noData: null,

  drawings: [],
  activeTool: 'cursor',
  selectedDrawingId: null,
  showDrawings: true,

  analysis: null,
  analysisPending: false,
  analysisError: null,
  lastAnalysisTrigger: null,
  lastAnalysisAt: null,
  autoIntervalSeconds: 45,
  liveTick: null,
  paper: null,
  paperFill: null,
  exitAlert: null,
  alertSound: true,
  showOverlays: true,
  autoAnalysis: true,
  includeAi: true,

  tradingMode: null,
  angelStatus: null,

  connection: 'connecting',
  bootError: null,

  theme: readStoredTheme(),
  activePanel: 'analysis',

  async bootstrap() {
    try {
      const [symbols, timeframes, aiStatus] = await Promise.all([
        api.symbols(), api.timeframes(), api.aiStatus(),
      ])
      set({
        symbols,
        timeframes,
        aiEnabled: aiStatus.enabled,
        aiModel: aiStatus.enabled ? aiStatus.model : aiStatus.fallback,
        bootError: null,
      })
    } catch (error) {
      set({ bootError: error instanceof Error ? error.message : 'Cannot reach the API' })
      return
    }

    api.clock().then((clock) => set({ clock })).catch(() => undefined)
    api.quotes().then((quotes) => set({ quotes })).catch(() => undefined)

    const pollTradingStatus = async () => {
      try {
        const [tradingMode, angelStatus] = await Promise.all([
          api.tradingMode(), api.angelStatus(),
        ])
        set({ tradingMode })
        get().setAngelStatus(angelStatus)
        // An empty chart is asked for again: the bars arrive when the broker
        // session does, and the feed behind it takes a moment to load.
        if (get().candles.length === 0 && !get().loadingCandles) void get().refreshMarket()
      } catch {
        /* transient — next poll retries */
      }
    }
    void pollTradingStatus()
    window.setInterval(pollTradingStatus, TRADING_STATUS_POLL_MS)

    stream.onStatus((connection) => set({ connection }))
    stream.onMessage((message) => {
      const state = get()
      switch (message.type) {
        case 'quote': {
          set((prev) => ({
            quotes: { ...prev.quotes, [message.symbol]: message.data },
            quote: message.symbol === prev.symbol ? message.data : prev.quote,
          }))
          break
        }
        case 'candle':
        case 'bar_closed': {
          if (message.symbol !== state.symbol || message.timeframe !== state.timeframe) break
          set((prev) => {
            const candles = prev.candles.slice()
            const last = candles[candles.length - 1]
            if (last && last.time === message.candle.time) {
              candles[candles.length - 1] = message.candle
            } else if (!last || message.candle.time > last.time) {
              candles.push(message.candle)
              if (candles.length > CANDLE_LIMIT + 200) candles.shift()
            }
            return { candles }
          })
          break
        }
        case 'analysis_pending':
          set({ analysisPending: true, analysisError: null })
          break
        case 'analysis': {
          const result = message.data
          // Discard results for a view the trader has already left.
          if (result.symbol !== get().symbol || result.timeframe !== get().timeframe) {
            set({ analysisPending: false })
            break
          }
          set({
            analysis: result,
            analysisPending: false,
            analysisError: null,
            lastAnalysisTrigger: message.trigger,
            lastAnalysisAt: Date.now(),
          })
          break
        }
        case 'paper_state':
          // Merge rather than replace: the socket only carries the account and
          // the open trade, and clobbering `recent` would blank the trade
          // history the panel is showing.
          set((state) => ({
            paper: {
              account: message.data.account,
              open_trade: message.data.open_trade,
              recent: state.paper?.recent ?? [],
            },
          }))
          break
        case 'paper_fill':
          set({ paperFill: { trade: message.data, reason: message.reason } })
          break
        case 'position_tick':
          set({ liveTick: message.data })
          break
        case 'exit_alert':
          // A terminal alert always replaces a proximity one.
          set({ exitAlert: message.data, liveTick: message.data })
          break
        case 'hello':
          set({ autoIntervalSeconds: message.auto_analysis_interval })
          break
        case 'analysis_error':
          set({ analysisPending: false, analysisError: message.message })
          break
        case 'error':
          set({ analysisError: message.message })
          break
        default:
          break
      }
    })

    stream.connect()
    stream.subscribe(get().symbol, get().timeframe)
    await Promise.all([get().loadCandles(), get().loadDrawings()])

    window.setInterval(() => {
      api.clock().then((clock) => set({ clock })).catch(() => undefined)
    }, 60_000)
  },

  async setSymbol(symbol) {
    if (symbol === get().symbol) return
    set({ symbol, analysis: null, selectedDrawingId: null, candles: [],
          liveTick: null, exitAlert: null })
    stream.subscribe(symbol, get().timeframe)
    await Promise.all([get().loadCandles(), get().loadDrawings()])
  },

  async setTimeframe(timeframe) {
    if (timeframe === get().timeframe) return
    set({ timeframe, analysis: null, selectedDrawingId: null, candles: [] })
    stream.subscribe(get().symbol, timeframe)
    await Promise.all([get().loadCandles(), get().loadDrawings()])
  },

  async loadCandles(quiet = false) {
    const { symbol, timeframe } = get()
    // A retry of a chart already known to be empty does not flash "loading".
    if (!quiet) set({ loadingCandles: true })
    try {
      const batch = await api.candles(symbol, timeframe, CANDLE_LIMIT)
      // Guard against a slow response for a view we have since left.
      if (get().symbol !== symbol || get().timeframe !== timeframe) return
      set({ candles: batch.candles, loadingCandles: false, noData: null })
    } catch (error) {
      if (get().symbol !== symbol || get().timeframe !== timeframe) return
      const reason = error instanceof Error ? error.message : 'Failed to load candles'
      set({ loadingCandles: false, noData: reason, analysisError: reason })
    }
  },

  /** What there is to trade, and the bars behind the open chart: both come
   *  from Angel One, so both are read again when its session changes. */
  async refreshMarket() {
    try {
      set({ symbols: await api.symbols() })
    } catch {
      /* transient — the next poll retries */
    }
    api.quotes().then((quotes) => set({ quotes })).catch(() => undefined)
    if (get().candles.length === 0 && !get().loadingCandles) await get().loadCandles(true)
  },

  async loadDrawings() {
    const { symbol, timeframe } = get()
    try {
      const drawings = await api.listDrawings(symbol, timeframe)
      if (get().symbol !== symbol || get().timeframe !== timeframe) return
      set({ drawings })
    } catch {
      /* drawings are non-critical for rendering the chart */
    }
  },

  async addDrawing(type, points, extra) {
    const { symbol, timeframe } = get()
    const drawing = await api.createDrawing({
      symbol, timeframe, type, points,
      text: extra?.text,
      label: extra?.label,
    })
    set((prev) => ({
      drawings: [...prev.drawings, drawing],
      activeTool: 'cursor',
      selectedDrawingId: drawing.id,
    }))
    // The drawing changes the evidence, so re-analyse immediately.
    stream.requestAnalysis()
  },

  async moveDrawing(id, points) {
    // Optimistic: the drag already rendered, so reflect it before the round-trip.
    set((prev) => ({
      drawings: prev.drawings.map((d) => (d.id === id ? { ...d, points } : d)),
    }))
    try {
      await api.updateDrawing(id, { points })
      stream.requestAnalysis()
    } catch {
      await get().loadDrawings()
    }
  },

  async removeDrawing(id) {
    set((prev) => ({
      drawings: prev.drawings.filter((d) => d.id !== id),
      selectedDrawingId: prev.selectedDrawingId === id ? null : prev.selectedDrawingId,
    }))
    try {
      await api.deleteDrawing(id)
      stream.requestAnalysis()
    } catch {
      await get().loadDrawings()
    }
  },

  async clearDrawings() {
    const { symbol, timeframe } = get()
    set({ drawings: [], selectedDrawingId: null })
    await api.clearDrawings(symbol, timeframe)
    stream.requestAnalysis()
  },

  setActiveTool(tool) {
    set({ activeTool: tool, selectedDrawingId: tool === 'cursor' ? get().selectedDrawingId : null })
  },

  selectDrawing(id) {
    set({ selectedDrawingId: id })
  },

  requestAnalysis() {
    set({ analysisPending: true, analysisError: null })
    stream.requestAnalysis()
  },

  async askQuestion(question) {
    const { symbol, timeframe } = get()
    set({ analysisPending: true, analysisError: null })
    try {
      const result = await api.ask(symbol, timeframe, question)
      set({
        analysis: result, analysisPending: false,
        lastAnalysisTrigger: 'question', lastAnalysisAt: Date.now(),
      })
    } catch (error) {
      set({
        analysisPending: false,
        analysisError: error instanceof Error ? error.message : 'Question failed',
      })
    }
  },

  toggleOverlays: () => set((prev) => ({ showOverlays: !prev.showOverlays })),
  toggleDrawingsVisible: () => set((prev) => ({ showDrawings: !prev.showDrawings })),

  toggleAutoAnalysis: () => {
    const next = !get().autoAnalysis
    stream.setAutoAnalysis(next)
    set({ autoAnalysis: next })
  },

  toggleIncludeAi: () => {
    const next = !get().includeAi
    stream.setIncludeAi(next)
    set({ includeAi: next })
  },

  setActivePanel: (activePanel) => set({ activePanel }),

  setPaper: (paper) => set({ paper }),
  dismissPaperFill: () => set({ paperFill: null }),
  setTradingMode: (tradingMode) => set({ tradingMode }),
  setAngelStatus: (angelStatus) => {
    const before = get().angelStatus
    set({ angelStatus })
    if (before !== null && before.connected !== angelStatus.connected) {
      void get().refreshMarket()
      // The feed reloads behind the connection, scrip master included.
      window.setTimeout(() => void get().refreshMarket(), 5_000)
    }
  },

  setTheme: (theme) => {
    applyTheme(theme)
    set({ theme })
  },
  toggleTheme: () => {
    const theme: Theme = get().theme === 'dark' ? 'light' : 'dark'
    applyTheme(theme)
    set({ theme })
  },

  dismissExitAlert: () => set({ exitAlert: null }),
  toggleAlertSound: () => set((prev) => ({ alertSound: !prev.alertSound })),
}))

/** Instrument metadata for the active symbol (digits drive price formatting). */
export function useActiveInstrument(): SymbolInfo | undefined {
  return useStore((state) => state.symbols.find((s) => s.symbol === state.symbol))
}
