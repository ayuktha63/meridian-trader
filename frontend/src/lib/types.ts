/** Mirrors the backend pydantic contracts in `backend/app/schemas`. */

export type Decision = 'BUY' | 'SELL' | 'WAIT'
export type Bias = 'bullish' | 'bearish' | 'neutral'

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface SymbolInfo {
  symbol: string
  display: string
  asset_class: 'fx' | 'metal' | 'crypto' | 'currency_fut' | 'index'
    | 'commodity' | 'stock'
  digits: number
  pip_size: number
  contract_size: number
  typical_spread_pips: number
  quote_currency: string
  live: boolean
  venue: string
  exchange: string | null
  broker_symbol: string | null
  tradable_in_india: boolean
  note: string
  provider: string
}

export interface TimeframeInfo {
  code: string
  label: string
  seconds: number
}

export interface Quote {
  symbol: string
  bid: number
  ask: number
  last: number
  spread_pips: number
  change_pct: number
  change_abs: number
  day_high: number
  day_low: number
  updated_at: number
  live: boolean
}

export interface MarketClock {
  utc_time: string
  active_session: string
  liquidity_regime: string
  sessions: { name: string; open: boolean; opens_in_minutes?: number; closes_in_minutes?: number }[]
}

// ── drawings ────────────────────────────────────────────────

export type DrawingType =
  | 'trendline' | 'horizontal' | 'support' | 'resistance'
  | 'rectangle' | 'supply_zone' | 'demand_zone'
  | 'channel' | 'parallel_channel'
  | 'fib_retracement' | 'fib_extension'
  | 'risk_reward' | 'arrow' | 'text' | 'brush'

export interface Point {
  time: number
  price: number
}

export interface DrawingStyle {
  color: string
  width: number
  dash?: number[] | null
  fill?: string | null
  opacity: number
  extend_left: boolean
  extend_right: boolean
  show_labels: boolean
}

export interface Drawing {
  id: string
  symbol: string
  timeframe: string
  type: DrawingType
  points: Point[]
  style: DrawingStyle
  label?: string | null
  text?: string | null
  locked: boolean
  visible: boolean
  meta: Record<string, unknown>
  created_at: number
  updated_at: number
}

export interface TouchEvent {
  time: number
  price: number
  index: number
  kind: string
  distance_pips: number
  volume_ratio: number
  reaction_pips: number
}

export interface DrawingAnalytics {
  drawing_id: string
  type: DrawingType
  role: string
  current_value?: number | null
  upper_value?: number | null
  lower_value?: number | null
  angle_degrees: number
  length_bars: number
  age_bars: number
  touches: number
  reactions: number
  breaks: number
  fake_breaks: number
  retests: number
  distance_pips: number
  distance_atr: number
  approaching: boolean
  price_side: string
  strength: number
  validity: 'untested' | 'respected' | 'broken' | 'invalidated'
  volume_at_touches: number
  fib_levels: Record<string, number>
  events: TouchEvent[]
  notes: string[]
}

// ── analysis ────────────────────────────────────────────────

export interface IndicatorSnapshot {
  ema: Record<string, number>
  sma: Record<string, number>
  vwap?: number | null
  atr?: number | null
  atr_pips?: number | null
  atr_pct?: number | null
  rsi?: number | null
  rsi_divergence?: string | null
  macd: Record<string, number>
  macd_state?: string | null
  adx?: number | null
  di_plus?: number | null
  di_minus?: number | null
  stoch_rsi: Record<string, number>
  ichimoku: Record<string, number>
  ichimoku_state?: string | null
  supertrend?: number | null
  supertrend_dir?: number | null
  bollinger: Record<string, number>
  bb_squeeze: boolean
  obv?: number | null
  obv_slope?: number | null
  mfi?: number | null
  cci?: number | null
  roc?: number | null
  williams_r?: number | null
  pivots: Record<string, number>
  volume_profile: {
    poc?: number
    vah?: number
    val?: number
    bins?: { price: number; volume: number }[]
    hvn?: number[]
    lvn?: number[]
  }
  relative_volume?: number | null
  volatility_regime: string
  trend_strength: string
}

export interface SwingPoint {
  index: number
  time: number
  price: number
  kind: string
  label?: string | null
  strength: number
}

export interface StructureEvent {
  kind: string
  direction: string
  time: number
  index: number
  price: number
  bars_ago: number
  note?: string | null
  significance: number
}

