"""Live WebSocket stream.

One socket per chart. The client declares which (symbol, timeframe) view it is
looking at; the server pushes quotes, forming-bar updates, bar closes, and
periodic AI analyses for exactly that view. Changing symbol or timeframe is a
`subscribe` message, not a reconnect.

Analysis is triggered on bar close and on a timer, whichever comes first, with
a floor between runs so a fast timeframe cannot spam the analyst.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import alerts, runtime_settings
from ..analysis import autotrader
from ..analysis import live as live_tier
from ..analysis import risk as risk_engine
from ..broker import live_limits
from ..broker.angelone.execution import (
    RecommendationNotExecutable, RecommendationNotFound, angel_execution,
)
from ..broker.trading_mode import trading_mode
from ..config import settings
from ..data.feed import feed
from ..data.store import store
from ..db.session import session_scope
from ..instruments import SYMBOLS, TIMEFRAME_CODES, get_instrument
from ..schemas.analysis import Decision
from ..schemas.execution import ExecutionStatus
from ..services import paper as paper_repo
from ..services import positions as position_repo
from .analysis import run_for_stream

log = logging.getLogger("meridian.ws")
router = APIRouter()

MIN_ANALYSIS_GAP = 15.0     # seconds — hard floor between analyses per socket

#: How often the socket re-reads the open position from the database. The
#: per-tick maths then runs against that cached copy, so a one-second alert
#: costs no query.
POSITION_REFRESH_SECONDS = 5.0

#: Don't re-send an identical alert on every tick; repeat it on this cadence
#: so it stays on screen without flooding the socket.
ALERT_REPEAT_SECONDS = 10.0


class Connection:
    """Per-socket state: the active view and the analysis cadence."""

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self.symbol: Optional[str] = None
        self.timeframe: Optional[str] = None
        self.auto_analysis = True
        self.include_ai = True
        self.last_analysis = 0.0
        self.analysis_task: Optional[asyncio.Task] = None
        self.closed = False
        # ── fast tier state ──
        self.position = None
        self.position_checked = 0.0
        self.last_alert_key: Optional[str] = None
        self.last_alert_at = 0.0
        # ── every open position, not just the chart on screen — a stop
        # being hit has to warn you regardless of what you're looking at.
        self.all_positions: Dict[str, Any] = {}
        self.all_positions_checked = 0.0
        self.cross_alert_state: Dict[str, tuple] = {}   # position_id -> (alert, monotonic_at)
        # ── auto-trader state ──
        self.paper_trade = None
        self.paper_status = ""

    async def send(self, payload: Dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(json.dumps(payload, default=str))
        except (RuntimeError, WebSocketDisconnect):
            self.closed = True

    def watches(self, symbol: str, timeframe: Optional[str] = None) -> bool:
        if self.symbol != symbol:
            return False
        return timeframe is None or self.timeframe == timeframe

    def set_view(self, symbol: str, timeframe: str) -> None:
        if self.symbol and self.timeframe:
            feed.unwatch(self.symbol, self.timeframe)
        self.symbol, self.timeframe = symbol, timeframe
        feed.watch(symbol, timeframe)
        self.last_analysis = 0.0
        self.position = None
        self.position_checked = 0.0
        self.last_alert_key = None
        self.paper_trade = None

    def release(self) -> None:
        self.closed = True
        if self.symbol and self.timeframe:
            feed.unwatch(self.symbol, self.timeframe)
            self.symbol = self.timeframe = None
        if self.analysis_task and not self.analysis_task.done():
            self.analysis_task.cancel()


async def _refresh_position(conn: Connection) -> None:
    """Re-read the open position for the watched symbol, on a slow cadence."""
    if not conn.symbol:
        return
    now = time.monotonic()
    if now - conn.position_checked < POSITION_REFRESH_SECONDS:
        return
    conn.position_checked = now
    try:
        async with session_scope() as session:
            conn.position = await position_repo.open_for(session, conn.symbol)
    except Exception as exc:  # noqa: BLE001
        log.debug("position refresh failed: %s", exc)


async def _refresh_all_positions(conn: Connection) -> None:
    """Every open position, across every symbol — feeds the cross-chart exit
    warning so a stop hit on RELIANCE still interrupts you while you're
    looking at NIFTY. Same slow cadence as `_refresh_position`."""
    now = time.monotonic()
    if now - conn.all_positions_checked < POSITION_REFRESH_SECONDS:
        return
    conn.all_positions_checked = now
    try:
        async with session_scope() as session:
            positions = await position_repo.list_positions(session, status="open")
            conn.all_positions = {p.symbol: p for p in positions}
    except Exception as exc:  # noqa: BLE001
        log.debug("all-positions refresh failed: %s", exc)


async def _check_cross_symbol_alert(conn: Connection, symbol: str, price: float) -> None:
    """Stop/target evaluation for a position on a symbol the trader is *not*
    currently viewing. The symbol they ARE viewing is already covered by
    `_emit_live_tick`, which also drives that chart's live P&L display —
    this only ever fires the alert, never the position_tick UI update, so a
    background position doesn't spam ticks for a chart that isn't showing it.
    """
    position = conn.all_positions.get(symbol)
    if position is None or (conn.position and conn.position.symbol == symbol):
        return
    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return

    frame = store.frame(symbol, position.timeframe, 60)
    atr = None
    if frame is not None and len(frame) >= 20:
        atr = float((frame["high"] - frame["low"]).tail(20).mean())
    quote = feed.quote(symbol)

    tick = live_tier.evaluate_tick(
        position, price, instrument, atr, quote.spread_pips if quote else None)
    if tick is None or not tick.alert:
        return

    now = time.monotonic()
    prev_alert, prev_at = conn.cross_alert_state.get(tick.position_id, (None, 0.0))
    if tick.alert == prev_alert and now - prev_at < ALERT_REPEAT_SECONDS:
        return
    conn.cross_alert_state[tick.position_id] = (tick.alert, now)
    await conn.send({"type": "exit_alert", "data": tick.model_dump()})


async def _persist_protection(conn: Connection, tick) -> None:
    """Ratchet the tracked stop and the water marks.

    Two things happen here, and the difference matters.

    The **water marks** (peak and trough R) are recorded unconditionally. They
    are observations, not decisions, and they come from ticks the bar closes
    never see — a spike to +2.4R that closes at +0.8R is real, and the journal
    should know about it.

    The **stop** only moves when auto-protect is on. Even then it moves only in
    this tracker: Meridian has no broker connection and will not pretend it
    does. The position is flagged unmirrored until the trader confirms they
    have moved it in the market, and the UI says so in the loudest way it has.
    """
    position = conn.position
    if position is None:
        return

    cfg = runtime_settings.get()
    update: Dict[str, float] = {}

    if tick.peak_r > position.peak_r + 1e-9:
        update["peak_r"] = tick.peak_r
    if tick.trough_r < position.trough_r - 1e-9:
        update["trough_r"] = tick.trough_r

    advanced = tick.protection_tier > position.protection_tier
    if cfg.auto_protect and advanced and tick.suggested_stop is not None:
        update["stop_loss"] = tick.suggested_stop
        update["protection_tier"] = tick.protection_tier

    if not update:
        return
    try:
        async with session_scope() as session:
            conn.position = await position_repo.apply_protection(
                session, position.id, **update)
    except Exception as exc:  # noqa: BLE001 - never lose the stream over this
        log.debug("protection persist failed: %s", exc)


async def _emit_live_tick(conn: Connection, symbol: str, price: float) -> None:
    """Price-level evaluation of the open position — the one-second tier."""
    position = conn.position
    if position is None or position.symbol != symbol:
        return
    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return

    # ATR comes from the cached frame rather than a fresh computation — this
    # runs every tick, and the ladder only needs a noise scale, not precision.
    frame = store.frame(symbol, position.timeframe, 60)
    atr = None
    if frame is not None and len(frame) >= 20:
        atr = float((frame["high"] - frame["low"]).tail(20).mean())
    quote = feed.quote(symbol)

    tick = live_tier.evaluate_tick(
        position, price, instrument, atr,
        quote.spread_pips if quote else None)
    if tick is None:
        return

    await _persist_protection(conn, tick)
    await conn.send({"type": "position_tick", "data": tick.model_dump()})

    if not tick.alert:
        conn.last_alert_key = None
        return

    # An alert repeats on a cadence rather than every tick, and re-fires
    # immediately when the *kind* of alert changes.
    now = time.monotonic()
    key = "{}:{}".format(tick.position_id, tick.alert)
    if key == conn.last_alert_key and now - conn.last_alert_at < ALERT_REPEAT_SECONDS:
        return
    conn.last_alert_key = key
    conn.last_alert_at = now
    await conn.send({"type": "exit_alert", "data": tick.model_dump()})

    # A terminal alert is also worth a fresh structural read.
    if tick.urgency == "immediate":
        conn.last_analysis = 0.0
        _schedule_analysis(conn, "alert")


async def _auto_trade_tick(conn: Connection, symbol: str, price: float) -> None:
    """Price-level management of the paper position — the one-second tier.

    Only stop and target fills happen here. They are arithmetic against levels
    already set, which is exactly why they belong on this clock: a stop that
    waits for the next structural pass to be noticed is not a stop.
    """
    trade = conn.paper_trade
    if trade is None or trade.symbol != symbol:
        return
    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return

    quote = feed.quote(symbol)
    bid = quote.bid if quote else price
    ask = quote.ask if quote else price

    # Trail the stop first, so a rung that arms on this very tick protects the
    # trade before the level check runs against it.
    frame = store.frame(symbol, trade.timeframe, 60)
    if frame is not None and len(frame) >= 20:
        atr = float((frame["high"] - frame["low"]).tail(20).mean())
        plan = risk_engine.build_plan(
            _as_position(trade), price, instrument, atr,
            quote.spread_pips if quote else None)
        if plan is not None:
            update = {"peak_r": plan.peak_r, "trough_r": plan.trough_r}
            tier = next((t.index for t in plan.tiers if t.active), 0)
            if plan.action_available and tier > trade.protection_tier:
                update["stop_loss"] = plan.suggested_stop
                update["protection_tier"] = tier
            try:
                async with session_scope() as session:
                    await paper_repo.apply_protection(session, trade.id, **update)
                    conn.paper_trade = await paper_repo.open_trade_for(session, symbol)
                trade = conn.paper_trade or trade
            except Exception as exc:  # noqa: BLE001
                log.debug("paper protection failed: %s", exc)

    intent = autotrader.check_levels(trade, bid, ask, price)
    if intent is None:
        return

    try:
        async with session_scope() as session:
            closed = await paper_repo.close_trade(
                session, trade.id, bid, ask, price, intent.exit_reason,
                instrument,
                fill_at=trade.stop_loss if intent.exit_reason.value == "stop"
                else trade.take_profit)
            conn.paper_trade = None
        if closed is not None:
            log.info("paper %s %s → %s at %.5g (%+.2fR, %+.2f)",
                     closed.side, closed.symbol, intent.exit_reason.value,
                     closed.exit, closed.realised_r or 0.0, closed.net_pnl)
            await conn.send({"type": "paper_fill",
                             "data": closed.model_dump(mode="json"),
                             "reason": intent.reason})
            await _emit_paper_state(conn)
    except Exception as exc:  # noqa: BLE001
        log.warning("paper exit failed: %s", exc)


def _as_position(trade):
    """Adapt a paper trade to the shape the protection ladder expects."""
    from ..schemas.positions import Position, PositionSide

    return Position(
        id=trade.id, symbol=trade.symbol, timeframe=trade.timeframe,
        side=PositionSide(trade.side), entry=trade.entry,
        stop_loss=trade.stop_loss, initial_stop=trade.initial_stop,
        take_profit=trade.take_profit, lots=trade.quantity,
        opened_at=trade.entry_at, protection_tier=trade.protection_tier,
        peak_r=trade.peak_r, trough_r=trade.trough_r,
        broker_stop=trade.stop_loss,
    )


async def _emit_paper_state(conn: Connection) -> None:
    try:
        async with session_scope() as session:
            cfg = runtime_settings.get()
            account = await paper_repo.get_account(
                session, currency=cfg.account_currency)
            open_trade = await paper_repo.open_trade_for(session)
            closed = await paper_repo.list_trades(session, "closed", 200)
            price = feed.price(open_trade.symbol) if open_trade else None
            account = paper_repo.summarise(account, closed, open_trade, price)
            conn.paper_trade = open_trade
        await conn.send({
            "type": "paper_state",
            "data": {"account": account.model_dump(mode="json"),
                     "open_trade": open_trade.model_dump(mode="json") if open_trade else None},
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("paper state emit failed: %s", exc)


async def _auto_trade_entry(conn: Connection, result) -> None:
    """Open a paper position when the analysis clears every gate."""
    symbol = result.symbol
    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return

    quote = feed.quote(symbol)
    price = result.price
    bid = quote.bid if quote else price
    ask = quote.ask if quote else price

    async with session_scope() as session:
        cfg = runtime_settings.get()
        account = await paper_repo.get_account(
            session, currency=cfg.account_currency)
        open_trade = await paper_repo.open_trade_for(session)

        if open_trade is not None:
            if open_trade.symbol != symbol:
                return
            intent = autotrader.evaluate_exit(open_trade, result, time.time())
            if intent.action == "exit":
                closed = await paper_repo.close_trade(
                    session, open_trade.id, bid, ask, price,
                    intent.exit_reason, instrument)
                conn.paper_trade = None
                if closed is not None:
                    log.info("paper %s %s → signal exit (%+.2fR)",
                             closed.side, closed.symbol, closed.realised_r or 0.0)
                    await conn.send({"type": "paper_fill",
                                     "data": closed.model_dump(mode="json"),
                                     "reason": intent.reason})
            return

        intent = autotrader.evaluate_entry(
            result, account, instrument,
            quote.spread_pips if quote else None)
        if intent.action != "enter":
            conn.paper_status = intent.reason
            return

        trade = await paper_repo.open_trade(
            session, symbol, result.timeframe, intent.side, bid, ask, price,
            intent.stop_loss, intent.take_profit, instrument,
            confidence=result.confidence.final_confidence,
            source=result.verdict.source, reason=intent.reason,
            analysis_id=result.id)
        conn.paper_trade = trade

    if conn.paper_trade is not None:
        log.info("paper opened %s %s at %.5g (stop %.5g, target %s)",
                 conn.paper_trade.side, symbol, conn.paper_trade.entry,
                 conn.paper_trade.stop_loss, conn.paper_trade.take_profit)
        await conn.send({"type": "paper_fill",
                         "data": conn.paper_trade.model_dump(mode="json"),
                         "reason": intent.reason})
    await _emit_paper_state(conn)


async def _auto_trade_live_entry(conn: Connection, result) -> None:
    """Fire a real order when auto-live is armed and a signal clears every
    gate the manual workflow would — the same strategy, the same thresholds
    (`autotrader.evaluate_entry`), the same `PreTradeRiskGate` on the way out
    (measured edge, daily loss against the real ledger, funds, the MCX/CDS
    blocks). This function only decides *whether to ask*; it is never the
    thing that decides *whether to allow* — that stays entirely in the gate,
    so this can never become a second, looser way to place a live order.
    """
    if not trading_mode.can_auto_trade_live():
        return
    if result.verdict.decision not in (Decision.BUY, Decision.SELL):
        return

    symbol = result.symbol
    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return

    async with session_scope() as session:
        cfg = runtime_settings.get()
        account = await paper_repo.get_account(session, currency=cfg.account_currency)
        quote = feed.quote(symbol)
        intent = autotrader.evaluate_entry(
            result, account, instrument, quote.spread_pips if quote else None)
        if intent.action != "enter":
            return

        # One live attempt in flight per symbol at a time — a second signal
        # arriving before the last one has filled or been refused is a race,
        # not a second opportunity.
        existing = await angel_execution.list_executions(session)
        if any(e.symbol == symbol and e.status.value in live_limits.WORKING
               for e in existing):
            return

        try:
            record = await angel_execution.execute(session, result.id, confirmed=True)
        except (RecommendationNotFound, RecommendationNotExecutable) as exc:
            log.info("auto-live skipped %s: %s", symbol, exc)
            return

    if record.status == ExecutionStatus.REJECTED:
        log.info("auto-live refused %s %s: %s", record.side, symbol, record.error_message)
        alerts.emit("info", "AUTO_LIVE_REFUSED",
                   "Auto-live refused {} {}: {}".format(
                       record.side, symbol, record.error_message),
                   throttle=60.0, symbol=symbol)
    else:
        log.warning("AUTO-LIVE placed %s %s x%s", record.side, symbol, record.requested_quantity)
        alerts.emit("warning", "AUTO_LIVE_PLACED",
                   "Auto-live placed {} {} x{}".format(
                       record.side, symbol, record.requested_quantity),
                   symbol=symbol)

    # Best-effort, immediate feedback to whichever socket is watching this
    # symbol right now — the alert above is the durable record either way,
    # so a closed or unrelated socket losing this is not a problem.
    try:
        if conn.watches(symbol, result.timeframe):
            await conn.send({"type": "live_auto_attempt",
                             "data": record.model_dump(mode="json")})
    except Exception:  # noqa: BLE001
        pass


async def _emit_analysis(conn: Connection, trigger: str) -> None:
    symbol, timeframe = conn.symbol, conn.timeframe
    if not symbol or not timeframe or conn.closed:
        return
    conn.last_analysis = time.monotonic()
    await conn.send({"type": "analysis_pending", "symbol": symbol,
                     "timeframe": timeframe, "trigger": trigger})

    # A manual request bypasses the cache — the trader asked for fresh work,
    # usually because they just changed something on the chart.
    result = await run_for_stream(
        symbol, timeframe, conn.include_ai, force=trigger in ("manual", "subscribe"))
    if result is None:
        await conn.send({
            "type": "analysis_error", "symbol": symbol, "timeframe": timeframe,
            "message": "Analysis failed — the platform will retry on the next bar close",
        })
        return
    # The view may have changed while the analysis was running.
    if conn.watches(result.symbol, result.timeframe):
        await conn.send({"type": "analysis", "trigger": trigger,
                         "data": result.model_dump(mode="json")})
        # Entries need the full evidence set, so they ride the structural pass
        # rather than the tick tier. Never allowed to break the stream.
        try:
            await _auto_trade_entry(conn, result)
        except Exception as exc:  # noqa: BLE001
            log.warning("auto-trade entry failed: %s", exc)
        # Independent of the paper auto-trader above: gated on auto-live
        # being armed, and a no-op the instant it is not.
        try:
            await _auto_trade_live_entry(conn, result)
        except Exception as exc:  # noqa: BLE001
            log.warning("auto-live entry failed: %s", exc)


def _schedule_analysis(conn: Connection, trigger: str) -> None:
    if not conn.auto_analysis or conn.closed:
        return
    if conn.analysis_task and not conn.analysis_task.done():
        return
    if time.monotonic() - conn.last_analysis < MIN_ANALYSIS_GAP:
        return
    conn.analysis_task = asyncio.create_task(_emit_analysis(conn, trigger))


async def _pump_market_events(conn: Connection) -> None:
    """Forward feed events that belong to this socket's view."""
    queue = feed.subscribe()
    try:
        while not conn.closed:
            event = await queue.get()
            etype = event.get("type")
            symbol = event.get("symbol")

            if etype == "quote":
                price = (event.get("data") or {}).get("last")
                # Cross-symbol stop/target monitoring runs regardless of
                # what's on screen — every connection already receives every
                # symbol's ticks on this shared queue, this just stops
                # discarding the ones for positions open elsewhere.
                await _refresh_all_positions(conn)
                if price:
                    await _check_cross_symbol_alert(conn, symbol, float(price))
                if conn.watches(symbol):
                    await conn.send(event)
                    await _refresh_position(conn)
                    if price:
                        await _emit_live_tick(conn, symbol, float(price))
                        await _auto_trade_tick(conn, symbol, float(price))
            elif etype in ("candle", "bar_closed"):
                if conn.watches(symbol, event.get("timeframe")):
                    await conn.send(event)
                    if etype == "bar_closed":
                        _schedule_analysis(conn, "bar_close")
    except asyncio.CancelledError:
        raise
    finally:
        feed.unsubscribe(queue)


