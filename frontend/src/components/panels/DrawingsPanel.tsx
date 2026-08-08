import { fmtPips, fmtPrice } from '../../lib/format'
import type { DrawingAnalytics } from '../../lib/types'
import { useActiveInstrument, useStore } from '../../store/useStore'
import { Badge, EmptyState } from '../ui'

const VALIDITY_TONE = {
  respected: 'bull',
  untested: 'neutral',
  broken: 'warn',
  invalidated: 'bear',
} as const

export default function DrawingsPanel() {
  const analysis = useStore((s) => s.analysis)
  const drawings = useStore((s) => s.drawings)
  const selectedId = useStore((s) => s.selectedDrawingId)
  const selectDrawing = useStore((s) => s.selectDrawing)
  const removeDrawing = useStore((s) => s.removeDrawing)
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5

  const analytics = analysis?.drawings ?? []
  const byId = new Map<string, DrawingAnalytics>(
    analytics.map((item) => [item.drawing_id, item]),
  )

  if (drawings.length === 0) {
    return (
      <EmptyState
        title="Nothing drawn on this chart yet"
        hint="Pick a tool on the left and mark up the chart. Everything you draw is scored for touches, reactions and breaks, then sent to the analyst as evidence."
      />
    )
  }

  return (
    <div className="h-full scroll-y px-3 py-3 space-y-2">
      {drawings.map((drawing) => {
        const stats = byId.get(drawing.id)
        const selected = drawing.id === selectedId
        return (
          <button
            key={drawing.id}
            type="button"
            onClick={() => selectDrawing(selected ? null : drawing.id)}
              title="Select this drawing to see its touches, reactions and angle"
            className={`w-full text-left rounded-lg border px-2.5 py-2 transition-colors
              ${selected
                ? 'border-accent/50 bg-accent/10'
                : 'border-hairline bg-tint/[0.02] hover:bg-tint/[0.05]'}`}
          >
            <div className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 rounded-full shrink-0"
                style={{ background: drawing.style?.color ?? '#4C8DFF' }}
              />
              <span className="text-2xs font-medium text-ink capitalize truncate flex-1">
                {drawing.label ?? drawing.type.replace(/_/g, ' ')}
              </span>
              {stats && (
                <Badge tone={VALIDITY_TONE[stats.validity] ?? 'neutral'}>
                  {stats.validity}
                </Badge>
              )}
              <span
                role="button"
                tabIndex={0}
                onClick={(event) => {
                  event.stopPropagation()
                  void removeDrawing(drawing.id)
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.stopPropagation()
                    void removeDrawing(drawing.id)
                  }
                }}
                className="text-faint hover:text-bear text-2xs px-1 cursor-pointer"
                title="Delete"
              >
                ✕
              </span>
            </div>

            {stats ? (
              <>
                <div className="mt-1.5 grid grid-cols-4 gap-1.5 text-[10px]">
                  <Metric label="level" value={
                    stats.upper_value !== null && stats.upper_value !== undefined
                      ? `${fmtPrice(stats.lower_value, digits)}–${fmtPrice(stats.upper_value, digits)}`
                      : fmtPrice(stats.current_value, digits)} />
                  <Metric label="touches" value={String(stats.touches)} />
                  <Metric label="reacts" value={String(stats.reactions)} />
                  <Metric label="breaks" value={String(stats.breaks)}
                          tone={stats.breaks ? 'text-warn' : undefined} />
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <div className="flex-1 h-1 rounded-full bg-tint/[0.06] overflow-hidden">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${Math.min(100, stats.strength)}%` }}
                    />
                  </div>
                  <span className="text-[10px] tnum text-faint shrink-0">
                    {stats.strength.toFixed(0)}/100
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-2 text-[10px] text-faint tnum">
                  <span>{fmtPips(stats.distance_pips)} away</span>
                  <span>{stats.distance_atr.toFixed(2)} ATR</span>
                  {stats.approaching && <span className="text-warn">approaching</span>}
                  {stats.fake_breaks > 0 && <span>{stats.fake_breaks} fakeout</span>}
                  {stats.angle_degrees !== 0 && <span>{stats.angle_degrees.toFixed(0)}°</span>}
                </div>
                {selected && stats.notes.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {stats.notes.slice(0, 4).map((note, index) => (
                      <li key={index} className="text-[10px] text-muted leading-relaxed">
                        · {note}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="mt-1 text-[10px] text-faint">
                Analytics arrive with the next analysis.
              </p>
            )}
          </button>
        )
      })}
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-faint uppercase tracking-wide truncate">{label}</div>
      <div className={`tnum truncate ${tone ?? 'text-muted'}`}>{value}</div>
    </div>
  )
}
