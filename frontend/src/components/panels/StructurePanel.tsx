import { fmtPrice } from '../../lib/format'
import type { Zone } from '../../lib/types'
import { useActiveInstrument, useStore } from '../../store/useStore'
import { Badge, EmptyState } from '../ui'

export default function StructurePanel() {
  const analysis = useStore((s) => s.analysis)
  const instrument = useActiveInstrument()
  const digits = instrument?.digits ?? 5

  if (!analysis) return <EmptyState title="No structural read yet" />

  const { structure, patterns, candlestick_patterns: candles, indicators } = analysis

  return (
    <div className="h-full scroll-y px-3 py-3 space-y-4">
      {/* Headline structure */}
      <section className="flex flex-wrap items-center gap-1.5">
        <Badge tone={structure.trend === 'bullish' ? 'bull'
          : structure.trend === 'bearish' ? 'bear' : 'neutral'}>
          {structure.structure_label}
        </Badge>
        <Badge tone={structure.premium_discount === 'discount' ? 'bull'
          : structure.premium_discount === 'premium' ? 'bear' : 'neutral'}>
          {structure.premium_discount}
        </Badge>
        <Badge tone={indicators.trend_strength === 'strong' ? 'accent' : 'neutral'}>
          ADX {indicators.adx?.toFixed(0) ?? '—'} · {indicators.trend_strength}
        </Badge>
        <Badge tone={indicators.volatility_regime === 'compressed' ? 'warn' : 'neutral'}>
          vol {indicators.volatility_regime}
        </Badge>
      </section>

      {/* Recent structural events */}
      <section>
        <h3 className="panel-title mb-1.5">Recent events</h3>
        <div className="space-y-1">
          {structure.events.slice(-6).reverse().map((event, index) => (
            <div key={`${event.time}-${index}`}
                 className="flex items-baseline gap-2 text-2xs">
              <span className={`w-16 shrink-0 font-semibold ${
                event.direction === 'bullish' ? 'text-bull' : 'text-bear'}`}>
                {event.kind === 'liquidity_sweep' ? 'SWEEP' : event.kind}
              </span>
              <span className="tnum text-muted">{fmtPrice(event.price, digits)}</span>
              <span className="text-faint">{event.bars_ago} bars ago</span>
            </div>
          ))}
          {structure.events.length === 0 && (
            <p className="text-2xs text-faint">No structural breaks in this window.</p>
          )}
        </div>
      </section>

      {/* Swing labels */}
      {structure.swings.length > 0 && (
        <section>
          <h3 className="panel-title mb-1.5">Swing sequence</h3>
          <div className="flex flex-wrap gap-1">
            {structure.swings.slice(-8).map((swing, index) => (
              <span
                key={`${swing.time}-${index}`}
                title={fmtPrice(swing.price, digits)}
                className={`rounded px-1.5 py-0.5 text-2xs font-semibold tnum border
                  ${swing.label?.startsWith('H')
                    ? 'border-bull/30 bg-bull/10 text-bull'
                    : swing.label?.startsWith('L')
                      ? 'border-bear/30 bg-bear/10 text-bear'
                      : 'border-hairline bg-tint/[0.03] text-faint'}`}
              >
                {swing.label ?? (swing.kind === 'swing_high' ? 'H' : 'L')}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* Smart money zones */}
      <ZoneList title="Order blocks" zones={structure.order_blocks} digits={digits} />
      <ZoneList title="Fair value gaps" zones={structure.fair_value_gaps} digits={digits} />
      <ZoneList title="Breaker blocks" zones={structure.breaker_blocks} digits={digits} />
      <ZoneList title="Supply" zones={structure.supply_zones} digits={digits} />
      <ZoneList title="Demand" zones={structure.demand_zones} digits={digits} />

      {/* Liquidity */}
      {structure.liquidity_pools.length > 0 && (
        <section>
          <h3 className="panel-title mb-1.5">Liquidity</h3>
          <div className="space-y-1">
            {structure.liquidity_pools.slice(0, 4).map((pool, index) => (
              <div key={index} className="flex items-baseline justify-between gap-2 text-2xs">
                <span className="text-muted tnum">
                  {fmtPrice((pool.top + pool.bottom) / 2, digits)}
                </span>
                <span className="text-faint truncate flex-1 text-right">{pool.note}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Levels */}
      <section className="grid grid-cols-2 gap-3">
        <div>
          <h3 className="panel-title mb-1.5 text-bull/70">Support</h3>
          {structure.support_levels.slice(0, 4).map((level) => (
            <div key={level} className="text-2xs tnum text-muted">{fmtPrice(level, digits)}</div>
          ))}
          {structure.support_levels.length === 0 && (
            <p className="text-2xs text-faint">none nearby</p>
          )}
        </div>
        <div>
          <h3 className="panel-title mb-1.5 text-bear/70">Resistance</h3>
          {structure.resistance_levels.slice(0, 4).map((level) => (
            <div key={level} className="text-2xs tnum text-muted">{fmtPrice(level, digits)}</div>
          ))}
          {structure.resistance_levels.length === 0 && (
            <p className="text-2xs text-faint">none nearby</p>
          )}
        </div>
      </section>

      {/* Patterns */}
      <section>
        <h3 className="panel-title mb-1.5">Patterns</h3>
        <div className="space-y-1.5">
          {patterns.map((pattern, index) => (
            <div key={index} className="flex items-baseline justify-between gap-2">
              <span className={`text-2xs font-medium ${
                pattern.direction === 'bullish' ? 'text-bull'
                  : pattern.direction === 'bearish' ? 'text-bear' : 'text-muted'}`}>
                {pattern.name}
              </span>
              <span className="text-2xs tnum text-faint">
                {Math.round(pattern.confidence * 100)}%
                {pattern.target ? ` → ${fmtPrice(pattern.target, digits)}` : ''}
              </span>
            </div>
          ))}
          {candles.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {candles.slice(0, 6).map((candle, index) => (
                <Badge
                  key={index}
                  tone={candle.direction === 'bullish' ? 'bull'
                    : candle.direction === 'bearish' ? 'bear' : 'neutral'}
                >
                  {candle.name}
                </Badge>
              ))}
            </div>
          )}
          {patterns.length === 0 && candles.length === 0 && (
            <p className="text-2xs text-faint">Nothing formed right now.</p>
          )}
        </div>
      </section>

      {structure.notes.length > 0 && (
        <section className="space-y-1">
          {structure.notes.map((note) => (
            <p key={note} className="text-2xs text-faint leading-relaxed">· {note}</p>
          ))}
        </section>
      )}
    </div>
  )
}

function ZoneList({ title, zones, digits }: { title: string; zones: Zone[]; digits: number }) {
  if (zones.length === 0) return null
  return (
    <section>
      <h3 className="panel-title mb-1.5">{title}</h3>
      <div className="space-y-1">
        {zones.slice(0, 3).map((zone, index) => (
          <div key={index} className="flex items-baseline justify-between gap-2 text-2xs">
            <span className={zone.direction === 'bullish' ? 'text-bull' : 'text-bear'}>
              {fmtPrice(zone.bottom, digits)} – {fmtPrice(zone.top, digits)}
            </span>
            <span className="text-faint tnum">
              {zone.distance_pct.toFixed(2)}% away
              {zone.tested > 0 ? ` · ${zone.tested}×` : ''}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
