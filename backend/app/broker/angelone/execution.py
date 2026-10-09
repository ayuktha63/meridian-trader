"""AngelOneExecutionService — turns a validated recommendation into a real
order, and nothing else.

Boundary, strictly kept: this module never decides whether a trade *should*
happen (that's `PreTradeRiskGate`) and never talks to the SmartAPI SDK
directly (that's `AngelOneBrokerService`). It only orchestrates the two in
the fixed order the spec requires, and persists exactly what happened.

This module places the *entry* only. Everything after it — following the
order to a fill, resting a stop and target behind that fill, recovering
orders in flight across a restart — is `reconciler.py`, which is handed the
row the moment the entry is submitted.

  - The intent is committed, with its idempotency tag, *before* the broker
    is called. If the process dies mid-request the reconciler still knows an
    order may exist and can find it by tag.
  - No retry of any kind on an ambiguous outcome — see `_submit`.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import AnalysisRow, LiveExecutionRow
from ...runtime_settings import get as get_runtime_settings
from ...schemas.execution import ExecutionRecord, ExecutionStatus
from ..risk_gate import GateContext, Recommendation, risk_gate
from .client import AngelOneAmbiguousError, AngelOneOrderError, angel_broker
from .orders import find_order_by_tag, order_tag

log = logging.getLogger("meridian.angelone.execution")

#: Intraday (MIS): the broker squares the position off before the close, so
#: nothing here can be carried overnight unprotected by a DAY stop order.
PRODUCT_TYPE = "INTRADAY"


class RecommendationNotFound(RuntimeError):
    pass


class RecommendationNotExecutable(RuntimeError):
    """The recommendation exists but isn't a trade — e.g. it was a WAIT."""


