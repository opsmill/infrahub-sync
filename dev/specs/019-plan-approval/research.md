# Research: Approve a plan without applying it, and a `verify` that checks the destination

Each entry records a decision, why it was chosen, and the alternatives considered. File references are to this
branch (`verify-apply-approval`, based on `feature/v3-develop` at `3c617102`).

## R1. What `verify` and `apply` check today

**Finding**:

- The service `verify` stage (`infrahub_sync/service/flow.py`, `_execute_stage`, `stage == "verify"`) rehydrates the
  plan checkpoint and calls `execute_run(operation="verify", _require_verified=True)`, which runs `verify_plan`
  (`infrahub_sync/plan/verify.py`) without constructing adapters (`execution.py`, `_read_verified_plan`). Its
  runtime scope is empty (`_worker_execution_context`: "nothing at all for verify, which constructs no adapter").
  It merges `results.verification` and does not change the phase.
- The service `apply` stage runs, in order, before its first write:
  1. `rehydrate_plan_checkpoint` (checkpoint integrity);
  2. `_verify_registered_apply`: `verify_plan` (run binding, plan checksum, source snapshot, configuration
     version, format), configuration binding, `expected_checksum`;
  3. under the write guard, one live destination schema read and `_require_planned_schema` (schema fingerprint);
  4. `PlanApplier.open_existing` constructs the destination adapter (no data load);
  5. `PlanApplier._require_recorded_destination` (destination URL and branch, `utils.py`);
  6. `Potenda.apply_plan` gate: `verify_plan` again with the write-surface check, `expected_checksum_refusal`,
     `parse_plan_artifact`, `_require_applyable_format`, `_validate_infrahub_plan_payload` (every planned Infrahub
     field), `resolve_destination_lineage` (lineage account);
  7. the write loop.

**Decision**: the "pre-write checks" of the specification are steps 1–6 minus `expected_checksum` (verify has no
operator checksum). `verify` runs them all and stops before step 7.

## R2. How `verify` runs the destination checks without duplicating them

**Decision**: split `Potenda.apply_plan` into a pre-write gate and the write loop. The gate becomes
`Potenda.check_plan(...)`, returning the parsed plan and the checks it ran; `apply_plan` calls it, then runs the
loop with the same parsed bytes. `PlanApplier` gains `check_plan()`, which runs `_require_recorded_destination` and
then `engine.check_plan(...)`. The service `verify` stage, after today's file checks, builds the destination-only
runtime models (the `apply` scope), runs `_require_planned_schema`, opens a `PlanApplier`, and calls `check_plan()`.

**Rationale**: SC-004 requires that `verify` fails on every check `apply` refuses on. Sharing one gate function makes
that true by construction, and a test can assert it per check. The apply order and its comments (one read, gate then
loop) stay intact because `apply_plan` still owns the single artifact read and passes it to the gate.

**Alternatives considered**:

- Copy the checks into the verify stage: rejected, the two lists would drift.
- Run `apply` with a "dry-run" flag: rejected, a flag on the write path is the riskiest place to add a branch that
  must never write; a separate gate function cannot reach the loop.

## R3. Whether `verify` takes the configuration write guard

**Decision**: no. `verify` writes nothing, and `apply-guard.md` already states that standalone `plan` and `verify`
do not acquire the guard. Its result describes the destination at the time it read it (spec edge case).

**Alternatives considered**: taking the guard would make `verify` wait behind a long apply and block writers while it
reads, for no safety gain.

## R4. Which branch `verify` checks against

**Finding**: `apply` receives the operator's `--branch`; the run stores `destination_branch`, the effective branch
resolved when the run was created (`service.py`, `create_run`). `verify` receives no branch today.

**Decision**: the service passes the run's stored `destination_branch` as the `branch` parameter of the `verify`
stage. The destination binding check then compares the plan's recorded destination with the destination the run was
created for. Runs created before the store recorded the branch (`destination_branch is None`) keep the configuration
default, as `apply` without `--branch` does.

## R5. Credentials `verify` needs

**Decision**: `verify` resolves destination credentials only, like a saved-plan `apply`
(`resolve_source_credentials=stage not in ("apply", "verify")`). It never reads the source.

**Rationale**: the source snapshot check is a file check; nothing in R1 reads the source.

## R6. Where an approval is recorded, and by whom

**Decision**: approval is an API-side decision handled by `SyncService.approve_run`, like the admission checks of
`apply` and `sync`. It does not submit a Prefect flow run. The approval is stored on the run (`ProductRun.approval`)
and the request is recorded in the audit history (`_audit`, operation `approve`) with outcomes `accepted`,
`refused-*` and `replayed`.

**Rationale**: approving reads only what the API already holds (the run and its published plan resource) and writes
one record. A worker stage would add a flow run, a lease and a failure path for a decision that contacts no system.