export interface Zone {
  kind: string
  direction: string
  top: number
  bottom: number
  start_time: number
  end_time: number
  mitigated: boolean
  tested: number
  strength: number
  distance_pct: number
  note?: string | null
}

export interface MarketStructure {
  trend: Bias
  structure_label: string
  swings: SwingPoint[]
  events: StructureEvent[]
  order_blocks: Zone[]
  breaker_blocks: Zone[]
  mitigation_blocks: Zone[]
  fair_value_gaps: Zone[]
  supply_zones: Zone[]
  demand_zones: Zone[]
  liquidity_pools: Zone[]
  equal_highs: number[]
  equal_lows: number[]
  range_high?: number | null
  range_low?: number | null
  equilibrium?: number | null
  premium_discount: string
  last_bos?: StructureEvent | null
  last_choch?: StructureEvent | null
  support_levels: number[]
  resistance_levels: number[]
  notes: string[]
}

export interface PatternHit {
  name: string
  category: string
  direction: string
  start_time: number
  end_time: number
  confidence: number
  target?: number | null
  invalidation?: number | null
  points: { time: number; price: number }[]
  note?: string | null
}

export interface TimeframeView {
  timeframe: string
  bias: Bias
  structure: string
  score: number
  rsi?: number | null
  adx?: number | null
  ema_stack?: string | null
  close?: number | null
  key_level?: number | null
  note?: string | null
}

export interface MTFReport {
  views: TimeframeView[]
  aligned_bias: Bias
  alignment_score: number
  weighted_score: number
  conflicts: string[]
  narrative: string
}

export interface ConfidenceComponent {
  name: string
  weight: number
  raw_score: number
  contribution: number
  detail: string
}

export interface ConfidenceBreakdown {
  components: ConfidenceComponent[]
  directional_score: number
  agreement: number
  final_confidence: number
  conflict: boolean
  reason: string
}

export interface AIVerdict {
  decision: Decision
  confidence: number
  entry?: number | null
  stop_loss?: number | null
  take_profit?: number | null
  take_profit_2?: number | null
  risk_reward?: number | null
  trend: string
  market_structure: string
  probability: number
  bullish_points: string[]
  bearish_points: string[]
  invalidation?: string | null
  timeframe_alignment?: string | null
  key_levels: number[]
  summary: string
  uncertainty: string
  source: 'claude' | 'rule_engine'
  model?: string | null
  latency_ms?: number | null
}

export interface TradePlan {
  decision: Decision
  entry?: number | null
  stop_loss?: number | null
  take_profit?: number | null
  take_profit_2?: number | null
  risk_pips?: number | null
  reward_pips?: number | null
  risk_reward?: number | null
  expected_move_pips?: number | null
  atr_pips?: number | null
  spread_pips?: number | null
  suggested_lots?: number | null
  risk_amount?: number | null
  account_balance?: number | null
  position_notes: string[]
}

export interface OverlayObject {
  id: string
  kind: 'line' | 'ray' | 'box' | 'zone' | 'label' | 'arrow' | 'marker' | 'path'
  role: string
  color: string
  points: { time: number; price: number }[]
  price?: number | null
  top?: number | null
  bottom?: number | null
  start_time?: number | null
  end_time?: number | null
  label?: string | null
  dash?: number[] | null
  width: number
  opacity: number
  fill?: string | null
}

// ── derivatives ─────────────────────────────────────────────

export interface ExpiryChoice {
  date: string
  kind: 'weekly' | 'monthly' | 'next-month'
  days_to_expiry: number
  note: string
}

export interface OptionLeg {
  contract: string
  strike: number
  option_type: 'CE' | 'PE'
  moneyness: 'ITM' | 'ATM' | 'OTM'
  expiry: string
  days_to_expiry: number
  estimated_premium: number
  delta: number
  theta_per_day: number
  theta_pct_per_day: number
  vega: number
  cost_per_lot: number
  max_loss_per_lot: number
  premium_stop: number
  premium_target?: number | null
  breakeven: number
  suggested_lots?: number | null
  note: string
}

export interface DerivativeSuggestion {
  symbol: string
  underlying_price: number
  direction: 'long' | 'short'
  lot_size: number
  implied_vol_estimate: number
  expiries: ExpiryChoice[]
  futures_symbol?: string | null
  futures_expiry?: string | null
  futures_risk_per_lot: number
  futures_reward_per_lot?: number | null
  futures_lots?: number | null
  futures_notes: string[]
  source: string
  options: OptionLeg[]
  recommended?: string | null
  notes: string[]
  disclaimer: string
}

