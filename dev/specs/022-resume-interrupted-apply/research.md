# Research: Resume an interrupted apply

Each item records a decision, why it was made, and what was rejected. Code references are on
`022-resume-interrupted-apply`, which starts from `020-plan-problem-reporting` (PR #409).

## R1. How a killed worker's run becomes interrupted today

**Finding**: `infrahub_sync/service/liveness.py` calls `ProductProjection.interrupt_execution` for a claimed
execution whose worker stopped reporting. `_terminalize_execution` ends the execution and the run together, and
ADR 0010 derives `product_runs.reconciliation_required` from the verdict and the execution's purpose. A worker that
fails after its first proven dispatch ends `interrupted` / `ambiguous` through the same column.

**Decision**: a run is resumable exactly when `reconciliation_required` is true, its latest write execution is
terminal, and the run is not cancelled. No new detection is added (spec FR-013; the user confirmed the existing
interrupted state is the trigger).

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
(`infrahub_sync/service/apply_progress.py`) that throttles saves (every 500 commits, every 30 seconds, at each kind
boundary, at the end or failure) and tracks the time of the last successful save. The CLI-local and test paths pass
nothing and behave as today.

**Rationale**: mirrors spec 018's `SideReadObserver` / `StoreSideReadObserver` split, which keeps the engine free of
store imports and keeps the save failure policy in the service.

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
dispatched and are not counted as applied by this execution; skipped deletes keep being counted only once, by the
execution that reached them. Every pre-write check still runs on the whole plan before the first dispatch. The
resume's `ApplyRecord` lists only the operations it dispatched; the run's progress view (FR-004) combines all
executions through the latest marker.

## R7. The stronger `verify`

**Decision**: when the run is resumable (R1), the `verify` stage, after the pre-write checks pass, asks the
destination adapter for the nodes of the operations after the marker and compares the planned payload and
relationships with what it reads:

- an update is fetched by its recorded `destination_id`;
- a create is fetched by its complete human-friendly identifier, which plan format 3 and later guarantee
  (ADR 0013);
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
