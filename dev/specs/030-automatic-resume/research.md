# Research: Automatic resume of an interrupted apply

Each entry gives the decision, the reason, and the alternatives that were rejected. The code references are to
`feature/v3-develop` at `44743494`.

## R1. Where Sync decides

**Decision**: The liveness reconciler (`RunLivenessReconciler` in `infrahub_sync/service/liveness.py`) decides. When
it ends a claimed write execution under rule 6 or rule 8 (`reconcile_execution`, the two `interrupt_execution`
calls), the product store writes the decision row in the same transaction as the interruption.

**Reason**: Rules 6 and 8 exist only in the liveness reconciler, so this is the one place that knows the cause. The
same transaction means a crash between "interrupted" and "decided" cannot happen (FR-013, SC-005). The reconciler
already runs as a loop in the Sync API lifespan (`infrahub_sync/service/app.py`, `_lifespan`) and needs no new
process.

**Rejected**:

- A separate background task. It would need its own leader rule and would read the interruption after the fact,
  with no way to tell rule 6 from rule 8.
- The worker. A worker that disappeared cannot decide anything about its own run.

## R2. Which rule 6 endings count as "worker disappeared"

**Decision**: Rule 6 counts only when Prefect reports the flow run as `crashed`. A flow run that Prefect reports as
`failed`, `cancelled` or `completed` while the execution has no verdict does not count.

**Reason**: Prefect sets `crashed` when the flow's process or its infrastructure ended. `failed` means the flow
raised an error: the worker was running and reported a failure, but its failure record did not reach the store, for
example because the store was unreachable. FR-002 excludes a failure the worker reported (critique E1). `cancelled`
means a person cancelled the flow run in Prefect, outside the Sync API, and FR-002 excludes cancellations.
`completed` with no verdict means the worker ended its flow without recording its end, which is a defect, not a lost
worker. A worker that recorded its own failure sets `terminal_at` first, so the reconciler never sees it
(`reconcile_execution` returns early when `terminal_at` is set).

**Rejected**:

- Every terminal Prefect state (`_TERMINAL_STATES`). It would resume a run that a person cancelled in the Prefect
  interface.
- `crashed` and `failed`. It would resume a run whose worker raised an error it could not record.

## R3. How the decision is driven: a stored state machine stepped by the reconciler

**Decision**: A new table `automatic_resumes` holds one row for each interrupted write execution that the reconciler
considered. The row moves through these states, each move a compare-and-set on the row:

1. `waiting-check`: decided "resume, subject to the check"; no execution submitted yet.
2. `checking`: the automatic check (a `verify` execution) is submitted.
3. `waiting-resume`: the check passed with no `differs`; the resume is not submitted yet.
4. `resuming`: the resume execution is submitted.
5. `ended`: final. The row keeps the decision (`resume` or `no-resume`) and its reason.

Each loop of the reconciler (`reconcile_once`, the background loop), after its existing pass, steps every row that
is not `ended`. The request-time `reconcile_run`, which runs before a run is rendered, may insert a row, because the
insert is part of the interruption transaction, but it never steps a row: a `GET` request never submits a flow run
(critique E4).

The reconciler reaches `RunService` through a narrow protocol defined in `liveness.py`, `AutomaticResumeSubmitter`,
with `submit_check(run_id, key)` and `submit_resume(run_id, key, start_after)`. `RunService` satisfies it, and
`serve.py`, where the reconciler is built, wires it. `service.py` already imports from `liveness.py`, so a direct
import back would be a cycle (critique E2).

**Reason**: Each step is short and repeatable, and the state is in the store, so a restart of the Sync API continues
where it stopped (FR-014). A compare-and-set on the row lets several Sync API processes run the loop at the same time:
only one moves each row.

**Rejected**:

- Submitting the check and the resume inside the interruption transaction. Submitting talks to Prefect, which cannot
  be part of a database transaction.
