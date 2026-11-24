# 56. A write whose worker disappeared resumes automatically when the site allows it

**Status**: Accepted
**Date**: 2026-11-24
**Source**: `dev/specs/030-automatic-resume/spec.md` (FR-002, FR-004, FR-008 to FR-020),
`dev/specs/030-automatic-resume/research.md` (R1 to R5, R8 to R12, R15)

## Context

[ADR 0027](0027-an-interrupted-apply-resumes-from-its-saved-position.md) made an interrupted
write resumable: the worker saves a progress marker, `verify` reports each uncertain operation,
and `runs resume` applies the rest of the saved plan on the same run. A person runs each of
those steps. The liveness reconciler of the Sync API, which ends a claimed write execution
whose worker is gone, states the matching rule: no rule submits or resubmits work, and an
execution is never replayed after any verdict.

Two of the reconciler's rules end a write because its worker disappeared, not because the
write failed:

- Rule 6: Prefect reports the flow run in a terminal state while the execution has no verdict.
- Rule 8: the work pool no longer shows the claiming worker as fresh (online, with a heartbeat
  within `max(3 * heartbeat interval, 30)` seconds).

After such an ending, a scheduled run, or a long apply that nobody watches, stays interrupted
until a person runs `verify` and `runs resume`. The steps the person runs are always the same
when the check finds nothing that differs, and a write whose operations are convergent upserts
is safe to repeat (ADR 0027).

## Decision

- **The setting.** `INFRAHUB_SYNC_AUTOMATIC_RESUME` turns automatic resume on for the whole
  Sync API. It is off by default and takes the values of `INFRAHUB_SYNC_REQUIRE_APPROVAL`. A
  value that is not accepted stops the Sync API at startup. The reconciler reads it when it ends
  an execution. While it is off, the reconciler records nothing for automatic resume, so a site
  that does not turn it on keeps the behavior of ADR 0027. A row recorded while the setting was
  on continues after the setting is turned off.
- **Which endings count.** The reconciler records a write (`apply`, `sync` or `resume`) for
  automatic resume when it ends the write under rule 8, or under rule 6 when Prefect reports the
  flow run as `crashed`. `failed` means that the flow raised an error the worker could
  not record, `cancelled` means that a person cancelled the flow run in Prefect, and `completed`
  without a verdict is a defect; none of them means that the worker disappeared. A failure that
  the worker recorded itself never reaches the reconciler.
- **A record for each interrupted execution.** In the transaction that ends the execution, the
  product store inserts a row in the table `automatic_resumes`, keyed by the run and the flow
  run of the interrupted execution. The row either waits for the check (`waiting-check`) or ends
  at once as `no-resume` with the first reason that applies, in this order:
    - `not-resumable`: the run has no saved plan, was cancelled, or is a scheduled run with
      branch review.
    - `approval-missing`: the Sync API requires an approved plan and the run has no approval.
    - `no-progress`: the interrupted execution was an automatic resume, and the progress marker
      did not move past the position that resume started after.
    - `limit-reached`: the run already received 3 automatic resumes. A resume that a person
      started does not count.
- **The steps.** Each loop of the reconciler moves every row that is not `ended` by one step:
  `waiting-check`, `checking`, `waiting-resume`, `resuming`, `ended`. Each move is a
  compare-and-set on the row. A request that renders a run never moves a row. A row submits its
  check or its resume only while the pool has a live worker, so the time it waits does not count
  against the admission time limit.
- **The check.** The check is a `verify` of the run, submitted through `RunService.verify_run`.
  The reconciler reads only the report that the check's own flow run wrote, and a report with no
  per-operation comparison with the destination is not a pass. The row ends `no-resume` when the
  check fails or its report is not usable (`check-failed`), when it reports an operation
  as `differs` (`check-found-differs`), or when no worker picks it up (`not-picked-up`). The
  2,000-operation limit of `verify` applies: `differs` is counted among the operations the check
  reported, and the row records how many operations the check left out
  (`operations_not_checked`). When the check passes and reports no `differs`, the row moves to
  `waiting-resume`, even when the check left operations out.
- **The resume.** The resume is submitted through `RunService.resume_run`, so every condition of
  `runs resume` applies again: the checksum, the write lock, the pre-write checks, the
  confirmation of left-out records and the approval. The receipt stores a fingerprint of the
  request, not the request, so the confirmation of left-out records is derived: it is given when
  the plan leaves out records and the interrupted write was an `apply` or a `resume` with a
  receipt; a scheduled `sync` uses the schedule's `write_when_left_out`; a manual `sync` gets
  none. A refusal of the check or the resume with a 4xx status ends the row as `refused` with
  its code. A refusal with a 5xx status, such as `orchestration-unavailable` or
  `plan-unavailable`, says the service could not serve the request at that time: the next loop
  tries again with the same key and logs one line for each try. A resume that no worker picks
  up ends the row as `not-picked-up`.
