"""The paper broker — a demo account trading live prices.

This is the honest way to find out whether the engine has an edge. Real prices,
real spreads, real costs, no real money. Every fill is recorded and every trade
lands in the same journal the manual workflow uses, so the accuracy-by-
confidence table builds from it.

**The costs are the point.** A paper engine that fills at the mid price and
charges nothing will show a profit on almost any strategy, because the average
edge of a coin flip is exactly zero and the average edge of a coin flip minus
costs is negative. Three things are modelled here specifically so the result
means something:

* **You cross the spread twice.** A long buys at the ask and sells at the bid.
  On a CoinDCX USDT book that is a rounding error; on the INR books it is 0.4%
  a side and it will eat a scalping strategy alive. Both are shown for real.
* **Stops fill at the stop or worse, never better.** Price gapping through a
  level does not politely stop at it.
* **Commission is charged per side** at the venue's taker rate.

If a strategy is profitable in here, that is genuine evidence. It is not proof
— paper trading still omits slippage on size, partial fills, and the fact that
you will not actually follow the rules when it is your own money.
"""
from __future__ import annotations

import time
import uuid
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import PaperAccountRow, PaperTradeRow
from ..instruments import Instrument
from ..schemas.paper import ExitReason, PaperAccount, PaperStatus, PaperTrade

#: Taker fee per side, as a fraction of notional. CoinDCX charges 0.05% on the
#: USDT books at the default tier; NSE equity delivery is far less but F&O
#: brokerage plus STT lands near this once everything is counted.
COMMISSION_RATE = 0.0005

#: Slippage applied against you on a stop fill, as a fraction of the ATR-free
#: distance. A stop is a market order once triggered, and it fills where the
#: book is, which is never in your favour.
STOP_SLIPPAGE_PIPS = 0.5

ACCOUNT_ID = "demo"


# ── fill pricing ──────────────────────────────────────────────

def fill_price(side: str, entering: bool, bid: float, ask: float,
               last: float) -> float:
    """The price you actually get, not the one on the chart.

    Entering long buys the ask; exiting long sells the bid. Reversed for a
    short. Where no book is available the last price is used, which flatters
    the result slightly — that case is logged as a warning by the caller.
    """
    long = side == "LONG"
    if bid <= 0 or ask <= 0 or ask < bid:
        return last
    if entering:
        return ask if long else bid
    return bid if long else ask


def commission(notional: float) -> float:
    return abs(notional) * COMMISSION_RATE


#: How much notional the account may carry per unit of balance.
#:
#: Crypto on CoinDCX is spot: you buy the coin, so a $10,000 account can hold
#: $10,000 of it and no more. Exchange-traded derivatives are margined, and
#: posting roughly 20% gets you five times the notional. Assuming leverage the
#: venue does not give you — or refusing leverage it does — both make the demo
#: stop resembling the thing it is testing.
MAX_LEVERAGE = {"crypto": 1.0, "fx": 5.0, "metal": 5.0}
DERIVATIVE_LEVERAGE = 5.0


def max_leverage(instrument: Instrument) -> float:
    if instrument.venue in ("NFO", "CDS", "MCX"):
        return DERIVATIVE_LEVERAGE
    return MAX_LEVERAGE.get(instrument.asset_class, 1.0)


def position_size(balance: float, risk_pct: float, entry: float, stop: float,
                  instrument: Instrument) -> Tuple[float, float]:
    """Units to trade, and the cash that puts at risk.

    Sized so that a stop-out costs exactly `risk_pct` of the balance — the
    same rule the manual trade panel uses, so paper results transfer. Capped
    by what the account could actually fund at the venue's leverage.
    """
    risk_amount = balance * max(risk_pct, 0.0) / 100.0
    distance = abs(entry - stop)
    if distance <= 0 or risk_amount <= 0 or entry <= 0:
        return 0.0, 0.0

    quantity = risk_amount / distance
    affordable = balance * max_leverage(instrument) / entry
    quantity = min(quantity, affordable)
    return quantity, quantity * distance


