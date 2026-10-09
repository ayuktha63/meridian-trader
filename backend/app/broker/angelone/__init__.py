from .auth import AngelOneAuthService, angel_auth
from .client import (
    AngelOneAmbiguousError, AngelOneBrokerService, AngelOneOrderError, angel_broker,
)
from .instruments import AngelOneInstrumentService, angel_instruments

__all__ = [
    "AngelOneAuthService", "angel_auth",
    "AngelOneBrokerService", "angel_broker",
    "AngelOneAmbiguousError", "AngelOneOrderError",
    "AngelOneInstrumentService", "angel_instruments",
    "AngelOneExecutionService", "angel_execution",
]


def __getattr__(name: str):
    # The execution service imports the risk gate, which imports this package
    # for the auth and instrument services. Loading it on first use rather
    # than here is what keeps that from being a cycle.
    if name in ("AngelOneExecutionService", "angel_execution"):
        from . import execution
        return getattr(execution, name)
    raise AttributeError("module {!r} has no attribute {!r}".format(__name__, name))