- **Exactly once.** The reconciler submits first and compare-and-sets the row second, with the
  new flow run identifier. The idempotency keys are fixed:
  `automatic-check:<run>:<interrupted flow run>` and `automatic-resume:<run>:<interrupted flow
  run>`. A restart of the Sync API, or a second Sync API process, between the submission and the
  move repeats the submission with the same key, which replays the stored receipt instead of
  creating a second execution.
- **The reserved actor.** The check and the resume run as `sync:automatic-resume`, which the
  answers show as `Sync (automatic resume)`. It is not an account: account identifiers start
  with `acct_`, account names cannot take the value, and HTTP authentication refuses it from any
  credential. Ownership accepts it only for `verify` on a run whose row is `waiting-check`, and
  for `resume` on a run whose row is `waiting-resume`.
- **A person takes over.** A `verify` or `runs resume` from a person while a row waits
  (`waiting-check` or `waiting-resume`) ends the row as `person-took-over` once its receipt is
  reserved and not refused, and the request then proceeds as before. A refused request, such as a
  resume refused with `verify-required`, leaves the row waiting. The event names the person as its actor. When the automatic submission of
  that row already has a receipt, the person's request is refused with
  `409 run-execution-conflict`. While a row is `checking` or `resuming`, a person's request is
  refused with the same code, because an execution of the run is unfinished.
- **Schedules.** A run with a row that is not `ended` is in progress for the overlap policy.
  The schedule's failure count is settled once, when the chain of rows ends: a resume that
  applies the plan sets the count to zero; a row that ends `no-resume`, or a resume that fails,
  counts as one failed run.
- **Events.** `infrahub_sync.run.automatic_resume_started` is recorded when a row moves to
  `resuming`, and `infrahub_sync.run.automatic_resume_not_started` when a row ends `no-resume`.
- **The rule of the reconciler.** "An execution is never replayed" becomes "an execution is never
  replayed, except an automatic resume after rule 6 (`crashed`) or rule 8 when the site turned it
  on". The new execution is a `verify` and then a `resume`; the ended execution is never
  submitted again. Principle I of the constitution names automatic resume as the second case
  in which a write runs with no person present.

This decision amends ADR 0027, whose steps a person ran, and the liveness reconciler's rule that
no execution is replayed.

## Consequences

- On a site that turns the setting on, a run whose worker disappeared reaches `applied` without
  a person when the check finds no node that differs. A run that Sync does not resume keeps its
  row, its reason and its event, and a person resumes it with `verify` and `runs resume` as
  before.
- Every row has a reason in words in `GET /runs/{run_id}` (`automatic_resumes`) and in
  `infrahub-sync runs show`. The run list shows `automatic_resume_count`.
- When more than 2,000 operations are uncertain, the check reads only the first 2,000. A node
  that differs among the rest is not found before the resume writes the plan's values over it.
  The row and the event record how many operations the check left out.
- Within one Sync API process, a person who takes over cannot race the reconciler: the lookup of
  the receipt and the move of the row run without an `await` between them. With several Sync API
  processes, a short window remains in which the reconciler submits after a person took over.
  The reconciler logs the warning "automatic resume submitted after a person took over" with the
  flow run. A resume can then run under a `person-took-over` row and does not count toward the
  limit.
- The reconciler reads the position the resume starts after before it submits. After a restart
  between the submission and the move, the next loop reads the position again, later than the
  true start. The next row can then end as `no-progress` although the resume moved the
  marker, which leaves the run for a person instead of resuming it.
- When a scheduled run's resume ends before its flow run is recorded on the row (a restart
  between the submission and the move), the schedule does not count that run.
- The limit of 3 automatic resumes and the limit of 2,000 operations of the check are
  constants. No source confirms the limit of 3 yet.
- Pre-release V3 databases are recreated, not migrated, for the new table, as ADR 0010 states.

## Alternatives considered

**A separate background task records the rows.** It would need its own rule for which process leads, and
it would read the interruption after the fact, with no way to tell rule 6 from rule 8. The
reconciler is the one place that holds the cause, and it writes the row in the transaction that
ends the execution.

**The worker records the rows.** A worker that disappeared cannot record anything about its own run.

**Every terminal Prefect state of rule 6 counts.** It would resume a run that a person cancelled
in the Prefect interface. Counting `crashed` and `failed` would resume a run whose worker raised
an error that it could not record.

**Submit the check and the resume in the transaction that ends the execution.** Submitting
talks to Prefect, which cannot be part of a database transaction.

**One Prefect flow that runs the check and then the resume.** ADR 0027 requires a passing `verify`
newer than the latest write before a resume, so the check and the resume stay separate
executions.

**Move the row first and submit second**, the order that research R3 and R4 of the spec first
described. Ownership accepts the reserved actor only while the row waits, so a row already moved
to `checking` or `resuming` would refuse its own submission, and the row could not hold the flow
run identifier of an execution that was not submitted yet. Submitting first, with a fixed
idempotency key, makes a repeated submission replay the stored receipt.

**A new submission path for automatic executions.** It would copy the admission checks of
`verify` and `runs resume` and drift from them.

**Act as the run's own actor, or as an administrator.** As the run's actor, the events would say
that the person resumed the run, which is false. An administrator principal would bypass
ownership for every run and would name an account that did not act.
