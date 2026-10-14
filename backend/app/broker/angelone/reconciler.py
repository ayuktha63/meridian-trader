"""The live-order supervisor: what the broker says happened, acted on.

`execution.py` submits an entry and stops. This module owns everything
after that, and runs it as one repeated pass over every execution that is
not yet finished:

* follow the entry to a terminal state — fill, partial fill, rejection —
  including one whose submission timed out (found again by its tag) and one
  left in flight by a restart;
* cancel an entry the market never came back to, rather than let a stale
  setup fill hours later, and any entry still working while the kill switch
  is on;
* keep a **stop order resting at the broker** behind every share still held.
  If it cannot be placed the position is flattened, the kill switch is
  tripped and an alert is raised — an unprotected live position is never
  left open;
* rest a target alongside it, and when either one fills cancel the other;
* move the real stop up the protection ladder (`analysis/risk.py`);
* close what is still open shortly before the session ends, ahead of the
  broker's own intraday square-off;
* notice a position closed outside Meridian and pull the orphaned stop
  before it can open a new position by itself.

**What is held is counted, never assumed.** Each pass adds up the fills of
every exit order this execution ever placed — including ones since
cancelled or replaced — and subtracts that from what the entry filled. A
partial fill, two exits that both filled, a target that filled for less
than the stop covers: all of them are just a number that is not the
expected one, and the pass acts on the number.

**Nothing is cancelled on the strength of a missing reading.** If the
positions endpoint cannot be read, that is "unknown", not "flat". If it says
flat but no order in the book explains how, the stop stays and a human is
told.

Every action here either reduces risk or records what already happened, so
none of it is gated on LIVE mode or the kill switch: a tripped kill switch
must never be the reason a position has no stop.

Unverified against a live account, and stated here rather than discovered
later: the exact order parameters in `_stop_params` and `_flatten`, the
broker's status strings, whether `ordertag` is echoed back in the order
book (every lookup also works from the order ids recorded when an order is
placed), and whether Angel One's risk system accepts a resting target
beside a resting stop for the same intraday position (it may ask for extra
margin). A rejected target is handled — the target is then watched and
exited in software — but watch the first live order in Angel One's own app.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import alerts, audit, runtime_settings
from ...config import settings
from ...db.models import LiveExecutionRow
from ...db.session import session_scope
from ...schemas.execution import ExecutionStatus as S
from .. import order_states  # noqa: F401 - registers the status-change check
from ..live_limits import load_ledger, trading_day_start
from ..trading_mode import trading_mode
from .client import AngelOneAmbiguousError, AngelOneOrderError, angel_broker
from .orders import order_tag, parse_broker_time, round_to_tick

log = logging.getLogger("meridian.angelone.reconciler")

ENTRY_STATES = (S.SUBMITTING.value, S.UNKNOWN.value, S.SUBMITTED.value,
                S.OPEN.value, S.PARTIALLY_FILLED.value, S.CANCEL_PENDING.value)
ACTIVE_STATES = ENTRY_STATES + (S.FILLED.value, S.CLOSING.value)

#: How long an order nobody can find is given to appear before concluding
#: the request never reached the broker.
UNKNOWN_GRACE_SECONDS = 120.0
#: After a protective order's submission timed out, how long to wait for it
#: to show up before placing another. Too short risks two stops; too long
#: leaves the position without one.
AMBIGUOUS_GRACE_SECONDS = 8.0
#: Consecutive passes the broker must report no position before a fill we
#: recorded is even considered closed elsewhere — the positions endpoint can
#: lag the order book.
FLAT_CONFIRMATIONS = 3
FLAT_MIN_AGE_SECONDS = 20.0
#: Passes a tracked order must be absent from the order book before it is
#: treated as gone.
MISSING_CONFIRMATIONS = 2
#: A market exit should fill at once. Past this, someone needs to look.
EXIT_STUCK_SECONDS = 30.0
#: Ceiling on the wait between passes while the broker cannot be reached. A
#: failing login retried every three seconds gets an account locked.
BACKOFF_MAX_SECONDS = 300.0
CANCEL_RETRY_SECONDS = 15.0

PRODUCT_TYPE = "INTRADAY"
_DONE = ("complete", "filled")
_DEAD = ("rejected", "cancelled", "canceled")

#: (row attribute, tag suffix, order variety, exit reason when it fills)
LEGS = (
    ("stop_order_id", "S", "STOPLOSS", "STOP"),
    ("target_order_id", "T", "NORMAL", "TARGET"),
    ("exit_order_id", "X", "NORMAL", None),
)
#: A buy-back (or sell-back) that undoes an exit which filled twice.
CORRECTION = "R"


def object_session_of(row) -> Optional[AsyncSession]:
    """The AsyncSession a row is attached to, so an audit entry can commit
    with the change it describes."""
    from sqlalchemy.ext.asyncio import async_object_session
    return async_object_session(row)


def _status(order: Optional[dict]) -> str:
    return ((order or {}).get("status") or "").lower()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _filled(order: dict) -> float:
    return _num(order.get("filledshares") or order.get("fillquantity"))


def _working(order: Optional[dict]) -> bool:
    return order is not None and _status(order) not in _DONE + _DEAD


def _same_product(entry: dict) -> bool:
    """Angel One keeps a position — and reports orders — per product, so the
    same stock held for delivery is a different line from the intraday trade
    being supervised here. An entry that does not say is taken as ours."""
    return (entry.get("producttype") or PRODUCT_TYPE).upper() == PRODUCT_TYPE


@dataclass
class Book:
    """One reading of the broker, shared by every execution in a pass."""
    orders: List[dict]
    positions: Optional[List[dict]]          # None = could not be read
    by_id: Dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_id = {o["orderid"]: o for o in self.orders if o.get("orderid")}


@dataclass
class Holding:
    """What an execution still holds, from the fills in the order book."""
    open_quantity: int
    #: order id -> leg ("S", "T", "X", "R") for every child present today.
    legs: Dict[str, str]
    exit_quantity: float = 0.0
    exit_value: float = 0.0
    fix_quantity: float = 0.0
    fix_value: float = 0.0
    filled_by_leg: Dict[str, float] = field(default_factory=dict)


@dataclass
class _View:
    """One execution, one pass: everything the supervision steps look at."""
    row: LiveExecutionRow
    book: Book
    holding: Holding
    now: float
    position: Optional[dict]
    ltp: Optional[float] = None
    #: Shares the stop and target should cover (see `_coverable`).
    protect_qty: int = 0

    @property
    def long(self) -> bool:
        return self.row.side == "BUY"

    @property
    def settled(self) -> bool:
        """Long enough after the fill for the broker's positions to have
        caught up with its order book."""
        return self.now - (self.row.filled_at or self.now) > FLAT_MIN_AGE_SECONDS

    @property
    def open_qty(self) -> int:
        return self.holding.open_quantity

    @property
    def held(self) -> float:
        """Shares the broker reports on our side of the market."""
        sign = 1 if self.long else -1
        return max(_num((self.position or {}).get("netqty")) * sign, 0.0)

    def order(self, order_id: Optional[str]) -> Optional[dict]:
        return self.book.by_id.get(order_id) if order_id else None


class LiveReconciler:
    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None
        self._task: Optional[asyncio.Task] = None
        # Per-execution scratch state. Losing it in a restart only costs a
        # few passes of re-confirmation; nothing here is a record.
        self._flat_seen: Dict[str, int] = {}
        self._reduced_seen: Dict[str, int] = {}
        self._missing: Dict[Tuple[str, str], int] = {}
        self._ambiguous: Dict[Tuple[str, str], float] = {}
        self._triggered: Dict[str, int] = {}
        self._cancel_asked: Dict[str, float] = {}
        self._target_unavailable: set = set()
        self._corrected: set = set()

    # ── lifecycle ─────────────────────────────────────────────

    def _mutex(self) -> asyncio.Lock:
        if self._lock is None:   # built inside the running loop (py3.9)
            self._lock = asyncio.Lock()
        return self._lock

    def exclusive(self) -> asyncio.Lock:
        """Held by anything that changes a live execution, so two of them
        never act on the same row at once: `async with reconciler.exclusive()`."""
        return self._mutex()

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
        failures = 0
        while True:
            try:
                await self.run_once()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must outlive any one pass
                failures += 1
                alerts.emit("critical", "RECONCILE_FAILED",
                            "Live orders could not be checked against Angel One — "
                            "open positions are not being supervised.",
                            throttle=300.0, error=str(exc))
            base = max(1.0, settings.live_reconcile_interval_seconds)
            await asyncio.sleep(min(base * (2 ** min(failures, 8)), BACKOFF_MAX_SECONDS)
                                if failures else base)

    # ── passes ────────────────────────────────────────────────

    async def _active_ids(self) -> List[str]:
        async with session_scope() as session:
            stmt = select(LiveExecutionRow.id).where(
                LiveExecutionRow.status.in_(ACTIVE_STATES))
            return list((await session.execute(stmt)).scalars().all())

    async def _read_book(self) -> Book:
        orders = await angel_broker.get_orders()
        try:
            positions: Optional[List[dict]] = await angel_broker.get_positions()
        except Exception:  # noqa: BLE001 - None means "unknown", never "flat"
            positions = None
        return Book(orders=orders, positions=positions)

    async def run_once(self) -> int:
        """One pass over every unfinished execution. Returns how many."""
        ids = await self._active_ids()
        if not ids:
            return 0          # nothing live: never touch the broker
        async with self._mutex():
            book = await self._read_book()
            for row_id in ids:
                try:
                    async with session_scope() as session:
                        row = await session.get(LiveExecutionRow, row_id)
                        if row is not None:
                            await self._reconcile(session, row, book)
                except Exception as exc:  # noqa: BLE001 - one bad row must not strand the rest
                    log.exception("reconcile failed for execution %s", row_id)
                    alerts.emit("critical", "SUPERVISION_FAILED",
                                "A live execution could not be checked this pass — "
                                "its position may not be supervised. Look at it in "
                                "Angel One.", throttle=300.0, execution=row_id,
                                error=str(exc))
        return len(ids)

    async def reconcile_now(self, session: AsyncSession, row: LiveExecutionRow) -> None:
        """One pass for one row. The caller already holds `exclusive()`."""
        await self._reconcile(session, row, await self._read_book())

    async def reconcile_row(self, session: AsyncSession, row: LiveExecutionRow) -> None:
        async with self._mutex():
            await self.reconcile_now(session, row)

    # ── one execution ─────────────────────────────────────────

    async def _reconcile(self, session: AsyncSession, row: LiveExecutionRow,
                         book: Book) -> None:
        if row.status in ENTRY_STATES:
            await self._follow_entry(row, book)
        if row.status in ACTIVE_STATES and (row.filled_quantity or 0) > 0:
            await self._supervise_position(row, book)
        row.updated_at = time.time()
        await session.flush()
        if row.status == S.CLOSED.value:
            await self._enforce_daily_loss(session)

    @staticmethod
    async def _enforce_daily_loss(session: AsyncSession) -> None:
        """Trip the kill switch the moment the day's realised loss crosses
        the limit — not at the next attempted entry."""
        limit = settings.live_daily_loss_limit
        if not limit or trading_mode.kill_switch_active:
            return
        lost = -(await load_ledger(session)).realised_today
        if lost >= limit:
            reason = "Daily loss limit reached: {:.2f} lost today (limit {:.2f}).".format(
                lost, limit)
            trading_mode.activate_kill_switch(reason)
            alerts.emit("critical", "DAILY_LOSS_LIMIT", reason)

    # ── the entry order ───────────────────────────────────────

    async def _follow_entry(self, row: LiveExecutionRow, book: Book) -> None:
        now = time.time()
        sent = row.submitted_at or row.created_at or now
        order = self._locate_entry(row, book, now, sent)
        if order is None:
            return

        state, filled = _status(order), _filled(order)
        row.filled_quantity = filled
        if _num(order.get("averageprice")):
            row.average_fill_price = _num(order.get("averageprice"))
        if filled > 0 and not row.filled_at:
            row.filled_at = now

        if state in _DONE:
            row.status = S.FILLED.value
        elif state == "rejected":
            row.status = S.REJECTED.value
            row.error_message = order.get("text") or row.error_message
        elif state in _DEAD:
            # Cancelled with shares already bought is still a position.
            row.status = S.FILLED.value if filled > 0 else S.CANCELLED.value
        else:
            await self._entry_still_working(row, filled, now, sent)

    def _locate_entry(self, row: LiveExecutionRow, book: Book, now: float,
                      sent: float) -> Optional[dict]:
        """The entry order as the broker has it, or None when there is
        nothing to act on this pass (the row is updated if that is final)."""
        if not row.broker_order_id:
            found = self._entry_by_tag(book.orders, row)
            if found is not None:
                row.broker_order_id = found.get("orderid")
                alerts.emit("warning", "ORDER_RECOVERED",
                            "Found an order whose submission never confirmed.",
                            symbol=row.symbol, order_id=row.broker_order_id)
            elif now - sent > UNKNOWN_GRACE_SECONDS:
                row.status = S.REJECTED.value
                row.error_message = ("The order never appeared at Angel One — "
                                     "the request did not reach the broker.")
                return None
            else:
                return None

        order = book.by_id.get(row.broker_order_id)
        if order is not None:
            return order
        if sent < trading_day_start(now):
            # The order book is today's. A DAY order from an earlier session
            # is not working any more, whatever became of it.
            if (row.filled_quantity or 0) > 0:
                row.status = S.FILLED.value
            else:
                row.status = S.CANCELLED.value
                row.error_message = ("Order expired with its session — it is no "
                                     "longer in Angel One's order book.")
        elif now - sent > 30.0:
            alerts.emit("warning", "ORDER_MISSING",
                        "A submitted order is not in Angel One's order book.",
                        throttle=300.0, symbol=row.symbol, order_id=row.broker_order_id)
        return None

    async def _entry_still_working(self, row: LiveExecutionRow, filled: float,
                                   now: float, sent: float) -> None:
        if row.status == S.CANCEL_PENDING.value:
            # Asked, not yet confirmed. Ask again if it is taking too long.
            if now - self._cancel_asked.get(row.id, now) > CANCEL_RETRY_SECONDS:
                await self._cancel_entry(row, None)
        elif filled > 0:
            row.status = S.PARTIALLY_FILLED.value
            # Take what filled and stop there: the size is then final and
            # the stop covers exactly it.
            await self._cancel_entry(row, None)
        else:
            row.status = S.OPEN.value
            timeout = settings.live_entry_timeout_seconds
            if trading_mode.kill_switch_active:
                await self._cancel_entry(row, "Kill switch is on — entry cancelled.")
            elif timeout and now - sent > timeout:
                await self._cancel_entry(
                    row, "Entry not filled within {:.0f}s — cancelled as stale.".format(timeout))

    async def _cancel_entry(self, row: LiveExecutionRow, note: Optional[str]) -> bool:
        if not await self._cancel(row, row.broker_order_id, "NORMAL"):
            return False
        self._cancel_asked[row.id] = time.time()
        row.status = S.CANCEL_PENDING.value
        if note:
            row.error_message = note
        return True

    # ── the position ──────────────────────────────────────────

    def _known_legs(self, row: LiveExecutionRow, book: Book) -> Dict[str, str]:
        """order id -> leg, for every exit-side order this execution owns."""
        tags = {order_tag(row.recommendation_id, leg): leg
                for leg in ("S", "T", "X", CORRECTION)}
        legs: Dict[str, str] = {}
        for entry in row.child_order_ids or []:
            leg, _, order_id = entry.partition(":")
            legs[order_id] = leg
        # An order carrying one of our tags that we have no record of: its
        # submission timed out, or this process restarted between sending it
        # and writing it down.
        for order in book.orders:
            order_id = order.get("orderid")
            if order_id and order_id not in legs and order.get("ordertag") in tags:
                legs[order_id] = tags[order["ordertag"]]
        recorded = sorted("{}:{}".format(leg, order_id) for order_id, leg in legs.items())
        if recorded != sorted(row.child_order_ids or []):
            row.child_order_ids = recorded
        return legs

    def _holding(self, row: LiveExecutionRow, book: Book) -> Holding:
        """Count what is still held from the fills in the order book."""
        exit_side = self._exit_side(row)
        holding = Holding(open_quantity=0, legs=self._known_legs(row, book))
        for order_id, leg in holding.legs.items():
            order = book.by_id.get(order_id)
            if order is None:
                continue
            quantity = _filled(order)
            value = quantity * _num(order.get("averageprice"))
            if leg == CORRECTION and order.get("transactiontype") == row.side:
                holding.fix_quantity += quantity
                holding.fix_value += value
            elif order.get("transactiontype") == exit_side:
                holding.exit_quantity += quantity
                holding.exit_value += value
                holding.filled_by_leg[leg] = holding.filled_by_leg.get(leg, 0.0) + quantity
        # Never let recorded exits go down: an order missing from one
        # reading must not make the position look bigger than it is.
        gross = max(holding.exit_quantity, row.exit_quantity or 0.0)
        row.exit_quantity = gross
        holding.open_quantity = int(round(
            (row.filled_quantity or 0.0) - gross + holding.fix_quantity))
        return holding

    async def _supervise_position(self, row: LiveExecutionRow, book: Book) -> None:
        now = time.time()
        if not row.filled_at:
            # Only a row written before fills were timed has none. It filled
            # around when it was sent — calling it fresh would have it treated
            # as a fill still settling, and protected on that basis.
            row.filled_at = row.submitted_at or row.created_at or now
        holding = self._holding(row, book)
        for order_id, variety in self._track_legs(row, book, holding):
            # Two working orders for one leg — a submission that timed out,
            # was re-sent, and then both landed. One has to go before both fill.
            if await self._cancel(row, order_id, variety):
                alerts.emit("warning", "DUPLICATE_ORDER",
                            "Cancelled a duplicate protective order.",
                            symbol=row.symbol, order_id=order_id)

        if holding.open_quantity < 0:
            await self._correct_overexit(row, book, -holding.open_quantity)
            return
        if holding.open_quantity == 0:
            await self._finish(row, book, holding)
            return

        view = _View(row=row, book=book, holding=holding, now=now,
                     position=self._position_for(row, book.positions))
        view.protect_qty = self._coverable(view)
        # Each step answers "is this execution done for this pass?" — in the
        # order that matters: nothing competes with an exit already working,
        # and no order is placed for a position the broker says is not there.
        for step in (self._exit_in_flight, self._broker_disagrees,
                     self._session_ending, self._keep_stop, self._keep_target):
            if await step(view):
                return
        await self._ladder(view)

    async def _exit_in_flight(self, view: "_View") -> bool:
        row = view.row
        exit_order = view.order(row.exit_order_id)
        if _working(exit_order) or self._awaiting(row, "X", view.now):
            asked = parse_broker_time((exit_order or {}).get("updatetime")) or view.now
            if view.now - asked > EXIT_STUCK_SECONDS:
                alerts.emit("critical", "EXIT_NOT_FILLING",
                            "A market exit has not filled. Check Angel One.",
                            throttle=120.0, symbol=row.symbol, quantity=view.open_qty)
            return True
        if row.status == S.CLOSING.value:
            row.status = S.FILLED.value     # the exit died; supervise again
        return False

    async def _broker_disagrees(self, view: "_View") -> bool:
        if view.book.positions is None:
            return False                    # unknown is not flat
        if view.held > 0:
            self._flat_seen.pop(view.row.id, None)
            return False
        if await self._closed_elsewhere(view.row, view.book, view.holding, view.now):
            return True
        # Not there, and nothing yet says why. Straight after a fill the
        # positions endpoint may only be behind the order book, and the stop
        # cannot wait for it. Any later, an order placed now would be an order
        # for a position the broker says does not exist — so place none, and
        # leave what is resting alone, until the two agree again.
        return view.settled

    def _coverable(self, view: "_View") -> int:
        """How many shares the stop and target should cover: what our fills
        say is held, but never more than Angel One itself shows.

        Selling part of a position by hand leaves the two apart, and a stop
        still resting for the full size would sell shares that are no longer
        there — opening a short with nothing behind it.
        """
        row = view.row
        fewer = (view.book.positions is not None and view.settled
                 and 0 < view.held < view.open_qty)
        if not fewer:
            self._reduced_seen.pop(row.id, None)
            return view.open_qty
        self._reduced_seen[row.id] = self._reduced_seen.get(row.id, 0) + 1
        if self._reduced_seen[row.id] < MISSING_CONFIRMATIONS:
            return view.open_qty            # one reading is not proof
        alerts.emit("warning", "POSITION_REDUCED",
                    "Angel One shows fewer shares than this trade's fills account "
                    "for — part of it was closed outside Meridian. The stop and "
                    "target now cover what is there.",
                    throttle=300.0, symbol=row.symbol, expected=view.open_qty,
                    held=int(view.held))
        return int(view.held)

    async def _session_ending(self, view: "_View") -> bool:
        return (self._near_close(view.row, view.now)
                and await self._flatten(view.row, "EOD", view.book, view.open_qty))

    async def _keep_stop(self, view: "_View") -> bool:
        """A stop behind every share still held."""
        row, long = view.row, view.long
        view.ltp = await self._ltp(row, view.position)
        through = bool(view.ltp) and ((view.ltp <= row.stop_price) if long
                                      else (view.ltp >= row.stop_price))
        stop = view.order(row.stop_order_id)
        if row.stop_order_id and stop is None:
            return False      # tracked but absent from this reading: not "none"
        if stop is None:
            if self._awaiting(row, "S", view.now):
                return False  # one was sent and may yet appear; do not send two
            if through:
                # Price is already past the stop: a stop order placed now
                # would be refused. Nothing has malfunctioned — get out.
                return await self._flatten(row, "STOP", view.book, view.open_qty)
            if not await self._place_stop(row, view.protect_qty):
                await self._protection_failed(row, view.book, view.open_qty)
                return True
            return False
        if _status(stop) == "open" and (_filled(stop) > 0 or through):
            # Triggered but not filled: a stop-limit the market ran past.
            self._triggered[row.id] = self._triggered.get(row.id, 0) + 1
            return (self._triggered[row.id] >= 2
                    and await self._flatten(row, "STOP", view.book, view.open_qty))
        self._triggered.pop(row.id, None)
        if _filled(stop) == 0 and int(_num(stop.get("quantity"))) != view.protect_qty:
            await self._modify_stop(row, stop, quantity=view.protect_qty)
        return False

    async def _keep_target(self, view: "_View") -> bool:
        row, long = view.row, view.long
        if not row.target_price:
            return False
        target = view.order(row.target_order_id)
        if row.target_order_id and target is None:
            return False      # tracked but absent from this reading
        if target is not None:
            if _filled(target) == 0 and int(_num(target.get("quantity"))) != view.protect_qty:
                await self._modify_target(row, view.protect_qty)
            return False
        if row.id in self._target_unavailable:
            # The broker would not rest one, so it is taken by price instead.
            reached = bool(view.ltp) and ((view.ltp >= row.target_price) if long
                                          else (view.ltp <= row.target_price))
            return reached and await self._flatten(row, "TARGET", view.book, view.open_qty)
        if not self._awaiting(row, "T", view.now):
            await self._place_target(row, view.protect_qty)
        return False

    def _track_legs(self, row: LiveExecutionRow, book: Book,
                    holding: Holding) -> List[Tuple[str, str]]:
        """Keep each tracked order id pointing at an order that is working.
        Returns the (order id, variety) of any working duplicates to cancel."""
        duplicates: List[Tuple[str, str]] = []
        for attr, leg, variety, _ in LEGS:
            self._release_if_gone(row, book, attr, leg)
            if not getattr(row, attr):
                self._adopt(row, book, holding, attr, leg)
            duplicates += [
                (order_id, variety) for order_id, its_leg in holding.legs.items()
                if its_leg == leg and order_id != getattr(row, attr)
                and _working(book.by_id.get(order_id))]
        return duplicates

    def _release_if_gone(self, row: LiveExecutionRow, book: Book, attr: str,
                         leg: str) -> None:
        order_id = getattr(row, attr)
        if not order_id:
            return
        key = (row.id, attr)
        order = book.by_id.get(order_id)
        if order is None:
            self._missing[key] = self._missing.get(key, 0) + 1
            if self._missing[key] < MISSING_CONFIRMATIONS:
                return
            self._drop(row, attr, leg)
        elif not _working(order):
            self._drop(row, attr, leg)
            if _status(order) in _DEAD and _filled(order) == 0:
                self._leg_refused(row, leg, order)
        self._missing.pop(key, None)

    def _adopt(self, row: LiveExecutionRow, book: Book, holding: Holding,
               attr: str, leg: str) -> None:
        """Pick up a working order of this leg that we own but are not
        tracking — one whose submission timed out and then landed."""
        adopted = next((order_id for order_id, its_leg in holding.legs.items()
                        if its_leg == leg and _working(book.by_id.get(order_id))), None)
        if adopted is None:
            return
        setattr(row, attr, adopted)
        self._ambiguous.pop((row.id, leg), None)
        if leg == "S":
            row.protection_status = "PROTECTED"
            row.protected_quantity = _num(book.by_id[adopted].get("quantity"))

    def _drop(self, row: LiveExecutionRow, attr: str, leg: str) -> None:
        setattr(row, attr, None)
        if leg == "S":
            row.protection_status = "NONE"

    def _leg_refused(self, row: LiveExecutionRow, leg: str, order: dict) -> None:
        if leg == "T":
            self._target_unavailable.add(row.id)
        elif leg == "X":
            alerts.emit("critical", "EXIT_REJECTED",
                        "An exit order was rejected — the position is still open.",
                        throttle=60.0, symbol=row.symbol, reason=order.get("text"))

    def _awaiting(self, row: LiveExecutionRow, leg: str, now: float) -> bool:
        asked = self._ambiguous.get((row.id, leg))
        return asked is not None and now - asked < AMBIGUOUS_GRACE_SECONDS

    @staticmethod
    def _near_close(row: LiveExecutionRow, now: float) -> bool:
        window = settings.live_squareoff_minutes_before_close
        if not window:
            return False
        from ...data.sessions import minutes_to_close
        from ...instruments import get_instrument
        try:
            left = minutes_to_close(now, get_instrument(row.symbol).asset_class)
        except KeyError:
            return False
        return left is not None and left <= window

    async def _ltp(self, row: LiveExecutionRow, position: Optional[dict]) -> Optional[float]:
        price = _num((position or {}).get("ltp"))
        if price > 0:
            return price
        try:
            quote = await angel_broker.get_quote(
                row.exchange, row.trading_symbol, row.instrument_token)
            return _num((quote or {}).get("ltp")) or None
        except Exception:  # noqa: BLE001 - no price means no price-based action
            return None

    # ── protective orders ─────────────────────────────────────

    @staticmethod
    def _exit_side(row: LiveExecutionRow) -> str:
        return "SELL" if row.side == "BUY" else "BUY"

    def _base(self, row: LiveExecutionRow, quantity: int, leg: str) -> dict:
        return {
            "tradingsymbol": row.trading_symbol, "symboltoken": row.instrument_token,
            "exchange": row.exchange,
            "transactiontype": row.side if leg == CORRECTION else self._exit_side(row),
            "producttype": PRODUCT_TYPE, "duration": "DAY",
            "quantity": str(int(quantity)),
            "ordertag": order_tag(row.recommendation_id, leg),
        }

    def _stop_params(self, row: LiveExecutionRow, quantity: int, limit: bool,
                     trigger: Optional[float] = None) -> dict:
        tick = row.tick_size or 0.05
        level = row.stop_price if trigger is None else trigger
        long = row.side == "BUY"
        params = self._base(row, quantity, "S")
        params.update({"variety": "STOPLOSS", "triggerprice": round_to_tick(level, tick)})
        if limit:
            # Marketable: far enough through the trigger to fill in a fast tape.
            buffer = max(level * 0.005, tick * 5)
            price = level - buffer if long else level + buffer
            params.update({"ordertype": "STOPLOSS_LIMIT",
                           "price": round_to_tick(price, tick, "down" if long else "up")})
        else:
            params.update({"ordertype": "STOPLOSS_MARKET", "price": "0"})
        return params

    def _remember(self, row: LiveExecutionRow, leg: str, order_id: str) -> None:
        row.child_order_ids = sorted(set(row.child_order_ids or [])
                                     | {"{}:{}".format(leg, order_id)})

    async def _send(self, row: LiveExecutionRow, leg: str, params: dict) -> Optional[str]:
        """Place a child order. Returns its id, or None when the outcome is
        unknown (the grace period then keeps a second one from being sent).
        Raises AngelOneOrderError when the broker refused it."""
        try:
            order_id = await angel_broker.place_order(params)
        except AngelOneAmbiguousError:
            order_id = await self._refetch_by_tag(row, leg)
            if order_id is None:
                self._ambiguous[(row.id, leg)] = time.time()
                return None
        self._ambiguous.pop((row.id, leg), None)
        self._remember(row, leg, order_id)
        return order_id

    async def _place_stop(self, row: LiveExecutionRow, quantity: int) -> bool:
        """False only when the broker refused every kind of stop."""
        # SL-M first; some segments only take a stop-limit, so try that
        # before concluding the position cannot be protected.
        for limit in (False, True):
            try:
                order_id = await self._send(row, "S", self._stop_params(row, quantity, limit))
            except AngelOneOrderError as exc:
                log.warning("stop order rejected (%s): %s",
                            "SL-L" if limit else "SL-M", exc)
                continue
            if order_id is None:
                return True      # unknown, not refused: the next pass looks again
            row.stop_order_id = order_id
            row.protection_status = "PROTECTED"
            row.protected_quantity = quantity
            log.info("[PROTECT] %s stop resting at %s for %s (order %s)",
                     row.symbol, row.stop_price, quantity, order_id)
            await audit.record("stop_placed", row.recommendation_id,
                               object_session_of(row), symbol=row.symbol,
                               order_id=order_id, trigger=row.stop_price,
                               quantity=quantity)
            return True
        return False

    async def _place_target(self, row: LiveExecutionRow, quantity: int) -> None:
        long = row.side == "BUY"
        params = self._base(row, quantity, "T")
        params.update({"variety": "NORMAL", "ordertype": "LIMIT",
                       "price": round_to_tick(row.target_price, row.tick_size or 0.05,
                                              "down" if long else "up")})
        try:
            row.target_order_id = await self._send(row, "T", params)
        except AngelOneOrderError as exc:
            self._target_unavailable.add(row.id)
            alerts.emit("info", "TARGET_NOT_RESTING",
                        "Angel One would not rest a target beside the stop; the "
                        "target is being watched here instead. The stop is in place.",
                        symbol=row.symbol, reason=str(exc))

    async def _modify_stop(self, row: LiveExecutionRow, order: dict,
                           quantity: Optional[int] = None,
                           trigger: Optional[float] = None) -> bool:
        """Change the resting stop's size or level, keeping its order type."""
        size = int(_num(order.get("quantity"))) if quantity is None else quantity
        params = self._stop_params(
            row, size, limit=order.get("ordertype") == "STOPLOSS_LIMIT", trigger=trigger)
        params.pop("ordertag", None)
        params.pop("transactiontype", None)
        params["orderid"] = row.stop_order_id
        try:
            await angel_broker.modify_order(params)
        except AngelOneOrderError as exc:
            alerts.emit("warning", "STOP_MODIFY_FAILED",
                        "Could not change the stop at Angel One; the previous stop "
                        "is still resting.", throttle=120.0, symbol=row.symbol,
                        error=str(exc))
            return False
        row.protected_quantity = size
        return True

    async def _modify_target(self, row: LiveExecutionRow, quantity: int) -> None:
        long = row.side == "BUY"
        params = self._base(row, quantity, "T")
        params.pop("ordertag", None)
        params.pop("transactiontype", None)
        params.update({"variety": "NORMAL", "ordertype": "LIMIT",
                       "orderid": row.target_order_id,
                       "price": round_to_tick(row.target_price, row.tick_size or 0.05,
                                              "down" if long else "up")})
        try:
            await angel_broker.modify_order(params)
        except AngelOneOrderError as exc:
            log.warning("target resize for %s failed: %s", row.symbol, exc)

    async def _ladder(self, view: "_View") -> None:
        """Ratchet the *real* stop as the trade runs (analysis/risk.py)."""
        row, ltp = view.row, view.ltp
        stop = view.order(row.stop_order_id)
        if stop is None or not ltp or not row.average_fill_price or _filled(stop) > 0:
            return
        if not runtime_settings.get().auto_protect:
            return
        from ...analysis import risk as risk_engine
        from ...data.store import store
        from ...instruments import get_instrument
        from ...schemas.positions import Position, PositionSide

        timeframe = row.timeframe or "15m"
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
            take_profit=row.target_price, lots=1.0,
            opened_at=row.filled_at or row.created_at,
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
        if await self._modify_stop(row, stop, trigger=new_stop):
            log.info("[PROTECT] %s stop moved %s -> %s (tier %s)",
                     row.symbol, row.stop_price, new_stop, tier)
            await audit.record("stop_moved", row.recommendation_id,
                               object_session_of(row), symbol=row.symbol,
                               old=row.stop_price, new=new_stop, tier=tier)
            row.stop_price, row.protection_tier = new_stop, tier

    # ── exits ─────────────────────────────────────────────────

    async def _protection_failed(self, row: LiveExecutionRow, book: Book,
                                 open_qty: int) -> None:
        row.protection_status = "FAILED"
        trading_mode.activate_kill_switch(
            "A stop could not be placed for {} — position flattened.".format(row.symbol))
        alerts.emit("critical", "STOP_NOT_PLACED",
                    "Angel One refused the stop order. The position is being closed "
                    "at market and live trading is halted.",
                    throttle=60.0, symbol=row.symbol)
        await self._flatten(row, "NO_STOP", book, open_qty)

    async def _cancel_children(self, row: LiveExecutionRow, book: Book,
                               everything: bool = False) -> bool:
        """Cancel this execution's working exit orders.

        By default — on the way to a market exit — the target goes first and
        then the stop, stopping at the first one that will not cancel: if the
        target cannot be pulled it may be filling, and the stop must stay
        where it is. With `everything`, the position is already gone and all
        of them, a working exit included, are cancelled regardless.
        """
        legs = reversed(LEGS) if everything else (LEGS[1], LEGS[0])
        confirmed = True
        for attr, leg, variety, _ in legs:
            order_id = getattr(row, attr)
            if not order_id:
                continue
            order = book.by_id.get(order_id)
            if order is not None and not _working(order):
                self._drop(row, attr, leg)
            elif await self._cancel(row, order_id, variety):
                self._drop(row, attr, leg)
            elif everything:
                confirmed = False
            else:
                return False
        return confirmed

    async def _flatten(self, row: LiveExecutionRow, reason: str, book: Book,
                       open_qty: int) -> bool:
        """Close what is held at market. True once an exit is on its way.

        The other exit orders are cancelled *first*: two exits filling would
        open the opposite position. And the size comes from two sources that
        must agree — our count of the fills, and the broker's position — so a
        market order is never sent for shares that are not there.
        """
        if row.exit_order_id or self._awaiting(row, "X", time.time()):
            return True
        quantity = open_qty
        if book.positions is not None:
            sign = 1 if row.side == "BUY" else -1
            held = max(_num((self._position_for(row, book.positions) or {})
                            .get("netqty")) * sign, 0.0)
            if held <= 0:
                return False      # nothing there to sell; not this path's call
            quantity = min(quantity, int(held))
        if quantity <= 0 or not await self._cancel_children(row, book):
            return False
        params = self._base(row, quantity, "X")
        params.update({"variety": "NORMAL", "ordertype": "MARKET", "price": "0"})
        try:
            order_id = await self._send(row, "X", params)
        except AngelOneOrderError as exc:
            alerts.emit("critical", "FLATTEN_FAILED",
                        "Could not close the position at Angel One. CLOSE IT BY HAND.",
                        throttle=60.0, symbol=row.symbol, quantity=quantity,
                        error=str(exc))
            return False
        row.exit_order_id = order_id
        row.exit_reason, row.status = reason, S.CLOSING.value
        await audit.record("exit_sent", row.recommendation_id, object_session_of(row),
                           symbol=row.symbol, reason=reason, quantity=quantity,
                           order_id=order_id)
        return True

    async def _correct_overexit(self, row: LiveExecutionRow, book: Book,
                                excess: int) -> None:
        """More was sold than was held — two exits both filled — which leaves
        the opposite position open with nothing behind it. Buy the excess
        back, but only if the broker's own position confirms it is there."""
        await self._cancel_children(row, book, everything=True)
        if row.id in self._corrected:
            return
        sign = 1 if row.side == "BUY" else -1
        position = self._position_for(row, book.positions)
        reverse = max(-_num((position or {}).get("netqty")) * sign, 0.0)
        if book.positions is None or reverse < excess:
            alerts.emit("critical", "OVER_EXIT",
                        "Exit orders filled for more than was held, but the broker's "
                        "position does not confirm it. Not acting — check Angel One.",
                        throttle=120.0, symbol=row.symbol, excess=excess,
                        broker_reverse=reverse if book.positions is not None else None)
            return
        params = self._base(row, excess, CORRECTION)
        params.update({"variety": "NORMAL", "ordertype": "MARKET", "price": "0"})
        try:
            order_id = await self._send(row, CORRECTION, params)
        except AngelOneOrderError as exc:
            alerts.emit("critical", "OVER_EXIT",
                        "Two exits filled and the correcting order was refused. "
                        "A reverse position is open — CLOSE IT BY HAND.",
                        throttle=60.0, symbol=row.symbol, excess=excess, error=str(exc))
            return
        self._corrected.add(row.id)
        alerts.emit("critical", "OVER_EXIT",
                    "Two exit orders both filled; the excess has been bought back.",
                    symbol=row.symbol, excess=excess, order_id=order_id)

    def _foreign_exits(self, row: LiveExecutionRow, book: Book,
                       holding: Holding) -> Tuple[float, float]:
        """Fills on this instrument's exit side that are not ours — a manual
        close in Angel One's app, or the broker's square-off. (quantity, value)"""
        exit_side = self._exit_side(row)
        since = (row.filled_at or 0.0) - 60.0
        quantity = value = 0.0
        for order in book.orders:
            if (order.get("orderid") in holding.legs
                    or str(order.get("symboltoken")) != str(row.instrument_token)
                    or order.get("transactiontype") != exit_side or _filled(order) <= 0
                    or not _same_product(order)):
                continue
            stamp = parse_broker_time(order.get("updatetime") or order.get("exchtime"))
            if stamp is not None and stamp < since:
                continue
            quantity += _filled(order)
            value += _filled(order) * _num(order.get("averageprice"))
        return quantity, value

    async def _closed_elsewhere(self, row: LiveExecutionRow, book: Book,
                                holding: Holding, now: float) -> bool:
        """The broker shows no position where our count says there is one.

        Closed by hand, or squared off — or the reading is wrong. Only the
        first two justify cancelling a stop, so act only when the order book
        explains the exit, or the session the position belonged to is over.
        """
        aged = now - (row.filled_at or now) > FLAT_MIN_AGE_SECONDS
        self._flat_seen[row.id] = self._flat_seen.get(row.id, 0) + 1 if aged else 0
        if self._flat_seen[row.id] < FLAT_CONFIRMATIONS:
            return False

        foreign_qty, foreign_value = self._foreign_exits(row, book, holding)
        session_over = (row.filled_at or now) < trading_day_start(now)
        if foreign_qty < holding.open_quantity and not session_over:
            alerts.emit("warning", "POSITION_MISMATCH",
                        "Angel One shows no position, but no order explains how it "
                        "was closed. The stop is being left in place — check the account.",
                        throttle=300.0, symbol=row.symbol, expected=holding.open_quantity)
            return False

        # A stop left resting with no position behind it would open one
        # when it triggers.
        await self._cancel_children(row, book, everything=True)
        holding.exit_quantity += foreign_qty
        holding.exit_value += foreign_value
        await self._finish(row, book, holding,
                           reason="EXTERNAL" if foreign_qty else "SQUARED_OFF")
        alerts.emit("warning", "CLOSED_EXTERNALLY",
                    "Position is no longer open at Angel One (closed by hand or "
                    "squared off). Its protective orders were cancelled.",
                    symbol=row.symbol)
        return True

    async def _finish(self, row: LiveExecutionRow, book: Book, holding: Holding,
                      reason: Optional[str] = None) -> None:
        """Everything held has been sold. Cancel what is left and record it."""
        await self._cancel_children(row, book, everything=True)
        if reason is None:
            leg = max(holding.filled_by_leg, key=holding.filled_by_leg.get, default="X")
            reason = {"S": "STOP", "T": "TARGET"}.get(leg) or row.exit_reason or "EXIT"
        quantity = holding.exit_quantity - holding.fix_quantity
        value = holding.exit_value - holding.fix_value
        price = value / quantity if quantity > 0 else None
        row.status, row.protection_status = S.CLOSED.value, "CLOSED"
        row.exit_reason, row.exit_price, row.closed_at = reason, price, time.time()
        if price and row.average_fill_price:
            sign = 1.0 if row.side == "BUY" else -1.0
            row.gross_pnl = round((value - row.average_fill_price * quantity) * sign, 2)
        for store_ in (self._flat_seen, self._reduced_seen, self._triggered,
                       self._cancel_asked):
            store_.pop(row.id, None)
        for store_ in (self._missing, self._ambiguous):
            for key in [k for k in store_ if k[0] == row.id]:
                store_.pop(key, None)
        self._target_unavailable.discard(row.id)
        self._corrected.discard(row.id)
        log.info("[CLOSED] %s %s at %s pnl=%s", row.symbol, reason, price, row.gross_pnl)
        await audit.record("position_closed", row.recommendation_id,
                           object_session_of(row), symbol=row.symbol,
                           reason=reason, exit_price=price, gross_pnl=row.gross_pnl)

    # ── kill switch ───────────────────────────────────────────

    async def cancel_working_entries(self) -> int:
        """Pull every entry not yet filled, now rather than on the next
        pass. Stops are deliberately left."""
        count = 0
        async with self._mutex():
            async with session_scope() as session:
                stmt = select(LiveExecutionRow).where(LiveExecutionRow.status.in_(
                    (S.SUBMITTED.value, S.OPEN.value, S.PARTIALLY_FILLED.value)))
                for row in (await session.execute(stmt)).scalars().all():
                    if row.broker_order_id and await self._cancel_entry(
                            row, "Kill switch is on — entry cancelled."):
                        count += 1
        return count

    async def flatten_all(self, reason: str = "FLATTEN_ALL") -> int:
        count = 0
        async with self._mutex():
            book = await self._read_book()
            async with session_scope() as session:
                stmt = select(LiveExecutionRow).where(LiveExecutionRow.status.in_(ACTIVE_STATES))
                for row in (await session.execute(stmt)).scalars().all():
                    if (row.filled_quantity or 0) <= 0:
                        continue
                    open_qty = self._holding(row, book).open_quantity
                    if open_qty > 0 and await self._flatten(row, reason, book, open_qty):
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
    def _entry_by_tag(orders: List[dict], row: LiveExecutionRow) -> Optional[dict]:
        match = None
        for order in orders:
            if (not row.order_tag or order.get("ordertag") != row.order_tag
                    or order.get("tradingsymbol") != row.trading_symbol
                    or order.get("transactiontype") != row.side):
                continue
            # A rejected attempt and its retry share a tag: prefer the one
            # that is still alive.
            if _status(order) not in _DEAD:
                return order
            match = match or order
        return match

    async def _refetch_by_tag(self, row: LiveExecutionRow, leg: str) -> Optional[str]:
        try:
            orders = await angel_broker.get_orders()
        except Exception:  # noqa: BLE001
            return None
        tag = order_tag(row.recommendation_id, leg)
        known = {entry.partition(":")[2] for entry in row.child_order_ids or []}
        for order in orders:
            if (order.get("ordertag") == tag and order.get("orderid") not in known
                    and _status(order) not in _DEAD):
                return order.get("orderid")
        return None

    @staticmethod
    def _position_for(row: LiveExecutionRow,
                      positions: Optional[List[dict]]) -> Optional[dict]:
        for position in positions or []:
            if (str(position.get("symboltoken")) == str(row.instrument_token)
                    and position.get("exchange") == row.exchange
                    and _same_product(position)):
                return position
        return None


reconciler = LiveReconciler()