# ── account ───────────────────────────────────────────────────

def _account_to_schema(row: PaperAccountRow) -> PaperAccount:
    return PaperAccount(
        id=row.id, currency=row.currency,
        starting_balance=row.starting_balance, balance=row.balance,
        equity=row.balance, enabled=row.enabled,
        risk_per_trade_pct=row.risk_per_trade_pct,
        min_confidence=row.min_confidence,
        symbols=[s for s in (row.symbols or "").split(",") if s],
        timeframes=[t for t in (row.timeframes or "").split(",") if t],
        total_trades=row.total_trades, wins=row.wins, losses=row.losses,
        created_at=row.created_at, updated_at=row.updated_at,
        peak_equity=row.peak_equity or row.starting_balance,
        max_drawdown=row.max_drawdown or 0.0,
    )


def _trade_to_schema(row: PaperTradeRow) -> PaperTrade:
    return PaperTrade(
        id=row.id, account_id=row.account_id, symbol=row.symbol,
        timeframe=row.timeframe, side=row.side, status=PaperStatus(row.status),
        entry=row.entry, entry_at=row.entry_at, exit=row.exit_price,
        exit_at=row.exit_at,
        exit_reason=ExitReason(row.exit_reason) if row.exit_reason else None,
        stop_loss=row.stop_loss, initial_stop=row.initial_stop,
        take_profit=row.take_profit, quantity=row.quantity,
        notional=row.notional, entry_cost=row.entry_cost,
        exit_cost=row.exit_cost, gross_pnl=row.gross_pnl, net_pnl=row.net_pnl,
        realised_r=row.realised_r, peak_r=row.peak_r, trough_r=row.trough_r,
        protection_tier=row.protection_tier, confidence=row.confidence,
        decision_source=row.decision_source or "", reason=row.reason or "",
        analysis_id=row.analysis_id,
    )


async def get_account(session: AsyncSession,
                      starting_balance: float = 10_000.0,
                      currency: str = "USD") -> PaperAccount:
    """The demo account, created on first use."""
    row = await session.get(PaperAccountRow, ACCOUNT_ID)
    if row is None:
        now = time.time()
        row = PaperAccountRow(
            id=ACCOUNT_ID, currency=currency,
            starting_balance=starting_balance, balance=starting_balance,
            peak_equity=starting_balance, created_at=now, updated_at=now,
        )
        session.add(row)
        await session.flush()
    return _account_to_schema(row)


async def update_account(session: AsyncSession, **fields) -> PaperAccount:
    row = await session.get(PaperAccountRow, ACCOUNT_ID)
    if row is None:
        await get_account(session)
        row = await session.get(PaperAccountRow, ACCOUNT_ID)
    for key, value in fields.items():
        if value is None:
            continue
        if key in ("symbols", "timeframes") and isinstance(value, list):
            value = ",".join(value)
        if hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = time.time()
    await session.flush()
    return _account_to_schema(row)


async def reset_account(session: AsyncSession, starting_balance: float,
                        currency: str) -> PaperAccount:
    """Wipe the demo and start again. Trades go too — a reset that keeps the
    history would produce a performance table describing two different
    accounts."""
    rows = (await session.execute(
        select(PaperTradeRow).where(PaperTradeRow.account_id == ACCOUNT_ID)
    )).scalars().all()
    for row in rows:
        await session.delete(row)

    account = await session.get(PaperAccountRow, ACCOUNT_ID)
    now = time.time()
    if account is None:
        account = PaperAccountRow(id=ACCOUNT_ID, created_at=now)
        session.add(account)
    account.currency = currency
    account.starting_balance = starting_balance
    account.balance = starting_balance
    account.peak_equity = starting_balance
    account.max_drawdown = 0.0
    account.total_trades = account.wins = account.losses = 0
    account.updated_at = now
    await session.flush()
    return _account_to_schema(account)


