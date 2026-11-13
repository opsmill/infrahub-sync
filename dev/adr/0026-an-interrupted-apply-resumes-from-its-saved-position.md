# 26. An interrupted apply resumes from its saved position

**Status**: Accepted
**Date**: 2026-11-13
**Source**: `dev/specs/022-resume-interrupted-apply/research.md` (R1, R2, R5, R7, R8)

## Context

[ADR 0010](0010-a-managed-write-ends-in-a-verdict-not-a-retry.md) ends a write that may have
reached the destination in the verdict `interrupted` / `ambiguous` with
`product_runs.reconciliation_required` set. It states that nothing clears that column, that one
run admits one write, that a read needs a run that has admitted no write, and that an uncertain
run is terminal: the operator inspects the destination and creates a new plan. It rejected a
write-attempt table because the attempts would say how many writes were tried, not what reached
the destination.

On a large import, a new plan repeats hours of work. A first sync of the tier L benchmark
(87,815 objects) takes about 8.5 hours, and a crash late in that apply means reading both sides
again and writing again.

Three facts of the current code make a cheaper recovery possible:

- A saved plan cannot change, and apply writes its operations in the order the plan records
  them.
- Apply writes one operation at a time and stops at the first failure. At any moment, the
  operations before the current one are committed, the current one is uncertain, and the ones
  after it were not reached.
- Every create, update and relationship write of the Infrahub destination adapter is a
  convergent upsert keyed by the recorded destination ID or the complete human-friendly ID
  ([ADR 0012](0012-the-convergent-upsert-is-the-replace-set-write.md),
  [ADR 0013](0013-writes-are-keyed-by-recorded-id-and-complete-hfid.md)). Writing a committed
  operation again leaves the destination unchanged.

## Decision

- **Progress marker.** During an apply, a `sync` and a resume, the worker saves the position of
  the last committed operation in the plan's order in `prefect_executions.apply_progress`. It
  saves after every 500 committed operations, every 30 seconds, at the end of each kind, when an
  operation fails and when the apply ends. A position is saved only after every operation up to
  it was committed. The update applies only while the execution is not terminal. When saves fail
  for 5 minutes, counted from the first failed save, the apply stops and the run is interrupted.
- **`verify` on an interrupted run.** A run is resumable when `reconciliation_required` is set,
  the run is not cancelled and no execution of it is unfinished. A `verify` is admitted on a
  resumable run; this is the one exception to "a read needs a run that has admitted no write".
  After the pre-write checks pass, `verify` reads the nodes of the uncertain operations from the
  destination and reports each one as `matches`, `differs` or `missing`. The report never makes
  `verify` fail.
- **Resume.** `POST /runs/{run_id}/resume` and `infrahub-sync runs resume` add a new execution of
  purpose `resume` to the same run. A resume passes the conditions of `apply`, and is admitted
  only when the latest `verify` of the run came after its latest write execution and passed. It
  is recorded in a new `resume_admissions` table; the run's `write_admissions` row is kept, so
  "one run admits one write" still holds for every other stage. A resume applies the saved plan
  from the operation after the highest saved position over all the run's write executions, and
  it never reads the source or creates a plan.
- **Clearing the reconciliation column.** When a resume execution ends `succeeded`, the store
  clears `reconciliation_required` and sets the phase `applied` in the same transaction. This
  replaces "nothing clears it" for this one case.

## Consequences

- An interrupted run is no longer terminal when its writes are safe to repeat. A resume repeats
  at most the operations after the saved position, which is at most 500 operations or 30 seconds
  of writes when every save succeeded.
- A resume writes the plan's values over every node reported as `differs`, including a change
  made at the destination after the plan. The `verify` output says so.
- A resume that ends interrupted leaves the run resumable, and a new `verify` is required
  before the next resume.
- Each apply adds one row update per save to the store.
- A destination adapter whose writes are not safe to repeat cannot use this mechanism. Today the
  Infrahub adapter is the only one that applies a saved plan.
- Pre-release V3 development databases are recreated rather than migrated across the new column
  and table, as ADR 0010 states for its own changes.

## Alternatives considered

**A write-attempt table with generations**, the alternative ADR 0010 rejected. Its objection
was that the attempts would not say what reached the destination. The progress marker and the
per-operation `verify` report now say that, and the retry is safe because each write is a
convergent upsert. The resume reuses the run and its executions instead of a separate attempt
table.

**A row for each committed operation.** It would cost about 88,000 inserts on tier L, while the
fixed order makes one position enough.

**A marker for each kind.** A crash late in a large kind would repeat hours of writes.

**Keeping the marker in the run's local state file or an artifact.** Artifacts are written once,
and the local state file stays on the worker, so the last position of a killed worker would be
lost.
