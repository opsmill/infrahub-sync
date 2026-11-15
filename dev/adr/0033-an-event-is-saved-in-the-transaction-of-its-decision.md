# 33. An event is saved in the same transaction as the decision it records

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/012-sync-run-events-logs/research.md` (R2, R6, R10),
`dev/specs/archive/012-sync-run-events-logs/data-model.md` ("Event")

## Context

A person who operates Sync views the history of a run as a list of events: the request was
accepted, the plan was computed, the apply was requested, the apply ended. The Sync API returns
this list from `GET /events`, and the CLI prints it with `infrahub-sync events`.

Before this decision, the Sync API wrote an audit record for each request it accepted or
refused, and nothing else:

- The audit record was written by a second store call, with its own connection and commit, after
  the call that saved the decision. A failure between the two calls left a decision with no audit
  record.
- The worker wrote no audit record when a stage ended, so the history did not hold the result of
  a plan or an apply.
- An audit record held an actor, an operation, a reason and an outcome. It did not name the
  configuration, the configuration version or the branch, so the list could not be filtered by
  them.

## Decision

An event is a Sync audit record with a type. The event of an accepted request, of a stage result
and of a claimed stage that the liveness check interrupts or cancels is saved by the store call
that saves the answer or the result it records, in the same database transaction.

- **One transaction.** The store methods that save an answer or a result take the event as an
  argument and insert it with the same cursor, before their commit. When the insert fails, the
  whole transaction is rolled back, so the answer or the result is not saved either. The methods
  are:
    - `complete_mutation`, `record_approval`, `cancel_planned_run` and
      `acknowledge_execution_cancellation`, which save the answer to an accepted request in the
      request's receipt;
    - `commit_claimed_execution`, which saves a stage result;
    - `cancel_execution` and `interrupt_execution`, which save the end of a stage that the
      liveness check cancels or interrupts.

  `acknowledge_execution_cancellation` also takes the events of the two refusals it can store
  when the stage ended before the cancellation was acknowledged, and inserts the one it stores
  in the same transaction.
- **Decisions whose event is written alone.** The event of a replayed request, of a request
  that could not be confirmed, and of every other refused request is written by a call of its
  own, `record_audit`.
- **Who writes which event.** The Sync API writes the events of requests (accepted, refused,
  replayed, unavailable, cancelled) and of configuration changes. The worker writes the event of
  each stage result. The liveness check writes the event of a claimed stage that it interrupts
  or cancels.
- **Type.** Each event has a type of the form `infrahub_sync.<run or config>.<action>`.
  `infrahub_sync/service/events.py` builds the type of a request decision from the outcome of
  the request, and the type of a stage result from the stage and its outcome. An accepted
  configuration request takes its type from what it created: `config.registered` for a new
  configuration, `config.version_registered` for a new version, and `config.version_retrieved`
  when the content matches an existing version and the Sync API returns that version. The Sync
  HTTP API reference lists the types.
- **Resources.** The `audit_events` table holds the type, the configuration ID, the configuration
  version and the branch. The table `audit_event_resources (event_id, kind, resource_id)` holds
  the resources an event relates to: the account, the configuration, the configuration version
  and the branch. The filter on a related resource reads this table.
- **Actor of a stage result.** The event of a stage result names the actor of the request that
  started that stage. When that request cannot be found or read, it names the actor of the run.
- **Requests with a missing or invalid token.** The Sync API still stores an audit record for
  such a request, as evidence. The record has no type, and `GET /events` never returns it.
- **Older stores.** When the store opens, it gives a type to each older record from its operation
  and outcome, except the record of a request with a missing or invalid token, and writes the
  account resource of each older record. Both steps run at every start and change only records
  that they have not changed yet.
- **Paging.** `GET /events` takes `limit` and `offset`, the names Infrahub uses, and returns
  events ordered by time and then by event ID.

## Consequences

- The answer to an accepted request, a stage result, and the interruption or cancellation of a
  claimed stage by the liveness check are never stored without their event, and such an event is
  never stored when the answer or the result was not saved.
- The liveness check ends two kinds of execution with no event, because the store methods take
  none: an execution that no worker claimed in time (`abandon_execution`), and a cancellation
  whose recovery deadline passed (`expire_execution_cancellation`).
- A new store method that saves the answer to an accepted request or a stage result takes the
  event and inserts it before its commit. Code must not save the answer first and call
  `record_audit` afterwards.
- The transaction covers the answer, not every record the request creates. Three cases save a
  record and its event in separate transactions, so a failure between the two leaves the record
  with no event:
    - A request for a plan, a sync, an apply or a resume. The run record and the link to its
      flow run are saved before the answer. The `accepted` event is saved with the answer
      (`complete_mutation` in `RunService._submit`).
    - A configuration registration and a new configuration version. The configuration version is
      saved by its own store call. The event is saved afterwards, with the answer
      (`complete_mutation` in `ConfigurationRoutes.mutate`).
    - A refusal that the store decides inside a call: an approval, a withdrawal of a plan or a
      resume that the store refuses, and a cancellation that the worker or the liveness check
      answered first. The store saves the refusal as the answer in the request's receipt, and
      the Sync API then writes the refusal event with `record_audit` (`approve_run`,
      `_withdraw_planned_run`, `resume_run` and `cancel_run` in
      `infrahub_sync/service/service.py`).
- A new request outcome or a new stage needs a type in `infrahub_sync/service/events.py`.
  Without one, building the event raises `ValueError`.
- An event passes through the same secret redaction as every other stored record.
- A reader who pages by `offset` while events are being written can skip one event or read one
  event twice, when an event with an earlier time is saved late. The API reference states this
  limit.
- The configuration, the version and the branch are stored twice: as columns and as related
  resources. Nothing checks that the two agree.
- An older record names no configuration and no branch, because the old record did not hold
  them.

## Alternatives considered

- **A unit-of-work object shared across store calls.** Rejected: it is a new abstraction with
  one use, and each store method already wraps its write in one transaction.
- **Stop storing the record of a request with a missing or invalid token.** Rejected: the record
  is evidence that the run discovery tests rely on.
- **Page with an opaque cursor, as `GET /runs` does.** Not chosen: `limit` and `offset` are the
  names Infrahub uses for its events, and the events were to follow Infrahub's layout.
- **Send Sync's events to Prefect.** Not chosen. The specification lists it as out of scope and
  gives no reason.
