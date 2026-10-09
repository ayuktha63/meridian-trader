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

from ..config import get_settings
from ..instruments import Instrument
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
    warnings: list = field(default_factory=list)


class PreTradeRiskGate:
    """Runs checks 1-10 of the live-trading spec's pre-trade gate.

    Check 11 (daily loss, trades per day, open positions, exposure) runs
    against the live account's own history — `broker/live_limits.py`, kept
    current by the reconciler — never against the paper ledger.
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
        "_check_price_tick", "_check_quantity", "_check_funds", "_check_risk_amount",
        "_check_market_hours", "_check_price_band", "_check_order_limits",
        "_check_account_limits",
    )

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

    # ── tick grid ─────────────────────────────────────────────
    def _check_price_tick(self, ctx: GateContext) -> RiskGateResult:
        """Put every price on the broker's tick grid and make sure the trade
        still makes sense there. An off-grid price is rejected by the
        exchange; a tick that is absurd for the price means the contract
        data is wrong and nothing derived from it can be trusted."""
        from .angelone.orders import round_to_tick

        match, rec = ctx.match, ctx.recommendation
        if match is None:
            return RiskGateResult(allowed=False, check="price_tick",
                                  reason="Instrument was not resolved.")
        tick = match.tick_size
        if not tick or tick <= 0 or tick > rec.entry * 0.05:
            return RiskGateResult(
                allowed=False, check="price_tick",
                reason="Tick size {} is not plausible for a price of {} — "
                      "refusing to round a live order against it."
                      .format(tick, rec.entry))
        entry, stop = round_to_tick(rec.entry, tick), round_to_tick(rec.stop_loss, tick)
        long = rec.side == "BUY"
        if (long and float(stop) >= float(entry)) or (not long and float(stop) <= float(entry)):
            return RiskGateResult(
                allowed=False, check="price_tick",
                reason="Stop is less than one tick ({}) from entry once both "
                      "are rounded to the exchange grid.".format(tick))
        ctx.entry_price, ctx.stop_price = entry, stop
        if rec.take_profit:
            target = round_to_tick(rec.take_profit, tick)
            if (float(target) > float(entry)) == long and float(target) != float(entry):
                ctx.target_price = target
        return RiskGateResult(allowed=True)

    # ── check 8 ───────────────────────────────────────────────
    def _check_quantity(self, ctx: GateContext) -> RiskGateResult:
        match = ctx.match
        rec = ctx.recommendation
        if match is None:
            return RiskGateResult(allowed=False, check="quantity",
                                  reason="Instrument was not resolved.")
        lot_size = match.lot_size
        # Meridian's suggested_lots is a Meridian "lot" (contract_size units),
        # not necessarily Angel One's tradable lot — round DOWN to the
        # nearest whole broker lot. Rounding up would silently increase risk
        # beyond what the recommendation sized for, which §8 forbids outright.
        raw_units = rec.suggested_lots * lot_size
        broker_lots = int(raw_units // lot_size)
        quantity = broker_lots * lot_size
        if quantity <= 0:
            return RiskGateResult(
                allowed=False, check="quantity",
                reason="Sized position rounds down to zero at this "
                      "instrument's lot size ({}) — the risk budget cannot "
                      "buy even one lot.".format(lot_size))
        ctx.quantity = quantity
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
        notional = ctx.recommendation.entry * ctx.quantity
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
        rec = ctx.recommendation
        if ctx.quantity is None:
            return RiskGateResult(allowed=False, check="risk_amount",
                                  reason="Quantity not resolved.")
        entry = float(ctx.entry_price) if ctx.entry_price else rec.entry
        stop = float(ctx.stop_price) if ctx.stop_price else rec.stop_loss
        actual_risk = abs(entry - stop) * ctx.quantity
        # Broker-lot rounding can only ever reduce quantity (check 8), so the
        # actual risk can come in under the recommendation's budget but must
        # never come in over it.
        tick_slack = (ctx.match.tick_size if ctx.match else 0.0) * ctx.quantity
        if actual_risk > rec.risk_amount * 1.0001 + tick_slack:  # float + one tick of rounding
            return RiskGateResult(
                allowed=False, check="risk_amount",
                reason="Broker-valid quantity implies {:.2f} risk, which "
                      "exceeds the recommendation's budget of {:.2f}."
                      .format(actual_risk, rec.risk_amount))
        return RiskGateResult(allowed=True)


    # ── session ───────────────────────────────────────────────
    def _check_market_hours(self, ctx: GateContext) -> RiskGateResult:
        if not get_settings().live_enforce_market_hours:
            return RiskGateResult(allowed=True)
        import time

        from ..data.sessions import is_market_open
        from ..instruments import get_instrument
        asset = get_instrument(ctx.recommendation.symbol).asset_class
        if not is_market_open(time.time(), "equity" if asset == "stock" else asset):
            return RiskGateResult(
                allowed=False, check="market_hours",
                reason="The market for {} is closed — an order placed now "
                      "would rest until the open and fill into whatever the "
                      "gap is.".format(ctx.recommendation.symbol))
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
        value = ctx.recommendation.entry * (ctx.quantity or 0)
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
        from .live_limits import sector_of

        cfg, ledger = get_settings(), ctx.ledger
        if ledger is None:
            return RiskGateResult(allowed=False, check="account_limits",
                                  reason="Live trade history was not loaded.")

        def no(check: str, reason: str) -> RiskGateResult:
            return RiskGateResult(allowed=False, check=check, reason=reason)

        if cfg.live_daily_loss_limit and ledger.realised_today <= -cfg.live_daily_loss_limit:
            reason = ("Daily loss limit reached: {:.2f} lost today against a "
                      "limit of {:.2f}.".format(-ledger.realised_today,
                                                cfg.live_daily_loss_limit))
            trading_mode.activate_kill_switch(reason)
            return no("daily_loss", reason)
        if cfg.live_max_trades_per_day and ledger.trades_today >= cfg.live_max_trades_per_day:
            return no("trades_per_day", "{} live trades already today (limit {})."
                      .format(ledger.trades_today, cfg.live_max_trades_per_day))
        if cfg.live_max_open_positions and ledger.open_positions >= cfg.live_max_open_positions:
            return no("open_positions", "{} live positions or orders already open "
                      "(limit {}).".format(ledger.open_positions,
                                           cfg.live_max_open_positions))
        symbol = ctx.recommendation.symbol
        value = ctx.recommendation.entry * (ctx.quantity or 0)
        held = ledger.symbol_exposure.get(symbol, 0.0)
        if cfg.live_max_symbol_exposure and held + value > cfg.live_max_symbol_exposure:
            return no("symbol_exposure", "{} exposure would be {:.2f} (limit {:.2f})."
                      .format(symbol, held + value, cfg.live_max_symbol_exposure))
        sector = sector_of(symbol)
        if sector and cfg.live_max_sector_exposure:
            total = ledger.sector_exposure.get(sector, 0.0) + value
            if total > cfg.live_max_sector_exposure:
                return no("sector_exposure", "{} sector exposure would be {:.2f} "
                          "(limit {:.2f}).".format(sector, total,
                                                   cfg.live_max_sector_exposure))
        return RiskGateResult(allowed=True)


def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)


risk_gate = PreTradeRiskGate()
