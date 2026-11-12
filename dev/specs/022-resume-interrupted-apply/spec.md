# Feature Specification: Resume an interrupted apply

**Feature Branch**: `022-resume-interrupted-apply`

**Created**: 2026-11-12

**Status**: Draft

**Input**: The idea brief "Resume an interrupted apply", grilled with the user on 2026-11-12. Every decision in the
brief was confirmed by the user. The brief is compared with this specification in
[`alignment-check.md`](./alignment-check.md).

## Background

An apply writes the operations of a saved plan to the destination one at a time, in one fixed order, and stops at
the first failure. When a write may have reached the destination and the apply then fails — the worker is killed, the
session is lost, the run is cancelled during the write — the run ends with the phase `apply-interrupted` or
`sync-interrupted`, the execution outcome `ambiguous`, and `reconciliation_required` set (ADR 0010). Nothing clears
that state. The only remedy is for the operator to inspect the destination, create a new plan and apply it.

On a large import this repeats hours of work. A first sync of the tier L benchmark (87,815 objects) takes about
8.5 hours. A crash late in that apply means reading the whole source and the whole destination again, then writing
again.

Three facts of the current code make a cheaper recovery possible:

- The saved plan cannot change, and apply writes its operations in the order the plan records them (sorted by tier,
  then operation identifier).
- Apply runs one operation at a time and stops at the first failure. At any moment, the operations before the
  current one are committed, the current one is uncertain, and the ones after it were not reached.
- Every create, update and relationship write of the Infrahub destination adapter is a convergent upsert keyed by the
  recorded destination identifier or the complete human-friendly identifier (ADR 0012, ADR 0013). Writing an already
  committed operation again leaves the destination unchanged.

So one saved position in the plan describes how far an apply went, and repeating a few operations before that
position is safe.

### Terms used in this specification

- **Progress marker**: the position, in the saved plan's order, of the last operation that the apply knows was
  committed. It is saved during the apply.
- **Interrupted run**: a run whose apply or `sync` stage ended with `reconciliation_required` set, after at least one
  write may have reached the destination.
- **Resume**: a new execution on an interrupted run that applies the same saved plan, starting after the progress
  marker.
- **Pre-write checks**: every check that `apply` runs before its first destination write, as defined by spec 019.
- **Uncertain operations**: the operations after the progress marker that may have reached the destination. When
  the failing operation is known, they run through it. When the worker was killed, it is not known, so they are the
  next 500 operations after the marker if the last save attempt succeeded, and every remaining operation if it did
  not.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resume an interrupted apply (Priority: P1)

An operator applies a large plan. The worker is killed part way through. The operator views the run's progress,
runs `verify` on it, and then runs `runs resume RUN_ID --expected-checksum CHECKSUM`. Sync applies the rest of the
saved plan in a new execution on the same run, starting at the saved progress marker.

**Why this priority**: It removes the main cost of an interruption today: creating and applying a new plan from the
start.

**Independent Test**: Apply a plan, stop the worker during the apply, run `verify` and `runs resume`, then create a
new plan of the same configuration version and check that it holds no creates and no updates.

**Acceptance Scenarios**:

1. **Given** an apply of a 10,000-operation plan whose worker was killed after operation 6,200, with the progress
   marker saved at 6,000, **When** the operator runs `verify RUN_ID`, sees every pre-write check pass, then runs
   `runs resume RUN_ID --expected-checksum C`, **Then** Sync writes operations 6,001 to 10,000 in a new execution on
   the same run, the run ends applied, and a new plan of the same configuration version shows 0 creates and 0
   updates.
2. **Given** an interrupted run, **When** the operator runs `runs show`, **Then** the run shows the progress marker
   and, for each kind, how many operations were committed, how many are uncertain and how many were not reached.
3. **Given** an interrupted run and a `verify` that passed after the interruption, **When** the operator resumes with
   a checksum that is not the saved plan's checksum, **Then** the resume is refused and the error shows the expected
   and the given checksum.
4. **Given** a resume, **When** the operator gives a reason, **Then** the run records the actor, the time and the
   reason; **When** the operator gives no reason, **Then** the run records the actor and the time.

---

### User Story 2 - Learn what an uncertain write did (Priority: P2)

After an interruption, the operator runs `verify` on the run. Besides the pre-write checks, `verify` reads from the
destination the nodes of the uncertain operations, and reports for each one whether the destination
already matches the plan.