- One Prefect flow that runs the check and then the resume. The check and the resume must stay separate executions,
  because ADR 0027 requires a passing `verify` newer than the latest write before a resume.

## R4. Exactly once: deterministic idempotency keys

**Decision**: The reconciler submits the check and the resume through the existing `RunService.verify_run` and
`RunService.resume_run` paths, with idempotency keys derived from the decision row: `automatic-check:<run_id>:<flow
run id of the interrupted execution>` and `automatic-resume:<run_id>:<same>`.

**Reason**: The mutation receipts already give exactly-once submission for a repeated request with the same actor
and key (`_lookup_existing_receipt`, `reserve_resume`). A restart between storing `checking` and the Prefect
submission repeats the call with the same key, which replays the same receipt instead of creating a second execution
(FR-014, SC-005). Reusing the paths also reuses every check of `verify` and `runs resume`: ownership aside, the
checksum, the write lock, the pre-write checks, the left-out confirmation and the approval (FR-008).

**Rejected**: a new submission path for automatic executions. It would copy the admission checks and drift from them.

## R5. The actor of an automatic check and resume

**Decision**: A reserved actor value, `sync:automatic-resume`, used only by the reconciler. It is not an account:
account identifiers start with `acct_` (`ACCOUNT_ID_PATTERN`), so the value cannot collide with one, and the account
name middleware (`infrahub_sync/service/presentation.py`) leaves it alone. The middleware gains one rule: it shows
this value as `Sync (automatic resume)`. `_owned_run` accepts this actor for a run that has a decision row in state
`waiting-check` or `waiting-resume`, and for no other run. No HTTP credential resolves to this actor.

**Reason**: Receipts and events require an actor (`MutationReceipt.actor` has `min_length=1`). An administrator
principal would bypass ownership for every run; the narrow rule allows only the runs the reconciler decided.

**Rejected**:

- Acting as the run's own actor. The events would say that the person resumed the run, which is false (FR-015).
- An administrator principal. Too broad, and it would show an account that did not act.

## R6. The left-out confirmation

**Decision**: The automatic resume passes `confirm_left_out=True` when the receipt that admitted the interrupted
write had it.

**Reason**: Spec 007 requires the confirmation before any write of a plan that left out records. The interrupted
write could not start without it, and the plan cannot change, so the person's confirmation covers the same plan.
A scheduled run takes it from the schedule's left-out setting, which already admitted the run.

**Rejected**: refusing every automatic resume of a plan that left out records. It would make automatic resume useless
for scheduled runs that write when records are left out.

## R7. Reading the check's result

**Decision**: When the check's `verify` execution ends, the reconciler reads the run's merged result
(`results["verification"]`, written by the `verify` branch of `_execute_stage` in `infrahub_sync/service/flow.py`):
`checks` for failed pre-write checks, the per-operation report for the count of `differs` and `missing`, and the count
of operations left out by `VERIFY_REPORT_LIMIT` (2,000, `infrahub_sync/plan/destination_check.py`). A failed or
abandoned execution ends the row as `no-resume` with the reason `check-failed` and the failure's cause.

**Reason**: The report already exists and is stored; nothing new is computed.

## R8. Waiting for a live worker

**Decision**: The reconciler submits a check or a resume only when the pool snapshot of its current loop has at least
one live worker, using the rule of `_owner_is_fresh` generalised to any worker: status online and a heartbeat within
`max(3 * heartbeat interval, 30)` seconds. The rule moves to one function in `liveness.py`, `live_workers(pool,
now)`, so that `/status` and, later, `/api/health` (INFP-663) use it too.

**Reason**: A submission with no live worker waits in the queue and the reconciler abandons it after the admission
time limit (rule 4, `INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS`, 300 seconds by default). Not submitting until a worker
is live means the waiting time does not count against that limit (FR-012).

A pool snapshot that is not available (`pool.detail_available` false) counts as "no live worker", and the row waits
(critique E3).

