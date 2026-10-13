"""PAPER cannot reach a broker's order calls — checked on the source, not on a
label.

The order-placing methods of the broker client (`place_order`, `modify_order`,
`cancel_order`) may be called from exactly two modules: the execution service
(behind the pre-trade gate, which refuses unless the mode is LIVE) and the
reconciler (which only protects or closes what the execution service opened).
Nothing in paper trading, the analysis engine, the backtest, the research
package or a router may call them, directly or by importing the client.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
ORDER_METHODS = {"place_order", "modify_order", "cancel_order",
                 "placeOrder", "placeOrderFullResponse", "modifyOrder", "cancelOrder"}
MAY_SEND = {"broker/angelone/execution.py", "broker/angelone/reconciler.py",
            "broker/angelone/client.py"}
#: Packages that must not even import the order-capable client.
NO_BROKER_ORDERS = ("services/paper.py", "routers/paper.py", "analysis", "backtest",
                    "research", "data/feed.py", "data/store.py")


def _files(prefix: str):
    target = APP / prefix
    return [target] if target.is_file() else sorted(target.rglob("*.py"))


def _calls(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ORDER_METHODS:
            yield node.attr, node.lineno


def test_only_the_execution_path_calls_an_order_method():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel in MAY_SEND:
            continue
        offenders += ["{}:{} {}".format(rel, line, name) for name, line in _calls(path)]
    assert not offenders, offenders


def test_the_execution_service_sends_an_order_only_after_the_gate():
    source = (APP / "broker/angelone/execution.py").read_text()
    submit = source[source.index("async def execute("):]
    assert submit.index("risk_gate.evaluate") < submit.index("place_order"), \
        "the gate is consulted before the first call that can send"
    assert "if not result.allowed" in source.split("async def _submit(")[0]


@pytest.mark.parametrize("prefix", NO_BROKER_ORDERS)
def test_paper_analysis_backtest_and_research_do_not_import_the_order_client(prefix):
    offenders = []
    for path in _files(prefix):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [(node.module or "") + "." + alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if any(part in name for part in (
                        "angelone.client", "angelone.execution", "angelone.reconciler",
                        "angel_broker", "risk_gate")):
                    offenders.append("{}: {}".format(path.relative_to(APP).as_posix(), name))
    assert not offenders, offenders


def test_the_gate_refuses_every_order_unless_the_mode_is_live():
    import asyncio

    from app.broker.risk_gate import GateContext, Recommendation, risk_gate
    from app.broker.trading_mode import PAPER, trading_mode

    assert trading_mode.mode == PAPER
    rec = Recommendation("r1", "RELIANCE", "BUY", 1160.0, 1150.0, 1180.0, 100.0, 1.0, 80.0)
    ctx = GateContext(recommendation=rec, confirmed=True, already_executed=False)
    result = asyncio.run(risk_gate.evaluate_cheap(ctx))
    assert not result.allowed and result.check == "trading_mode"


def test_a_restart_comes_back_in_paper_whatever_the_last_mode_was(monkeypatch):
    from app.broker.trading_mode import LIVE, PAPER, TradingModeStore

    first = TradingModeStore()
    first.set_mode(LIVE, confirmed=True)
    first.set_auto_live(True, confirmed=True)
    assert first.mode == LIVE and first.auto_live_enabled
    second = TradingModeStore()          # the process after a crash
    assert second.mode == PAPER and not second.auto_live_enabled


def test_a_missing_or_garbled_mode_setting_fails_to_paper(monkeypatch):
    from app import config
    from app.broker.trading_mode import PAPER, TradingModeStore

    for value in ("", "live ", "LIVE!", "yes", "1"):
        monkeypatch.setattr(config.get_settings(), "trading_mode", value, raising=False)
        assert TradingModeStore().mode == PAPER, value


def test_the_live_edge_requirement_and_broker_validation_default_to_the_safe_side():
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["live_require_measured_edge"].default is True
    assert fields["live_broker_validated"].default is False
    assert fields["trading_mode"].default == "PAPER"
