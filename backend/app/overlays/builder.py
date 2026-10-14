"""Analysis → chart overlay primitives.

The frontend renders these on a dedicated Konva layer above the candles. Every
object is expressed in chart space (time + price), never pixels, so overlays
stay locked to the data through zoom and pan.

Ordering matters: zones are emitted before lines so the renderer can paint
fills first and strokes on top without sorting.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..instruments import Instrument, get_timeframe
from ..schemas.analysis import (
    AIVerdict, Decision, MarketStructure, OverlayObject, PatternHit, TradePlan,
)
from ..schemas.strategies import StrategyReport

PALETTE: Dict[str, str] = {
    "entry": "#4C8DFF",
    "stop": "#FF4D6A",
    "target": "#2FD3A0",
    "profit_zone": "rgba(47, 211, 160, 0.13)",
    "risk_zone": "rgba(255, 77, 106, 0.13)",
    "order_block_bull": "rgba(76, 141, 255, 0.18)",
    "order_block_bear": "rgba(255, 122, 89, 0.18)",
    "fvg_bull": "rgba(47, 211, 160, 0.14)",
    "fvg_bear": "rgba(255, 77, 106, 0.12)",
    "supply": "rgba(255, 122, 89, 0.16)",
    "demand": "rgba(76, 141, 255, 0.16)",
    "liquidity": "#F5C453",
    "support": "#2FD3A0",
    "resistance": "#FF7A59",
    "projection": "#9B8CFF",
    "pattern": "#9B8CFF",
    "equilibrium": "#6B7A99",
}

#: A zone's band is a low-alpha fill, which is unreadable as text. Labels get
#: their own solid colour so the caption stays legible on top of the band.
LABEL_COLOR: Dict[str, str] = {
    "order_block_bull": "#7FAEFF",
    "order_block_bear": "#FFA98F",
    "fvg_bull": "#6FE0BE",
    "fvg_bear": "#FF8FA3",
    "supply": "#FFA98F",
    "demand": "#7FAEFF",
    "risk_zone": "#FF8FA3",
    "profit_zone": "#6FE0BE",
}


def _future_time(last_time: int, timeframe: str, bars: int = 14) -> int:
    return int(last_time + get_timeframe(timeframe).seconds * bars)


#: The model's own entry, stop and target are a prediction, not an order. They
#: are drawn dotted, thin and faded and labelled "AI", so they can never be
#: read as a level that is actually set at the broker (solid, on the chart's
#: broker layer).
PREDICTED_DASH = [2, 5]
PREDICTED_OPACITY = 0.6


def _level(idx: str, role: str, price: float, start: int, end: int, label: str,
           color: str, dash: Optional[List[int]] = None, width: int = 1,
           opacity: float = 1.0) -> OverlayObject:
    return OverlayObject(
        id=idx, kind="line", role=role, color=color, price=price,
        start_time=start, end_time=end, label=label, dash=dash, width=width,
        opacity=opacity,
        points=[{"time": float(start), "price": price},
                {"time": float(end), "price": price}],
    )


def _zone(idx: str, role: str, top: float, bottom: float, start: int, end: int,
          label: str, fill: str, palette_key: str,
          opacity: float = 1.0) -> OverlayObject:
    return OverlayObject(
        id=idx, kind="zone", role=role,
        color=LABEL_COLOR.get(palette_key, "#C9D2E4"), fill=fill,
        top=top, bottom=bottom, start_time=start, end_time=end,
        label=label, opacity=opacity,
        points=[{"time": float(start), "price": top},
                {"time": float(end), "price": bottom}],
    )


def build_overlays(verdict: AIVerdict, plan: TradePlan, structure: MarketStructure,
                   patterns: List[PatternHit], instrument: Instrument,
                   timeframe: str, last_time: int, price: float,
                   max_zones: int = 4) -> List[OverlayObject]:
    overlays: List[OverlayObject] = []
    d = instrument.digits
    horizon = _future_time(last_time, timeframe, 16)
    history_start = int(last_time - get_timeframe(timeframe).seconds * 120)
    n = 0

    def nid(prefix: str) -> str:
        nonlocal n
        n += 1
        return "{}-{}".format(prefix, n)

    # ── zones first (painted underneath) ─────────────────────

    for zone in structure.order_blocks[:max_zones]:
        overlays.append(_zone(
            nid("ob"), "order_block", zone.top, zone.bottom,
            zone.start_time, horizon,
            "{} OB".format(zone.direction[:4].upper()),
            PALETTE["order_block_bull" if zone.direction == "bullish" else "order_block_bear"],
            "order_block_bull" if zone.direction == "bullish" else "order_block_bear",
        ))

    for zone in structure.breaker_blocks[:2]:
        overlays.append(_zone(
            nid("bb"), "order_block", zone.top, zone.bottom,
            zone.start_time, horizon, "Breaker",
            PALETTE["order_block_bear" if zone.direction == "bearish" else "order_block_bull"],
            "order_block_bear" if zone.direction == "bearish" else "order_block_bull",
        ))

    for zone in structure.fair_value_gaps[:max_zones]:
        overlays.append(_zone(
            nid("fvg"), "fvg", zone.top, zone.bottom,
            zone.start_time, horizon,
            "FVG{}".format("" if not zone.mitigated else " (filled)"),
            PALETTE["fvg_bull" if zone.direction == "bullish" else "fvg_bear"],
            "fvg_bull" if zone.direction == "bullish" else "fvg_bear",
        ))

    for zone in structure.supply_zones[:2]:
        overlays.append(_zone(nid("supply"), "zone", zone.top, zone.bottom,
                              zone.start_time, horizon, "Supply", PALETTE["supply"], "supply"))
    for zone in structure.demand_zones[:2]:
        overlays.append(_zone(nid("demand"), "zone", zone.top, zone.bottom,
                              zone.start_time, horizon, "Demand", PALETTE["demand"], "demand"))

    # ── structural levels ────────────────────────────────────

    for pool in structure.liquidity_pools[:4]:
        overlays.append(_level(
            nid("liq"), "liquidity", round(pool.mid, d), history_start, horizon,
            "Liquidity {:.{d}f}".format(pool.mid, d=d),
            PALETTE["liquidity"], dash=[2, 4],
        ))

    for level in structure.support_levels[:3]:
        overlays.append(_level(
            nid("sup"), "support", level, history_start, horizon,
            "S {:.{d}f}".format(level, d=d), PALETTE["support"], dash=[6, 4],
        ))
    for level in structure.resistance_levels[:3]:
        overlays.append(_level(
            nid("res"), "resistance", level, history_start, horizon,
            "R {:.{d}f}".format(level, d=d), PALETTE["resistance"], dash=[6, 4],
        ))

    if structure.equilibrium is not None:
        overlays.append(_level(
            nid("eq"), "support", structure.equilibrium, history_start, horizon,
            "EQ {:.{d}f}".format(structure.equilibrium, d=d),
            PALETTE["equilibrium"], dash=[1, 5],
        ))

    # ── detected chart patterns ──────────────────────────────

    for hit in patterns[:2]:
        if len(hit.points) < 2:
            continue
        overlays.append(OverlayObject(
            id=nid("pattern"), kind="path", role="trendline",
            color=PALETTE["pattern"], width=2, dash=[4, 3],
            points=[{"time": float(p["time"]), "price": float(p["price"])}
                    for p in hit.points],
            label=hit.name,
        ))
        if hit.target is not None:
            overlays.append(_level(
                nid("ptgt"), "target", hit.target, hit.end_time, horizon,
                "{} target".format(hit.name), PALETTE["pattern"], dash=[2, 6],
            ))

    # ── the trade plan ───────────────────────────────────────

    if verdict.decision != Decision.WAIT and plan.entry and plan.stop_loss:
        entry, stop = plan.entry, plan.stop_loss
        target = plan.take_profit

        overlays.append(_zone(
            nid("risk"), "risk_zone", max(entry, stop), min(entry, stop),
            last_time, horizon,
            "Risk {:.1f} pips".format(plan.risk_pips or 0.0),
            PALETTE["risk_zone"], "risk_zone",
        ))
        if target is not None:
            overlays.append(_zone(
                nid("reward"), "profit_zone", max(entry, target), min(entry, target),
                last_time, horizon,
                "Reward {:.1f} pips ({:.2f}R)".format(
                    plan.reward_pips or 0.0, plan.risk_reward or 0.0),
                PALETTE["profit_zone"], "profit_zone",
            ))

        overlays.append(_level(
            nid("entry"), "entry", entry, last_time, horizon,
            "AI {} @ {:.{d}f}".format(verdict.decision.value, entry, d=d),
            PALETTE["entry"], dash=PREDICTED_DASH, opacity=PREDICTED_OPACITY,
        ))
        overlays.append(_level(
            nid("stop"), "stop", stop, last_time, horizon,
            "AI SL {:.{d}f}".format(stop, d=d), PALETTE["stop"],
            dash=PREDICTED_DASH, opacity=PREDICTED_OPACITY,
        ))
        if target is not None:
            overlays.append(_level(
                nid("tp"), "target", target, last_time, horizon,
                "AI TP1 {:.{d}f}".format(target, d=d), PALETTE["target"],
                dash=PREDICTED_DASH, opacity=PREDICTED_OPACITY,
            ))
        if plan.take_profit_2 is not None:
            overlays.append(_level(
                nid("tp2"), "target", plan.take_profit_2, last_time, horizon,
                "AI TP2 {:.{d}f}".format(plan.take_profit_2, d=d),
                PALETTE["target"], dash=PREDICTED_DASH, opacity=PREDICTED_OPACITY,
            ))

        # Expected path: entry → target, drawn as a projection arrow.
        if target is not None:
            overlays.append(OverlayObject(
                id=nid("path"), kind="arrow", role="projection",
                color=PALETTE["projection"], width=2,
                points=[
                    {"time": float(last_time), "price": entry},
                    {"time": float(_future_time(last_time, timeframe, 10)), "price": target},
                ],
                label="Expected move",
            ))
    else:
        # On WAIT, still project the levels that would trigger a call.
        watch = []
        if structure.resistance_levels:
            watch.append(("resistance", structure.resistance_levels[0]))
        if structure.support_levels:
            watch.append(("support", structure.support_levels[0]))
        for role, level in watch:
            overlays.append(_level(
                nid("watch"), role, level, last_time, horizon,
                "Watch {:.{d}f}".format(level, d=d),
                PALETTE[role], dash=[3, 3], width=2,
            ))

    # Levels Claude named that the engine did not already draw.
    drawn = {round(o.price, d) for o in overlays if o.price is not None}
    for level in verdict.key_levels[:4]:
        if round(level, d) in drawn:
            continue
        overlays.append(_level(
            nid("ai"), "resistance" if level > price else "support", level,
            last_time, horizon, "AI {:.{d}f}".format(level, d=d),
            PALETTE["projection"], dash=[1, 4],
        ))

    return overlays


# ── strategy scenario paths ───────────────────────────────────

#: Each strategy family gets its own hue so overlapping paths stay readable
#: without a legend lookup.
SCENARIO_COLOR: Dict[str, str] = {
    "trend": "#4C8DFF",
    "reversion": "#F5C453",
    "breakout": "#2FD3A0",
    "smc": "#9B8CFF",
    "momentum": "#FF7A59",
}


def build_scenario_overlays(report: StrategyReport,
                            max_paths: int = 3) -> List[OverlayObject]:
    """Draw each engaged strategy's scenario as a path into the future.

    These are the only overlays that extend past the last bar, and they are
    deliberately drawn as thin dashed lines rather than a single bold forecast
    curve: several of them disagree at once, and that disagreement is the
    honest picture. The invalidation leg is drawn too, so the path to the stop
    is as visible as the path to the target.
    """
    overlays: List[OverlayObject] = []
    engaged = [r for r in report.results if r.stance in ("long", "short")][:max_paths]

    for rank, strat in enumerate(engaged):
        colour = SCENARIO_COLOR.get(strat.family, PALETTE["projection"])
        if strat.projection:
            overlays.append(OverlayObject(
                id="scenario-{}".format(strat.key), kind="path", role="projection",
                color=colour,
                width=3 if rank == 0 else 2,
                opacity=1.0 if rank == 0 else 0.62,
                dash=None if rank == 0 else [5, 4],
                label="{} · {:.0f}%".format(strat.name, strat.conviction),
                points=[{"time": float(p.time), "price": p.price}
                        for p in strat.projection],
            ))
        if strat.invalidation_path and rank == 0:
            overlays.append(OverlayObject(
                id="scenario-inv-{}".format(strat.key), kind="path", role="risk_zone",
                color=PALETTE["stop"], width=1, opacity=0.55, dash=[2, 5],
                label="invalidation",
                points=[{"time": float(p.time), "price": p.price}
                        for p in strat.invalidation_path],
            ))

    return overlays