# ── trades ────────────────────────────────────────────────────

async def open_trade(session: AsyncSession, symbol: str, timeframe: str,
                     side: str, bid: float, ask: float, last: float,
                     stop_loss: float, take_profit: Optional[float],
                     instrument: Instrument, confidence: float = 0.0,
                     source: str = "", reason: str = "",
                     analysis_id: Optional[str] = None) -> Optional[PaperTrade]:
    account = await get_account(session)
    entry = fill_price(side, True, bid, ask, last)

    # Re-validate against the *fill*, not the signal price. A stop that was
    # below the mid can be above the ask on a wide book, and taking that trade
    # would open a position already through its own stop.
    long = side == "LONG"
    if (long and stop_loss >= entry) or (not long and stop_loss <= entry):
        return None

    quantity, _ = position_size(
        account.balance, account.risk_per_trade_pct, entry, stop_loss, instrument)
    if quantity <= 0:
        return None

    notional = quantity * entry
    cost = commission(notional)
    now = time.time()

    row = PaperTradeRow(
        id=uuid.uuid4().hex, account_id=ACCOUNT_ID, symbol=symbol.upper(),
        timeframe=timeframe, side=side, status=PaperStatus.OPEN.value,
        entry=entry, entry_at=now, stop_loss=stop_loss, initial_stop=stop_loss,
        take_profit=take_profit, quantity=quantity, notional=notional,
        entry_cost=cost, confidence=confidence, decision_source=source,
        reason=reason, analysis_id=analysis_id,
    )
    session.add(row)

    account_row = await session.get(PaperAccountRow, ACCOUNT_ID)
    account_row.total_trades += 1
    account_row.updated_at = now
    await session.flush()
    return _trade_to_schema(row)


async def close_trade(session: AsyncSession, trade_id: str, bid: float,
                      ask: float, last: float, reason: ExitReason,
                      instrument: Instrument,
                      fill_at: Optional[float] = None) -> Optional[PaperTrade]:
    """Close a paper trade and settle it against the account.

    `fill_at` forces the exit price — used for stop and target fills, where the
    trade left at the level rather than at the current quote.
    """
    row = await session.get(PaperTradeRow, trade_id)
    if row is None or row.status != PaperStatus.OPEN.value:
        return None

    long = row.side == "LONG"
    exit_price = fill_at if fill_at is not None else fill_price(
        row.side, False, bid, ask, last)

    if reason is ExitReason.STOP:
        # A stop is a market order once triggered. Fill it slightly against
        # you, never at the level exactly and never better.
        slip = instrument.from_pips(STOP_SLIPPAGE_PIPS)
        exit_price = (exit_price - slip) if long else (exit_price + slip)

    gross = ((exit_price - row.entry) if long else (row.entry - exit_price)) * row.quantity
    exit_cost = commission(row.quantity * exit_price)
    net = gross - row.entry_cost - exit_cost

    risk_unit = abs(row.entry - row.initial_stop) * row.quantity
    now = time.time()

    row.status = PaperStatus.CLOSED.value
    row.exit_price = exit_price
    row.exit_at = now
    row.exit_reason = reason.value
    row.gross_pnl = gross
    row.exit_cost = exit_cost
    row.net_pnl = net
    # R is measured net of costs against the original risk. A trade that
    # reached +1R gross and paid 0.1R in costs made 0.9R, and the journal
    # should say so.
    row.realised_r = round(net / risk_unit, 3) if risk_unit > 0 else 0.0

    account = await session.get(PaperAccountRow, ACCOUNT_ID)
    account.balance += net
    if net > 0:
        account.wins += 1
    else:
        account.losses += 1
    account.peak_equity = max(account.peak_equity or 0.0, account.balance)
    drawdown = (account.peak_equity - account.balance) / account.peak_equity \
        if account.peak_equity > 0 else 0.0
    account.max_drawdown = max(account.max_drawdown or 0.0, drawdown)
    account.updated_at = now

    await session.flush()
    return _trade_to_schema(row)