async def _analysis_timer(conn: Connection) -> None:
    """Periodic re-analysis so slow timeframes still refresh."""
    interval = max(15, settings.ai_auto_analysis_interval)
    while not conn.closed:
        await asyncio.sleep(interval)
        if conn.auto_analysis and conn.symbol:
            _schedule_analysis(conn, "interval")


@router.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    conn = Connection(websocket)

    await conn.send({
        "type": "hello",
        "symbols": SYMBOLS,
        "timeframes": TIMEFRAME_CODES,
        "auto_analysis_interval": settings.ai_auto_analysis_interval,
        "server_time": time.time(),
    })

    # Send the demo account straight away so the balance is on screen before
    # the first analysis lands.
    await _emit_paper_state(conn)

    pump = asyncio.create_task(_pump_market_events(conn))
    timer = asyncio.create_task(_analysis_timer(conn))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError:
                await conn.send({"type": "error", "message": "Malformed JSON"})
                continue

            action = message.get("type")

            if action == "subscribe":
                symbol = str(message.get("symbol", "")).upper()
                timeframe = str(message.get("timeframe", "15m")).lower()
                if symbol not in SYMBOLS or timeframe not in TIMEFRAME_CODES:
                    await conn.send({"type": "error",
                                     "message": "Unknown symbol or timeframe"})
                    continue
                conn.set_view(symbol, timeframe)
                await feed.ensure_history(symbol, timeframe)
                quote = feed.quote(symbol)
                await conn.send({
                    "type": "subscribed", "symbol": symbol, "timeframe": timeframe,
                    "quote": quote.model_dump() if quote else None,
                })
                _schedule_analysis(conn, "subscribe")

            elif action == "set_auto_analysis":
                conn.auto_analysis = bool(message.get("enabled", True))
                await conn.send({"type": "auto_analysis",
                                 "enabled": conn.auto_analysis})

            elif action == "set_include_ai":
                conn.include_ai = bool(message.get("enabled", True))
                await conn.send({"type": "include_ai", "enabled": conn.include_ai})

            elif action == "analyse":
                # Manual trigger bypasses the cadence floor.
                conn.last_analysis = 0.0
                _schedule_analysis(conn, "manual")

            elif action == "ping":
                await conn.send({"type": "pong", "server_time": time.time()})

            else:
                await conn.send({"type": "error",
                                 "message": "Unknown action: {}".format(action)})

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("websocket closed with error: %s", exc)
    finally:
        conn.release()
        pump.cancel()
        timer.cancel()
        for task in (pump, timer):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
