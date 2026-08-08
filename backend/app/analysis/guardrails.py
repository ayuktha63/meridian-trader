"""Account-level risk limits.

The protection ladder keeps one trade from becoming a disaster. This keeps a
*sequence* of them from becoming one, which is how trading accounts actually
die — not on a single bad trade, but on the six that follow it while the trader
is trying to win the money back.

Four limits, all of them boring on purpose:

* **Daily loss** — once the day is down by more than the limit, stop. The
  evidence on revenge trading is not ambiguous; the losses after a bad run are
  worse than the bad run.
* **Consecutive losses** — three in a row usually means the market regime has
  changed and the setup you are trading no longer fits it.
* **Open risk** — the sum of what every open position would cost if they all
  stopped out together. In a correlated basket, they do.
* **Trades per day** — overtrading is the most reliable predictor of a losing
  month in every retail dataset that has been published.

A HALT here does not lock anything — the platform cannot and should not stop
you from trading. It refuses to *help*: the trade panel will not pre-fill a
new position, and it tells you why in plain language.
"""
from __future__ import annotations

import time
from typing import List, Optional

from ..instruments import Instrument, get_instrument
from ..schemas.positions import Position, PositionSide
from ..schemas.risk import AccountRisk, Guardrail, GuardrailStatus

#: Multiples of the per-trade risk. A 1% per-trade risk with these defaults
#: means: stop for the day at -3%, warn at -2%, cap total open risk at 3%.
DAILY_LOSS_LIMIT_R = 3.0
DAILY_LOSS_WARN_R = 2.0
MAX_OPEN_RISK_R = 3.0
MAX_CONSECUTIVE_LOSSES = 3
MAX_TRADES_PER_DAY = 6

#: Trading day boundary. Crypto never closes, so the cut has to be arbitrary;
#: 00:00 UTC is the convention most exchanges report against.
DAY_SECONDS = 86_400


