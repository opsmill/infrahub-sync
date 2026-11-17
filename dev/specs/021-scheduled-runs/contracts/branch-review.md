# Contract: branch and proposed change operations

Five operations that the worker of a scheduled run calls. This specification builds them (FR-029,
FR-030, research R15). They are two parts of INFP-655; only a scheduled run uses them here.

All five run in the worker, with the Infrahub client and the destination credentials the worker
already holds.

| Operation | Input | Result | Used for |
| --- | --- | --- | --- |
| Create a branch for a run | A branch name | The branch exists in Infrahub, created from the default branch, with no synchronization to Git. The run's schema read, plan, verify and apply steps use it. | FR-001, FR-029 |
| Delete a branch | A branch name that Infrahub Sync created | The branch no longer exists. | FR-020, FR-022, FR-029 |
| Open a proposed change | A branch that a run wrote to, a title, a description | The identifier of an open proposed change from the branch to the default branch | FR-001, FR-030 |
| Read a proposed change | An identifier | One of `open`, `approved`, `merged`, `closed` | FR-021, FR-031 |
| Close a proposed change | An identifier, a comment | The proposed change is closed and holds the comment | FR-020, FR-030 |

## Rules

1. **The branch exists before the plan.** The plan is made on the branch it is applied to,
   because the plan's manifest records the branch and the apply step refuses another one
   (research R11).
2. **`approved` means a person approved.** The state is `approved` when the proposed change is
   open and its `approved_by` holds at least one account.
3. **Failures are typed.** Each operation fails with a failure class that the run's failure record
   can hold (ADR 0021), with no text from Infrahub.
4. **Repeating is safe.** Creating a branch that already exists, closing a proposed change that is
   already closed, and deleting a branch that no longer exists succeed.
5. **Only what a schedule created.** The delete and close operations are called only with a branch
   and a proposed change that a `schedule_branches` row names.

## What the scheduled run passes

- The branch name: `sync/<schedule name>-<YYYYMMDD>-<6 hexadecimal characters>`, derived from the
  schedule and the start time.
- The title of the proposed change: `Scheduled sync <schedule name> <start time in UTC>`.
- The description: the run identifier, the configuration and version, and the plan's counts of
  created, updated and left-out records. No source data.
- The comment on a replaced proposed change: `Replaced by <identifier of the new proposed
  change> (run <run_id>)`.

## Order of the calls in one start

1. Read the state of the schedule's earlier proposed changes. One that is approved and open
   stops the start (FR-031).
2. Create the branch, plan, verify and apply on it. Delete it when the plan holds no operation.
3. After the write is committed: open the proposed change, then close the earlier ones and
   delete their branches.

## Permissions in Infrahub

The token of the worker must be allowed to create and delete branches, and to create, update and
comment on proposed changes. The deployment guide states this, and the first task of phase 4
records the exact permissions.

## Tests

- Unit tier: a fake Infrahub client, for the order of the calls and for each failure class.
- Integration tier (`integration` marker): the five operations against a running Infrahub, with a
  branch that the test deletes.
