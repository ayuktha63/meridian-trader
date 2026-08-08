"""Thin async wrapper around the SmartAPI SDK.

SmartConnect's methods are synchronous (blocking HTTP under the hood), so
every call here runs in a thread via `asyncio.to_thread` — otherwise one slow
Angel One response stalls the whole event loop, including price ticks for
every other instrument on the platform.

This module talks to Angel One. It does not decide *whether* to trade —
that's the execution service and the pre-trade risk gate, both upstream of
this. Keeping the boundary here means a bug in "should we trade" can never
also be a bug in "how do we talk to the broker."
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .auth import AngelOneAuthError, angel_auth

log = logging.getLogger("meridian.angelone.client")


class AngelOneOrderError(RuntimeError):
    """A broker call failed cleanly — Angel One responded and said no.

    Safe to treat as final: no order exists, nothing to reconcile.
    """


class AngelOneAmbiguousError(RuntimeError):
    """The call failed in a way that does not tell us whether Angel One
    actually received and processed it — a timeout, a connection drop, or a
    response so malformed it can't be trusted either way.

    This is the one that must never trigger a blind retry (§30): the caller
    has to query order status before doing anything else, because "the
    request failed" and "the order doesn't exist" are not the same fact.
    """


class AngelOneBrokerService:
    """connect / account / orders — every live call to Angel One goes through here."""

    # ── connection ────────────────────────────────────────────

    async def connect(self) -> Dict[str, Any]:
        await asyncio.to_thread(angel_auth.login)
        return angel_auth.status()

    async def disconnect(self) -> None:
        await asyncio.to_thread(angel_auth.logout)

    def status(self) -> Dict[str, Any]:
        return angel_auth.status()

    # ── account ───────────────────────────────────────────────

    async def get_profile(self) -> Dict[str, Any]:
        """Login already fetches the profile — no need for a second round trip."""
        await asyncio.to_thread(angel_auth.ensure)
        session = angel_auth.session
        if session is None:
            raise AngelOneOrderError("Not connected to Angel One.")
        return session.profile

    async def get_funds(self) -> Dict[str, Any]:
        client = await asyncio.to_thread(angel_auth.ensure)
        data = await asyncio.to_thread(client.rmsLimit)
        return self._unwrap(data, "funds")

    async def get_positions(self) -> List[Dict[str, Any]]:
        client = await asyncio.to_thread(angel_auth.ensure)
        data = await asyncio.to_thread(client.position)
        return self._unwrap(data, "positions") or []

    async def get_orders(self) -> List[Dict[str, Any]]:
        client = await asyncio.to_thread(angel_auth.ensure)
        data = await asyncio.to_thread(client.orderBook)
        return self._unwrap(data, "orders") or []

    async def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        orders = await self.get_orders()
        for order in orders:
            if order.get("orderid") == order_id:
                return order
        return None

    async def get_quote(self, exchange: str, trading_symbol: str,
                        token: str) -> Dict[str, Any]:
        client = await asyncio.to_thread(angel_auth.ensure)
        data = await asyncio.to_thread(
            client.ltpData, exchange, trading_symbol, token)
        return self._unwrap(data, "quote")

    # ── orders ────────────────────────────────────────────────
    #
    # These place/modify/cancel real orders. Nothing here decides whether a
    # trade *should* happen — the caller (execution service) has already run
    # every pre-flight check by the time these are invoked. This layer's only
    # job is submitting exactly what it's told and reporting exactly what
    # Angel One says happened.

    async def place_order(self, params: Dict[str, Any]) -> str:
        """Returns the Angel One order id.

        Uses `placeOrderFullResponse`, not `placeOrder` — the plain variant
        swallows the broker's rejection message and returns bare `None` on
        failure, which makes a clean rejection indistinguishable from a
        network failure. The full-response variant keeps the message, which
        is what makes the ALLOW/REJECT distinction below possible.

        Raises `AngelOneOrderError` when Angel One responded and explicitly
        rejected the order — final, no order exists, nothing to reconcile.
        Raises `AngelOneAmbiguousError` when the outcome is genuinely
        unknown (network failure, malformed response) — the caller must
        query order status before concluding anything, never retry blindly.
        """
        client = await asyncio.to_thread(angel_auth.ensure)
        try:
            data = await asyncio.to_thread(client.placeOrderFullResponse, params)
        except Exception as exc:  # noqa: BLE001 - network/SDK failure: outcome unknown
            raise AngelOneAmbiguousError(
                "Angel One order submission did not complete cleanly — "
                "outcome unknown, not retrying blindly: {}".format(exc)) from exc

        if data is None:
            # The SDK itself returns bare None on some malformed responses,
            # not just on a clean rejection — same "we don't actually know"
            # situation as a network failure.
            raise AngelOneAmbiguousError(
                "Angel One returned no response to the order request — "
                "outcome unknown, not retrying blindly.")

        if not data.get("status"):
            raise AngelOneOrderError(
                "Angel One rejected the order: {}".format(
                    data.get("message", "no reason given")))

        order_id = (data.get("data") or {}).get("orderid")
        if not order_id:
            raise AngelOneAmbiguousError(
                "Angel One reported success but returned no order id — "
                "outcome unknown, not retrying blindly: {}".format(data))
        return order_id

    async def modify_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        client = await asyncio.to_thread(angel_auth.ensure)
        try:
            data = await asyncio.to_thread(client.modifyOrder, params)
        except Exception as exc:  # noqa: BLE001
            raise AngelOneOrderError(
                "Angel One order modification failed: {}".format(exc)) from exc
        return self._unwrap(data, "modify")

    async def cancel_order(self, order_id: str, variety: str = "NORMAL") -> Dict[str, Any]:
        client = await asyncio.to_thread(angel_auth.ensure)
        try:
            data = await asyncio.to_thread(client.cancelOrder, order_id, variety)
        except Exception as exc:  # noqa: BLE001
            raise AngelOneOrderError(
                "Angel One order cancellation failed: {}".format(exc)) from exc
        return self._unwrap(data, "cancel")

    # ── internals ─────────────────────────────────────────────

    @staticmethod
    def _unwrap(data: Optional[Dict[str, Any]], what: str) -> Any:
        if not data or not data.get("status"):
            message = (data or {}).get("message", "no response")
            raise AngelOneOrderError(
                "Angel One {} request failed: {}".format(what, message))
        return data.get("data")


angel_broker = AngelOneBrokerService()
