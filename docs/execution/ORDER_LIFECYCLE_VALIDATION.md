# Order lifecycle validation

**Read 14 October 2026**, against `backend/app/broker/order_states.py`,
`backend/app/broker/angelone/execution.py`, `reconciler.py`, and the tests
that exercise them. Phase 3 of the master engineering prompt asked for an
explicit order state machine, duplicate-submission prevention, partial-fill
handling, and layered broker validation. Most of this already existed
before this pass — this document audits it against the prompt's own list
rather than replacing it, and states plainly what's still open.

## 1. The state machine

`ExecutionStatus` (`backend/app/schemas/execution.py:16-31`) has 14 states.
Mapped against the prompt's named list:

| Prompt's name | This system's name | Note |
|---|---|---|
| CREATED | `RECOMMENDED` | a stored analysis, before any execution attempt |
| APPROVED | `PENDING_CONFIRMATION` → `VALIDATING` | split into two: confirmed by the user, then passing the gate |
| RISK_REJECTED | `REJECTED` | gate and broker rejections share one state (see §5, a real gap) |
| SUBMISSION_PENDING | `SUBMITTING` | |
| SUBMITTED | `SUBMITTED` | |
| ACKNOWLEDGED | `OPEN` | broker has it, still working |
| PARTIALLY_FILLED | `PARTIALLY_FILLED` | exact match |
| FILLED | `FILLED` | exact match |
| CANCEL_PENDING | `CANCEL_PENDING` | exact match |
| CANCELLED | `CANCELLED` | exact match |
| UNKNOWN | `UNKNOWN` | a submission whose outcome timed out — see §3 |
| RECONCILIATION_REQUIRED | *(no exact state)* | handled as a critical alert instead — see §4 |

Plus two the prompt didn't name but this system needs: `CLOSING` (an exit
order working) and `CLOSED` (position flat, position accounted for).

**Legal transitions are a table** (`order_states.py:33-51`), enforced by a
SQLAlchemy event listener on `LiveExecutionRow.status` itself
(`order_states.py:91-94`) — not by a wrapper function someone has to
remember to call. A write to that column from *any* code path, including a
row freshly loaded from the database, is checked
(`test_observability.py:196-`, `test_the_check_also_guards_rows_loaded_from_the_database`).

Two rules, both tested:
- **Finished is finished.** `CLOSED`/`CANCELLED` → anything raises
  `IllegalTransition` (`test_a_finished_execution_cannot_be_brought_back`,
  `order_states.py:65-67`, `70-74`).
- **An unforeseen transition is reported, not blocked** — a critical alert,
  because refusing a change the table didn't anticipate could leave a real
  position without its stop (`test_any_other_surprise_is_reported_and_allowed`).

Coverage already includes: every state has a table entry
(`test_the_ordinary_life_of_an_order_is_in_the_table`), the retry-after-
rejection path, an unfilled-to-filled walk, and — the one the prompt asks
for by name — recovery across a restart
(`test_reconciler.py:459`, `test_an_order_in_flight_across_a_restart_is_recovered_by_tag`).

## 2. Duplicate orders and idempotency

Already tested, not newly built:
- `test_duplicate_execution_is_blocked_after_a_successful_submit`
- `test_ambiguous_response_does_not_retry_and_queries_status` — a timed-out
  submission is never retried blind; the broker is asked first.
- `test_ambiguous_response_recovers_only_the_order_carrying_our_tag` /
  `…_never_adopts_a_lookalike_order` — recovery by an order tag, not a
  price/quantity guess.
- `test_retry_after_pre_broker_rejection_is_allowed` — a *gate* rejection
  (never reached the broker) is retriable; a broker-side ambiguity is not.

## 3. Partial fills and protective stops

Covered by the reconciler's own test file (not re-audited line by line
here, since `docs/EXECUTION_AND_ORDER_LIFECYCLE.md` already covers it): a
partial fill shrinks the stop to what's actually held, a target fill and a
stop fill both arriving is handled, and — the hard rule with no setting —
**no stop is ever placed for a position Angel One does not itself show is
open.**

## 4. What's genuinely still open

- **No real broker has ever carried an order.** Every claim above is
  proven against an in-memory fake (`tests/fake_angel.py`), which is honest
  about what it can and can't prove: status words, the order-tag echo, and
  the product field on positions are from Angel One's documentation, not
  from watching a real fill. Layer B/C/D of the prompt's §3.4 (sandbox,
  controlled live verification) cannot be completed without either a
  broker sandbox Angel One does not appear to offer, or a real authorized
  order — which this session will not place on its own initiative.
- **`RISK_REJECTED` and a broker-side `REJECTED` share one state.** A gate
  refusal (nothing ever reached Angel One) and an exchange rejection (it
  did, and was refused there) are distinguishable today only by whether
  `broker_order_id` is set on the row — not by the state name itself. Low
  risk (nothing acts on the state name alone), but a real, nameable gap if
  someone later builds a dashboard that reads the enum value as the whole
  story.
- **No distinct `RECONCILIATION_REQUIRED` state.** The critical-alert path
  serves the same purpose functionally, but doesn't show up as a filterable
  execution status the way the prompt's list implies it should.

## 5. Conclusion

Phase 3's core ask — an explicit, enforced state machine with real
adversarial test coverage, including restarts and ambiguous broker
responses — was already built and tested before this pass, to a standard
at least as strict as the prompt describes. What remains open is either
unclosable without a real broker (stated as exactly that, not hidden) or a
small naming/granularity gap (§4) worth fixing in a future pass, not urgent
enough to justify restructuring a working, tested state machine today.
