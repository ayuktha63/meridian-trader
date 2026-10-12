import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import ExitAlert from './components/ExitAlert'
import PaperFillToast from './components/PaperFillToast'
import SettingsModal from './components/SettingsModal'
import ChartCanvas from './components/chart/ChartCanvas'
import DrawingLayer from './components/chart/DrawingLayer'
import AIOverlay from './components/chart/AIOverlay'
import Toolbar from './components/chart/Toolbar'
import StatusBar from './components/layout/StatusBar'
import TopBar from './components/layout/TopBar'
import AccountRiskBar from './components/panels/AccountRiskBar'
import AnalysisPanel from './components/panels/AnalysisPanel'
import BacktestPanel from './components/panels/BacktestPanel'
import DerivativesPanel from './components/panels/DerivativesPanel'
import DrawingsPanel from './components/panels/DrawingsPanel'
import JournalPanel from './components/panels/JournalPanel'
import ManagementCard from './components/panels/ManagementCard'
import MTFPanel from './components/panels/MTFPanel'
import PositionPanel from './components/panels/PositionPanel'
import ProtectionCard from './components/panels/ProtectionCard'
import StrategiesPanel from './components/panels/StrategiesPanel'
import StructurePanel from './components/panels/StructurePanel'
import TradePanel from './components/panels/TradePanel'
import { api } from './lib/api'
import { fmtPrice } from './lib/format'
import type { Candle, Position as PositionType } from './lib/types'
import { useActiveInstrument, useStore } from './store/useStore'

const PANELS = [
  { id: 'analysis', label: 'Analysis',
    hint: 'Confidence breakdown — how each component voted' },
  { id: 'position', label: 'Trade',
    hint: 'Open and track a position, with lot sizing' },
  { id: 'strategies', label: 'Plays',
    hint: 'Five named strategies scored side by side, with every condition' },
  { id: 'derivatives', label: 'F&O',
    hint: 'Turn the call into a futures or options contract' },
  { id: 'structure', label: 'Structure',
    hint: 'Swings, BOS/CHoCH, order blocks, FVGs, liquidity' },
  { id: 'drawings', label: 'Levels',
    hint: 'Analytics on the lines and zones you drew' },
  { id: 'journal', label: 'Journal',
    hint: 'Logged calls and accuracy by confidence band' },
  { id: 'backtest', label: 'Backtest',
    hint: 'Historical performance of the engine on this symbol' },
  // 'live-positions' and 'live-history' are deliberately not in this list —
  // see TopBar.tsx: each is a self-contained button + full-size popup now,
  // not a tab in a strip that already scrolls at 8 in a 360px rail.
] as const