**Why this priority**: It replaces "inspect the destination by hand" with a report that names each uncertain
operation's state, which is what an operator needs before deciding to resume.

**Independent Test**: Interrupt an apply, run `verify`, and compare the per-operation report with the destination's
content for those nodes.

**Acceptance Scenarios**:

1. **Given** an interrupted run whose marker is at 6,000 and whose apply stopped during operation 6,201, **When** the
   operator runs `verify`, **Then** the result lists operations 6,001 to 6,201, each as "already matches the plan",
   "differs from the plan" or "missing at the destination".
2. **Given** that report holds operations that differ or are missing, **When** every pre-write check passed, **Then**
   `verify` succeeds and the run accepts a resume.
3. **Given** the destination schema changed since the plan, **When** the operator runs `verify`, **Then** the
   pre-write check that fails is named, and a resume is refused until a `verify` passes.

---

### User Story 3 - Resume an interrupted `sync` run (Priority: P3)

A confirmed `sync` run plans and applies without a pause. Its apply is interrupted. The operator resumes it in the
same way as an interrupted apply, with the checksum of the plan that the `sync` run saved.

**Why this priority**: Unattended `sync` runs are the runs most likely to be long and to be interrupted with nobody
watching.

**Independent Test**: Start a confirmed `sync`, stop the worker during its apply, run `verify` and `runs resume`, and
check that no new plan was created.

**Acceptance Scenarios**:

1. **Given** an interrupted `sync` run, **When** the operator runs `verify` and then `runs resume` with the checksum
   of the plan the run saved, **Then** Sync applies that plan from the marker in a new execution and does not read
   the source or create a new plan.

---

### Edge Cases

- **A node was deleted at the destination before the resume.** The update keyed by its recorded identifier gets
  "not found"; Sync proves that the operation wrote nothing, and the resume stops with a failure that names the
  operation and the node.
- **Values of a node were changed at the destination before the resume.** The resume writes the plan's values, as a
  late `apply` would.
- **The destination schema changed.** The pre-write check fails in `verify` and again inside the resume; the resume
  is refused.
- **A resume is itself interrupted.** The run gets a newer progress marker. A new `verify` is required before the
  next resume.
- **Another run holds the configuration's write lock.** The resume is refused cleanly, as `apply` is, and nothing is
  written.
- **The marker cannot be saved during an apply.** Writing continues and the next save tries again with a newer
  position. After 5 minutes with no successful save, the apply stops and the run is interrupted.
- **The apply stopped before its first write.** The run is an ordinary failed run, not an interrupted one, and
  cannot be resumed; the operator applies it again as today.
- **The operator resumes a run that was not interrupted** (applied, failed before writing, withdrawn, or never
  applied). The resume is refused with the reason.
- **The interrupted run's plan left out records** (spec 020). The resume requires the same left-out confirmation as
  `apply`.
- **The site requires approval.** The resume requires the run's recorded approval for the same checksum, as `apply`
  does.

## Requirements *(mandatory)*

### Functional Requirements

#### Progress marker

- **FR-001**: While applying a saved plan, the system MUST save the progress marker. It MUST save it after every 500
  committed operations, every 30 seconds, at the end of each kind, when an operation fails, and when the apply ends,
  whichever comes first.
- **FR-002**: The system MUST save a position only after every operation up to and including that position was
  committed. A saved marker MUST never point past an operation that was not committed.
- **FR-003**: When a marker save fails, the system MUST log a warning that names the run and keep writing; the next
  save MUST try again with the newer position. When no save has succeeded for 5 minutes, the system MUST stop the
  apply and end the run as interrupted.
- **FR-004**: Users MUST be able to view, for an interrupted run and for a run being applied, the progress marker and,
  for each kind, how many operations were committed, how many are uncertain and how many were not reached. The system
  derives these counts from the marker and the saved plan.

#### Stronger `verify` for an interrupted run

- **FR-005**: The system MUST accept `verify` on an interrupted run. For such a run, `verify` MUST run every
  pre-write check, as it does for any run, and MUST also read from the destination the nodes of the uncertain
  operations: the operations after the marker through the failing operation when it is known; otherwise the next 500
  operations when the last save attempt succeeded, and every operation after the marker when it did not.
- **FR-006**: For each uncertain operation, `verify` MUST report one of "already matches the plan", "differs from the
  plan" or "missing at the destination", and MUST return this report through the Sync API and the CLI. The output
  MUST say that a resume writes the plan's values over every operation reported as "differs from the plan".
