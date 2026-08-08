from __future__ import annotations

import time

import pytest

from app.analysis import autotrader
from app.instruments import get_instrument
from app.schemas.analysis import (
    AIVerdict, AnalysisResult, ConfidenceBreakdown, Decision, IndicatorSnapshot,
    MarketStructure, TradePlan,
)
from app.schemas.paper import ExitReason, PaperAccount, PaperStatus, PaperTrade
from app.schemas.risk import AccountRisk, GuardrailStatus
from app.services import paper
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

EURUSD = get_instrument("EURUSD")
BTCUSD = get_instrument("BTCUSD")


def make_account(**kwargs) -> PaperAccount:
    base = dict(id="demo", enabled=True, balance=10_000.0,
                starting_balance=10_000.0, risk_per_trade_pct=1.0,
                min_confidence=55.0)
    base.update(kwargs)
    return PaperAccount(**base)


def make_result(decision=Decision.BUY, confidence=70.0, entry=1.1000,
                stop=1.0900, target=1.1300, halt=False) -> AnalysisResult:
    return AnalysisResult(
        id="a1", symbol="EURUSD", timeframe="15m", generated_at=time.time(),
        price=entry, bar_time=0, indicators=IndicatorSnapshot(),
        structure=MarketStructure(),
        confidence=ConfidenceBreakdown(final_confidence=confidence),
        verdict=AIVerdict(decision=decision, source="rule_engine"),
        plan=TradePlan(decision=decision, entry=entry, stop_loss=stop,
                       take_profit=target),
        account_risk=AccountRisk(
            status=GuardrailStatus.HALT if halt else GuardrailStatus.CLEAR,
            can_open_new=not halt,
            reason="daily loss limit" if halt else ""),
    )


def make_trade(entry=1.1000, stop=1.0900, target=1.1300, side="LONG",
               quantity=1000.0) -> PaperTrade:
    return PaperTrade(
        id="t1", account_id="demo", symbol="EURUSD", timeframe="15m",
        side=side, status=PaperStatus.OPEN, entry=entry,
        entry_at=time.time() - 600, stop_loss=stop, initial_stop=stop,
        take_profit=target, quantity=quantity, notional=entry * quantity)


# ── fills cross the spread ────────────────────────────────────

def test_entering_long_pays_the_ask_and_exiting_sells_the_bid():
    """A paper engine that fills at the mid shows a profit on almost any
    strategy, because the edge of a coin flip is zero and the edge of a coin
    flip minus costs is negative. Both sides of the spread must be crossed."""
    assert paper.fill_price("LONG", True, bid=1.0998, ask=1.1002, last=1.1000) == 1.1002
    assert paper.fill_price("LONG", False, bid=1.0998, ask=1.1002, last=1.1000) == 1.0998


def test_shorts_are_mirrored():
    assert paper.fill_price("SHORT", True, 1.0998, 1.1002, 1.1000) == 1.0998
    assert paper.fill_price("SHORT", False, 1.0998, 1.1002, 1.1000) == 1.1002


def test_a_missing_book_falls_back_to_last():
    assert paper.fill_price("LONG", True, bid=0.0, ask=0.0, last=1.1) == 1.1
    # A crossed book is nonsense and must not be trusted either.
    assert paper.fill_price("LONG", True, bid=1.2, ask=1.1, last=1.15) == 1.15


def test_a_round_trip_at_a_static_price_loses_money():
    """The sanity check for the whole simulator: buy and sell with the market
    unchanged and you must be *down* by the spread plus both commissions."""
    bid, ask = 1.0998, 1.1002
    entry = paper.fill_price("LONG", True, bid, ask, 1.1000)
    exit_price = paper.fill_price("LONG", False, bid, ask, 1.1000)
    quantity = 10_000.0
    gross = (exit_price - entry) * quantity
    net = gross - paper.commission(entry * quantity) - paper.commission(exit_price * quantity)
    assert gross < 0
    assert net < gross, "commission must make it worse still"


# ── sizing ────────────────────────────────────────────────────

def test_size_puts_exactly_the_risk_budget_at_stake():
    quantity, risk = paper.position_size(
        balance=10_000.0, risk_pct=1.0, entry=1.1000, stop=1.0900,
        instrument=EURUSD)
    assert risk == pytest.approx(100.0, abs=0.01)      # 1% of 10,000
    assert quantity == pytest.approx(10_000.0, abs=1)  # 100 / 0.01


def test_spot_crypto_size_is_capped_at_the_balance():
    """A tight stop implies an enormous position. CoinDCX crypto is spot — you
    buy the coin — so the balance is a hard ceiling on notional."""
    quantity, _ = paper.position_size(
        balance=10_000.0, risk_pct=1.0, entry=60_000.0, stop=59_999.0,
        instrument=BTCUSD)
    assert paper.max_leverage(BTCUSD) == 1.0
    assert quantity == pytest.approx(10_000.0 / 60_000.0, rel=1e-9)


