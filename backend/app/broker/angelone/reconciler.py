"""The live-order supervisor: what the broker says happened, acted on.

`execution.py` submits an entry and stops. This module owns everything
after that, and runs it as one repeated pass over every execution that is
not yet finished:

* follow the entry to a terminal state — fill, partial fill, rejection —
  including one whose submission timed out (found again by its tag) and one
  left in flight by a restart;
* cancel an entry the market never came back to, rather than let a stale
  setup fill hours later;
* keep a **stop order resting at the broker** behind every filled share. If
  it cannot be placed the position is flattened, the kill switch is tripped
  and an alert is raised — an unprotected live position is never left open;
* rest a target alongside it, and when either one fills cancel the other;
* move the real stop up the protection ladder (`analysis/risk.py`);
* notice a position closed outside Meridian and pull the orphaned stop
  before it can open a new position by itself.

Every action here either reduces risk or records what already happened, so
none of it is gated on LIVE mode or the kill switch: a tripped kill switch
must never be the reason a position has no stop.

Unverified against a live account, and stated here rather than discovered
later: the exact order parameters in `_stop_params` / `_exit_params`, and
whether Angel One's risk system accepts a resting target beside a resting
stop for the same intraday position (it may ask for extra margin). A
rejected target is handled — the target is then watched and exited in
software — but watch the first live order in Angel One's own app.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import alerts, runtime_settings
from ...config import settings
from ...db.models import AnalysisRow, LiveExecutionRow
from ...db.session import session_scope
from ...schemas.execution import ExecutionStatus as S
from ..trading_mode import trading_mode
from .client import AngelOneAmbiguousError, AngelOneOrderError, angel_broker
from .orders import order_tag, round_to_tick

log = logging.getLogger("meridian.angelone.reconciler")

ENTRY_STATES = (S.SUBMITTING.value, S.UNKNOWN.value, S.SUBMITTED.value,
                S.OPEN.value, S.PARTIALLY_FILLED.value, S.CANCEL_PENDING.value)
ACTIVE_STATES = ENTRY_STATES + (S.FILLED.value, S.CLOSING.value)

#: How long an order nobody can find is given to appear before concluding
#: the request never reached the broker.
UNKNOWN_GRACE_SECONDS = 120.0
#: Consecutive passes the broker must report no position before a fill we
#: recorded is treated as closed elsewhere — the positions endpoint can lag
#: the order book, and acting on one stale read would strip a real stop.
FLAT_CONFIRMATIONS = 3
FLAT_MIN_AGE_SECONDS = 20.0

PRODUCT_TYPE = "INTRADAY"
_DONE = ("complete", "filled")
_DEAD = ("rejected", "cancelled", "canceled")


def _status(order: Optional[dict]) -> str:
    return ((order or {}).get("status") or "").lower()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _filled(order: dict) -> float:
    return _num(order.get("filledshares") or order.get("fillquantity"))


class LiveReconciler:
    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None
        self._task: Optional[asyncio.Task] = None
        self._flat_seen: Dict[str, int] = {}
        self._target_unavailable: set = set()
        self._last_failure_alert = 0.0

    # ── lifecycle ─────────────────────────────────────────────

    def _mutex(self) -> asyncio.Lock:
        if self._lock is None:   # built inside the running loop (py3.9)
            self._lock = asyncio.Lock()
        return self._lock

    async def start(self) -> None:
        if self._task is None and settings.live_reconciler_enabled:
            self._lock = None
            self._task = asyncio.create_task(self._loop(), name="live-reconciler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must outlive any one pass
                if time.time() - self._last_failure_alert > 60:
                    self._last_failure_alert = time.time()
                    alerts.emit("critical", "RECONCILE_FAILED",
                                "Live orders could not be checked against Angel One — "
                                "open positions are not being supervised.",
                                error=str(exc))
            await asyncio.sleep(max(1.0, settings.live_reconcile_interval_seconds))

    # ── passes ────────────────────────────────────────────────

    async def _active_ids(self) -> List[str]:
        async with session_scope() as session:
            stmt = select(LiveExecutionRow.id).where(
                LiveExecutionRow.status.in_(ACTIVE_STATES))
            return list((await session.execute(stmt)).scalars().all())

    async def run_once(self) -> int:
        """One pass over every unfinished execution. Returns how many."""
        ids = await self._active_ids()
        if not ids:
            return 0          # nothing live: never touch the broker
        async with self._mutex():
            orders = await angel_broker.get_orders()
            positions = await self._positions()
            for row_id in ids:
                try:
                    async with session_scope() as session:
                        row = await session.get(LiveExecutionRow, row_id)
                        if row is not None:
                            await self._reconcile(session, row, orders, positions)
                except Exception:  # noqa: BLE001 - one bad row must not strand the rest
                    log.exception("reconcile failed for execution %s", row_id)
        return len(ids)

    async def reconcile_row(self, session: AsyncSession, row: LiveExecutionRow) -> None:
        async with self._mutex():
            orders = await angel_broker.get_orders()
            await self._reconcile(session, row, orders, await self._positions())

    @staticmethod
    async def _positions() -> Optional[List[dict]]:
        try:
            return await angel_broker.get_positions()
        except Exception:  # noqa: BLE001 - None means "unknown", never "flat"
            return None

    # ── one execution ─────────────────────────────────────────

    async def _reconcile(self, session: AsyncSession, row: LiveExecutionRow,
                         orders: List[dict], positions: Optional[List[dict]]) -> None:
        by_id = {o.get("orderid"): o for o in orders if o.get("orderid")}
        if row.status in ENTRY_STATES:
            await self._follow_entry(row, orders, by_id)
        if row.status in ACTIVE_STATES and (row.filled_quantity or 0) > 0:
            await self._supervise_position(session, row, orders, by_id, positions)
        row.updated_at = time.time()
        await session.flush()
        if row.status == S.CLOSED.value:
            await self._enforce_daily_loss(session)

    @staticmethod
    async def _enforce_daily_loss(session: AsyncSession) -> None:
        """Trip the kill switch the moment the day's realised loss crosses
        the limit — not at the next attempted entry."""
        from ..live_limits import load_ledger
        limit = settings.live_daily_loss_limit
        if not limit or trading_mode.kill_switch_active:
            return
        lost = -(await load_ledger(session)).realised_today
        if lost >= limit:
            reason = "Daily loss limit reached: {:.2f} lost today (limit {:.2f}).".format(
                lost, limit)
            trading_mode.activate_kill_switch(reason)
            alerts.emit("critical", "DAILY_LOSS_LIMIT", reason)

    async def _follow_entry(self, row: LiveExecutionRow, orders: List[dict],
                            by_id: Dict[str, dict]) -> None:
        now = time.time()
        if not row.broker_order_id:
            found = self._by_tag(orders, row.order_tag, row)
            if found is not None:
                row.broker_order_id = found.get("orderid")
                alerts.emit("warning", "ORDER_RECOVERED",
                            "Found an order whose submission never confirmed.",
                            symbol=row.symbol, order_id=row.broker_order_id)
            elif now - (row.created_at or now) > UNKNOWN_GRACE_SECONDS:
                row.status = S.REJECTED.value
                row.error_message = ("The order never appeared at Angel One — "
                                     "the request did not reach the broker.")
                return
            else:
                return
        order = by_id.get(row.broker_order_id)
        if order is None:
            return
        state, filled = _status(order), _filled(order)
        row.filled_quantity = filled
        if _num(order.get("averageprice")):
            row.average_fill_price = _num(order.get("averageprice"))

        if state in _DONE:
            row.status = S.FILLED.value
        elif state == "rejected":
            row.status = S.REJECTED.value
            row.error_message = order.get("text") or row.error_message
        elif state in _DEAD:
            # Cancelled with shares already bought is still a position.
            row.status = S.FILLED.value if filled > 0 else S.CANCELLED.value
        elif row.status == S.CANCEL_PENDING.value:
            pass
        elif filled > 0:
            row.status = S.PARTIALLY_FILLED.value
            # Take what filled and stop there: the size is then final and
            # the stop below covers exactly it.
            if await self._cancel(row, row.broker_order_id, "NORMAL"):
                row.status = S.CANCEL_PENDING.value
        else:
            row.status = S.OPEN.value
            timeout = settings.live_entry_timeout_seconds
            if timeout and now - (row.created_at or now) > timeout:
                if await self._cancel(row, row.broker_order_id, "NORMAL"):
                    row.status = S.CANCEL_PENDING.value
                    row.error_message = ("Entry not filled within {:.0f}s — "
                                         "cancelled as stale.".format(timeout))

    async def _supervise_position(self, session: AsyncSession, row: LiveExecutionRow,
                                  orders: List[dict], by_id: Dict[str, dict],
                                  positions: Optional[List[dict]]) -> None:
        quantity = int(row.filled_quantity)
        position = self._position_for(row, positions)

        # ── did an exit fill? ─────────────────────────────────
        for attr, reason, leg in (("stop_order_id", "STOP", "S"),
                                  ("target_order_id", "TARGET", "T"),
                                  ("exit_order_id", row.exit_reason or "EXIT", "X")):
            order_id = getattr(row, attr)
            if not order_id:
                found = self._by_tag(orders, order_tag(row.recommendation_id, leg), None)
                if found is not None and _status(found) not in _DEAD:
                    setattr(row, attr, found.get("orderid"))
                    order_id = found.get("orderid")
            order = by_id.get(order_id) if order_id else None
            if order is None:
                continue
            if _status(order) in _DONE:
                await self._close(row, reason, _num(order.get("averageprice")), keep=attr)
                return
            if _status(order) in _DEAD:
                setattr(row, attr, None)
                if attr == "target_order_id":
                    self._target_unavailable.add(row.id)
                if attr == "exit_order_id":
                    row.status = S.FILLED.value
                    alerts.emit("critical", "EXIT_REJECTED",
                                "An exit order was rejected — the position is still open.",
                                symbol=row.symbol, reason=order.get("text"))
                if attr == "stop_order_id":
                    row.protection_status = "NONE"

        if row.exit_order_id:
            return            # an exit is working; nothing may compete with it

        # ── closed somewhere else? ────────────────────────────
        if positions is not None:
            flat = position is None or int(_num(position.get("netqty"))) == 0
            aged = time.time() - (row.created_at or 0) > FLAT_MIN_AGE_SECONDS
            seen = self._flat_seen.get(row.id, 0) + 1 if (flat and aged) else 0
            self._flat_seen[row.id] = seen
            if seen >= FLAT_CONFIRMATIONS:
                # A stop left resting with no position behind it would open
                # one when it triggers.
                await self._close(row, "EXTERNAL", None, keep=None)
                alerts.emit("warning", "CLOSED_EXTERNALLY",
                            "Position is no longer open at Angel One (closed by hand "
                            "or squared off). Its protective orders were cancelled.",
                            symbol=row.symbol)
                return

        # ── a stop behind every filled share ──────────────────
        if not row.stop_order_id:
            if not await self._place_stop(row, quantity):
                await self._protection_failed(row, positions)
                return
        elif int(row.protected_quantity or 0) != quantity:
            await self._modify_stop(row, quantity, row.stop_price)

        # ── the target ────────────────────────────────────────
        if row.target_price and not row.target_order_id:
            if row.id not in self._target_unavailable:
                await self._place_target(row, quantity)
            else:
                ltp = _num((position or {}).get("ltp"))
                long = row.side == "BUY"
                if ltp and ((ltp >= row.target_price) if long else (ltp <= row.target_price)):
                    await self._flatten(row, "TARGET", positions)
                    return

        await self._ladder(session, row, position)

    # ── protective orders ─────────────────────────────────────

    def _exit_side(self, row: LiveExecutionRow) -> str:
        return "SELL" if row.side == "BUY" else "BUY"

    def _base(self, row: LiveExecutionRow, quantity: int, leg: str) -> dict:
        return {
            "tradingsymbol": row.trading_symbol, "symboltoken": row.instrument_token,
            "exchange": row.exchange, "transactiontype": self._exit_side(row),
            "producttype": PRODUCT_TYPE, "duration": "DAY",
            "quantity": str(int(quantity)),
            "ordertag": order_tag(row.recommendation_id, leg),
        }

    def _stop_params(self, row: LiveExecutionRow, quantity: int, limit: bool) -> dict:
        tick = row.tick_size or 0.05
        trigger = round_to_tick(row.stop_price, tick)
        params = self._base(row, quantity, "S")
        params.update({"variety": "STOPLOSS", "triggerprice": trigger})
        if limit:
            # Marketable: far enough through the trigger to fill in a fast tape.
            buffer = max(row.stop_price * 0.005, tick * 5)
            price = row.stop_price - buffer if row.side == "BUY" else row.stop_price + buffer
            params.update({"ordertype": "STOPLOSS_LIMIT", "price": round_to_tick(price, tick)})
        else:
            params.update({"ordertype": "STOPLOSS_MARKET", "price": "0"})
        return params

    async def _place_stop(self, row: LiveExecutionRow, quantity: int) -> bool:
        # SL-M first; some segments only take a stop-limit, so try that
        # before concluding the position cannot be protected.
        for limit in (False, True):
            try:
                row.stop_order_id = await angel_broker.place_order(
                    self._stop_params(row, quantity, limit))
            except AngelOneOrderError as exc:
                log.warning("stop order rejected (%s): %s",
                            "SL-L" if limit else "SL-M", exc)
                continue
            except AngelOneAmbiguousError:
                found = await self._refetch_by_tag(row, "S")
                if found is None:
                    return True      # unknown, not failed: next pass looks again
                row.stop_order_id = found
            row.protection_status = "PROTECTED"
            row.protected_quantity = quantity
            log.info("[PROTECT] %s stop resting at %s for %s (order %s)",
                     row.symbol, row.stop_price, quantity, row.stop_order_id)
            return True
        return False

    async def _place_target(self, row: LiveExecutionRow, quantity: int) -> None:
        params = self._base(row, quantity, "T")
        params.update({"variety": "NORMAL", "ordertype": "LIMIT",
                       "price": round_to_tick(row.target_price, row.tick_size or 0.05)})
        try:
            row.target_order_id = await angel_broker.place_order(params)
        except AngelOneOrderError as exc:
            self._target_unavailable.add(row.id)
            alerts.emit("info", "TARGET_NOT_RESTING",
                        "Angel One would not rest a target beside the stop; the "
                        "target is being watched here instead. The stop is in place.",
                        symbol=row.symbol, reason=str(exc))
        except AngelOneAmbiguousError:
            row.target_order_id = await self._refetch_by_tag(row, "T")

    async def _modify_stop(self, row: LiveExecutionRow, quantity: int,
                           trigger: float) -> bool:
        params = self._stop_params(row, quantity, limit=False)
        params.pop("ordertag", None)
        params.pop("transactiontype", None)
        params.update({"orderid": row.stop_order_id,
                       "triggerprice": round_to_tick(trigger, row.tick_size or 0.05)})
        try:
            await angel_broker.modify_order(params)
        except AngelOneOrderError as exc:
            alerts.emit("warning", "STOP_MODIFY_FAILED",
                        "Could not move the stop at Angel One; the previous stop "
                        "is still resting.", symbol=row.symbol, error=str(exc))
            return False
        row.protected_quantity = quantity
        return True

    async def _ladder(self, session: AsyncSession, row: LiveExecutionRow,
                      position: Optional[dict]) -> None:
        """Ratchet the *real* stop as the trade runs (analysis/risk.py)."""
        ltp = _num((position or {}).get("ltp"))
        if not ltp or not row.stop_order_id or not row.average_fill_price:
            return
        if not runtime_settings.get().auto_protect:
            return
        from ...analysis import risk as risk_engine
        from ...data.store import store
        from ...instruments import get_instrument
        from ...schemas.positions import Position, PositionSide

        analysis = await session.get(AnalysisRow, row.recommendation_id)
        timeframe = analysis.timeframe if analysis else "15m"
        frame = store.frame(row.symbol, timeframe, 60)
        if frame is None or len(frame) < 20:
            return
        try:
            instrument = get_instrument(row.symbol)
        except KeyError:
            return
        atr = float((frame["high"] - frame["low"]).tail(20).mean())
        long = row.side == "BUY"
        plan = risk_engine.build_plan(Position(
            id=row.id, symbol=row.symbol, timeframe=timeframe,
            side=PositionSide.LONG if long else PositionSide.SHORT,
            entry=row.average_fill_price, stop_loss=row.stop_price,
            initial_stop=row.initial_stop or row.stop_price,
            take_profit=row.target_price, lots=1.0, opened_at=row.created_at,
            protection_tier=row.protection_tier or 0), ltp, instrument, atr)
        if plan is None or not plan.action_available or plan.suggested_stop is None:
            return
        tier = next((t.index for t in plan.tiers if t.active), 0)
        if tier <= (row.protection_tier or 0):
            return
        new_stop = float(round_to_tick(plan.suggested_stop, row.tick_size or 0.05))
        # Only ever tighter, and never through the market.
        if long and not (row.stop_price < new_stop < ltp):
            return
        if not long and not (ltp < new_stop < row.stop_price):
            return
        if await self._modify_stop(row, int(row.filled_quantity), new_stop):
            log.info("[PROTECT] %s stop moved %s -> %s (tier %s)",
                     row.symbol, row.stop_price, new_stop, tier)
            row.stop_price, row.protection_tier = new_stop, tier

    # ── exits ─────────────────────────────────────────────────

    async def _protection_failed(self, row: LiveExecutionRow,
                                 positions: Optional[List[dict]]) -> None:
        row.protection_status = "FAILED"
        trading_mode.activate_kill_switch(
            "A stop could not be placed for {} — position flattened.".format(row.symbol))
        alerts.emit("critical", "STOP_NOT_PLACED",
                    "Angel One refused the stop order. The position is being closed "
                    "at market and live trading is halted.", symbol=row.symbol)
        await self._flatten(row, "NO_STOP", positions)

    async def _flatten(self, row: LiveExecutionRow, reason: str,
                       positions: Optional[List[dict]]) -> bool:
        """Close at market. The other exit orders are cancelled *first* and
        must be confirmed gone — two exits filling would open the opposite
        position."""
        if row.exit_order_id:
            return True
        for attr, variety in (("stop_order_id", "STOPLOSS"), ("target_order_id", "NORMAL")):
            order_id = getattr(row, attr)
            if order_id:
                if not await self._cancel(row, order_id, variety):
                    return False
                setattr(row, attr, None)
        quantity = int(row.filled_quantity or 0)
        position = self._position_for(row, positions)
        if positions is not None:
            # Never sell more than is actually held.
            quantity = min(quantity, abs(int(_num((position or {}).get("netqty")))))
        if quantity <= 0:
            await self._close(row, "EXTERNAL", None, keep=None)
            return True
        params = self._base(row, quantity, "X")
        params.update({"variety": "NORMAL", "ordertype": "MARKET", "price": "0"})
        try:
            row.exit_order_id = await angel_broker.place_order(params)
        except AngelOneAmbiguousError:
            row.exit_order_id = await self._refetch_by_tag(row, "X")
        except AngelOneOrderError as exc:
            alerts.emit("critical", "FLATTEN_FAILED",
                        "Could not close the position at Angel One. CLOSE IT BY HAND.",
                        symbol=row.symbol, quantity=quantity, error=str(exc))
            row.protection_status = "NONE"
            return False
        row.exit_reason, row.status = reason, S.CLOSING.value
        return True

    async def _close(self, row: LiveExecutionRow, reason: str,
                     price: Optional[float], keep: Optional[str]) -> None:
        """Record the exit and cancel whatever exit orders did not fill."""
        for attr, variety in (("stop_order_id", "STOPLOSS"), ("target_order_id", "NORMAL")):
            order_id = getattr(row, attr)
            if order_id and attr != keep:
                await self._cancel(row, order_id, variety)
        row.status, row.protection_status = S.CLOSED.value, "CLOSED"
        row.exit_reason, row.exit_price, row.closed_at = reason, price or None, time.time()
        if price and row.average_fill_price:
            sign = 1.0 if row.side == "BUY" else -1.0
            row.gross_pnl = round(
                (price - row.average_fill_price) * sign * (row.filled_quantity or 0), 2)
        self._flat_seen.pop(row.id, None)
        self._target_unavailable.discard(row.id)
        log.info("[CLOSED] %s %s at %s pnl=%s", row.symbol, reason, price, row.gross_pnl)

    # ── kill switch ───────────────────────────────────────────

    async def cancel_working_entries(self) -> int:
        """Pull every entry not yet filled. Stops are deliberately left."""
        count = 0
        async with self._mutex():
            async with session_scope() as session:
                stmt = select(LiveExecutionRow).where(LiveExecutionRow.status.in_(
                    (S.SUBMITTED.value, S.OPEN.value, S.PARTIALLY_FILLED.value)))
                for row in (await session.execute(stmt)).scalars().all():
                    if row.broker_order_id and await self._cancel(
                            row, row.broker_order_id, "NORMAL"):
                        row.status = S.CANCEL_PENDING.value
                        count += 1
        return count

    async def flatten_all(self, reason: str = "FLATTEN_ALL") -> int:
        count = 0
        async with self._mutex():
            positions = await self._positions()
            async with session_scope() as session:
                stmt = select(LiveExecutionRow).where(LiveExecutionRow.status.in_(ACTIVE_STATES))
                for row in (await session.execute(stmt)).scalars().all():
                    if (row.filled_quantity or 0) > 0 and await self._flatten(
                            row, reason, positions):
                        count += 1
        return count

    # ── small helpers ─────────────────────────────────────────

    @staticmethod
    async def _cancel(row: LiveExecutionRow, order_id: str, variety: str) -> bool:
        try:
            await angel_broker.cancel_order(order_id, variety)
            return True
        except AngelOneOrderError as exc:
            log.warning("cancel of %s for %s failed: %s", order_id, row.symbol, exc)
            return False

    @staticmethod
    def _by_tag(orders: List[dict], tag: Optional[str],
                row: Optional[LiveExecutionRow]) -> Optional[dict]:
        if not tag:
            return None
        match = None
        for order in orders:
            if order.get("ordertag") != tag:
                continue
            if row is not None and (
                    order.get("tradingsymbol") != row.trading_symbol
                    or order.get("transactiontype") != row.side):
                continue
            # A tag is reused when a rejected order is replaced: prefer the
            # one that is still alive.
            if _status(order) not in _DEAD:
                return order
            match = match or order
        return match

    async def _refetch_by_tag(self, row: LiveExecutionRow, leg: str) -> Optional[str]:
        try:
            orders = await angel_broker.get_orders()
        except Exception:  # noqa: BLE001
            return None
        found = self._by_tag(orders, order_tag(row.recommendation_id, leg), None)
        return found.get("orderid") if found and _status(found) not in _DEAD else None

    @staticmethod
    def _position_for(row: LiveExecutionRow,
                      positions: Optional[List[dict]]) -> Optional[dict]:
        for position in positions or []:
            if (str(position.get("symboltoken")) == str(row.instrument_token)
                    and position.get("exchange") == row.exchange):
                return position
        return None


reconciler = LiveReconciler()