def _row_to_record(row: LiveExecutionRow) -> ExecutionRecord:
    return ExecutionRecord(
        id=row.id, recommendation_id=row.recommendation_id, broker=row.broker,
        symbol=row.symbol, side=row.side, exchange=row.exchange,
        trading_symbol=row.trading_symbol, instrument_token=row.instrument_token,
        requested_quantity=row.requested_quantity, filled_quantity=row.filled_quantity,
        requested_price=row.requested_price, average_fill_price=row.average_fill_price,
        stop_price=row.stop_price, target_price=row.target_price,
        risk_amount=row.risk_amount, status=ExecutionStatus(row.status),
        broker_order_id=row.broker_order_id, error_code=row.error_code,
        error_message=row.error_message,
        protection_status=row.protection_status or "NONE",
        stop_order_id=row.stop_order_id, target_order_id=row.target_order_id,
        protection_tier=row.protection_tier or 0, exit_price=row.exit_price,
        exit_reason=row.exit_reason, gross_pnl=row.gross_pnl,
        closed_at=row.closed_at, created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _recommendation_from_row(row: AnalysisRow) -> Recommendation:
    plan = (row.payload or {}).get("plan") or {}
    confidence = (row.payload or {}).get("confidence") or {}
    # row.decision is already the Decision enum's .value ("BUY"/"SELL"/"WAIT"),
    # written verbatim by record_analysis — no translation needed here.
    return Recommendation(
        id=row.id, symbol=row.symbol, side=row.decision,
        entry=plan.get("entry"), stop_loss=plan.get("stop_loss"),
        take_profit=plan.get("take_profit"), risk_amount=plan.get("risk_amount"),
        suggested_lots=plan.get("suggested_lots"),
        confidence=confidence.get("final_confidence", row.confidence),
    )


def _extract_available_funds(funds: Optional[dict]) -> Optional[float]:
    """Angel One's rmsLimit response. `availablecash` is the conservative,
    non-leveraged figure — deliberately not `net` or a margin-inclusive
    number, per the spec's instruction to stay conservative."""
    if not funds:
        return None
    for key in ("availablecash", "net"):
        value = funds.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


class AngelOneExecutionService:
    async def get_execution(self, session: AsyncSession,
                            recommendation_id: str) -> Optional[ExecutionRecord]:
        row = await self._get_row(session, recommendation_id)
        return _row_to_record(row) if row else None

    @staticmethod
    async def list_executions(session: AsyncSession, limit: int = 100) -> list:
        stmt = (select(LiveExecutionRow)
                .order_by(LiveExecutionRow.created_at.desc()).limit(limit))
        return [_row_to_record(r) for r in (await session.execute(stmt)).scalars().all()]

    @staticmethod
    async def _get_row(session: AsyncSession,
                       recommendation_id: str) -> Optional[LiveExecutionRow]:
        stmt = select(LiveExecutionRow).where(
            LiveExecutionRow.recommendation_id == recommendation_id)
        return (await session.execute(stmt)).scalars().first()

    async def execute(self, session: AsyncSession, recommendation_id: str,
                      confirmed: bool) -> ExecutionRecord:
        """The only entry point. Returns the execution record either way —
        a REJECTED record (risk gate said no) and a persisted broker order
        are both valid, meaningful results; only an unhandled exception
        means something actually went wrong in the plumbing.
        """
        analysis = await session.get(AnalysisRow, recommendation_id)
        if analysis is None:
            raise RecommendationNotFound(recommendation_id)
        if analysis.decision not in ("BUY", "SELL"):
            raise RecommendationNotExecutable(
                "Recommendation {} was a {}, not a trade.".format(
                    recommendation_id, analysis.decision))

        existing_row = await self._get_row(session, recommendation_id)
        # Only a row that actually reached the broker blocks a retry — a
        # prior REJECTED-before-submission attempt (bad funds, gate failure)
        # must not permanently lock out a recommendation that could succeed
        # once the underlying issue is fixed.
        # A row still SUBMITTING or UNKNOWN has no order id yet may well have
        # an order: it blocks too, until the reconciler settles which.
        already_executed = bool(existing_row and (
            existing_row.broker_order_id
            or existing_row.status in (ExecutionStatus.SUBMITTING.value,
                                       ExecutionStatus.UNKNOWN.value)))

        recommendation = _recommendation_from_row(analysis)

        ctx = GateContext(
            recommendation=recommendation, confirmed=confirmed,
            already_executed=already_executed,
            settings=get_runtime_settings(),
        )

        # Checks 1-5 (mode, confirmation, connection, kill switch, duplicate,
        # recommendation shape) touch nothing but local state — run them
        # before any network call. Angel One's instrument/funds lookups are
        # real requests against a real account; a rejection that was already
        # certain from local state alone must never pay for them, in tests
        # or in production.
        cheap_result = await risk_gate.evaluate_cheap(ctx)
        if not cheap_result.allowed:
            return await self._persist_rejection(
                session, existing_row, recommendation, ctx, cheap_result.reason)

        try:
            from .instruments import angel_instruments
            match = await angel_instruments.resolve(recommendation.symbol)
            ctx.match = match
        except Exception:  # noqa: BLE001 - the gate's own check re-derives and reports this
            ctx.match = None

        if ctx.match is not None:
            try:
                funds = await angel_broker.get_funds()
                ctx.available_funds = _extract_available_funds(funds)
            except AngelOneOrderError:
                ctx.available_funds = None

        gate_result = await risk_gate.evaluate(ctx)

        if not gate_result.allowed:
            return await self._persist_rejection(
                session, existing_row, recommendation, ctx, gate_result.reason)

        return await self._submit(session, existing_row, recommendation, ctx)

    # ── internals ─────────────────────────────────────────────

    @staticmethod
    def _upsert(existing: Optional[LiveExecutionRow], session: AsyncSession,
               **fields) -> LiveExecutionRow:
        """One row per recommendation_id, always — a retry after a
        pre-submission rejection updates that same row rather than colliding
        with its unique constraint."""
        if existing is not None:
            for key, value in fields.items():
                if key == "created_at":
                    continue  # preserve the original creation time on a retry
                setattr(existing, key, value)
            existing.updated_at = time.time()
            return existing
        row = LiveExecutionRow(**fields)
        session.add(row)
        return row

    async def _persist_rejection(self, session: AsyncSession,
                                 existing: Optional[LiveExecutionRow],
                                 rec: Recommendation, ctx: GateContext,
                                 reason: Optional[str]) -> ExecutionRecord:
        now = time.time()
        row = self._upsert(
            existing, session,
            recommendation_id=rec.id, symbol=rec.symbol, side=rec.side,
            exchange=(ctx.match.exchange if ctx.match else ""),
            trading_symbol=(ctx.match.trading_symbol if ctx.match else ""),
            instrument_token=(ctx.match.token if ctx.match else ""),
            requested_quantity=ctx.quantity or 0.0, requested_price=rec.entry,
            stop_price=rec.stop_loss, target_price=rec.take_profit,
            risk_amount=rec.risk_amount, status=ExecutionStatus.REJECTED.value,
            error_message=reason, created_at=now, updated_at=now,
        )
        await session.flush()
        log.info("[EXECUTION] recommendation_id=%s status=REJECTED reason=%s",
                 rec.id, reason)
        return _row_to_record(row)

    async def _submit(self, session: AsyncSession,
                      existing: Optional[LiveExecutionRow],
                      rec: Recommendation, ctx: GateContext) -> ExecutionRecord:
        match = ctx.match
        now = time.time()
        tag = order_tag(rec.id)
        row = self._upsert(
            existing, session,
            recommendation_id=rec.id, symbol=rec.symbol, side=rec.side,
            exchange=match.exchange, trading_symbol=match.trading_symbol,
            instrument_token=match.token, requested_quantity=ctx.quantity,
            requested_price=float(ctx.entry_price),
            stop_price=float(ctx.stop_price), initial_stop=float(ctx.stop_price),
            target_price=float(ctx.target_price) if ctx.target_price else None,
            risk_amount=rec.risk_amount, order_tag=tag, tick_size=match.tick_size,
            status=ExecutionStatus.SUBMITTING.value, error_message=None,
            created_at=now, updated_at=now,
        )
        # Write-ahead: the intent is durable before the broker hears of it.
        await session.commit()

        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": match.trading_symbol,
            "symboltoken": match.token,
            "transactiontype": rec.side,
            "exchange": match.exchange,
            # Explicit LIMIT at the recommendation's entry price — never
            # silently widened to MARKET (§16): it will not fill through a
            # gap past the intended entry.
            "ordertype": "LIMIT",
            "producttype": PRODUCT_TYPE,
            "duration": "DAY",
            "price": ctx.entry_price,
            "quantity": str(int(ctx.quantity)),
            "ordertag": tag,
        }

        log.info("[EXECUTION] recommendation_id=%s symbol=%s side=%s quantity=%s",
                 rec.id, rec.symbol, rec.side, ctx.quantity)
        log.info("[RISK_GATE] status=PASS")

        try:
            order_id = await angel_broker.place_order(order_params)
        except AngelOneOrderError as exc:
            row.status = ExecutionStatus.REJECTED.value
            row.error_message = str(exc)
            row.updated_at = time.time()
            await session.flush()
            log.info("[ANGEL_ONE] status=REJECTED reason=%s", exc)
            return _row_to_record(row)
        except AngelOneAmbiguousError as exc:
            # §30: never retry blindly. Look for *this* order — by tag — and
            # otherwise surface UNKNOWN; the reconciler keeps looking.
            matched_id = await self._find_submitted_order(row, since=now)
            if matched_id:
                row.broker_order_id = matched_id
                row.status = ExecutionStatus.SUBMITTED.value
                row.error_message = (
                    "Recovered after an ambiguous response: {}".format(exc))
                log.warning("[ANGEL_ONE] status=RECOVERED order_id=%s (%s)",
                           matched_id, exc)
            else:
                row.status = ExecutionStatus.UNKNOWN.value
                row.error_message = str(exc)
                log.exception("[ANGEL_ONE] status=UNKNOWN — a human must check "
                              "the Angel One order book before doing anything "
                              "else with this recommendation.")
            row.updated_at = time.time()
            await session.flush()
            return _row_to_record(row)

        row.broker_order_id = order_id
        row.status = ExecutionStatus.SUBMITTED.value
        row.updated_at = time.time()
        await session.commit()
        log.info("[ANGEL_ONE] status=SUBMITTED order_id=%s", order_id)

        # One immediate pass: an instant fill gets its stop now rather than
        # on the supervisor's next tick.
        from .reconciler import reconciler
        try:
            await reconciler.reconcile_row(session, row)
        except Exception:  # noqa: BLE001 - the loop will retry; the order stands
            log.exception("immediate reconcile failed for %s", rec.id)
        await session.flush()
        return _row_to_record(row)

    @staticmethod
    def _apply_order_status(row: LiveExecutionRow, status: dict) -> None:
        broker_status = (status.get("status") or "").lower()
        filled = status.get("filledshares") or status.get("fillquantity") or 0
        try:
            row.filled_quantity = float(filled)
        except (TypeError, ValueError):
            pass
        avg_price = status.get("averageprice")
        if avg_price:
            try:
                row.average_fill_price = float(avg_price)
            except (TypeError, ValueError):
                pass
        if broker_status in ("complete", "filled"):
            row.status = ExecutionStatus.FILLED.value
        elif broker_status in ("rejected",):
            row.status = ExecutionStatus.REJECTED.value
            row.error_message = status.get("text") or row.error_message
        elif broker_status in ("cancelled", "canceled"):
            row.status = ExecutionStatus.CANCELLED.value
        elif row.filled_quantity and row.filled_quantity < row.requested_quantity:
            row.status = ExecutionStatus.PARTIALLY_FILLED.value
        else:
            row.status = ExecutionStatus.OPEN.value
        row.updated_at = time.time()

    @staticmethod
    async def _find_submitted_order(row: LiveExecutionRow,
                                    since: float) -> Optional[str]:
        try:
            orders = await angel_broker.get_orders()
        except AngelOneOrderError:
            return None
        order = find_order_by_tag(
            orders, row.order_tag or "", since=since,
            trading_symbol=row.trading_symbol, side=row.side,
            quantity=int(row.requested_quantity or 0))
        return order.get("orderid") if order else None


angel_execution = AngelOneExecutionService()