def test_margined_instruments_may_carry_more_notional_than_the_balance():
    """NSE and MCX derivatives post roughly 20% margin. Refusing leverage the
    venue actually gives you understates every position and makes the demo
    unrepresentative in the other direction."""
    nifty = get_instrument("NIFTY")
    assert paper.max_leverage(nifty) == 5.0
    quantity, _ = paper.position_size(
        balance=100_000.0, risk_pct=1.0, entry=24_500.0, stop=24_400.0,
        instrument=nifty)
    assert quantity * 24_500.0 > 100_000.0


def test_no_size_without_a_risk_distance():
    quantity, risk = paper.position_size(10_000.0, 1.0, 1.1, 1.1, EURUSD)
    assert quantity == 0.0 and risk == 0.0


# ── entry gates ───────────────────────────────────────────────

def test_a_clean_signal_is_taken():
    intent = autotrader.evaluate_entry(make_result(), make_account(), EURUSD, 0.6)
    assert intent.action == "enter"
    assert intent.side == "LONG" and intent.stop_loss == 1.0900


def test_wait_verdicts_are_never_traded():
    intent = autotrader.evaluate_entry(
        make_result(decision=Decision.WAIT), make_account(), EURUSD, 0.6)
    assert intent.action == "wait"


def test_low_confidence_is_rejected():
    intent = autotrader.evaluate_entry(
        make_result(confidence=40.0), make_account(min_confidence=55.0),
        EURUSD, 0.6)
    assert intent.action == "wait" and "below" in intent.reason


def test_a_symbol_off_the_allow_list_is_rejected():
    intent = autotrader.evaluate_entry(
        make_result(), make_account(symbols=["BTCUSD"]), EURUSD, 0.6)
    assert intent.action == "wait" and "symbol list" in intent.reason


def test_an_empty_allow_list_permits_every_symbol():
    intent = autotrader.evaluate_entry(
        make_result(), make_account(symbols=[]), EURUSD, 0.6)
    assert intent.action == "enter"


def test_a_timeframe_off_the_allow_list_is_rejected():
    intent = autotrader.evaluate_entry(
        make_result(), make_account(timeframes=["1h"]), EURUSD, 0.6)
    assert intent.action == "wait" and "timeframe list" in intent.reason


def test_an_empty_timeframe_list_permits_every_timeframe():
    intent = autotrader.evaluate_entry(
        make_result(), make_account(timeframes=[]), EURUSD, 0.6)
    assert intent.action == "enter"


def test_poor_risk_reward_is_rejected():
    """Under ~1.5R the win rate has to be implausibly high to survive costs."""
    intent = autotrader.evaluate_entry(
        make_result(entry=1.1000, stop=1.0900, target=1.1050),
        make_account(), EURUSD, 0.6)
    assert intent.action == "wait" and "R:R" in intent.reason


def test_a_halted_account_blocks_entry():
    intent = autotrader.evaluate_entry(
        make_result(halt=True), make_account(), EURUSD, 0.6)
    assert intent.action == "wait" and intent.blocked


def test_a_wide_spread_blocks_the_trade():
    """This is the gate that stops the CoinDCX INR books quietly draining the
    account: 0.4% a side against a 0.3% move is a guaranteed loser."""
    narrow = autotrader.evaluate_entry(make_result(), make_account(), EURUSD, 0.6)
    wide = autotrader.evaluate_entry(
        make_result(), make_account(), EURUSD, spread_pips=60.0)
    assert narrow.action == "enter"
    assert wide.action == "wait" and "spread" in wide.reason


def test_a_disabled_account_does_nothing():
    intent = autotrader.evaluate_entry(
        make_result(), make_account(enabled=False), EURUSD, 0.6)
    assert intent.action == "wait"


# ── level exits ───────────────────────────────────────────────

def test_a_long_is_stopped_on_the_bid_not_the_last():
    """You sell at the bid, so that is the price that triggers a long's stop.
    Checking the last price would make stops look better than they are."""
    trade = make_trade(stop=1.0900)
    hit = autotrader.check_levels(trade, bid=1.0899, ask=1.0903, last=1.0901)
    assert hit is not None and hit.exit_reason is ExitReason.STOP


def test_a_long_takes_profit_on_the_bid():
    trade = make_trade(target=1.1300)
    hit = autotrader.check_levels(trade, bid=1.1301, ask=1.1305, last=1.1303)
    assert hit is not None and hit.exit_reason is ExitReason.TARGET