// ── named strategies ────────────────────────────────────────

export type StrategyStance = 'long' | 'short' | 'flat'
export type StrategyFamily = 'trend' | 'reversion' | 'breakout' | 'smc' | 'momentum'

export interface StrategyCondition {
  name: string
  met: boolean
  detail: string
  weight: number
}

export interface ScenarioPoint { time: number; price: number }

export interface StrategyResult {
  key: string
  name: string
  family: StrategyFamily
  description: string
  stance: StrategyStance
  conviction: number
  conditions: StrategyCondition[]
  conditions_met: number
  conditions_total: number
  entry?: number | null
  stop_loss?: number | null
  take_profit?: number | null
  take_profit_2?: number | null
  risk_reward?: number | null
  horizon_bars: number
  rationale: string[]
  waiting_for: string[]
  note: string
  projection: ScenarioPoint[]
  invalidation_path: ScenarioPoint[]
}

export interface StrategyReport {
  results: StrategyResult[]
  consensus_stance: StrategyStance
  consensus_score: number
  agreement: number
  leader?: string | null
  summary: string
  disclaimer: string
  by_family: Record<string, string>
}

// ── paper trading (the demo account) ────────────────────────

export type PaperExitReason = 'stop' | 'target' | 'signal' | 'guardrail' | 'manual' | 'stale'

export interface PaperTrade {
  id: string
  account_id: string
  symbol: string
  timeframe: string
  side: 'LONG' | 'SHORT'
  status: 'open' | 'closed'
  entry: number
  entry_at: number
  exit?: number | null
  exit_at?: number | null
  exit_reason?: PaperExitReason | null
  stop_loss: number
  initial_stop: number
  take_profit?: number | null
  quantity: number
  notional: number
  entry_cost: number
  exit_cost: number
  gross_pnl: number
  net_pnl: number
  realised_r?: number | null
  peak_r: number
  trough_r: number
  protection_tier: number
  confidence: number
  decision_source: string
  reason: string
}

export interface PaperAccount {
  id: string
  currency: string
  starting_balance: number
  balance: number
  equity: number
  enabled: boolean
  risk_per_trade_pct: number
  min_confidence: number
  symbols: string[]
  timeframes: string[]
  open_trades: number
  total_trades: number
  wins: number
  losses: number
  realised_pnl: number
  unrealised_pnl: number
  total_costs: number
  win_rate: number
  profit_factor: number
  expectancy_r: number
  avg_win_r: number
  avg_loss_r: number
  max_drawdown: number
  peak_equity: number
  return_pct: number
  status_line: string
  blocked_reason: string
}

export interface PaperState {
  account: PaperAccount
  open_trade?: PaperTrade | null
  recent: PaperTrade[]
}

// ── risk management ─────────────────────────────────────────

export type ProtectionState = 'at_risk' | 'armed' | 'safe' | 'locked'
export type GuardrailStatus = 'clear' | 'caution' | 'halt'

export interface ProtectionTier {
  index: number
  name: string
  trigger_r: number
  locked_r: number
  stop: number
  reached: boolean
  active: boolean
  note: string
}

export interface ProtectionPlan {
  position_id: string
  state: ProtectionState
  headline: string
  detail: string
  initial_stop: number
  current_stop: number
  suggested_stop?: number | null
  suggested_reason: string
  action_available: boolean
  r_multiple: number
  peak_r: number
  trough_r: number
  giveback: number
  locked_r: number
  risk_amount: number
  secured_amount: number
  tiers: ProtectionTier[]
  next_tier?: ProtectionTier | null
  to_next_tier_pips?: number | null
  broker_stop?: number | null
  broker_synced: boolean
  broker_warning: string
  notes: string[]
}

export interface Guardrail {
  key: string
  label: string
  status: GuardrailStatus
  value: number
  limit: number
  unit: string
  detail: string
}

export interface AccountRisk {
  status: GuardrailStatus
  headline: string
  reason: string
  can_open_new: boolean
  balance: number
  currency: string
  risk_per_trade_pct: number
  realised_today_r: number
  realised_today_amount: number
  trades_today: number
  consecutive_losses: number
  open_positions: number
  open_risk_r: number
  open_risk_amount: number
  guardrails: Guardrail[]
  notes: string[]
}

// ── open positions ──────────────────────────────────────────

export type PositionSide = 'LONG' | 'SHORT'
export type ManagementAction =
  | 'HOLD' | 'TRAIL_STOP' | 'TAKE_PARTIAL' | 'EXIT' | 'STOPPED' | 'TARGET_HIT'

