"""A failed login must not be retried on every poll.

On 13 October 2026 the quote poll, which asks for a session every three
seconds, logged in every three seconds for as long as a DNS lookup failed —
and the day before, told "Invalid Token" 559 times, it never logged in at
all. Both are the session service's to get right, and neither needs a
broker to test: the SDK is replaced by a counter.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.broker.angelone import auth as auth_module
from app.broker.angelone.auth import (
    LOGIN_BACKOFF_BASE_SECONDS, LOGIN_BACKOFF_MAX_REJECTED, LOGIN_BACKOFF_MAX_UNREACHABLE,
    AngelOneAuthError, AngelOneAuthService, AngelOneLoginBackoff, is_auth_failure,
)
from app.data.providers import angelone as provider_module
from app.data.providers.angelone import AngelOneMarketDataProvider


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def time(self) -> float:
        return self.now


class FakeSdk:
    """Stands in for SmartConnect: counts logins, answers as it is told."""

    def __init__(self) -> None:
        self.logins = 0
        self.answer = {"status": True, "data": {"feedToken": "f", "refreshToken": "r"}}

    def __call__(self, api_key):          # SmartConnect(api_key=...)
        return self

    def generateSession(self, client_code, password, totp):  # noqa: N802 - the SDK's name
        self.logins += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def broker(monkeypatch):
    clock, sdk = Clock(), FakeSdk()
    monkeypatch.setattr(auth_module.time, "time", clock.time)
    monkeypatch.setattr(auth_module, "_load_sdk", lambda: sdk)
    monkeypatch.setattr(auth_module, "get_settings", lambda: SimpleNamespace(
        angel_configured=True, angel_api_key="k", angel_client_code="C0000",
        angel_password="p", angel_totp_secret="JBSWY3DPEHPK3PXP"))
    return AngelOneAuthService(), sdk, clock


def test_a_poll_every_three_seconds_does_not_log_in_every_three_seconds(broker):
    """Ten minutes with the broker unreachable, asked every three seconds as
    the quote poll asks. It was 200 logins; it is now a handful."""
    auth, sdk, clock = broker
    sdk.answer = ConnectionError("name resolution failed")
    refused_without_asking = 0
    for _ in range(200):
        with pytest.raises(AngelOneAuthError) as failure:
            auth.ensure()
        refused_without_asking += isinstance(failure.value, AngelOneLoginBackoff)
        clock.now += 3.0
    assert sdk.logins <= 10
    assert refused_without_asking == 200 - sdk.logins
    assert auth.status()["failed_logins"] == sdk.logins


def test_the_wait_doubles_and_stops_at_its_ceiling(broker):
    auth, sdk, clock = broker
    sdk.answer = ConnectionError("down")
    waits = []
    for _ in range(8):
        with pytest.raises(AngelOneAuthError):
            auth.ensure()
        waits.append(auth.status()["login_retry_in_seconds"])
        clock.now += waits[-1] + 0.1
    assert waits[:4] == [LOGIN_BACKOFF_BASE_SECONDS * m for m in (1, 2, 4, 8)]
    assert waits[-1] == LOGIN_BACKOFF_MAX_UNREACHABLE


def test_a_login_the_broker_refused_is_asked_again_more_rarely_than_one_it_never_heard(broker):
    """A wrong PIN does not mend itself, and repeating it is what locks an
    account. An unreachable broker does, and a position may be waiting."""
    auth, sdk, clock = broker
    sdk.answer = {"status": False, "message": "Invalid totp", "errorcode": "AB1050"}
    for _ in range(12):
        with pytest.raises(AngelOneAuthError):
            auth.ensure()
        clock.now += (auth.status()["login_retry_in_seconds"] or 0) + 0.1
    with pytest.raises(AngelOneAuthError):
        auth.ensure()
    assert auth.status()["login_retry_in_seconds"] == LOGIN_BACKOFF_MAX_REJECTED
    assert LOGIN_BACKOFF_MAX_REJECTED > LOGIN_BACKOFF_MAX_UNREACHABLE
    assert auth.last_error == "Invalid totp"


def test_connecting_by_hand_is_never_made_to_wait_and_success_clears_it(broker):
    auth, sdk, clock = broker
    sdk.answer = ConnectionError("down")
    with pytest.raises(AngelOneAuthError):
        auth.ensure()
    with pytest.raises(AngelOneLoginBackoff):
        auth.ensure()
    assert sdk.logins == 1

    sdk.answer = {"status": True, "data": {"feedToken": "f", "refreshToken": "r"}}
    auth.login()                                   # the Connect button
    assert sdk.logins == 2 and auth.connected
    status = auth.status()
    assert status["failed_logins"] == 0 and status["login_retry_in_seconds"] is None
    assert auth.ensure() is sdk and sdk.logins == 2, "a live session is reused"


def test_two_callers_with_no_session_make_one_login_between_them(broker):
    import threading

    auth, sdk, _ = broker
    barrier = threading.Barrier(2)

    def ask():
        barrier.wait()
        auth.ensure()
    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sdk.logins == 1


def test_what_counts_as_the_broker_disowning_a_session():
    assert is_auth_failure({"success": False, "message": "Invalid Token",
                            "errorCode": "AG8001", "data": ""})
    assert is_auth_failure({"status": False, "message": "x", "errorcode": "AB1010"})
    assert is_auth_failure({"status": False, "message": "Token Expired"})
    assert not is_auth_failure({"status": True, "data": {}})
    assert not is_auth_failure({"status": False, "message": "Something Went Wrong",
                                "errorcode": "AB1004"})
    assert not is_auth_failure(None)
    assert not is_auth_failure("Access denied because of exceeding access rate")


def test_a_token_the_broker_calls_invalid_is_dropped_and_replaced(broker):
    """What happened at midnight: the session was three hours old, so not
    stale by its age, and every poll was answered 'Invalid Token'."""
    auth, sdk, clock = broker
    auth.ensure()
    clock.now += 3 * 3600
    assert auth.connected

    assert auth.invalidate("Invalid Token") is True
    assert not auth.connected and auth.last_error == "Invalid Token"
    assert auth.invalidate("again") is False, "nothing left to drop"
    assert auth.ensure() is sdk and sdk.logins == 2, "an old session is replaced at once"


def test_a_session_disowned_as_soon_as_it_is_granted_is_a_failed_login(broker):
    """Log in, be refused, log in: without this it is the storm again."""
    auth, sdk, clock = broker
    for _ in range(50):
        try:
            auth.ensure()
        except AngelOneLoginBackoff:
            pass
        else:
            auth.invalidate("Invalid Token")
        clock.now += 3.0
    assert sdk.logins <= 6


def test_the_quote_poll_gives_up_a_dead_token(monkeypatch):
    dropped = []

    class DeadSession:
        def getMarketData(self, mode, tokens):  # noqa: N802 - the SDK's name
            return {"success": False, "message": "Invalid Token", "errorCode": "AG8001",
                    "data": ""}

    async def resolve(symbol):
        return SimpleNamespace(exchange="NSE", token="2885")
    monkeypatch.setattr(provider_module.angel_instruments, "resolve", resolve)
    monkeypatch.setattr(provider_module.angel_auth, "ensure", lambda: DeadSession())
    monkeypatch.setattr(provider_module.angel_auth, "invalidate",
                        lambda reason: dropped.append(reason) or True)
    provider, ticks = AngelOneMarketDataProvider(), []

    async def handler(tick):
        ticks.append(tick)
    asyncio.run(provider._poll_once(["RELIANCE"], handler))  # noqa: SLF001
    assert ticks == [] and len(dropped) == 1 and "Invalid Token" in dropped[0]
    assert provider.last_error == "Invalid Token"


def test_a_rate_limit_is_not_mistaken_for_a_dead_session(monkeypatch):
    dropped = []

    class Throttled:
        def getMarketData(self, mode, tokens):  # noqa: N802 - the SDK's name
            return {"status": False, "message": "Access rate exceeded", "errorcode": "AB1019"}

    async def resolve(symbol):
        return SimpleNamespace(exchange="NSE", token="2885")
    monkeypatch.setattr(provider_module.angel_instruments, "resolve", resolve)
    monkeypatch.setattr(provider_module.angel_auth, "ensure", lambda: Throttled())
    monkeypatch.setattr(provider_module.angel_auth, "invalidate",
                        lambda reason: dropped.append(reason) or True)

    async def handler(tick):
        raise AssertionError("no tick from a refused poll")
    asyncio.run(AngelOneMarketDataProvider()._poll_once(["RELIANCE"], handler))  # noqa: SLF001
    assert dropped == []
