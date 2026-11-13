# Research: Resume an interrupted apply

Each item records a decision, why it was made, and what was rejected. Code references are on
`022-resume-interrupted-apply`, which starts from `020-plan-problem-reporting` (PR #409).

## R1. How a killed worker's run becomes interrupted today

**Finding**: `infrahub_sync/service/liveness.py` calls `ProductProjection.interrupt_execution` for a claimed
execution whose worker stopped reporting. `_terminalize_execution` ends the execution and the run together, and
ADR 0010 derives `product_runs.reconciliation_required` from the verdict and the execution's purpose. A worker that
fails after its first proven dispatch ends `interrupted` / `ambiguous` through the same column.

**Decision**: a run is resumable exactly when `reconciliation_required` is true, its latest write execution is
terminal, the run is not cancelled, and its plan is saved (R14 added the last condition). No new detection is added
(spec FR-013; the user confirmed the existing interrupted state is the trigger).

## R2. Where the progress marker is stored

**Decision**: one nullable JSON column, `apply_progress`, on `prefect_executions`, added by the existing idempotent
start-up column migration (`store.py`, the `ALTER TABLE prefect_executions ADD COLUMN` loop). The document holds
`position`, `operation_id`, `total`, `saved_at`. The write is a conditional `UPDATE ... WHERE terminal_at IS NULL`,
the same guard spec 018 uses for `source_read` / `destination_read`, so a worker that keeps running after the
liveness monitor ended its execution changes nothing.

**Rationale**: spec 018 stores per-execution read state in exactly this way, and a run's executions already carry
`position` and `purpose`. One row update per save, about 180 on tier L, is negligible against 88,000 destination
writes (SC-003).

**Rejected**:

- A row per operation: about 88,000 inserts on tier L, and the order already makes one position sufficient.
- A row per kind: a crash late in a large kind repeats hours of writes.
- Storing the marker in the run sidecar or an artifact: artifacts are written once and the sidecar is local to the
  worker, so a killed worker's last state would be lost.

## R3. How the engine reports progress without the engine knowing about the store

**Decision**: `Potenda.apply_plan` accepts an optional `progress: ApplyProgressObserver` protocol (in
`infrahub_sync/potenda/apply_progress.py`) with `committed(index, operation)`, `kind_finished(index)`,
`stopped(index)` and `should_stop() -> bool`. The service worker passes a `StoreApplyProgressObserver`
(`infrahub_sync/service/apply_progress.py`) that throttles saves (every 500 commits, at the first commit after 30
seconds, after the plan's last operation, and on failure) and tracks the time of the last successful save. The CLI-local and test paths pass
nothing and behave as today.

**Rationale**: mirrors spec 018's `SideReadObserver` / `StoreSideReadObserver` split, which keeps the engine free of
store imports and keeps the save failure policy in the service.

**No save at a change of kind** (amended): the engine still calls `kind_finished(index)` when the next operation's
kind differs, but the observer saves only when `index` is the plan's last position. The plan is sorted by tier and
then by `operation_id`, a hash, so the kinds of one tier interleave; a save at every change of kind updated the row
about once per commit, against the "about 180 on tier L" of R2.

**Ordering (FR-002)**: the observer is told about operation `i` only after the destination call for `i` returned
and `applied.append` ran, so a saved position never runs ahead of a commit.

## R4. Save failures and the 5-minute stop

**Decision**: `StoreApplyProgressObserver` catches the store's errors broadly, as `service/read_progress.py` does
for the same reason (two unrelated database drivers), logs a warning with the run identifier and the error class,
and keeps the time of the first failed attempt since the last success. `should_stop()` returns true once that
first failed attempt is 5 minutes old and no save has succeeded since, so a quiet period between scheduled saves is
never counted (critique E4).
The engine checks it before each dispatch, next to `ownership.before_operation()`, and raises
`ApplyProgressLostError`, which carries the partial `ApplyRecord`. Because at least one write was dispatched, the
existing ADR 0010 exit records the run as interrupted.

When the store answers that the execution has already ended (`record_apply_progress` returns `ended`), for example
because liveness marked it interrupted while the worker kept writing, the answer is final: no later save can succeed.
`should_stop()` then returns true at once, the observer tries no further save, and the engine raises
`ApplyProgressLostError` before its next dispatch. A save that raises, or that names an unknown execution, keeps the
5-minute rule.

## R5. Admitting a second write on the same run

**Finding**: `write_admissions` has `run_id` as its primary key, and `_reserve_existing(admit_write=True)` refuses
any second write on a run (ADR 0010, "one run admits one write"). A resume is a second write.

**Decision**: a new table `resume_admissions (run_id, resume_number, receipt_id UNIQUE, PRIMARY KEY (run_id,
resume_number))`. A resume reservation runs in one store transaction on the run's locked row and requires:

- `reconciliation_required` is true;
- no unfinished execution on the run;
- the latest `verify` execution's position is greater than the latest write execution's position, and it succeeded
  (FR-012);
- no earlier resume admission is unfinished.

The original `write_admissions` row stays, so every existing reader keeps its meaning.

**`verify` on an interrupted run (critique E1)**: `_reserve_existing` refuses every stage on a run that holds a
`write_admissions` row. The reservation gains one exception: a `verify` (a read stage) is admitted when the run is
resumable (R1). Every other stage keeps today's refusal.

**Rejected**: changing the `write_admissions` primary key. It needs a table rebuild on both backends and changes
the meaning of a table that six code paths read.

**ADR 0010's rejected alternative**: ADR 0010 rejected "a write-attempt table with generations" because the
attempts would not say what reached the destination. The progress marker and the stronger `verify` now say that,
so a new ADR (0026) records the change.

## R6. Where a resume starts and what it skips

**Decision**: `apply_plan` gains `start_after: int | None`. Operations at index `<= start_after` are not
dispatched and are not listed as applied by this execution; skipped deletes keep being listed only once, by the
execution that reached them. Every pre-write check still runs on the whole plan before the first dispatch. The
resume's `ApplyRecord` lists only the operations it dispatched, because the uncertain window (R11) compares the
last of them with the marker; the run's progress view (FR-004) combines all executions through the latest marker.

**Counts of the whole run** (amended): the counts a run reports describe every execution of the run, not the latest
one.

- The engine's action counts, which become the run's summary when an apply or a resume succeeds, are the whole
  plan's. A resume that succeeds leaves every operation of the plan applied or recorded, so its summary is the one an
  apply that was never interrupted writes.
- `ApplyRecord.operations_written`, and the failure record's `operations_written`, count the creates and updates the
  run wrote: every create and update at or before `start_after`, plus this execution's applied operations. The
  "written N of M" line uses this count, and falls back to the length of `applied_operations` for a record written
  before the field existed.
- Each entry of `RunResource.resumes` keeps that resume's own count, `operations_written`: the creates and updates
  after its `start_after`, through the end of the plan for a resume that succeeded, and through the position of its
  own last marker otherwise. A resume that succeeded wrote its whole tail even when its last save failed (FR-003).

**Rule chosen**: the count before a resume is derived from the saved position and the plan, not stored by the
earlier executions. Positions are not used directly (`position + 1`), because a recorded delete has a position and
is never written. The rule is exact for every operation at or before the marker, because a saved position never
points past an operation that was not committed (FR-002), and the resume writes every operation after it again, so
none is counted twice.

**Limit after a kill**: an execution that was stopped may have written operations after its last marker. Those
operations are counted only once a later execution writes them again. So, after a kill:

- the run's "written" count of a resume that stopped is a lower bound when the earlier execution wrote past the
  position at which the resume stopped;
- a resume entry's count, taken from its own last marker, is a lower bound for a resume that was stopped.

No stored data gives the exact count in that case (R11), and `verify` reports those operations as uncertain.

## R7. The stronger `verify`

**Decision**: when the run is resumable (R1), the `verify` stage, after the pre-write checks pass, asks the
destination adapter for the nodes of the operations after the marker and compares the planned payload and
relationships with what it reads:

- an update is fetched by its recorded `destination_id`;
- a create is fetched by its complete human-friendly identifier, which plan format 3 and later guarantee
  (ADR 0013); R13 states the matching rule, including a kind with no human-friendly identifier;
- "already matches the plan" when every planned field and relationship equals the destination value, "differs from
  the plan" when the node exists with any difference, "missing at the destination" when it does not exist.

The report is stored under `results.verification.operations` with `{operation_id, kind, action, state}` per
operation, and `results.verification.operations_truncated` when the window is larger than 2,000 operations (only
possible when marker saves failed for a long time). The read is grouped by kind, one query per kind, to meet SC-004.

**Rejected**: checking every plan operation (out of scope; a new plan does that).

## R8. Clearing `reconciliation_required`

**Decision**: when a resume execution ends `succeeded` and the whole plan is applied, `_terminalize_execution`
clears `reconciliation_required` for the run in the same transaction and sets the run's phase to `applied`. ADR
0026 records this exception to ADR 0010's "nothing clears it".

## R9. `sync` runs

**Decision**: a `sync` run saves its plan and checksum before its apply part. A resume of a `sync` run is the same
resume stage with the run's saved plan; the flow never calls the plan stage for `purpose == "resume"`.

## R10. Surface

- HTTP: `POST /runs/{run_id}/resume` with `{expected_checksum, confirm_writes, confirm_left_out, reason?}` and an
  idempotency key, mirroring `POST /runs/{run_id}/apply`.
- CLI: `infrahub-sync runs resume RUN_ID --expected-checksum C [--confirm-left-out] [--reason TEXT]`.
- Read: `GET /runs/{run_id}` gains `apply_progress` (marker and per-kind counts) and `resumes`; `runs show` prints
  them. `verify` results gain `operations`.

All changes are additive under `v3-unstable`.

## R11. Whether the last save attempt succeeded

**Question**: after the marker, how many operations may have reached the destination when the failing operation is
not known? The worker saves after 500 commits or 30 seconds, so after a successful save at most 500 commits follow
before the next attempt. After failed saves, the worker keeps committing for up to 5 minutes (R4), so any number of
operations may follow the marker.

**Stored data considered**: the marker's `saved_at`, the execution's `terminal_at`, and the worker heartbeat. None of
them tells a worker whose saves were failing from one whose saves succeeded:

- `terminal_at` is set by liveness at the time it notices the worker is gone, not at the time the worker stopped, and
  a worker can keep writing after it (R4).
- When the worker cannot reach the store, the store holds nothing the worker wrote after its last successful save; the
  worker heartbeat goes to the same store and is overwritten by later heartbeats.
- A time rule cannot bound commits: 500 commits can take less than a second, so a marker saved shortly before
  `terminal_at` can still be followed by more than 500 commits once a save failed.

**Decision**: the last save attempt counts as successful only when the latest write execution saved a marker of its
own and one of these holds:

- the execution is still running: its own marker is its last save, and the view is informational, because `verify`
  and a resume are admitted only once every execution has ended;
- the execution ended, its worker wrote its own failure record (one whose `flow_run_id` names it), the record is not
  `ApplyProgressLostError`, and the last operation the record lists in `applied_operations` is the one the marker
  names. The engine saves the last committed position just before that record is written, so the two agree exactly
  when that save succeeded.

In every other case, including a worker that was killed or whose execution liveness ended first, every operation
after the marker is uncertain. This makes the window wider for a killed worker than "the next 500"; `verify` reads at
most 2,000 of them (FR-005).

**Window size after a successful save**: counted in commits, not plan positions. A recorded delete is never
dispatched, so it is not a commit (FR-002). The window runs from the position after the marker through the 500th
create or update, and holds the recorded deletes between them. The progress view counts those deletes as uncertain,
because its counts are over plan positions; the `verify` report never lists a delete.

**Failure records**: results are merged across executions, so `<purpose>_failure` can belong to an earlier
execution of the same purpose. The worker stores the writing execution's `flow_run_id` in the record, and the view
uses the failing operation only from a record of the latest write execution. A record stored before this field
existed counts as not belonging to it, so the window falls back to every remaining operation.

## R12. Approving an interrupted run

**Question**: when the site requires approval (`INFRAHUB_SYNC_REQUIRE_APPROVAL`), a resume needs an approval of the
plan checksum (FR-011). Spec 019's `approve` accepts only a run in phase `planned`. A `sync` run is never approved
before its write, because the site refuses `sync` when it requires approval, and an apply can be interrupted before
the site required approval. Such a run could never be resumed: the resume was refused with `approval-required`, and
`approve` was refused with `run-not-approvable`.

**Decision**: `approve` also accepts a resumable run: `reconciliation_required` is true, the run is not cancelled,
its plan is saved, and no execution is unfinished (the state rules in data-model.md). The service checks this before it reads the
plan, and the store checks it again in the conditional update that stores the approval, under the run's row lock,
so an approval is never stored while a `verify` or a resume runs. Every other check of spec 019 is unchanged: the
checksum must be the saved plan's, the plan's checksum must have been verified when it was published, and a run has
at most one approval. An approval recorded before the interruption stays valid for the resume, and a second
`approve` is refused with `already-approved`, which carries the existing approval.

An `approve` receipt does not take part in a run's stage arbitration, so the write admission an interrupted run
holds does not refuse it.

The resume's `approval-required` message, and the CLI hint for it, name
`infrahub-sync approve RUN_ID --expected-checksum CHECKSUM`.

**Rejected**: letting a resume skip the approval when the run has none. Approval gates every write on a site that
requires it, and a resume writes.

## R13. How `verify` matches a create to a destination node

**Question**: R7 reads a create by its key. A review found three gaps in the first implementation: a key that
several destination nodes share was compared with one of them, chosen arbitrarily, and could be reported as "already
matches the plan"; for a kind with no human-friendly identifier, a create that does not set every identity attribute
of the other creates got no key and was reported as "missing at the destination" even when the node exists; and the
query of a key made of an attribute and a relationship filtered on the attribute only, so it listed every node with
that attribute value.

**Decision**:

- A kind with a human-friendly identifier is keyed on its components. An attribute component is filtered with
  `<attribute>__values`, and a relationship component with `<relationship>__ids` on the ids the plan's peers resolve
  to. The query sends both kinds of filter; the destination combines them with AND, as it does the peer resolver's
  several `__value` filters.
- A kind with no human-friendly identifier is keyed, for each create, on the identity's direct values that are not
  null, which is the rule the apply's peer resolver uses for such a kind. Creates are grouped by the set of attributes
  in their key, with one query for each set.
- When more than one destination node has the create's key, `verify` reports the operation as "differs from the
  plan", never "already matches the plan". The apply's peer resolver refuses the same key as ambiguous. The report
  has no reason field, so only the state records this.

The relationship filters were tested only against the in-memory client of the unit tests, not against a live
Infrahub.

**Not decided**: a kind with no human-friendly identifier is not keyed on its uniqueness constraints, which is what
the destination matches the create's upsert on. A constraint that includes a relationship, such as (device, name),
is therefore not part of the key, and two nodes that differ only on that relationship are reported as "differs from
the plan".

**Error mapping**: a `ValueError`, `KeyError` or `AttributeError` raised while the destination nodes are read is a
defect in Sync or in the data shape, not an unreachable destination. It is not mapped to `DestinationReadError`; the
`verify` fails, and its failure record names the error class only, without the message (secret-redaction
guideline). A unit test records this behaviour.

## R14. A `sync` killed while planning, and reading the plan for the progress view

**Finding 1**: the liveness monitor ends a claimed execution whose worker stopped reporting with `interrupted` /
`ambiguous`, and `_record_write_ambiguity` sets `reconciliation_required` for every execution of purpose `apply`,
`sync` or `resume`. A `sync` runs its plan part and its apply part in one execution, so a `sync` worker killed while
it was still planning also sets the flag. Before this decision such a run was reported as `resumable: yes (verify
required)`; `verify` answered `503 plan-unavailable` and `resume` answered `503 plan-unavailable`, and neither
answer was recorded in the audit history.

**Question**: should `reconciliation_required` stay unset for a `sync` that saved no plan? A `sync` publishes its
plan before its first write (R9), so at the time liveness ends the execution, a run without a plan has written
nothing. But ending the execution does not stop the worker. Before each write the apply checks the configuration's
write lock (`ProvenWriteOwnership`), not the execution; it stops only when a progress save is answered `ended` (R4),
and the first save follows the first commits. A worker that liveness ended while it was planning can still publish
its plan and write afterwards. So the absence of a plan when liveness acts
does not prove that nothing will be written, and clearing the flag could hide a write.

**Decision**: keep the flag as ADR 0010 derives it, and add a condition to the resumable-run rule: the run's plan
review document (artifact `plan-review`) is published. The rule is checked when a request arrives, not when liveness
acts, so a run whose worker published its plan late becomes resumable at that point.

- The store's `_run_is_resumable` checks the published `artifact_refs` row in the same query, on SQLite and on
  PostgreSQL. It checks the row, not whether the plan can be read, so an expired plan still answers `410` and an
  unreadable one `503` at the later plan read.
- `resume` refuses such a run with `409 run-not-resumable` before it reads the plan. The message says that no plan was
  saved and that a new `plan` or `sync` run is the next step, and the refusal is recorded in the audit history like
  the other resume refusals. The store gives the same answer on the run's locked row.
- A `resume` whose plan cannot be read is now recorded in the audit history too, with outcome
  `refused-plan-unavailable`, as `approve` does (FR-014).
- `verify` no longer reads the plan before it reserves the request when the run requires reconciliation and saved
  no plan. Such a run keeps its write admission, because only a `sync` that ended `planned` without requiring
  reconciliation gives it back, so the reservation refuses it with `409 run-execution-conflict`, the refusal every
  other non-resumable run with a write admission gets, and records it. Every other run still reads the plan first.
- The progress view (`apply_progress`) reports `resumable: false` and `verify_required: false` for such a run.
- `approve` refuses such a run with `409 run-not-approvable` before it reads the plan, because the run is neither in
  phase `planned` nor resumable.

**Rejected**: not setting `reconciliation_required` for a `sync` that has no plan when liveness ends it, for the
reason above.

**Finding 2**: `GET /runs/{run_id}` read and parsed the whole saved plan on every request for a run with a write
execution, to count each kind's operations. A client that waits for a run (`--wait`) polls this route, and a tier L
plan has about 88,000 operations (R2).

**Decision**: the Sync API keeps the plan's `(operation_id, kind, action)` triples, in stored order, in an in-process
cache keyed by the run ID and the plan artifact's digest, which the run's artifact reference holds without a read.

- A run publishes its plan once, and a published artifact never changes, so an entry is never stale. Each API
  process fills its own cache and computes the same entry, so several API processes need no shared state and no
  invalidation.
- A plan whose reference has passed its `expires_at` bypasses the cache, with the rule the artifact store applies,
  so every process stops reporting per-kind counts at the same time once the plan expired.
- A failed read, an expired plan included, is not cached. It is logged as a warning with the run ID, the error class
  and the error code, never the message. The module logs through the standard `logging` module, as the rest of
  `service/service.py` does.
- The bound is 200,000 operations over all cached runs, with the least recently read run dropped first; a plan
  larger than the bound is not cached. Kind and action strings are interned, so one cached operation takes about
  200 bytes (an estimate, not measured) and the cache stays under about 40 MB per API process. That holds two tier L plans, or many smaller ones.

**Rejected**:

- Storing the layout in the store when the write execution starts (a column or a results entry): it needs a column
  migration on both backends and a write in the worker, for data the published plan already holds.
- A bound counted in runs (for example 32 runs): 32 tier L plans would take about 560 MB.