export interface Position {
  initial_stop: number
  protection_tier: number
  peak_r: number
  trough_r: number
  broker_stop?: number | null
  id: string
  symbol: string
  timeframe: string
  side: PositionSide
  entry: number
  stop_loss: number
  take_profit?: number | null
  take_profit_2?: number | null
  lots: number
  notes?: string | null
  status: string
  opened_at: number
  opened_bar_time: number
  closed_at?: number | null
  exit_price?: number | null
  realised_r?: number | null
}

export interface LiveTick {
  position_id: string
  symbol: string
  price: number
  r_multiple: number
  unrealised_pips: number
  distance_to_stop_pips: number
  distance_to_target_pips: number
  alert?: string | null
  urgency: 'normal' | 'elevated' | 'immediate'
  headline: string
  detail: string
  protection_state: ProtectionState
  protection_tier: number
  locked_r: number
  peak_r: number
  trough_r: number
  suggested_stop?: number | null
}

export interface ManagementSignal {
  position_id: string
  action: ManagementAction
  urgency: 'normal' | 'elevated' | 'immediate'
  headline: string
  reasons: string[]
  cautions: string[]
  current_price: number
  unrealised_pips: number
  r_multiple: number
  max_favourable_r: number
  bars_held: number
  distance_to_stop_pips: number
  distance_to_target_pips: number
  suggested_stop?: number | null
  suggested_stop_reason?: string | null
  reduce_fraction?: number | null
  invalidation: string
  thesis_intact: boolean
}

export interface AnalysisResult {
  id: string
  symbol: string
  timeframe: string
  generated_at: number
  price: number
  bar_time: number
  indicators: IndicatorSnapshot
  structure: MarketStructure
  patterns: PatternHit[]
  candlestick_patterns: PatternHit[]
  mtf: MTFReport
  strategies: StrategyReport
  drawings: DrawingAnalytics[]
  confidence: ConfidenceBreakdown
  verdict: AIVerdict
  plan: TradePlan
  derivatives?: DerivativeSuggestion | null
  overlays: OverlayObject[]
  narrative: string
  evidence: Record<string, unknown>
  warnings: string[]
  management?: ManagementSignal | null
  protection?: ProtectionPlan | null
  account_risk?: AccountRisk | null
}

// ── journal & backtest ──────────────────────────────────────

export interface JournalEntry {
  id: string
  symbol: string
  timeframe: string
  decision: string
  confidence: number
  probability: number
  entry?: number | null
  stop_loss?: number | null
  take_profit?: number | null
  risk_reward?: number | null
  summary: string
  reasons: Record<string, unknown>
  tags: string[]
  notes?: string | null
  outcome: string
  exit_price?: number | null
  pnl?: number | null
  r_multiple?: number | null
  created_at: number
  closed_at?: number | null
  snapshot?: string | null
}

export interface JournalStats {
  total: number
  open: number
  wins: number
  losses: number
  breakeven: number
  win_rate: number
  avg_r: number
  total_r: number
  profit_factor: number
  expectancy: number
  best_r: number
  worst_r: number
  by_symbol: Record<string, { total: number; wins: number; losses: number; total_r: number }>
  by_decision: Record<string, { total: number; wins: number; losses: number }>
  accuracy_by_confidence: Record<string, { total: number; wins: number; win_rate: number }>
}

export interface BacktestMetrics {
  trades: number
  wins: number
  losses: number
  win_rate: number
  profit_factor: number
  max_drawdown: number
  max_drawdown_pct: number
  average_r: number
  total_r: number
  sharpe_ratio: number
  sortino_ratio: number
  expectancy: number
  largest_win_r: number
  largest_loss_r: number
  longest_win_streak: number
  longest_loss_streak: number
  ending_balance: number
  return_pct: number
  exposure_pct: number
}

export interface BacktestTrade {
  index: number
  time: number
  direction: string
  entry: number
  stop_loss: number
  take_profit: number
  exit_time?: number | null
  exit_price?: number | null
  outcome: string
  r_multiple: number
  pnl: number
  bars_held: number
  confidence: number
  reason: string
}

export interface BacktestResult {
  symbol: string
  timeframe: string
  strategy: string
  bars_tested: number
  from_time: number
  to_time: number
  metrics: BacktestMetrics
  trades: BacktestTrade[]
  equity_curve: { time: number; equity: number }[]
  notes: string[]
}

