# 38. Cancelling a finished plan withdraws it, and a cancelled run is never applied

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/014-plan-approval/research.md` (R11)

## Context

A team that reviewed a plan and decided not to apply it had no way to say so. The plan stayed in
phase `planned` and could be applied at any later time, with or without an approval.
`POST /runs/{run_id}/cancel` cancelled only an execution that was running: for a run whose plan
stage had finished, it answered `409 execution-terminal`.

`cancelled` was already a run phase, reached when Prefect confirmed the cancellation of any stage.
`apply` did not check the phase, so a run whose `verify` execution had been cancelled could still
be applied.

## Decision

- **Cancel withdraws a finished plan.** When a run has no active execution and is in phase
  `planned`, `POST /runs/{run_id}/cancel` and `runs cancel` withdraw the plan: the run's phase and
  outcome become `cancelled`. The Sync API answers `200` with the run. Nothing is sent to Prefect.
- **One transaction, one lock.** `cancel_planned_run` locks the run's row with the same lock that
  write admission and approval take. In one transaction it checks again that the run is in phase
  `planned`, that no write was admitted, that no request other than a cancel or an approval is
  unresolved and that no execution is unfinished. It then sets the phase, writes the event of the
  accepted request and the event of the run's end, and completes the cancel receipt. When a check
  fails, it completes the receipt with the refusal: `409 run-execution-conflict`,
  `409 run-already-cancelled` or `409 execution-terminal`.
- **A cancelled run is closed to writes and approvals.** `apply` and `approve` refuse a run in
  phase `cancelled` with `409 run-cancelled`, before any receipt is reserved. `resume` refuses it
  with `409 run-not-resumable`. The store's write admission also refuses it under the row lock, so
  a cancel and an apply sent at the same time never both succeed.
- **A withdrawn run keeps its phase.** A `verify` of a withdrawn run is still admitted, because it
  writes nothing. A `verify` that passes or fails adds its result to the run and sets no phase,
  and the store does not change the phase of a withdrawn run when that `verify` is abandoned or
  interrupted.
- **The approval is kept.** Withdrawing a plan does not clear its approval. The cancelled run
  shows who approved which checksum before it was withdrawn.
- **Other phases.** A run with no active execution in phase `cancelled` is answered
  `409 run-already-cancelled`. In any other phase, such as `applied` or `plan-failed`, a run whose
  executions all ended is answered `409 execution-terminal`, and a run with no execution record
  is answered `409 no-active-execution`, as before.

## Consequences

- An operator has one command to stop a plan from being applied, at any stage: `runs cancel`. To
  try again, they create a new plan run.
- `apply`, `approve` and `resume` refuse a run in phase `cancelled`. This changes one earlier
  behaviour: a run whose `verify` execution was cancelled can no longer be applied.
- One kind of run leaves phase `cancelled`. When the write of a run stopped part-way and a later
  `verify` or `resume` of that run is cancelled, the store sets the run back to phase
  `interrupted`, so that it stays resumable
  ([ADR 0027](0027-an-interrupted-apply-resumes-from-its-saved-position.md)). A withdrawn plan
  admitted no write, so this does not apply to it.
- `cancel` has two success answers. `202` means the Sync API asked Prefect to cancel an execution.
  `200` means the plan was withdrawn in the request. A client must accept both.
- A withdrawal sent again with the same idempotency key returns the stored answer. With a new key
  it is answered `409 run-already-cancelled`.
- Cancelling a write that is running can still leave an uncertain outcome
  ([ADR 0010](0010-a-managed-write-ends-in-a-verdict-not-a-retry.md)); a withdrawal cannot,
  because no write was admitted.

## Alternatives considered

- **A separate `withdraw` command.** Rejected: the operator already has `runs cancel`, and two
  commands would have to explain which one applies to which state.
- **Clear the approval and leave the run in phase `planned`.** Rejected: the run could be approved
  and applied again, and the record of the first approval would be lost.
