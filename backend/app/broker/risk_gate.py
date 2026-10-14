"""PreTradeRiskGate — the last thing standing between a recommendation and a
real order.

Every check runs in a fixed order and the first failure wins; nothing here
ever tries to be clever about salvaging a marginal trade. The order matters
because each check is progressively more expensive (mode flag, then a
network call to the broker for funds) — fail cheap first.

This gate decides ALLOW or REJECT. It never places an order itself — that
stays in `AngelOneExecutionService`, so a bug in "should we trade" can never
also be a bug in "how do we submit it".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .. import costs
from ..config import get_settings
from ..instruments import Instrument, get_instrument
from ..runtime_settings import RuntimeSettings
from ..schemas.execution import RiskGateResult
from .angelone.auth import angel_auth
from .angelone.instruments import AngelInstrumentMatch, InstrumentUnsupported, angel_instruments
from .trading_mode import trading_mode

log = logging.getLogger("meridian.risk_gate")

#: See instruments.py — the CDS tick-size conversion is not verified against
#: a real value, so no instrument on this exchange may be live-executed
#: until that's confirmed. This is deliberately a hard-coded safety block,
#: not a computed check, precisely because it must not be silently "fixed"
#: by a future refactor that forgets why it's here.
UNTRUSTED_TICK_EXCHANGES = {"CDS"}

#: Orders here are sized in contract units — lots × the scrip master's lot
#: size — which is what Angel One takes for equity and for NFO. Commodity
#: orders are widely reported to be taken in *lots* instead, and that has not
#: been confirmed against the broker. Guessing wrong one way is an order the
#: exchange rejects; the other way it is a position a hundred times the size
#: that was risked. Blocked for the same reason, and in the same way, as CDS.
UNVERIFIED_QUANTITY_EXCHANGES = {"MCX"}


def blocked_for_good(symbol: str) -> Optional[str]:
    """Why this instrument can never be ordered live right now, from local
    facts alone (no network): its segment is one of the hard blocks. None if
    the gate must decide. The gate itself still refuses these — this only
    lets the unattended path stop asking, instead of recording the same
    refusal on every bar."""
    try:
        asset = get_instrument(symbol).asset_class
    except KeyError:
        return "Unknown instrument."
    exchange = DERIVATIVE_EXCHANGE.get(asset)
    if exchange in UNTRUSTED_TICK_EXCHANGES:
        return ("{} orders are blocked: Angel One's tick-size scale for this segment is "
                "unverified.".format(exchange))
    if exchange in UNVERIFIED_QUANTITY_EXCHANGES:
        return ("{} orders are blocked: whether Angel One takes the quantity in lots or in "
                "units is unverified.".format(exchange))
    return None


#: Where each derivative asset class trades (`angelone/instruments.DERIVATIVE_VENUE`).
DERIVATIVE_EXCHANGE = {"index": "NFO", "currency_fut": "CDS", "commodity": "MCX"}


@dataclass
class Recommendation:
    """The server-derived facts about a trade candidate — never frontend input."""
    id: str
    symbol: str
    side: str                      # BUY | SELL
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_amount: Optional[float]
    suggested_lots: Optional[float]
    confidence: float
    timeframe: str = "15m"


@dataclass
class GateContext:
    recommendation: Recommendation
    confirmed: bool
    already_executed: bool
    instrument: Optional[Instrument] = None
    match: Optional[AngelInstrumentMatch] = None
    settings: Optional[RuntimeSettings] = None
    available_funds: Optional[float] = None
    quantity: Optional[float] = None
    #: Cash this order may lose: the recommendation's own budget, cut down
    #: to a share of what the account actually holds.
    risk_budget: Optional[float] = None
    #: Entry / stop / target on the instrument's tick grid, as API strings.
    entry_price: Optional[str] = None
    stop_price: Optional[str] = None
    target_price: Optional[str] = None
    #: Broker's last traded price, fetched for this decision.
    ltp: Optional[float] = None
    #: Margin the broker says this order blocks; None if it would not say.
    margin_required: Optional[float] = None
    #: The live account's own history (broker.live_limits.LiveLedger).
    ledger: Optional[object] = None
    #: services.edge.latest_edge() for this symbol and timeframe.
    edge: Optional[dict] = None
    warnings: list = field(default_factory=list)


class PreTradeRiskGate:
    """The live-trading spec's pre-trade gate.

    Check 11 (daily loss, trades per day, open positions, exposure) runs
    against the live account's own history — `broker/live_limits.py`, kept
    current by the reconciler — never against the paper ledger.

    The gate also *derives* the order: prices on the exchange's tick grid and
    a quantity sized from the risk budget. Those are computed here, not by
    the caller, so what was checked is exactly what is sent.
    """

    #: Checks 1-5: local state only, zero network calls. Run first, always.
    _CHEAP_CHECKS = (
        "_check_trading_mode", "_check_confirmation", "_check_angel_connection",
        "_check_kill_switch", "_check_duplicate", "_check_recommendation_shape",
    )
    #: Checks 6-10: need instrument resolution / broker funds already fetched
    #: into the context by the caller.
    _NETWORK_DEPENDENT_CHECKS = (
        "_check_instrument_capability", "_check_untrusted_tick_conversion",
        "_check_unverified_quantity_unit", "_check_price_tick", "_check_quantity", "_check_funds", "_check_risk_amount",
        "_check_market_hours", "_check_price_band", "_check_order_limits",
        "_check_account_limits", "_check_measured_edge",
    )

    def derive(self, ctx: GateContext) -> None:
        """Fill in the order's prices and quantity without judging it, so the
        caller can ask the broker what *that* order needs in margin. The same
        two checks run again inside `evaluate`, which is what decides."""
        if ctx.match is not None and self._check_price_tick(ctx).allowed:
            self._check_quantity(ctx)

    async def evaluate_cheap(self, ctx: GateContext) -> RiskGateResult:
        return await self._run(ctx, self._CHEAP_CHECKS)

    async def evaluate(self, ctx: GateContext) -> RiskGateResult:
        return await self._run(ctx, self._CHEAP_CHECKS + self._NETWORK_DEPENDENT_CHECKS)

    async def _run(self, ctx: GateContext, check_names: tuple) -> RiskGateResult:
        for name in check_names:
            check = getattr(self, name)
            result = await check(ctx) if _is_async(check) else check(ctx)
            if not result.allowed:
                log.info("risk gate rejected %s at %s: %s",
                         ctx.recommendation.id, result.check, result.reason)
                return result
        return RiskGateResult(allowed=True, check="all", reason="Passed every pre-trade check.")

    # ── check 1 ───────────────────────────────────────────────
    def _check_trading_mode(self, ctx: GateContext) -> RiskGateResult:
        if not trading_mode.is_live:
            return RiskGateResult(allowed=False, check="trading_mode",
                                  reason="Live trading mode is not enabled.")
        return RiskGateResult(allowed=True)

    # ── check 2 ───────────────────────────────────────────────
    def _check_confirmation(self, ctx: GateContext) -> RiskGateResult:
        if not ctx.confirmed:
            return RiskGateResult(
                allowed=False, check="confirmation",
                reason="Live trading has not been explicitly confirmed.")
        return RiskGateResult(allowed=True)

    # ── check 3 ───────────────────────────────────────────────
    def _check_angel_connection(self, ctx: GateContext) -> RiskGateResult:
        if not angel_auth.connected:
            return RiskGateResult(allowed=False, check="angel_connection",
                                  reason="Angel One is not connected.")
        return RiskGateResult(allowed=True)

    # ── check 4 ───────────────────────────────────────────────
    def _check_kill_switch(self, ctx: GateContext) -> RiskGateResult:
        if trading_mode.kill_switch_active:
            return RiskGateResult(
                allowed=False, check="kill_switch",
                reason="Live trading is disabled by the kill switch.")
        return RiskGateResult(allowed=True)

    # ── duplicate-order guard (recommendation-level) ─────────
    def _check_duplicate(self, ctx: GateContext) -> RiskGateResult:
        if ctx.already_executed:
            return RiskGateResult(
                allowed=False, check="duplicate",
                reason="This recommendation already has an execution record. "
                      "A recommendation can only ever be executed once.")
        return RiskGateResult(allowed=True)

    # ── check 5 ───────────────────────────────────────────────
    def _check_recommendation_shape(self, ctx: GateContext) -> RiskGateResult:
        rec = ctx.recommendation
        if not rec.symbol:
            return RiskGateResult(allowed=False, check="recommendation",
                                  reason="Missing symbol.")
        if rec.side not in ("BUY", "SELL"):
            return RiskGateResult(allowed=False, check="recommendation",
                                  reason="Missing or invalid side.")
        if rec.entry is None or rec.entry <= 0:
            return RiskGateResult(allowed=False, check="recommendation",
                                  reason="Missing or invalid entry price.")
        if rec.stop_loss is None or rec.stop_loss <= 0:
            return RiskGateResult(allowed=False, check="recommendation",
                                  reason="Missing or invalid stop price.")
        if rec.side == "BUY" and rec.stop_loss >= rec.entry:
            return RiskGateResult(
                allowed=False, check="recommendation",
                reason="Stop is not below entry on a BUY — refusing to size "
                      "risk against an invalid stop.")
        if rec.side == "SELL" and rec.stop_loss <= rec.entry:
            return RiskGateResult(
                allowed=False, check="recommendation",
                reason="Stop is not above entry on a SELL — refusing to size "
                      "risk against an invalid stop.")
        if rec.risk_amount is None or rec.risk_amount <= 0:
            return RiskGateResult(allowed=False, check="recommendation",
                                  reason="Missing or invalid risk amount.")
        if rec.suggested_lots is None or rec.suggested_lots <= 0:
            return RiskGateResult(allowed=False, check="recommendation",
                                  reason="Missing or invalid position size.")
        return RiskGateResult(allowed=True)

    # ── check 6 ───────────────────────────────────────────────
    async def _check_instrument_capability(self, ctx: GateContext) -> RiskGateResult:
        try:
            match = await angel_instruments.resolve(ctx.recommendation.symbol)
        except InstrumentUnsupported as exc:
            return RiskGateResult(allowed=False, check="instrument_capability",
                                  reason=str(exc))
        ctx.match = match
        return RiskGateResult(allowed=True)

    # ── check 7 ───────────────────────────────────────────────
    def _check_untrusted_tick_conversion(self, ctx: GateContext) -> RiskGateResult:
        if ctx.match and ctx.match.exchange in UNTRUSTED_TICK_EXCHANGES:
            return RiskGateResult(
                allowed=False, check="untrusted_tick_conversion",
                reason="{} tick-size conversion from Angel One's scrip master "
                      "is not verified against a known-correct value — live "
                      "execution is refused on this exchange until that's "
                      "confirmed. See AngelOneInstrumentService.resolve() "
                      "for the specific gap.".format(ctx.match.exchange))
        return RiskGateResult(allowed=True)

    def _check_unverified_quantity_unit(self, ctx: GateContext) -> RiskGateResult:
        if ctx.match and ctx.match.exchange in UNVERIFIED_QUANTITY_EXCHANGES:
            return RiskGateResult(
                allowed=False, check="unverified_quantity_unit",
                reason="Whether Angel One takes a {} order's quantity in lots or in "
                       "contract units has not been verified, and a wrong guess is "
                       "a position many times the intended size — live execution "
                       "is refused on this exchange until it is confirmed with a "
                       "real one-lot order.".format(ctx.match.exchange))
        return RiskGateResult(allowed=True)

    # ── tick grid ─────────────────────────────────────────────
    def _check_price_tick(self, ctx: GateContext) -> RiskGateResult:
        """Put every price on the broker's tick grid and make sure the trade
        still makes sense there. An off-grid price is rejected by the
        exchange; a tick that is absurd for the price means the contract
        data is wrong and nothing derived from it can be trusted.

        Each price moves the way that cannot make the trade worse than the
        one that was sized: the entry never to a worse price than asked, the
        stop and target toward the entry.
        """
        from .angelone.orders import round_to_tick, toward

        def no(reason: str) -> RiskGateResult:
            return RiskGateResult(allowed=False, check="price_tick", reason=reason)

        match, rec = ctx.match, ctx.recommendation
        if match is None:
            return no("Instrument was not resolved.")
        tick = match.tick_size
        if not tick or tick <= 0 or tick > rec.entry * 0.05:
            return no("Tick size {} is not plausible for a price of {} — refusing "
                      "to round a live order against it.".format(tick, rec.entry))
        long = rec.side == "BUY"
        entry = round_to_tick(rec.entry, tick, "down" if long else "up")
        stop = round_to_tick(rec.stop_loss, tick, toward(reference_is_above=long))
        if (float(stop) >= float(entry)) if long else (float(stop) <= float(entry)):
            return no("Stop is less than one tick ({}) from entry once both are "
                      "rounded to the exchange grid.".format(tick))
        target = None
        if rec.take_profit:
            target = round_to_tick(rec.take_profit, tick, toward(reference_is_above=not long))
            if (float(target) <= float(entry)) if long else (float(target) >= float(entry)):
                return no("Target {} is not on the profit side of entry {}."
                          .format(rec.take_profit, rec.entry))
        ctx.entry_price, ctx.stop_price, ctx.target_price = entry, stop, target
        return RiskGateResult(allowed=True)

    # ── check 8 ───────────────────────────────────────────────
    def _check_quantity(self, ctx: GateContext) -> RiskGateResult:
        """Size the order from the risk budget and the broker's real lot.

        Units = budget / (entry − stop), rounded *down* to a whole lot — up
        would risk more than was budgeted, which §8 forbids outright. The
        budget is the recommendation's, capped at a share of the cash Angel
        One says is there: the recommendation was sized against a balance
        typed into Settings, and a real order must answer to the real one.

        (This used to multiply the recommendation's "lots" by the broker's
        lot size. A Meridian lot is the F&O contract size, so a cash-equity
        order came out hundreds of times smaller than it was sized for.)
        """
        match, rec = ctx.match, ctx.recommendation
        if match is None or not ctx.entry_price or not ctx.stop_price:
            return RiskGateResult(allowed=False, check="quantity",
                                  reason="Instrument or prices were not resolved.")
        budget, source = float(rec.risk_amount), "the recommendation's risk budget"
        cap_pct = get_settings().live_max_risk_pct_of_funds
        if cap_pct:
            if ctx.available_funds is None:
                return RiskGateResult(allowed=False, check="funds",
                                      reason="Could not read available funds "
                                            "from Angel One.")
            from_funds = ctx.available_funds * cap_pct / 100.0
            if from_funds < budget:
                budget = from_funds
                source = "{:g}% of available funds ({:.2f})".format(
                    cap_pct, ctx.available_funds)
        per_lot = abs(float(ctx.entry_price) - float(ctx.stop_price)) * match.lot_size
        quantity = int(budget // per_lot) * match.lot_size if per_lot > 0 else 0
        if quantity <= 0:
            return RiskGateResult(
                allowed=False, check="quantity",
                reason="One lot ({}) risks {:.2f} between entry and stop, but "
                      "{} is {:.2f} — it cannot buy even one lot."
                      .format(match.lot_size, per_lot, source, budget))
        ctx.quantity, ctx.risk_budget = quantity, budget
        return RiskGateResult(allowed=True)

    # ── check 9 ───────────────────────────────────────────────
    def _check_funds(self, ctx: GateContext) -> RiskGateResult:
        if ctx.available_funds is None:
            return RiskGateResult(allowed=False, check="funds",
                                  reason="Could not read available funds "
                                        "from Angel One.")
        if ctx.match is None or ctx.quantity is None:
            return RiskGateResult(allowed=False, check="funds",
                                  reason="Instrument or quantity not resolved.")
        # The broker's own margin figure when it gives one; full notional
        # otherwise, which can only err towards refusing.
        notional = float(ctx.entry_price or ctx.recommendation.entry) * ctx.quantity
        required = ctx.margin_required if ctx.margin_required is not None else notional
        if required > ctx.available_funds:
            return RiskGateResult(
                allowed=False, check="funds",
                reason="Order {} ({:.2f}) exceeds available funds ({:.2f})."
                      .format("margin" if ctx.margin_required is not None
                              else "notional", required, ctx.available_funds))
        return RiskGateResult(allowed=True)

    # ── check 10 ──────────────────────────────────────────────
    def _check_risk_amount(self, ctx: GateContext) -> RiskGateResult:
        """True by construction after check 8 — kept as an independent
        assertion, because "the order risks no more than its budget" is the
        one property nothing upstream may ever break."""
        if ctx.quantity is None or ctx.risk_budget is None:
            return RiskGateResult(allowed=False, check="risk_amount",
                                  reason="Quantity not resolved.")
        actual_risk = abs(float(ctx.entry_price) - float(ctx.stop_price)) * ctx.quantity
        if actual_risk > ctx.risk_budget * 1.0001:  # float tolerance only
            return RiskGateResult(
                allowed=False, check="risk_amount",
                reason="Broker-valid quantity implies {:.2f} risk, which "
                      "exceeds the budget of {:.2f}."
                      .format(actual_risk, ctx.risk_budget))
        return RiskGateResult(allowed=True)

    # ── is there any reason to believe this makes money? ──────
    def _check_measured_edge(self, ctx: GateContext) -> RiskGateResult:
        """A recent walk-forward test of this symbol and timeframe showed an
        edge after costs — and this recommendation is the kind of trade that
        test took. An edge measured on setups above 65% confidence says
        nothing about one at 50%."""
        if not get_settings().live_require_measured_edge:
            return RiskGateResult(allowed=True)

        def no(reason: str) -> RiskGateResult:
            return RiskGateResult(allowed=False, check="measured_edge", reason=reason)

        edge, rec = ctx.edge or {}, ctx.recommendation
        if not edge.get("passed"):
            return no(edge.get("reason") or "No measured edge for this instrument.")
        floor = edge.get("min_confidence")
        if floor is not None and rec.confidence < floor:
            return no("No measured edge for this setup: confidence is {:.0f}%, and "
                      "the walk-forward test only traded setups at {:.0f}% or more."
                      .format(rec.confidence, floor))
        min_rr = edge.get("min_risk_reward")
        if min_rr is not None:
            risk = abs(rec.entry - rec.stop_loss)
            reward = abs(rec.take_profit - rec.entry) if rec.take_profit else 0.0
            ratio = reward / risk if risk > 0 else 0.0
            if ratio + 1e-9 < min_rr:
                return no("No measured edge for this setup: reward:risk is {:.2f}, "
                          "and the walk-forward test only traded {:.1f} or better."
                          .format(ratio, min_rr))
        return RiskGateResult(allowed=True)

    # ── session ───────────────────────────────────────────────
    def _check_market_hours(self, ctx: GateContext) -> RiskGateResult:
        cfg = get_settings()
        if not cfg.live_enforce_market_hours:
            return RiskGateResult(allowed=True)
        import time

        from ..data import sessions
        from ..instruments import get_instrument
        asset = get_instrument(ctx.recommendation.symbol).asset_class
        now = time.time()
        if not sessions.is_market_open(now, sessions.session_key(asset)):
            return RiskGateResult(
                allowed=False, check="market_hours",
                reason="The market for {} is closed — an order placed now "
                      "would rest until the open and fill into whatever the "
                      "gap is.".format(ctx.recommendation.symbol))
        left = sessions.minutes_to_close(now, asset)
        window = cfg.live_no_entry_minutes_before_close
        if left is not None and window and left < window:
            return RiskGateResult(
                allowed=False, check="market_hours",
                reason="{:.0f} minutes to the close. Orders are intraday and are "
                      "squared off {:.0f} minutes before it, so no new entry is "
                      "taken in the last {:.0f}.".format(
                          max(left, 0.0), cfg.live_squareoff_minutes_before_close, window))
        return RiskGateResult(allowed=True)

    # ── entry vs the market right now ─────────────────────────
    def _check_price_band(self, ctx: GateContext) -> RiskGateResult:
        band = get_settings().live_price_band_pct
        if not band:
            return RiskGateResult(allowed=True)
        if not ctx.ltp or ctx.ltp <= 0:
            return RiskGateResult(
                allowed=False, check="price_band",
                reason="Could not read a current price from Angel One — "
                      "refusing to send an order blind.")
        drift = abs(ctx.recommendation.entry - ctx.ltp) / ctx.ltp * 100.0
        if drift > band:
            return RiskGateResult(
                allowed=False, check="price_band",
                reason="Entry {} is {:.2f}% from the last traded price {} "
                      "(limit {:.2f}%) — the recommendation is stale or the "
                      "price is wrong.".format(ctx.recommendation.entry, drift,
                                               ctx.ltp, band))
        return RiskGateResult(allowed=True)

    # ── size of this one order ────────────────────────────────
    def _check_order_limits(self, ctx: GateContext) -> RiskGateResult:
        cfg = get_settings()
        value = float(ctx.entry_price or ctx.recommendation.entry) * (ctx.quantity or 0)
        if cfg.live_max_quantity and (ctx.quantity or 0) > cfg.live_max_quantity:
            return RiskGateResult(
                allowed=False, check="max_quantity",
                reason="Quantity {:.0f} exceeds the limit of {}."
                      .format(ctx.quantity, cfg.live_max_quantity))
        if cfg.live_max_order_value and value > cfg.live_max_order_value:
            return RiskGateResult(
                allowed=False, check="max_order_value",
                reason="Order value {:.2f} exceeds the limit of {:.2f}."
                      .format(value, cfg.live_max_order_value))
        return RiskGateResult(allowed=True)

    # ── check 11: the account, not the trade ──────────────────
    def _check_account_limits(self, ctx: GateContext) -> RiskGateResult:
        if ctx.ledger is None:
            return _no("account_limits", "Live trade history was not loaded.")
        cfg = get_settings()
        for limit in (self._daily_loss, self._activity, self._exposure):
            result = limit(ctx, ctx.ledger, cfg)
            if not result.allowed:
                return result
        return RiskGateResult(allowed=True)

    @staticmethod
    def _daily_loss(ctx: GateContext, ledger, cfg) -> RiskGateResult:
        limit = cfg.live_daily_loss_limit
        if not limit:
            return RiskGateResult(allowed=True)
        if ledger.realised_today <= -limit:
            reason = ("Daily loss limit reached: {:.2f} lost today against a "
                      "limit of {:.2f}.".format(-ledger.realised_today, limit))
            trading_mode.activate_kill_switch(reason)
            return _no("daily_loss", reason)
        # Not only what the day has lost, but what it would have lost if this
        # order and everything already open were stopped out — and paid for.
        new_risk = stop_out_loss(ctx)
        worst = -ledger.realised_today + ledger.open_risk + new_risk
        if worst > limit:
            return _no("daily_loss", "This order would lose {:.2f} at its stop, charges "
                       "included. With {:.2f} lost so far today and {:.2f} at risk in "
                       "open positions, being stopped out would take the day to "
                       "{:.2f} — past the daily loss limit of {:.2f}."
                       .format(new_risk, max(-ledger.realised_today, 0.0),
                               ledger.open_risk, worst, limit))
        return RiskGateResult(allowed=True)

    @staticmethod
    def _activity(ctx: GateContext, ledger, cfg) -> RiskGateResult:
        if cfg.live_max_trades_per_day and ledger.trades_today >= cfg.live_max_trades_per_day:
            return _no("trades_per_day", "{} live trades already today (limit {})."
                       .format(ledger.trades_today, cfg.live_max_trades_per_day))
        if cfg.live_max_open_positions and ledger.open_positions >= cfg.live_max_open_positions:
            return _no("open_positions", "{} live positions or orders already open "
                       "(limit {}).".format(ledger.open_positions,
                                            cfg.live_max_open_positions))
        symbol = ctx.recommendation.symbol
        if symbol in ledger.symbol_exposure:
            # Angel One nets every order in an instrument into one position.
            # A second entry — adding to it, or the other way — could not be
            # stopped, sized or even told apart on its own.
            return _no("instrument_open", "{} already has a live position or working "
                       "order. One at a time per instrument: close it first."
                       .format(symbol))
        return RiskGateResult(allowed=True)

    @staticmethod
    def _exposure(ctx: GateContext, ledger, cfg) -> RiskGateResult:
        from .live_limits import sector_of

        symbol = ctx.recommendation.symbol
        value = float(ctx.entry_price or ctx.recommendation.entry) * (ctx.quantity or 0)
        if cfg.live_max_symbol_exposure and value > cfg.live_max_symbol_exposure:
            return _no("symbol_exposure", "{} exposure would be {:.2f} (limit {:.2f})."
                       .format(symbol, value, cfg.live_max_symbol_exposure))
        sector = sector_of(symbol)
        total = ledger.sector_exposure.get(sector, 0.0) + value if sector else 0.0
        if cfg.live_max_sector_exposure and total > cfg.live_max_sector_exposure:
            return _no("sector_exposure", "{} sector exposure would be {:.2f} "
                       "(limit {:.2f}).".format(sector, total,
                                                cfg.live_max_sector_exposure))
        return RiskGateResult(allowed=True)


def _no(check: str, reason: str) -> RiskGateResult:
    return RiskGateResult(allowed=False, check=check, reason=reason)


def stop_out_charges(ctx: GateContext) -> float:
    """Estimated brokerage and statutory charges if this order fills and is
    then stopped out (app/costs.py — the model the backtest is charged by)."""
    if not ctx.entry_price or not ctx.stop_price or not ctx.quantity:
        return 0.0
    if ctx.instrument is None:
        try:
            ctx.instrument = get_instrument(ctx.recommendation.symbol)
        except KeyError:
            return 0.0
    return costs.charges(ctx.instrument, float(ctx.entry_price) * ctx.quantity,
                         float(ctx.stop_price) * ctx.quantity)


def stop_out_loss(ctx: GateContext) -> float:
    """What this order takes from the account if it is stopped at its stop:
    the distance to it, and the round trip's charges on top."""
    if not ctx.entry_price or not ctx.stop_price or not ctx.quantity:
        return 0.0
    distance = abs(float(ctx.entry_price) - float(ctx.stop_price)) * ctx.quantity
    return distance + stop_out_charges(ctx)


def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)


risk_gate = PreTradeRiskGate()
