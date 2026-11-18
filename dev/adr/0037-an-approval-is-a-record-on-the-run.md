# 37. An approval is a record on the run that the Sync API stores, and one service setting makes it required

**Status**: Accepted; amended on 2026-11-18 by
[ADR 0052](0052-a-scheduled-write-to-a-branch-that-sync-creates-needs-no-approval.md) — a
scheduled run that writes to a branch that Infrahub Sync created for it needs no approval, a
schedule that writes to the default branch is refused while approval is required, and the worker
checks the setting when it admits a scheduled start. The body below is unchanged
**Date**: 2026-11-14
**Source**: `dev/specs/archive/014-plan-approval/research.md` (R6, R7, R8, R9)

## Context

`apply` asks for the checksum of the reviewed plan, so the only way to accept a plan was to apply
it. A team that expects to validate and accept a plan first, and to apply it later, had no place to
record "this plan was reviewed and accepted", and a site could not refuse a write that nobody had
accepted.

The Sync API already holds what an approval needs: the run, and the checksum of the plan that the
worker published.

## Decision

- **A record, not a phase.** An approval is stored on the run in four nullable columns of
  `product_runs`: `approved_by`, `approved_at`, `approved_checksum` and `approval_reason`. A run
  has at most one approval, and it is never changed. An approved run stays in the phase it was in.
- **The Sync API answers.** `POST /runs/{run_id}/approve` is handled by `RunService.approve_run`.
  It submits no Prefect flow run and contacts no destination. It reads the run and the published
  plan, and writes one record. It does not verify the plan files again: that needs the runtime
  resolution that only the worker has, so the API uses the checksum and the checksum status of
  the published plan.
- **Which runs can be approved.** A run in phase `planned`, and a resumable run: one whose write
  stopped part-way and that is not cancelled.
  [ADR 0027](0027-an-interrupted-apply-resumes-from-its-saved-position.md) records why a resumable
  run can be approved, and that an approval recorded before the interruption stays valid.
- **What the request is checked against.** The service refuses, in this order: a caller who
  neither created the run nor is an administrator; a cancelled run; a run that is not in phase
  `planned` and is not resumable, or that has an execution in progress; a plan that cannot be
  read; a plan whose checksum was not confirmed when it was published; an `expected_checksum`
  that differs from the plan's checksum; a run that already has an approval.
- **One transaction.** `record_approval` locks the run's row with the same lock that write
  admission takes. In one transaction it checks that no execution of the run is unfinished, stores
  the approval with a conditional update, writes the run event of the accepted approval, and
  completes the request's mutation receipt with the response. When the update stores nothing, the
  same transaction completes the receipt with the refusal.
- **The setting.** `INFRAHUB_SYNC_REQUIRE_APPROVAL` applies to the whole service. It accepts
  `true`, `1`, `yes`, `on`, `false`, `0`, `no` and `off` in any case; unset means off. Any other
  value stops the service at start-up, with a message that names the variable and not the value.
  The service reads the setting again for each request.
- **What the setting changes.** When it is on, the Sync API refuses an `apply` or a `resume` of a
  run with no approval (`409 approval-required`), or with an approval of another checksum
  (`409 approval-checksum-mismatch`), before it reserves the receipt. It makes the same check
  again when a request is sent again and its receipt was reserved but not yet answered. It
  refuses `POST /runs` with `operation: sync` (`409 approval-required-for-sync`) before it creates
  the run record. `GET /me` returns `approval_required`, and `whoami` prints it.
- **Where it is enforced.** Only the Sync API checks the approval, when it admits the request. The
  worker does not check it.

## Consequences

- A team can accept a plan at one time and apply it at another, and the run shows who accepted
  which checksum, and when.
- Of two approvals sent at the same time, exactly one is stored. The other is answered
  `409 already-approved` with the stored approval.
- A response that was lost is recovered by sending the request again with the same idempotency
  key: the service returns the stored answer.
- When the setting is off, `apply` and `resume` do not read approvals, and approvals stay
  recorded.
- When the setting is on, `sync` cannot be used: the operator runs `plan`, `approve` and `apply`.
  A `sync` run that was started while the setting was off, and whose write stopped part-way, can
  be approved and then resumed.
- Approval does not run the destination checks. `verify` reports them, and `apply` runs them again.
- An approval records a decision. With one API token for each actor and no second reviewer, it
  does not prove that another person reviewed the plan.
- A change of the setting needs a restart of the Sync API and changes no stored run.
- A setting with a value that is not accepted stops the service. A site that meant to require
  approval therefore never runs without it by mistake.
- A stage that reaches the worker by another path than a request that the Sync API admitted is
  not checked for an approval.
- A later request that the Sync API answers without a flow run follows the same shape: lock the
  run's row, make the change and complete the receipt in one transaction.

## Alternatives considered

- **A worker stage that approves and also runs the checks of `verify`.** Rejected: it adds a flow
  run, a lease and a way to fail to a decision that contacts no system, and `apply` runs every
  check again.
- **One JSON column.** Rejected: the conditional update and later queries, such as "approved runs
  not yet applied", are simpler with columns.
- **A separate approvals table.** Rejected: a run has at most one approval and it never changes.
- **A setting for each configuration.** Deferred: the first version applies to the whole service.
- **Check the approval in the worker as well.** Deferred: the worker receives a stage only through
  a receipt that the Sync API admitted.