def _same_day(timestamp: float, now: float) -> bool:
    return int(timestamp // DAY_SECONDS) == int(now // DAY_SECONDS)


def _risk_amount(position: Position, instrument: Instrument) -> float:
    """Cash this position can still lose, measured from the entry.

    Deliberately *not* measured from the current price. Unrealised profit is
    not capital — a position 200 pips onside with its stop at breakeven has
    zero account risk, even though it would give back 200 pips from the mark.
    Counting that giveback as risk would make a fully protected book look
    dangerously exposed and trip the circuit breaker for no reason.

    Clamped at zero because a stop beyond the entry cannot produce a loss at
    all.
    """
    long = position.side == PositionSide.LONG
    exposure = ((position.entry - position.stop_loss) if long
                else (position.stop_loss - position.entry))
    exposure = max(exposure, 0.0)
    return instrument.to_pips(exposure) * instrument.pip_value_per_lot * position.lots


def evaluate(open_positions: List[Position], closed_positions: List[Position],
             balance: float, risk_per_trade_pct: float, currency: str = "USD",
             prices: Optional[dict] = None,
             now: Optional[float] = None) -> AccountRisk:
    """The account's current risk posture.

    `prices` is accepted so callers can pass live marks, but the loss maths
    deliberately does not use them — see `_risk_amount`.
    """
    now = now or time.time()
    per_trade = balance * risk_per_trade_pct / 100.0

    report = AccountRisk(
        balance=round(balance, 2), currency=currency,
        risk_per_trade_pct=risk_per_trade_pct,
        open_positions=len(open_positions),
    )

    # ── today's realised result ───────────────────────────────
    today = [p for p in closed_positions
             if p.closed_at and _same_day(p.closed_at, now)]
    report.trades_today = len(today)
    report.realised_today_r = round(sum(p.realised_r or 0.0 for p in today), 2)
    report.realised_today_amount = round(report.realised_today_r * per_trade, 2)

    # Consecutive losses, counting back from the most recent close.
    ordered = sorted([p for p in closed_positions if p.closed_at],
                     key=lambda p: p.closed_at or 0.0, reverse=True)
    streak = 0
    for position in ordered:
        if (position.realised_r or 0.0) < 0:
            streak += 1
        else:
            break
    report.consecutive_losses = streak

    # ── open exposure ─────────────────────────────────────────
    open_amount = 0.0
    for position in open_positions:
        try:
            instrument = get_instrument(position.symbol)
        except KeyError:
            continue
        open_amount += _risk_amount(position, instrument)
    report.open_risk_amount = round(open_amount, 2)
    report.open_risk_r = round(open_amount / per_trade, 2) if per_trade > 0 else 0.0

    # ── the rails ─────────────────────────────────────────────
    rails: List[Guardrail] = []

    loss_r = -min(report.realised_today_r, 0.0)
    rails.append(Guardrail(
        key="daily_loss", label="Daily loss",
        status=(GuardrailStatus.HALT if loss_r >= DAILY_LOSS_LIMIT_R
                else GuardrailStatus.CAUTION if loss_r >= DAILY_LOSS_WARN_R
                else GuardrailStatus.CLEAR),
        value=round(loss_r, 2), limit=DAILY_LOSS_LIMIT_R, unit="R",
        detail=("Down {:.1f}R today against a {:.0f}R limit. The trades taken "
                "after a day like this are, on average, the worst ones you will "
                "take all month.".format(loss_r, DAILY_LOSS_LIMIT_R)
                if loss_r >= DAILY_LOSS_WARN_R
                else "{:.1f}R realised today.".format(report.realised_today_r)),
    ))

    rails.append(Guardrail(
        key="consecutive_losses", label="Losing streak",
        status=(GuardrailStatus.HALT if streak >= MAX_CONSECUTIVE_LOSSES
                else GuardrailStatus.CAUTION if streak == MAX_CONSECUTIVE_LOSSES - 1
                else GuardrailStatus.CLEAR),
        value=float(streak), limit=float(MAX_CONSECUTIVE_LOSSES), unit="count",
        detail=("{} losses in a row. That usually means the regime changed and "
                "the setup you are trading no longer fits it — re-read the "
                "higher timeframe before the next one.".format(streak)
                if streak >= MAX_CONSECUTIVE_LOSSES - 1
                else "No streak."),
    ))

    rails.append(Guardrail(
        key="open_risk", label="Open risk",
        status=(GuardrailStatus.HALT if report.open_risk_r > MAX_OPEN_RISK_R
                else GuardrailStatus.CAUTION if report.open_risk_r > MAX_OPEN_RISK_R * 0.7
                else GuardrailStatus.CLEAR),
        value=report.open_risk_r, limit=MAX_OPEN_RISK_R, unit="R",
        detail=("{:.1f}R exposed across {} position(s). Correlated positions "
                "stop out together, so treat this as one trade of that size."
                .format(report.open_risk_r, report.open_positions)
                if report.open_positions > 1
                else "{:.1f}R exposed.".format(report.open_risk_r)),
    ))

    rails.append(Guardrail(
        key="trade_count", label="Trades today",
        status=(GuardrailStatus.CAUTION if report.trades_today >= MAX_TRADES_PER_DAY
                else GuardrailStatus.CLEAR),
        value=float(report.trades_today), limit=float(MAX_TRADES_PER_DAY),
        unit="count",
        detail=("{} trades closed today. Frequency is the strongest predictor "
                "of a losing month in every retail dataset published — including "
                "SEBI's.".format(report.trades_today)
                if report.trades_today >= MAX_TRADES_PER_DAY
                else "{} closed today.".format(report.trades_today)),
    ))

    report.guardrails = rails

    halted = [r for r in rails if r.status is GuardrailStatus.HALT]
    cautions = [r for r in rails if r.status is GuardrailStatus.CAUTION]

    if halted:
        report.status = GuardrailStatus.HALT
        report.can_open_new = False
        report.headline = "Stop trading for today"
        report.reason = " ".join(r.detail for r in halted)
        report.notes.append(
            "Meridian will not pre-fill another trade while this is true. It "
            "cannot stop you — but the number of accounts that have been "
            "rescued by ignoring this message is zero.")
    elif cautions:
        report.status = GuardrailStatus.CAUTION
        report.headline = "Size down"
        report.reason = " ".join(r.detail for r in cautions)
        report.notes.append(
            "Half size until the next winner, if you take anything at all.")
    else:
        report.status = GuardrailStatus.CLEAR
        report.headline = "Within limits"
        report.reason = "{:.1f}R realised today, {:.1f}R open.".format(
            report.realised_today_r, report.open_risk_r)

    if balance <= 0:
        report.notes.append(
            "Account balance is not set, so every cash figure here is a "
            "placeholder. Settings → Risk.")

    return report
