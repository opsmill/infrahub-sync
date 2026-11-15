# 39. Workers publish the names of their credential variables so validation can check them

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/007-plan-problem-reporting/research.md` (R15),
`dev/specs/archive/007-plan-problem-reporting/plan.md` (Complexity Tracking)

## Context

A registered package names its credentials as `$credential` references. The worker that runs a
stage reads each value from an `INFRAHUB_SYNC_CREDENTIAL_*` variable in its own environment.
When the variable is missing, the team finds out only when a run fails while it reads the
source or the destination.

`configs validate --branch` checks a registered version before a run. To check credentials, it
needs to know which variables the workers hold. The Sync API process cannot read them: it runs
in another process, and in the Compose deployment the API and the worker receive different
variables ([ADR 0026](0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md)).
Reading the API's own environment would check the wrong process.

## Decision

- **What a worker writes.** On each poll, each worker writes the names of its set, non-empty
  `INFRAHUB_SYNC_CREDENTIAL_*` variables to the product-store table `worker_environments`
  (`worker_id`, `credential_names`, `observed_at`). It never writes a value. An empty variable
  counts as unset, because Compose passes an unset variable as an empty string.
- **One row for each worker name.** The row's key is the worker's name, which the worker
  process makes when it starts. Each poll replaces the names and the time of that row.
- **Best effort.** When the product store cannot be reached or does not answer in time, the poll
  goes on, the worker logs one fixed line with no error text, and the next poll writes again.
- **The table.** It is created with `CREATE TABLE IF NOT EXISTS`, as the other product-store
  tables are. It is the only database change of this decision.
- **Which workers count.** A worker counts as live while its last write is no older than three
  times `PREFECT_WORKER_QUERY_SECONDS`, and at least thirty seconds.
- **What validation reports.** Branch validation reports `credential-not-set-in-worker` (error)
  for each `$credential` reference whose variable no live worker holds, and
  `worker-environment-unknown` (warning) when no live worker has written its names.
- **Names only.** Validation does not check whether a credential's value is accepted.

## Consequences

- A team learns before a run that a worker does not hold a credential variable, by its name.
- No credential value reaches the product store, the API response or the CLI output.
- A worker that has not polled within the liveness threshold is not counted, so a check made
  right after the workers start can return `worker-environment-unknown`.
- The check reflects the live workers at the time of validation. A worker started later with
  other variables can still run the stage.
- Validation compares a reference with the names of all live workers together. When two live
  workers hold different variables, it does not report a variable that only one of them holds.
- In the Compose deployment, a variable that is not on the fixed list of ADR 0026 does not
  reach the worker, and validation reports it as `credential-not-set-in-worker`.
- The table grows by one row for each start of a worker process, because each start makes a new
  worker name. No statement in `infrahub_sync/` deletes a row of `worker_environments`. A row of
  a stopped worker stops counting once it passes the liveness threshold, and it stays in the
  table. Each branch validation reads every row and then keeps the live ones.

## Alternatives considered

- **Read the variables in the Sync API process.** Rejected: it checks a process that does not
  run the stage, and Compose gives the API and the worker different variables.