async def open_trade_for(session: AsyncSession,
                         symbol: Optional[str] = None) -> Optional[PaperTrade]:
    stmt = select(PaperTradeRow).where(
        PaperTradeRow.account_id == ACCOUNT_ID,
        PaperTradeRow.status == PaperStatus.OPEN.value)
    if symbol:
        stmt = stmt.where(PaperTradeRow.symbol == symbol.upper())
    row = (await session.execute(stmt)).scalars().first()
    return _trade_to_schema(row) if row else None


async def list_trades(session: AsyncSession, status: Optional[str] = None,
                      limit: int = 50) -> List[PaperTrade]:
    stmt = select(PaperTradeRow).where(PaperTradeRow.account_id == ACCOUNT_ID)
    if status:
        stmt = stmt.where(PaperTradeRow.status == status)
    stmt = stmt.order_by(PaperTradeRow.entry_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [_trade_to_schema(row) for row in rows]


async def apply_protection(session: AsyncSession, trade_id: str,
                           stop_loss: Optional[float] = None,
                           protection_tier: Optional[int] = None,
                           peak_r: Optional[float] = None,
                           trough_r: Optional[float] = None) -> None:
    """Ratchet a paper trade's stop. Tighten-only, same as the manual path."""
    row = await session.get(PaperTradeRow, trade_id)
    if row is None:
        return
    if stop_loss is not None:
        long = row.side == "LONG"
        tighter = (stop_loss > row.stop_loss) if long else (stop_loss < row.stop_loss)
        if tighter:
            row.stop_loss = stop_loss
    if protection_tier is not None:
        row.protection_tier = max(row.protection_tier or 0, protection_tier)
    if peak_r is not None:
        row.peak_r = max(row.peak_r or 0.0, peak_r)
    if trough_r is not None:
        row.trough_r = min(row.trough_r or 0.0, trough_r)
    await session.flush()


# ── performance ───────────────────────────────────────────────

def summarise(account: PaperAccount, closed: List[PaperTrade],
              open_trade: Optional[PaperTrade] = None,
              price: Optional[float] = None) -> PaperAccount:
    """Fold closed trades into the headline numbers."""
    wins = [t for t in closed if t.net_pnl > 0]
    losses = [t for t in closed if t.net_pnl <= 0]

    account.realised_pnl = round(sum(t.net_pnl for t in closed), 2)
    account.total_costs = round(
        sum(t.entry_cost + t.exit_cost for t in closed), 2)
    account.wins, account.losses = len(wins), len(losses)
    account.total_trades = len(closed)

    if closed:
        account.win_rate = round(len(wins) / len(closed) * 100, 1)
        gross_win = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        account.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0
        account.expectancy_r = round(
            sum(t.realised_r or 0.0 for t in closed) / len(closed), 3)
        account.avg_win_r = round(
            sum(t.realised_r or 0.0 for t in wins) / len(wins), 2) if wins else 0.0
        account.avg_loss_r = round(
            sum(t.realised_r or 0.0 for t in losses) / len(losses), 2) if losses else 0.0

    # Mark the open trade to the live price so equity is honest.
    account.unrealised_pnl = 0.0
    if open_trade is not None and price:
        long = open_trade.side == "LONG"
        move = (price - open_trade.entry) if long else (open_trade.entry - price)
        account.unrealised_pnl = round(move * open_trade.quantity, 2)
        account.open_trades = 1

    account.equity = round(account.balance + account.unrealised_pnl, 2)
    if account.starting_balance > 0:
        account.return_pct = round(
            (account.equity - account.starting_balance)
            / account.starting_balance * 100, 2)
    account.max_drawdown = round((account.max_drawdown or 0.0) * 100, 2)
    return account