// ── websocket ───────────────────────────────────────────────

export type StreamMessage =
  | { type: 'hello'; symbols: string[]; timeframes: string[]; auto_analysis_interval: number }
  | { type: 'subscribed'; symbol: string; timeframe: string; quote: Quote | null }
  | { type: 'quote'; symbol: string; data: Quote }
  | { type: 'candle'; symbol: string; timeframe: string; candle: Candle }
  | { type: 'bar_closed'; symbol: string; timeframe: string; candle: Candle }
  | { type: 'analysis'; trigger: string; data: AnalysisResult }
  | { type: 'analysis_pending'; symbol: string; timeframe: string; trigger: string }
  | { type: 'analysis_error'; symbol: string; timeframe: string; message: string }
  | { type: 'auto_analysis'; enabled: boolean }
  | { type: 'include_ai'; enabled: boolean }
  | { type: 'position_tick'; data: LiveTick }
  | { type: 'paper_state'; data: { account: PaperAccount; open_trade: PaperTrade | null } }
  | { type: 'paper_fill'; data: PaperTrade; reason: string }
  | { type: 'exit_alert'; data: LiveTick }
  | { type: 'pong'; server_time: number }
  | { type: 'error'; message: string }

// ── settings ────────────────────────────────────────────────

export interface RuntimeSettings {
  anthropic_api_key: string | null
  anthropic_api_key_set: boolean
  claude_model: string
  claude_effort: string
  ai_auto_analysis_interval: number
  market_provider: string
  hybrid_crypto_live: boolean
  twelvedata_api_key: string | null
  twelvedata_api_key_set: boolean
  zerodha_api_key: string | null
  zerodha_api_secret: string | null
  zerodha_api_secret_set: boolean
  zerodha_access_token: string | null
  zerodha_access_token_set: boolean
  zerodha_token_issued_at: number | null
  account_balance: number
  risk_per_trade_pct: number
  account_currency: string
}

export interface ProviderStatus {
  id: string
  label: string
  live: boolean
  ready: boolean
  active: boolean
  needs: string[]
  covers: string
}

export interface InstrumentStatus {
  symbol: string
  display: string
  asset_class: string
  venue: string
  exchange: string | null
  broker_symbol: string | null
  lot_size: number
  tick_size: number
  tick_value: number
  tradable_in_india: boolean
  note: string
  provider: string
  live: boolean
}

export interface SettingsPayload {
  settings: RuntimeSettings
  analyst: { enabled: boolean; model: string; effort: string; reason: string | null }
  providers: ProviderStatus[]
  instruments: InstrumentStatus[]
}

// ── live trading (Angel One) ─────────────────────────────────

export interface AngelStatus {
  configured: boolean
  connected: boolean
  session_age_seconds: number | null
  last_error: string | null
}

export interface TradingModeStatus {
  mode: 'PAPER' | 'LIVE'
  kill_switch_active: boolean
  live_confirmed_at: number | null
}

export interface InstrumentCapability {
  supported: boolean
  reason?: string | null
  exchange?: string | null
  trading_symbol?: string | null
  lot_size?: number | null
  tick_size?: number | null
}

export type ExecutionStatus =
  | 'RECOMMENDED' | 'PENDING_CONFIRMATION' | 'VALIDATING' | 'SUBMITTING'
  | 'SUBMITTED' | 'OPEN' | 'PARTIALLY_FILLED' | 'FILLED' | 'CANCEL_PENDING'
  | 'CANCELLED' | 'REJECTED' | 'CLOSING' | 'CLOSED' | 'UNKNOWN' | 'ERROR'

export interface ExecutionRecord {
  id: string
  recommendation_id: string
  broker: string
  symbol: string
  side: string
  exchange: string
  trading_symbol: string
  instrument_token: string
  requested_quantity: number
  filled_quantity: number
  requested_price: number | null
  average_fill_price: number | null
  stop_price: number | null
  target_price: number | null
  risk_amount: number | null
  status: ExecutionStatus
  broker_order_id: string | null
  error_code: string | null
  error_message: string | null
  created_at: number
  updated_at: number
}

// ── screener (best trades right now) ─────────────────────────

export interface ScreenerRow {
  symbol: string
  display: string
  timeframe: string
  decision: Decision
  confidence: number
  entry: number | null
  stop_loss: number | null
  take_profit: number | null
  risk_reward: number | null
  summary: string
  error?: string | null
  broker?: string | null
  broker_symbol?: string | null
}
