# Contract: branch and proposed change operations

Five operations that the worker of a scheduled run calls. This specification builds them (FR-029,
FR-030, research R15). They are two parts of INFP-655; only a scheduled run uses them here.

All five run in the worker, with an Infrahub client that the operations module builds from the
destination settings and credential the worker already holds, with the run's branch as its default
branch (research R15). It is built before the destination adapter and kept until the end of the
run.

| Operation | Input | Result | Used for |
| --- | --- | --- | --- |
| Create a branch for a run | A branch name | The branch exists in Infrahub, created from the default branch, with no synchronization to Git. The run's schema read, plan, verify and apply steps use it. | FR-001, FR-029 |
| Delete a branch | A branch name that Infrahub Sync created | The branch no longer exists. | FR-020, FR-022, FR-029 |
| Open a proposed change | A branch that a run wrote to, a title, a description | The identifier of an open proposed change from the branch to the default branch | FR-001, FR-030 |
| Read a proposed change | An identifier | One of `open`, `approved`, `merging`, `merged`, `closed`, `canceled`, or `missing` when Infrahub holds no proposed change with the identifier (a person deleted it) | FR-021, FR-031 |
| Close a proposed change | An identifier, a comment | The proposed change is closed and holds the comment, or it was already `closed`, `canceled` or `merged`, or it is `missing` | FR-020, FR-030 |

## Rules

1. **The branch exists before the plan.** The plan is made on the branch it is applied to,
   because the plan's manifest records the branch and the apply step refuses another one
   (research R11).
2. **`approved` means a person approved.** The state is `approved` when the proposed change is
   open and its `approved_by` holds at least one account.
3. **Failures are typed.** Each operation fails with a failure class that the run's failure record
   can hold (ADR 0021), with no text from Infrahub. A branch list that Infrahub answers in a form
   that cannot be read fails with `branch_list_failed`.
4. **Repeating is safe.** Creating a branch that already exists, closing a proposed change that is
   already closed, canceled, merged or missing, and deleting a branch that no longer exists
   succeed. A missing proposed change is kept with the cause `missing`, and its branch is left
   alone. Infrahub
   refuses an update of a proposed change that is not open, so the close reads the state first.
6. **The destination is `main`.** Infrahub accepts a proposed change only to `main`. A run with
   branch review on a destination whose default branch is another one fails before it creates its
   branch, with a failure class that says so.
7. **The close comes before the delete.** Deleting a branch sets its proposed changes to
   `canceled`.
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

Checked from the source code on 2026-11-18 (research, "Checked since, for R11 and R15"), not yet
against a running Infrahub. Because the requests go to the run's branch, the worker's account
needs:

- object permission `create` on `CoreProposedChange` and on `CoreChangeComment`, and `update` on
  `CoreProposedChange`, for branches other than the default branch;
- the global permission `delete_branch` only if the account did not create the branch itself.

Creating a branch needs no permission. The default group "Infrahub Users" holds all of these. The
deployment guide states them.

## Tests

- Unit tier: a fake Infrahub client, for the order of the calls and for each failure class.
- Integration tier (`integration` marker): the five operations against a running Infrahub, with a
  branch that the test deletes.