**Note**: If a submitted check or resume is abandoned anyway (the only live worker stopped right after the
submission), the row ends `no-resume` with the reason `not-picked-up`. A person can still resume.

## R9. The setting

**Decision**: `INFRAHUB_SYNC_AUTOMATIC_RESUME`, parsed by `_parse_flag` in `infrahub_sync/service/settings.py` like
`INFRAHUB_SYNC_REQUIRE_APPROVAL`, default `false`. The reconciler reads it when the interruption happens. While it is
off, no row and no event is written, so a site that did not turn it on sees no change (critique P1). A row already
written keeps going after the setting is turned off.

**Reason**: One flag, the same parsing rules, the same place as the other site-wide settings (FR-004).

## R10. Progress and the limits

**Decision**: When the reconciler writes a decision for an interrupted automatic resume, it compares the highest
saved progress marker of the run (`run_apply_progress`) with the marker the automatic resume started after
(`resume_start_after`, stored on the previous row). Equal means no progress: the new row ends `no-resume`,
`no-progress` (FR-009). The count of rows whose decision was `resume` and that reached `resuming` is the count of
automatic resumes; at 3, the new row ends `no-resume`, `limit-reached` (FR-010). Manual resumes have no row, so they
never count (FR-011).

## R11. Schedules

**Decision**:

- The interruption of a scheduled run's `sync` today settles the schedule's count as `failed` in the interruption
  transaction (`_settle_scheduled_run_end`). When the same transaction writes a decision row that is not `ended`, it
  settles `unchanged` instead. The count is settled later, once: `failed` when the row ends `no-resume`, `succeeded`
  when the automatic resume ends applied, and `failed` when the automatic resume ends failed. An automatic resume
  that is itself interrupted writes a new row and the same rule applies again (FR-018).
- `_run_in_progress` (`_SELECT_RUN_WITH_OPEN_EXECUTION`) also returns a run that has a row not in state `ended`, so
  the overlap policy skips or queues a start while the automatic resume waits or runs (FR-018).
- A scheduled run with branch review is refused by `_refuse_scheduled_branch_review` before any receipt. The
  reconciler checks the same condition first and writes `no-resume`, `not-resumable`, without trying (FR-003).

## R12. Approval

**Decision**: No new rule. `resume_run` calls `_require_matching_approval` when `approval_required()`. The reconciler
checks the same condition when it writes the decision row, so a missing approval ends the row at once as `no-resume`,
`approval-missing`, without a check. `approve` writes no decision row and starts nothing (FR-008).

## R13. The amendment of the constitution and the new ADR

**Decision**: Principle I gains a third bullet after the schedule case, and the constitution moves from 1.2.0 to
1.3.0 (a new named case is a minor change under its versioning rule). A new ADR, numbered after the highest number on
every branch at the time of writing (0055), amends ADR 0027 and replaces the liveness reconciler's "an execution is
never replayed after any verdict" with "an execution is never replayed, except an automatic resume after rule 6 or
rule 8 when the site turned it on".

## R14. Database changes

**Decision**: One new table `automatic_resumes` on SQLite and PostgreSQL, created by the start-up schema step. No
column changes on existing tables. Pre-release V3 databases are recreated, not migrated (ADR 0010).

## R15. A person acts while an automatic resume waits

**Decision**: When a person runs `verify` or `runs resume` on a run whose row is in `waiting-check` or
`waiting-resume`, the request ends the row as `no-resume`, `person-took-over` (FR-019), in its own transaction, and then proceeds as
today. While the row is in `checking` or `resuming`, the person's request is refused with the existing
`run-execution-conflict`, because an execution of the run is unfinished.

**Reason**: A waiting row has submitted nothing, so the person can take over without a race. Refusing the person
until a worker appears would block the operator on an automatic step they did not ask for.

## R16. What is not decided here

- The key of the issue about the 2,000-operation limit of `verify` (spec Open Questions).
- Whether 3 automatic resumes is the right limit (spec Open Questions). It is a constant in one place.
