# 52. A scheduled write to a branch that Infrahub Sync creates for the run needs no Sync approval

**Status**: Accepted
**Date**: 2026-11-18
**Source**: `dev/specs/archive/021-scheduled-runs/research.md` (R6, R11, R15),
`dev/specs/archive/021-scheduled-runs/spec.md` (FR-002, FR-003, FR-020, FR-021, FR-031)

Amends [ADR 0037](0037-an-approval-is-a-record-on-the-run.md).

## Context

[ADR 0037](0037-an-approval-is-a-record-on-the-run.md) makes `INFRAHUB_SYNC_REQUIRE_APPROVAL`
refuse a `sync` (`409 approval-required-for-sync`), and states that only the Sync API checks the
approval, when it admits a request.

A scheduled run is a `sync` with no person present to approve its plan. Version 1.1.0 of the
constitution (principle I) accepts one of two human approvals for it:

- the merge, in Infrahub, of the proposed change that the run opens, when the run writes to a
  branch that Infrahub Sync created for it;
- the creation of the schedule, when the schedule writes to the default branch. That write target
  must be chosen explicitly and refused while the Sync API requires an approved plan.

A scheduled run starts in the worker
([ADR 0051](0051-a-schedule-is-a-prefect-schedule-and-the-worker-admits-each-start.md)), not
from a request that the Sync API admits.

## Decision

- **Two write targets.** A schedule writes either to a new branch for each run with a proposed
  change in Infrahub (`branch-review`, the default), or to the destination's default branch
  (`default-branch`, only with `--write-to-default-branch`). A request to create a schedule that
  names no write target never gets the default branch. A change that names no write target keeps
  the schedule's write target, `default-branch` included.
- **Branch review needs no Sync approval.** A schedule with branch review is accepted, and its
  runs start, whatever `INFRAHUB_SYNC_REQUIRE_APPROVAL` is. The approval is the merge of the
  proposed change in Infrahub. The worker derives the branch name
  (`sync/<schedule name>-<YYYYMMDD>-<6 hexadecimal characters>`), so a caller cannot name the
  default branch or an existing branch.
- **Branch review writes only to its own branch.** The worker creates the branch from `main`,
  then plans, verifies and applies against it. After the stage result is committed, it opens the
  proposed change, then closes the schedule's earlier proposed change and deletes its branch. It
  never closes a proposed change that a person approved, and never deletes its branch. While a
  proposed change of the schedule is approved and open, a start does not run.
- **A run with branch review is never applied or resumed by a person.** The Sync API refuses
  `verify`, `apply` and `resume` on such a run.
- **A configuration version that declares a destination branch** cannot have a schedule with
  branch review, because the declared branch would win over the run's branch and the run would
  write with no review. The request is refused.
- **The default branch needs the setting off.** `POST /schedules` and a change of the write target
  are refused with `409 approval-required-for-schedule` when the setting is on and the write
  target is the default branch.
- **The setting is checked in three places for the default branch:**
    - the Sync API, when it accepts a schedule request;
    - the API process, when it starts: it stops each active schedule that writes to the default
      branch while the setting is on, with the reason `approval-required`;
    - the worker, when it admits a start: `decide_start` reads the setting from the worker's own
      environment, and when it is on, the start creates no run, the event
      `schedule.start_skipped` (`skipped-approval-required`) is written, and the schedule is
      stopped with the reason `approval-required`, in one transaction.
- **The worker check is what guarantees the rule.** It was added after a review found two gaps in
  the Sync API checks: a resume of a stopped schedule did not check approval, and a store error at
  the start of the API process skipped the stop step. The Compose deployment passes
  `INFRAHUB_SYNC_REQUIRE_APPROVAL` to `sync-worker` as to `sync-api`.

## Consequences

- On a Sync API that requires approval, a team can still run scheduled imports: each one waits in
  Infrahub as a proposed change until a person merges it. Nothing reaches the default branch before
  that merge.
- The statement of ADR 0037 that the worker does not check the approval setting no longer holds
  for scheduled starts. It still holds for stages that a request starts.
- A value of the setting that the worker does not accept makes the admission of a start fail, with
  no run and no write. The worker does not check the value when it starts.
- A deployment must pass the same value of the setting to the API process and to the worker. The
  Compose deployment does. The Helm chart sets it on the Sync API only, so on Kubernetes the worker
  check does not run; the Kubernetes deployment page states this gap.
- The worker's token needs the object permissions of other branches in Infrahub, to create the
  branch, write to it, open and close proposed changes, and add a comment. It does not need
  `edit_default_branch` for branch review. These permissions were read from the Infrahub source
  code. Not known: whether an account with only the permissions of the group "Infrahub Users"
  can run branch review; every check against a running Infrahub used the admin account.
- Branch review requires a destination whose default branch is `main`, because Infrahub accepts a
  proposed change only to `main`. The worker checks it before it creates the branch.
- One branch exists for the time of a plan when nothing changed, because the plan must be made on
  the branch it is applied to. The worker then deletes it.
- A failure to open or close a proposed change does not change the verdict of the write. It is
  recorded in the run's result and as an event, and the next successful run does the replacement
  again.

## Alternatives considered

- **Require a Sync approval for every scheduled run.** Rejected: nobody is present to approve, so
  a Sync API that requires approval could run no schedule.
- **Plan on the default branch and write to the new branch.** Rejected: the plan's manifest records
  the destination branch, and the apply step refuses a manifest that differs from the live adapter.
  It would need a second branch parameter and a change of the manifest and its checksum.
- **Create the branch only when the plan holds an operation.** Rejected for the same reason.
- **Let the operator name the branch.** Rejected: a caller could then name the default branch or an
  existing branch and write with no review.
- **Check the setting only in the Sync API.** Rejected after the review above: any gap in those
  checks would let a schedule write to the default branch with no approved plan.
