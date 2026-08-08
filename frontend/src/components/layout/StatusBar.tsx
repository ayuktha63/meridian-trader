import { timeAgo } from '../../lib/format'
import { useStore } from '../../store/useStore'

export default function StatusBar() {
  const clock = useStore((s) => s.clock)
  const connection = useStore((s) => s.connection)
  const analysis = useStore((s) => s.analysis)
  const trigger = useStore((s) => s.lastAnalysisTrigger)
  const candles = useStore((s) => s.candles)
  const drawings = useStore((s) => s.drawings)
  const symbols = useStore((s) => s.symbols)
  const symbol = useStore((s) => s.symbol)

  const instrument = symbols.find((s) => s.symbol === symbol)

  return (
    <footer className="glass rounded-xl flex items-center gap-4 px-3 py-1.5 shrink-0
                       text-[10px] text-faint">
      <span className="tnum">{clock?.utc_time ?? '—'}</span>

      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${
          clock?.liquidity_regime === 'closed' ? 'bg-faint'
            : clock?.liquidity_regime === 'overlap' ? 'bg-bull' : 'bg-accent'}`} />
        {clock?.active_session ?? 'session'} · {clock?.liquidity_regime ?? '—'} liquidity
      </span>

      <span>{instrument?.live ? 'live feed' : 'simulated feed'}</span>
      <span className="tnum">{candles.length} bars</span>
      <span className="tnum">{drawings.length} drawings</span>

      {analysis && (
        <span className="tnum">
          analysed {timeAgo(analysis.generated_at)}
          {trigger ? ` · ${trigger.replace('_', ' ')}` : ''}
        </span>
      )}

      <span className="flex-1" />

      <span className={connection === 'open' ? 'text-bull/70' : 'text-warn'}>
        stream {connection}
      </span>
      <span className="text-faint/60">
        Analysis only · no orders are placed · not financial advice
      </span>
    </footer>
  )
}
