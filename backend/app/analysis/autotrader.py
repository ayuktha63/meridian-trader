"""The auto-trader — decides when the demo account enters and exits.

It is deliberately *not* a new strategy. It takes the verdict the platform
already produces, applies the same risk rules the manual workflow applies, and
executes them without hesitating — which is the one thing a human reliably
cannot do.

Two clocks, same split as the rest of the platform:

* **Entries** run on the structural pass. A decision to open needs the full
  evidence set, so it happens when that evidence is refreshed.
* **Exits** run on the one-second tier, because stops and targets are price
  levels and waiting for a bar close to notice one is the whole problem.

Every entry has to clear five gates. They exist because the failure mode of an
automated system is not a bad trade, it is a hundred mediocre ones:

1. **Symbol allow-list** — if the account has been scoped to specific
   instruments (usually because a backtest showed edge on some and not
   others), everything outside that list is skipped, full stop.
2. **Confidence** — below the floor the engine is guessing, and paying costs to
   guess is a negative-expectancy business.
3. **Risk:reward** — under ~1.5 the win rate has to be implausibly high to
   survive the spread.
4. **Account guardrails** — the daily loss limit and losing-streak rules bind
   the auto-trader exactly as they bind you. A machine revenge-trades faster
   than a human.
5. **Cost sanity** — if the round-trip spread is a large fraction of the
   expected move, the setup is unprofitable before it starts. This is what
   stops the INR crypto books quietly draining the account.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import get_settings
from ..instruments import Instrument
from ..schemas.analysis import AnalysisResult, Decision
from ..schemas.paper import ExitReason, PaperAccount, PaperTrade
from ..schemas.risk import GuardrailStatus

log = logging.getLogger("meridian.autotrader")

#: Minimum reward-to-risk is `MIN_RISK_REWARD` in the environment (default 2.0).
#: Below it the spread and commission eat too much of the winner for the win
#: rate to carry the strategy. 2.0 was the floor with the best average
#: expectancy across a backtest grid of 879 qualifying runs (10 crypto pairs x
#: 2 timeframes x 2 strategies); 1.5 let marginal setups drag it negative. It
#: was once hard-coded down to 1.1 just to watch the open/close mechanism
#: fire, which has no backtested support — hence a setting, not an edit.
def min_risk_reward() -> float:
    return get_settings().min_risk_reward


#: Reject a setup when the round-trip cost exceeds this share of the distance
#: to target. At 25% the trade needs to be right by a third just to break even.
MAX_COST_SHARE = 0.25

#: Give a fresh trade this many seconds before a reversing signal can close it,
#: so one noisy re-analysis cannot flip the position immediately after entry.
MIN_HOLD_SECONDS = 120.0


class Intent:
    """What the auto-trader wants to do, and why."""

    def __init__(self, action: str, reason: str, **kwargs) -> None:
        self.action = action          # enter | exit | hold | wait
        self.reason = reason
        self.side: Optional[str] = kwargs.get("side")
        self.stop_loss: Optional[float] = kwargs.get("stop_loss")
        self.take_profit: Optional[float] = kwargs.get("take_profit")
        self.exit_reason: Optional[ExitReason] = kwargs.get("exit_reason")
        self.blocked: bool = kwargs.get("blocked", False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Intent {} {}>".format(self.action, self.reason)


def evaluate_entry(result: AnalysisResult, account: PaperAccount,
                   instrument: Instrument,
                   spread_pips: Optional[float] = None) -> Intent:
    """Should the demo account open a position on this analysis?"""
    if not account.enabled:
        return Intent("wait", "Auto-trading is off.")

    verdict, plan = result.verdict, result.plan
    if verdict.decision is Decision.WAIT:
        return Intent("wait", "Engine says WAIT — no directional edge.")

    # ── gate 0: symbol and timeframe allow-lists ──────────────
    if account.symbols and instrument.symbol not in account.symbols:
        return Intent("wait", "{} is not on the auto-trade symbol list."
                      .format(instrument.symbol))
    if account.timeframes and result.timeframe not in account.timeframes:
        return Intent("wait", "{} is not on the auto-trade timeframe list."
                      .format(result.timeframe))

    # ── gate 3: account guardrails ────────────────────────────
    risk = result.account_risk
    if risk is not None and risk.status is GuardrailStatus.HALT:
        return Intent("wait", "Blocked: {}".format(risk.reason or risk.headline),
                      blocked=True)

    # ── gate 1: confidence ────────────────────────────────────
    confidence = result.confidence.final_confidence
    if confidence < account.min_confidence:
        return Intent("wait", "Confidence {:.0f}% is below the {:.0f}% floor."
                      .format(confidence, account.min_confidence))

    if plan.entry is None or plan.stop_loss is None:
        return Intent("wait", "No stop level — nothing to size against.")

    long = verdict.decision is Decision.BUY
    entry, stop = plan.entry, plan.stop_loss
    target = plan.take_profit

    if (long and stop >= entry) or (not long and stop <= entry):
        return Intent("wait", "Plan stop is on the wrong side of entry.")

    # ── gate 2: risk:reward ───────────────────────────────────
    if target is None:
        return Intent("wait", "No target — cannot judge reward against risk.")
    reward, risk_distance = abs(target - entry), abs(entry - stop)
    rr = reward / risk_distance if risk_distance > 0 else 0.0
    if rr < min_risk_reward():
        return Intent("wait", "R:R {:.2f} is below the {:.1f} floor."
                      .format(rr, min_risk_reward()))

    # ── gate 4: is the spread eating the trade? ───────────────
    pips = spread_pips if spread_pips is not None else instrument.typical_spread_pips
    round_trip = instrument.from_pips(max(pips, 0.0) * 2.0)
    cost_share = round_trip / reward if reward > 0 else 1.0
    if cost_share > MAX_COST_SHARE:
        return Intent(
            "wait",
            "Round trip costs {:.0f}% of the move to target — the spread on "
            "{} is too wide for this setup.".format(cost_share * 100,
                                                    instrument.symbol))

    return Intent(
        "enter",
        "{} at {:.0f}% confidence, {:.2f}R:R, costs {:.0f}% of target.".format(
            verdict.decision.value, confidence, rr, cost_share * 100),
        side="LONG" if long else "SHORT",
        stop_loss=stop, take_profit=target,
    )


def evaluate_exit(trade: PaperTrade, result: AnalysisResult,
                  now: float) -> Intent:
    """Should an open paper trade be closed on structural grounds?

    Price-level exits (stop, target) are handled on the tick tier and never
    reach here — this is only for the thesis breaking.
    """
    risk = result.account_risk
    if risk is not None and risk.status is GuardrailStatus.HALT:
        return Intent("exit", "Account guardrail breached — flattening.",
                      exit_reason=ExitReason.GUARDRAIL)

    if now - trade.entry_at < MIN_HOLD_SECONDS:
        return Intent("hold", "Just entered — holding through the first minutes.")

    verdict = result.verdict
    long = trade.side == "LONG"
    opposed = (verdict.decision is Decision.SELL) if long else (verdict.decision is Decision.BUY)
    if opposed and result.confidence.final_confidence >= 55.0:
        return Intent(
            "exit",
            "Engine flipped to {} at {:.0f}% — the reason for the trade is "
            "gone.".format(verdict.decision.value,
                           result.confidence.final_confidence),
            exit_reason=ExitReason.SIGNAL)

    management = result.management
    if management is not None and management.action.value in ("EXIT", "STOPPED"):
        return Intent("exit", management.headline or "Management says exit.",
                      exit_reason=ExitReason.SIGNAL)

    return Intent("hold", "Thesis intact.")


def check_levels(trade: PaperTrade, bid: float, ask: float,
                 last: float) -> Optional[Intent]:
    """Has price traded through the stop or the target?

    Checked against the side of the book that would actually fill you: a long
    is stopped when the *bid* reaches the stop, because that is where you sell.
    Using the last price here would make stops look better than they are.
    """
    long = trade.side == "LONG"
    exit_side = bid if (long and bid > 0) else (ask if (not long and ask > 0) else last)

    if long:
        if exit_side <= trade.stop_loss:
            return Intent("exit", "Stop hit at {}".format(trade.stop_loss),
                          exit_reason=ExitReason.STOP)
        if trade.take_profit and exit_side >= trade.take_profit:
            return Intent("exit", "Target reached at {}".format(trade.take_profit),
                          exit_reason=ExitReason.TARGET)
    else:
        if exit_side >= trade.stop_loss:
            return Intent("exit", "Stop hit at {}".format(trade.stop_loss),
                          exit_reason=ExitReason.STOP)
        if trade.take_profit and exit_side <= trade.take_profit:
            return Intent("exit", "Target reached at {}".format(trade.take_profit),
                          exit_reason=ExitReason.TARGET)
    return None


def status_line(account: PaperAccount, trade: Optional[PaperTrade],
                intent: Optional[Intent]) -> str:
    """One sentence for the home screen."""
    if not account.enabled:
        return "Auto-trading off."
    if trade is not None:
        return "In a {} on {} — managing to stop {:.5g}.".format(
            trade.side.lower(), trade.symbol, trade.stop_loss)
    if intent is not None and intent.blocked:
        return intent.reason
    if intent is not None:
        return "Flat. {}".format(intent.reason)
    return "Flat, waiting for a setup."
