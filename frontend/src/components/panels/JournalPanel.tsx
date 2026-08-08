import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { decisionTone, fmtNumber, fmtSigned, timeAgo } from '../../lib/format'
import type { JournalEntry, JournalStats } from '../../lib/types'
import { Badge, EmptyState, Spinner, Stat } from '../ui'

export default function JournalPanel() {
  const [entries, setEntries] = useState<JournalEntry[]>([])
  const [stats, setStats] = useState<JournalStats | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [list, summary] = await Promise.all([api.journal(), api.journalStats()])
      setEntries(list)
      setStats(summary)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const close = async (id: string, outcome: string) => {
    await api.setOutcome(id, { outcome })
    void refresh()
  }

  if (loading) {
    return <div className="h-full grid place-items-center"><Spinner className="h-5 w-5" /></div>
  }

  if (entries.length === 0) {
    return (
      <EmptyState
        title="No logged recommendations yet"
        hint="Use “Log to journal” on the trade panel. Marking outcomes is what turns the confidence score into something measurable."
      />
    )
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      {stats && (
        <div className="shrink-0 grid grid-cols-4 gap-2 px-3 py-2.5 border-b border-hairline">
          <Stat label="Logged" value={stats.total} sub={`${stats.open} open`} />
          <Stat
            label="Win rate"
            value={`${stats.win_rate.toFixed(0)}%`}
            tone={stats.win_rate >= 50 ? 'text-bull' : 'text-bear'}
            sub={`${stats.wins}W / ${stats.losses}L`}
          />
          <Stat
            label="Avg R"
            value={fmtSigned(stats.avg_r, 2)}
            tone={stats.avg_r >= 0 ? 'text-bull' : 'text-bear'}
            sub={`total ${fmtSigned(stats.total_r, 1)}R`}
          />
          <Stat
            label="Profit factor"
            value={stats.profit_factor >= 999 ? '∞' : fmtNumber(stats.profit_factor, 2)}
            sub={`exp ${fmtSigned(stats.expectancy, 2)}R`}
          />
        </div>
      )}

      {stats && Object.values(stats.accuracy_by_confidence).some((b) => b.total > 0) && (
        <div className="shrink-0 px-3 py-2 border-b border-hairline">
          <h3 className="panel-title mb-1.5">Accuracy by confidence bucket</h3>
          <div className="grid grid-cols-4 gap-1.5">
            {Object.entries(stats.accuracy_by_confidence).map(([bucket, data]) => (
              <div key={bucket} className="rounded-md border border-hairline bg-tint/[0.02] px-1.5 py-1">
                <div className="text-[10px] text-faint">{bucket}</div>
                <div className={`text-2xs tnum font-semibold ${
                  data.total === 0 ? 'text-faint'
                    : data.win_rate >= 50 ? 'text-bull' : 'text-bear'}`}>
                  {data.total === 0 ? '—' : `${data.win_rate.toFixed(0)}%`}
                </div>
                <div className="text-[10px] text-faint tnum">n={data.total}</div>
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[10px] text-faint leading-relaxed">
            If high-confidence buckets do not win more often than low ones, the score is
            not calibrated for your market and timeframe.
          </p>
        </div>
      )}

      <div className="flex-1 min-h-0 scroll-y px-3 py-2 space-y-1.5">
        {entries.map((entry) => {
          const tone = decisionTone(entry.decision)
          return (
            <div key={entry.id}
                 className="rounded-lg border border-hairline bg-tint/[0.02] px-2.5 py-2">
              <div className="flex items-center gap-2">
                <span className={`text-2xs font-bold ${tone.text}`}>{entry.decision}</span>
                <span className="text-2xs text-muted">{entry.symbol}</span>
                <span className="text-2xs text-faint">{entry.timeframe}</span>
                <span className="text-2xs tnum text-faint">
                  {entry.confidence.toFixed(0)}%
                </span>
                <span className="flex-1" />
                {entry.outcome === 'open' ? (
                  <div className="flex gap-1">
                    <button type="button" onClick={() => close(entry.id, 'win')}
                      title="Mark this call correct — feeds the accuracy-by-confidence table"
                            className="chip hover:border-bull/50 hover:text-bull">win</button>
                    <button type="button" onClick={() => close(entry.id, 'loss')}
                      title="Mark this call wrong — feeds the accuracy-by-confidence table"
                            className="chip hover:border-bear/50 hover:text-bear">loss</button>
                  </div>
                ) : (
                  <Badge tone={entry.outcome === 'win' ? 'bull'
                    : entry.outcome === 'loss' ? 'bear' : 'neutral'}>
                    {entry.outcome}
                    {entry.r_multiple !== null && entry.r_multiple !== undefined
                      ? ` ${fmtSigned(entry.r_multiple, 2)}R` : ''}
                  </Badge>
                )}
              </div>
              <p className="mt-1 text-[10px] text-muted leading-relaxed line-clamp-2">
                {entry.summary}
              </p>
              <div className="mt-1 flex items-center gap-2 text-[10px] text-faint tnum">
                <span>{timeAgo(entry.created_at)}</span>
                {entry.risk_reward && <span>{entry.risk_reward.toFixed(2)}R:R</span>}
                {entry.tags.slice(3, 4).map((tag) => <span key={tag}>{tag}</span>)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