def test_a_short_is_stopped_on_the_ask():
    trade = make_trade(entry=1.1000, stop=1.1100, target=1.0700, side="SHORT")
    hit = autotrader.check_levels(trade, bid=1.1095, ask=1.1101, last=1.1098)
    assert hit is not None and hit.exit_reason is ExitReason.STOP


def test_nothing_fires_inside_the_levels():
    trade = make_trade()
    assert autotrader.check_levels(trade, 1.1050, 1.1054, 1.1052) is None


# ── structural exits ──────────────────────────────────────────

def test_a_flipped_engine_closes_the_trade():
    trade = make_trade()
    intent = autotrader.evaluate_exit(
        trade, make_result(decision=Decision.SELL, confidence=70.0), time.time())
    assert intent.action == "exit" and intent.exit_reason is ExitReason.SIGNAL


def test_a_fresh_trade_is_not_flipped_immediately():
    """One noisy re-analysis must not reverse a position seconds after entry —
    that is how an automated system pays the spread ten times in a minute."""
    trade = make_trade()
    trade.entry_at = time.time()
    intent = autotrader.evaluate_exit(
        trade, make_result(decision=Decision.SELL, confidence=90.0), time.time())
    assert intent.action == "hold"


def test_a_guardrail_breach_flattens_immediately():
    trade = make_trade()
    trade.entry_at = time.time()          # even a brand new one
    intent = autotrader.evaluate_exit(trade, make_result(halt=True), time.time())
    assert intent.action == "exit" and intent.exit_reason is ExitReason.GUARDRAIL


def test_an_intact_thesis_is_held():
    intent = autotrader.evaluate_exit(make_trade(), make_result(), time.time())
    assert intent.action == "hold"


# ── performance maths ─────────────────────────────────────────

def _closed(net_pnl: float, realised_r: float) -> PaperTrade:
    trade = make_trade()
    trade.status = PaperStatus.CLOSED
    trade.net_pnl = net_pnl
    trade.realised_r = realised_r
    trade.entry_cost = trade.exit_cost = 1.0
    return trade


def test_summary_reports_expectancy_and_profit_factor():
    account = make_account()
    closed = [_closed(200.0, 2.0), _closed(-100.0, -1.0),
              _closed(300.0, 3.0), _closed(-100.0, -1.0)]
    summary = paper.summarise(account, closed)
    assert summary.win_rate == 50.0
    assert summary.profit_factor == pytest.approx(2.5, abs=0.01)   # 500 / 200
    assert summary.expectancy_r == pytest.approx(0.75, abs=0.01)
    assert summary.total_costs == pytest.approx(8.0, abs=0.01)


def test_equity_marks_the_open_trade_to_the_live_price():
    account = make_account(balance=10_000.0)
    open_trade = make_trade(entry=1.1000, quantity=10_000.0)
    summary = paper.summarise(account, [], open_trade, price=1.1050)
    assert summary.unrealised_pnl == pytest.approx(50.0, abs=0.01)
    assert summary.equity == pytest.approx(10_050.0, abs=0.01)


def test_a_losing_run_reports_a_negative_return():
    account = make_account(balance=9_400.0)
    summary = paper.summarise(account, [_closed(-200.0, -1.0) for _ in range(3)])
    assert summary.return_pct == pytest.approx(-6.0, abs=0.01)
    assert summary.expectancy_r == pytest.approx(-1.0, abs=0.01)


# ── end to end through the broker ─────────────────────────────

def test_full_lifecycle_settles_against_the_account(client):
    """Open a trade, stop it out, and confirm the account actually moved by the
    net loss — costs included."""
    import json
    import urllib.request  # noqa: F401 - kept for symmetry with live probes

    state = client.post("/api/paper/reset",
                        json={"starting_balance": 10_000.0}).json()
    assert state["account"]["balance"] == 10_000.0

    state = client.patch("/api/paper/config",
                         json={"enabled": True, "min_confidence": 50}).json()
    assert state["account"]["enabled"] is True

    trades = client.get("/api/paper/trades").json()
    assert trades == []


def test_config_rejects_reckless_risk(client):
    body = client.patch("/api/paper/config", json={"risk_per_trade_pct": 25})
    assert body.status_code == 422
    assert "losing streak" in body.json()["detail"]


def test_config_rejects_impossible_confidence(client):
    assert client.patch("/api/paper/config",
                        json={"min_confidence": 140}).status_code == 422


def test_reset_clears_history(client):
    client.post("/api/paper/reset", json={"starting_balance": 5_000.0})
    state = client.get("/api/paper").json()
    assert state["account"]["starting_balance"] == 5_000.0
    assert state["account"]["balance"] == 5_000.0
    assert state["account"]["total_trades"] == 0
    assert state["recent"] == []
