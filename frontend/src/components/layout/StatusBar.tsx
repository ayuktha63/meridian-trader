import { useEffect, useState } from 'react'
import { timeAgo } from '../../lib/format'
import { STALE_QUOTE_SECONDS } from '../../lib/types'
import { useStore } from '../../store/useStore'

/** "5h 10m" from minutes. */
function span(minutes?: number | null): string {
  if (minutes == null) return '—'
  const hours = Math.floor(minutes / 60)
  return hours > 0 ? `${hours}h ${minutes % 60}m` : `${minutes}m`
}

export default function StatusBar() {
  const clock = useStore((s) => s.clock)
  const connection = useStore((s) => s.connection)
  const analysis = useStore((s) => s.analysis)
  const trigger = useStore((s) => s.lastAnalysisTrigger)
  const candles = useStore((s) => s.candles)
  const drawings = useStore((s) => s.drawings)
  const symbols = useStore((s) => s.symbols)
  const symbol = useStore((s) => s.symbol)
  const quote = useStore((s) => s.quotes[s.symbol] ?? s.quote)
  const tradingMode = useStore((s) => s.tradingMode)
  const angel = useStore((s) => s.angelStatus)

  // A quote does not announce that it has stopped arriving: its age has to be
  // watched against a clock that keeps moving.
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 5000)
    return () => window.clearInterval(timer)
  }, [])

  const instrument = symbols.find((s) => s.symbol === symbol)
  const nse = clock?.sessions.find((s) => s.name === 'NSE')
  const quoteAge = quote ? Math.max(0, now - quote.updated_at) : null
  const feed = !instrument?.live ? 'no feed'
    : quoteAge == null ? 'no quote yet'
      : quoteAge > STALE_QUOTE_SECONDS ? `quote ${timeAgo(quote!.updated_at)}`
        : 'live feed'
  const feedStale = !!instrument?.live && (quoteAge == null || quoteAge > STALE_QUOTE_SECONDS)
  const live = tradingMode?.mode === 'LIVE'
  const retry = angel && !angel.connected ? angel.login_retry_in_seconds : null

  return (
    <footer className="glass rounded-xl flex items-center gap-4 px-3 py-1.5 shrink-0
                       text-[10px] text-faint">
      <span className="tnum">{clock?.ist_time ?? clock?.utc_time ?? '—'}</span>

      <span className="flex items-center gap-1.5" title={clock?.basis}>
        <span className={`h-1.5 w-1.5 rounded-full ${nse?.open ? 'bg-accent' : 'bg-faint'}`} />
        {nse == null ? 'NSE —'
          : nse.open ? `NSE open · closes in ${span(nse.closes_in_minutes)}`
            : `NSE closed · opens in ${span(nse.opens_in_minutes)}`}
      </span>

      <span className={feedStale ? 'text-warn' : undefined}
            title={feedStale
              ? 'No quote has arrived recently: the market is shut or the feed has stopped. '
                + 'The price on screen is the last one received.'
              : undefined}>
        {feed}
      </span>
      {retry != null && (
        <span className="text-warn"
              title="The last login to Angel One failed. It is retried automatically, with a growing wait; Connect tries now.">
          Angel One login retry in {Math.ceil(retry)}s
        </span>
      )}
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
      <span className={live ? 'text-bear font-semibold' : 'text-faint/60'}>
        {tradingMode == null ? 'Mode unknown'
          : live ? 'LIVE · real orders can be sent to Angel One'
            : 'PAPER · fills are simulated, no order reaches a broker'}
        {' · not financial advice'}
      </span>
    </footer>
  )
}
