"""Research: finding out whether a strategy has an edge, reproducibly.

Nothing in this package can place an order. It reads history, simulates,
measures and records; what it concludes reaches live trading only through
`eligibility`, which can refuse but never approve on its own.
"""