export default function App() {
  const bootstrap = useStore((s) => s.bootstrap)
  const bootError = useStore((s) => s.bootError)
  const candles = useStore((s) => s.candles)
  const loading = useStore((s) => s.loadingCandles)
  const analysis = useStore((s) => s.analysis)
  const showOverlays = useStore((s) => s.showOverlays)
  const activePanel = useStore((s) => s.activePanel)
  const setActivePanel = useStore((s) => s.setActivePanel)
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5

  const management = analysis?.management ?? null
  const protection = analysis?.protection ?? null
  const accountRisk = analysis?.account_risk ?? null
  const [openPosition, setOpenPosition] = useState<PositionType | null>(null)
  const [positionVersion, setPositionVersion] = useState(0)

  // The management card needs the position itself for entry/stop/target, and
  // the analysis only carries the signal.
  useEffect(() => {
    if (!management) {
      setOpenPosition(null)
      return
    }
    let cancelled = false
    api.positions(undefined, 'open')
      .then((list) => {
        if (!cancelled) {
          setOpenPosition(list.find((p) => p.id === management.position_id) ?? null)
        }
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [management, management?.position_id, positionVersion])

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [hovered, setHovered] = useState<Candle | null>(null)
  const [showMovingAverages, setShowMovingAverages] = useState(true)
  const [showVolume, setShowVolume] = useState(true)

  useEffect(() => { void bootstrap() }, [bootstrap])

  if (bootError) {
    return (
      <div className="h-full grid place-items-center p-8">
        <div className="glass rounded-xl p-6 max-w-md text-center space-y-3">
          <h1 className="text-sm font-semibold text-bear">Cannot reach the analysis engine</h1>
          <p className="text-xs text-muted leading-relaxed">{bootError}</p>
          <p className="text-2xs text-faint leading-relaxed">
            Start the backend with <code className="text-ink">make backend</code>, then reload.
            It should answer on <code className="text-ink">http://localhost:8000/api/health</code>.
          </p>
          <button type="button" className="btn btn-primary"
                  onClick={() => window.location.reload()}
                  title="Reload the page and try to reach the backend again">
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col gap-2 p-2">
      <TopBar onOpenSettings={() => setSettingsOpen(true)} />

      <main className="flex-1 min-h-0 flex gap-2">
        <Toolbar />

        {/* Chart */}
        <section className="glass rounded-xl flex-1 min-w-0 relative overflow-hidden">
          {/* Read-out for the hovered bar. Kept top-left: the top-right is the
              price scale, and chips there collide with its labels. */}
          <div className="absolute left-3 top-2.5 z-10 flex items-center gap-2
                          text-[10px] tnum">
            <span className="text-muted font-medium pointer-events-none">
              {instrument?.display ?? ''}
            </span>
            <button
              type="button"
              onClick={() => setShowMovingAverages((prev) => !prev)}
              title="Show or hide the 20 / 50 / 200 EMAs"
              aria-pressed={showMovingAverages}
              className={`chip !py-0.5 ${showMovingAverages ? 'chip-active' : ''}`}
            >
              EMA
            </button>
            <button
              type="button"
              onClick={() => setShowVolume((prev) => !prev)}
              title="Show or hide the volume histogram"
              aria-pressed={showVolume}
              className={`chip !py-0.5 ${showVolume ? 'chip-active' : ''}`}
            >
              VOL
            </button>
            {hovered && (
              <span className="flex gap-2 text-faint">
                <span>O <span className="text-muted">{fmtPrice(hovered.open, digits)}</span></span>
                <span>H <span className="text-muted">{fmtPrice(hovered.high, digits)}</span></span>
                <span>L <span className="text-muted">{fmtPrice(hovered.low, digits)}</span></span>
                <span>C <span className={hovered.close >= hovered.open ? 'text-bull' : 'text-bear'}>
                  {fmtPrice(hovered.close, digits)}
                </span></span>
              </span>
            )}
          </div>

          {loading && candles.length === 0 ? (
            <div className="absolute inset-0 grid place-items-center">
              <div className="shimmer h-full w-full absolute inset-0 opacity-40" />
              <span className="relative text-2xs text-faint">Loading price history…</span>
            </div>
          ) : (
            <ChartCanvas
              candles={candles}
              digits={digits}
              showMovingAverages={showMovingAverages}
              showVolume={showVolume}
              onCrosshair={setHovered}
            >
              {(transform, container) => (
                <>
                  {showOverlays && analysis && (
                    <AIOverlay
                      overlays={analysis.overlays}
                      transform={transform}
                      digits={digits}
                    />
                  )}
                  <DrawingLayer
                    transform={transform}
                    container={container}
                    digits={digits}
                  />
                </>
              )}
            </ChartCanvas>
          )}
        </section>

        {/* Right rail. The trade panel sizes to its content, so the tabbed
            panel below needs a floor or a long AI summary squeezes it to
            nothing on shorter screens. */}
        <aside className="w-[360px] shrink-0 flex flex-col gap-2 min-h-0 overflow-y-auto
                          overscroll-contain">
          {/* Once a position is open, managing it outranks looking for the
              next entry — so the management card takes the top slot. */}
          {/* Account limits sit above everything. When they are breached the
              answer to 'what should I trade' is 'nothing', and that has to be
              visible before the signal is. */}
          {accountRisk && (
            <div className="shrink-0">
              <AccountRiskBar risk={accountRisk} />
            </div>
          )}

          <div className="shrink-0 space-y-2">
            {management && openPosition ? (
              <>
                {/* Protection outranks the hold/sell call: what the trade can
                    still cost you is the more urgent number. */}
                {protection && (
                  <ProtectionCard
                    plan={protection}
                    position={openPosition}
                    onChanged={() => setPositionVersion((v) => v + 1)}
                  />
                )}
                <ManagementCard
                  signal={management}
                  position={openPosition}
                  onChanged={() => setPositionVersion((v) => v + 1)}
                />
              </>
            ) : (
              <TradePanel />
            )}
          </div>

          <div className="glass rounded-xl flex-1 min-h-[240px] flex flex-col overflow-hidden">
            {/* Eight tabs no longer divide evenly into 360px, so they scroll
                rather than truncate — a clipped tab label is a tab the trader
                cannot find. */}
            <nav className="flex gap-0.5 p-1 border-b border-hairline shrink-0
                            overflow-x-auto no-scrollbar">
              {PANELS.map((panel) => (
                <button
                  key={panel.id}
                  type="button"
                  onClick={() => setActivePanel(panel.id)}
                  title={panel.hint}
                  className={`relative shrink-0 rounded-md px-2 py-1 text-2xs font-medium
                              transition-colors whitespace-nowrap
                              ${activePanel === panel.id
                                ? 'text-ink' : 'text-faint hover:text-muted'}`}
                >
                  {activePanel === panel.id && (
                    <motion.span
                      layoutId="panel-active"
                      className="absolute inset-0 rounded-md bg-tint/[0.07]"
                      transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                    />
                  )}
                  <span className="relative">{panel.label}</span>
                </button>
              ))}
            </nav>

            <div className="flex-1 min-h-0">
              <AnimatePresence mode="wait">
                <motion.div
                  key={activePanel}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.14 }}
                  className="h-full"
                >
                  {activePanel === 'analysis' && <AnalysisPanel />}
                  {activePanel === 'position' && <PositionPanel />}
                  {activePanel === 'strategies' && <StrategiesPanel />}
                  {activePanel === 'derivatives' && <DerivativesPanel />}
                  {activePanel === 'structure' && <StructurePanel />}
                  {activePanel === 'drawings' && <DrawingsPanel />}
                  {activePanel === 'journal' && <JournalPanel />}
                  {activePanel === 'backtest' && <BacktestPanel />}
                </motion.div>
              </AnimatePresence>
            </div>
          </div>

          <div className="glass rounded-xl shrink-0 max-h-[200px] overflow-hidden flex flex-col">
            <div className="px-3 py-1.5 border-b border-hairline shrink-0">
              <h2 className="panel-title">Multi-timeframe</h2>
            </div>
            <div className="flex-1 min-h-0">
              <MTFPanel />
            </div>
          </div>
        </aside>
      </main>

      <StatusBar />

      <ExitAlert />
      <PaperFillToast />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}