**Alternatives considered**: a worker `approve` stage that also runs `verify`'s checks. Rejected for v1: the user
chose `verify` and `approve` as separate steps, and `apply` re-runs every check anyway.

## R7. What approving checks

**Decision**: `approve_run` refuses, in order:

1. a run the caller does not own and is not an administrator for (`_owned_run`, 403);
2. a run that is not in phase `planned`, or that has an execution in progress (409 `run-not-approvable`, with the
   phase in the message), checked first so that a run still planning is not reported as a missing plan;
3. a run whose published plan cannot be read: the existing `_plan(run_id)` lookup answers 404 `run-not-found`,
   410 `artifact-expired` or 503 `plan-unavailable`;
4. a plan resource with `checksum_ok == false` (409 `plan-checksum-unverified`);
5. an `expected_checksum` different from the plan resource's `checksum` (409 `checksum-mismatch`, showing both);
6. a run that already has an approval (409 `already-approved`, the body carries the existing approval).

Only then is the approval stored, in the same transaction as the mutation receipt.

**Rationale**: the plan resource's `checksum` and `checksum_ok` were computed by the worker from the saved artifact
when the plan was published. Re-running `verify_plan` in the API would need the registered runtime resolution the
worker owns. The full file and destination checks run in `verify` and again in `apply`.

**Note on phase**: a `no-change` plan has phase `planned` and can be approved; applying it writes nothing, as today.

## R8. Storage of the approval

**Finding**: `product_runs` is a relational table; the store adds nullable columns at start-up with an idempotent
migration (`store.py`, the nullable-column list near line 163, and `_migrate_*` helpers).

**Decision**: four nullable columns on `product_runs`: `approved_by`, `approved_at`, `approved_checksum`,
`approval_reason`. `ProductRun.approval` is `RunApproval | None`, present when `approved_by` is set. A conditional
update (`... WHERE run_id = ? AND approved_by IS NULL AND phase = 'planned'`) records it, so two concurrent approvals
cannot both succeed; the loser is answered with `already-approved`.

**Alternatives considered**: a JSON column (rejected: the conditional update and future queries such as "approved
runs not yet applied" read better as columns); a separate `run_approvals` table (rejected: at most one approval per
run, never changed).

## R9. The approval-required setting

**Decision**: `INFRAHUB_SYNC_REQUIRE_APPROVAL`, read by `SyncService` when each `apply` or `sync` request is
admitted. Accepted values: `true`/`1`/`yes`/`on` and `false`/`0`/`no`/`off`, case-insensitive; unset means off; any
other value makes the service refuse to start with a message naming the variable. The prefix follows the service's
existing variables (`INFRAHUB_SYNC_DATABASE_URL`, `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`).

- `apply` admission (`apply_run`): when on, refuse with 409 `approval-required` if `run.approval is None`, or 409
  `approval-checksum-mismatch` if `run.approval.plan_checksum != request.expected_checksum`. Checked before the
  receipt is reserved, so nothing is submitted.
- `sync` admission (`create_run`, `operation == "sync"`): when on, refuse with 409 `approval-required-for-sync`
  before the run record is created; the message names `plan`, `approve`, `apply`.
- Exposure (FR-017): `GET /me` gains `approval_required: bool`; `whoami` prints it.

**Rationale**: the setting is service-wide (the user deferred per-configuration settings). Reading it per request
lets a restart with a new value take effect without touching stored runs. A malformed value fails closed at start-up
rather than silently meaning "off".

**Alternatives considered**: enforcing in the worker as well. Rejected for v1: admission is the single entry for
both stages, and the worker cannot be reached without a receipt the API admitted. Noted as defence in depth for a
later change.

## R10. Run events (spec 017)

**Finding**: spec 017 (run events and logs, PR #402) is not on this branch's base.

**Decision**: the approval is visible through the run resource and the audit history. When spec 017 merges, an
`approved` run event is a follow-up task, not part of this feature.

## R11. Withdrawing an approval

**Finding**: `cancel_run` cancels only the latest active service execution and refuses a run with no execution in
progress (`no-active-execution`, `execution-terminal`). A planned run whose plan stage finished cannot be cancelled.

**Decision**: no withdrawal in v1 (spec edge case and open question). Documented in the user docs.

## R12. CLI surface

**Decision**:

- `infrahub-sync approve RUN_ID --expected-checksum CHECKSUM [--reason] [--idempotency-key]`; prints the run with its
  approval. Not interactive; it does not ask for confirmation because it writes nothing to the destination.
- `runs show` prints `approved_by`, `approved_at` and `approved_checksum` when the run has an approval.
- `verify` prints each check that passed on success; on failure the existing failure output names the check.
- `whoami` prints `approval_required`.
- `apply` is unchanged; its refusal under the setting is printed by the existing error path.
