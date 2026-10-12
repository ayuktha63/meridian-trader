"""Which changes of state a live execution may make.

The supervisor sets an execution's status from what the broker reports, in
a dozen places. Written down in one table, the lifecycle can be checked
instead of assumed — the pattern order models in event-driven engines use
(a status and an event give the next status, and anything else is a bug).

Two strengths of rule, deliberately:

* **Finished is finished.** CLOSED and CANCELLED never become anything
  else. An execution brought back from either would be supervised again,
  and supervision places orders. That is refused outright.
* **Anything else unexpected is reported, not blocked.** The table is this
  code's understanding of the lifecycle, and it has never been checked
  against the real broker. Refusing a change it did not foresee could leave
  a real position without its stop; so it raises a critical alert and lets
  the supervisor carry on.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from sqlalchemy import event

from .. import alerts
from ..db.models import LiveExecutionRow
from ..schemas.execution import ExecutionStatus as S

#: The entry order has been, or is being, sent and has not finished.
ENTRY = frozenset({S.SUBMITTING, S.UNKNOWN, S.SUBMITTED, S.OPEN, S.PARTIALLY_FILLED,
                   S.CANCEL_PENDING})
#: Nothing further happens to these.
FINAL = frozenset({S.CLOSED, S.CANCELLED})
_BEFORE = frozenset({S.RECOMMENDED, S.PENDING_CONFIRMATION, S.VALIDATING})

TRANSITIONS: Dict[S, FrozenSet[S]] = {
    **{state: frozenset(_BEFORE | {S.SUBMITTING, S.REJECTED}) for state in _BEFORE},
    # An entry moves among its working states, fills, dies, or — if part of
    # it filled — goes straight to being closed.
    **{state: frozenset(ENTRY | {S.FILLED, S.CANCELLED, S.REJECTED, S.CLOSING, S.CLOSED})
       for state in ENTRY},
    S.FILLED: frozenset({S.CLOSING, S.CLOSED}),
    # An exit that died puts the position back under supervision.
    S.CLOSING: frozenset({S.FILLED, S.CLOSED}),
    # Refused before it reached the broker: the same recommendation may be tried again.
    S.REJECTED: frozenset({S.SUBMITTING}),
    S.CLOSED: frozenset(),
    S.CANCELLED: frozenset(),
    # Defined by the schema and written by nothing. A row found in it is
    # not understood, so nothing is expected of it: any change is reported.
    S.ERROR: frozenset(),
}


class IllegalTransition(RuntimeError):
    """A finished execution was about to be given a new state."""


def _state(value) -> Optional[S]:
    try:
        return S(value)
    except ValueError:
        return None


def allowed(old, new) -> bool:
    before, after = _state(old), _state(new)
    if before is None or after is None:
        return False
    return before == after or after in TRANSITIONS[before]


def check(old, new, subject: str = "") -> None:
    """Raise for a change out of a final state; alert for any other change
    the table does not list."""
    if allowed(old, new):
        return
    before = _state(old)
    if before in FINAL:
        raise IllegalTransition(
            "Execution {} is {} and cannot become {}.".format(subject, old, new))
    alerts.emit("critical", "UNEXPECTED_TRANSITION",
                "A live execution changed state in a way its lifecycle does not "
                "describe. Check it against Angel One.", throttle=60.0,
                execution=subject, was=str(old), now=str(new))


@event.listens_for(LiveExecutionRow.status, "set", propagate=True)
def _on_status(target, value, oldvalue, initiator):  # noqa: ARG001 - SQLAlchemy's signature
    if isinstance(oldvalue, str) and oldvalue != value:
        check(oldvalue, value, getattr(target, "id", "") or "")