- **FR-007**: The per-operation report MUST NOT make `verify` fail. Only a failed pre-write check makes it fail.
- **FR-008**: `verify` MUST NOT write to the destination, for an interrupted run as for any other.

#### Resume

- **FR-009**: Users MUST be able to resume an interrupted run with `runs resume RUN_ID --expected-checksum CHECKSUM`
  and the matching Sync API request. A resume MUST add a new execution to the same run; it MUST NOT create a new run.
- **FR-010**: A resume MUST apply the run's saved plan, starting with the first operation after the progress marker,
  in the plan's order. It MUST NOT read the source or create a new plan.
- **FR-011**: Before its first write, a resume MUST pass the same conditions as `apply`: the expected checksum, the
  configuration write lock, every pre-write check, the left-out confirmation when the plan left out records, and the
  run's approval when the site requires approval.
- **FR-012**: The system MUST refuse a resume unless a `verify` ran on the run after its latest interruption and all
  of that `verify`'s pre-write checks passed. The refusal MUST say that a `verify` is required.
- **FR-013**: The system MUST refuse a resume of a run that is not interrupted, including a run that was applied, a
  run that failed before its first write, a run that was never applied, and a withdrawn run. The refusal MUST say
  why.
- **FR-014**: The system MUST record, for each resume, the actor who started it, the time, the start position and,
  when given, the reason. The reason MUST be optional. Each resume request, accepted or refused, MUST appear in the
  run's audit history and event list, as for `apply`.
- **FR-015**: A resume MUST save its own progress marker under FR-001 to FR-003. When it is interrupted, the run is
  interrupted again and FR-012 applies to the next resume.
- **FR-016**: When a resume finishes, the run MUST end applied, and `reconciliation_required` MUST be cleared for
  that run. Its history MUST keep the interrupted execution and every resume.
- **FR-017**: Users MUST be able to resume an interrupted `sync` run in the same way, with the checksum of the plan
  the `sync` run saved.
- **FR-018**: Only the actor who started the run, or an administrator, MAY resume it, as for the other stages of a
  run.

### Key Entities

- **Run** (existing): gains its resume executions, the `verify` result recorded after the latest interruption, and
  the progress view of FR-004.
- **Execution** (existing): an apply, `sync` or resume execution gains its progress marker and the time of its last
  successful save.
- **Saved plan** (existing, cannot change after it is saved): defines the order the marker points into.
- **Resume** (new kind of execution): the actor, the time, the optional reason, the start position and the expected
  checksum.
- **Per-operation verify report** (new, part of a `verify` result): for each uncertain operation, its
  operation identifier, kind and state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a worker is killed during an apply, a resumed apply leaves the destination in the same state as an
  apply that was never interrupted: a new plan of the same configuration version shows 0 creates and 0 updates.
- **SC-002**: When every marker save succeeded, a resume repeats at most 500 operations or 30 seconds of writes.
- **SC-003**: Saving the progress marker adds at most 2% to the apply time on the tier M and tier L benchmarks.
- **SC-004**: On tier L, a `verify` of an interrupted run finishes in under 1 minute.
- **SC-005**: An operator goes from an interrupted run to a finished apply with `runs show`, `verify` and
  `runs resume`, without reading worker logs or Prefect.

## Assumptions

- The Infrahub destination adapter is the only adapter that applies saved plans, and its creates, updates and
  relationship writes give the same result when repeated (ADR 0012, ADR 0013, VAL-4).
- Apply stays sequential and stops at its first failure.
- Deletes stay recorded and never executed (ADR 0004), so a resume never repeats a delete.
- Pre-release V3 databases are recreated, not migrated, when the run storage changes (ADR 0010).
- This work is stacked on PR #409 (spec 020), so the left-out confirmation exists.
- A new decision record replaces ADR 0010's "an uncertain run is terminal" for writes proven safe to repeat.

## Out of Scope

- An automatic resume when a worker restarts.
- The recovery actions `replay-from-tier`, `re-extract`, `resolve-drift` and `escalate`.
- Continuing an apply after a failed operation, and a list of failed operations (the AD-12 policy for runs nobody
  watches).
- Adapter declarations of which writes are safe to repeat.
- Checking every operation of the plan against the destination.
- Executing deletes.
- The operator web UI, which another colleague builds against the Sync API.

## Open Questions

None. The command name (`runs resume`), the trigger (the existing interrupted state) and the base branch (PR #409)
were decided during grilling.
