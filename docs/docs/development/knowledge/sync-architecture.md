---
title: "Sync architecture"
---

## Sync architecture

Infrahub Sync compares data from a source system with data from a destination and records the
proposed changes in a saved plan. For example, you can review a plan from NetBox to
Infrahub before approving destination writes.

In the registered service workflow, the CLI submits work to the Sync API and a worker
executes it through Prefect.

### Service components

The Compose deployment runs five long-running services on one host:

| Component | Responsibility |
| --- | --- |
| Sync API | Accepts configuration packages and run requests; returns product records, plans and results. |
| Prefect server | Schedules flow runs and records their execution state and logs. |
| Sync worker | Polls a Prefect work pool and starts a process to execute each service flow run. Its adapters read the source and read or write the destination. |
| PostgreSQL | Stores Sync product records and Prefect execution records in separate databases. |
| S3-compatible object store | Stores immutable artifacts and internal plan checkpoints. The Compose bundle uses MinIO. |

```text
CLI / Python client ──requests──> Sync API ──submits──> Prefect server
                                     │                    │
                              product records       execution records
                                     │                    │
                                     v                    v
                                 PostgreSQL: separate databases

Sync worker ──polls──> Prefect server
     │
     ├──executes service flow──> source / destination adapters
     ├──reads and writes───────> Sync product records in PostgreSQL
     └──reads and writes───────> S3-compatible object store
                                ^
                                │ reads artifacts
                             Sync API
```

The CLI accesses the Sync API; it does not run adapters or read the worker's filesystem.
Bootstrap jobs create the databases, artifact bucket, work pool and service deployment
before the API and worker start. Configuration packages are registered separately.
See [Compose deployment](../../compose-deployment.mdx) for the operating procedure.

### One registered run

A **product run** is the durable Sync record identified by `run_id`. A **Prefect flow run**
is one execution associated with it; planning, verification and apply can have separate
Prefect execution IDs for the same product run.

1. **Submit.** The API reads the selected registered configuration version and binds the
   product run to its configuration ID, version and package checksum. It reserves the run
   before submitting the requested stage to Prefect, then stores the Prefect execution link.
2. **Execute.** The worker claims that execution, reads the registered package and resolves
   its credential references. For planning, it reads the destination schema and builds
   runtime models before loading the source and destination data.
3. **Save the plan.** The engine compares the two datasets and records the proposed
   operations. The worker publishes an internal plan checkpoint and then the review
   artifact available through the API, so a reviewable plan also has the retained data needed for a later apply.
4. **Review and apply.** You review the plan through the Sync API and give its checksum to
   `apply`. You can first record an approval of that checksum with `approve`; a site can
   require it.
   The apply stage retrieves the checkpoint, verifies its binding and checksum, and checks
   the destination schema before writing. It uses the saved operations rather than
   extracting the source again.
5. **Record the result.** The worker saves final evidence and updates the product run.
   The API returns the retained result alongside available Prefect execution information.

Verification can also run as a separate stage. It checks the retained plan files and, for a
run of a registered configuration, then runs against the destination every check that an apply
runs before its first write; it writes nothing and never reads the source. A confirmed `sync`
combines planning, verification and apply in one service execution, publishing the plan before
the first destination write.
Both `sync` and saved-plan `apply` hold a PostgreSQL advisory lock for the configuration
while they work. For `sync`, the lock covers planning, verification and apply. This serializes
writes for that configuration; it is not a lock on every destination object that other
configurations or external tools might change.

For command examples, follow the [reviewed-run procedure](../../compose-deployment.mdx#the-operator-sequence).
For the stage entrypoints, see [Prefect orchestration](orchestration-prefect.md#registered-service-integration).

### Read state of each side of a plan

While a `plan` or `sync` stage runs, the run response shows the state of the source read and of
the destination read. [ADR 0035](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0035-the-engine-reports-each-sides-read-to-an-optional-observer.md)
records why it works this way.

- **The engine reports.** `Potenda._load_side` calls an optional `SideReadObserver`
  (`infrahub_sync/potenda/read_progress.py`) when a side's read starts and when it ends. The end
  report is a `SideReadReport`: for a read that succeeded, the read mode (`full` or
  `incremental`) and the number of nodes of each kind, sorted by kind name; for a read that
  failed, the class name of the error and nothing else. The engine also writes one log line when
  a read ends, such as `Load: source read succeeded (full): 5 nodes; DcimDevice 3, LocationSite 2`
  or `Load: destination read failed (ConnectionError)`.
- **The caller chooses the observer.** `execute_run` takes the private keyword `_read_observer`
  and sets `Potenda.read_observer` after it built the engine. The direct Prefect flow passes none.
- **The worker stores.** For a `plan` or `sync` stage, the worker first stores both sides as
  `waiting` in one write (`record_waiting`), then passes a `StoreSideReadObserver`
  (`infrahub_sync/service/read_progress.py`). The observer writes one JSON document for each side
  through `record_side_reads`, into the `source_read` and `destination_read` columns of the
  execution's row in `prefect_executions`. The update applies only while the execution has no
  terminal verdict.
- **The Sync API derives.** `stage_reads` in the same module builds `OrchestrationSummary.reads`
  when the run is read. `reads` is `null` when the execution stored no document for either
  side: an execution recorded before the read state existed, an execution of a stage other than
  `plan` or `sync`, or a `plan` or `sync` execution the worker has not started. Otherwise it
  returns a side stored as `waiting` as `not_started`, and a side stored as `reading` as
  `interrupted`, once the execution ended without completing. It writes nothing.

A report never changes the stage. When the observer raises, when the node count fails, or when
the store write fails, the code logs a warning with the error's class name and the read
continues. The stored documents are described in
[Durable product records](../../reference/durable-product-records.mdx#record-and-identity-contract),
and the returned states in
[Read state of each side](../../reference/sync-http-api.mdx#read-state-of-each-side).

### Requests the Sync API answers without a flow run

Two requests change a run and submit nothing to Prefect. The Sync API checks the request, then
makes the change and completes the request's mutation receipt in one store transaction. That
transaction first locks the run's row with the lock that write admission takes. A withdrawal
therefore never succeeds together with an `apply` or an `approve` of the same run, and no approval
is stored while an execution of the run is unfinished.

- **Approve.** `RunService.approve_run` records that the reviewed plan is accepted, bound to its
  checksum. `record_approval` stores `approved_by`, `approved_at`, `approved_checksum` and
  `approval_reason` on the run, only when the run has no approval and no unfinished execution and
  is in phase `planned` or resumable. Approval is not a phase and runs no destination check.
  [ADR 0037](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0037-an-approval-is-a-record-on-the-run.md)
  records why.
- **Withdraw.** `RunService.cancel_run` withdraws a plan when the run is in phase `planned` and
  has no active execution. `cancel_planned_run` sets the phase and the outcome to `cancelled`.
  After that, `apply`, `approve` and `resume` refuse the run; a `verify` is still admitted. A run
  with an active execution is cancelled through Prefect instead, as before.
  [ADR 0038](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0038-cancelling-a-finished-plan-withdraws-it.md)
  records why.

`infrahub_sync/service/settings.py` reads `INFRAHUB_SYNC_REQUIRE_APPROVAL` for each request. When
it is on, the Sync API refuses an `apply` or a `resume` of a run without an approval of the same
checksum, and refuses a `sync` before the run record is created. The worker does not check the
approval. A value that is not accepted stops the service at start-up. The request order and the
error codes are in the [Sync HTTP API reference](../../reference/sync-http-api.mdx#approve-the-reviewed-plan).

### A scheduled run

Infrahub Sync has no loop of its own that starts scheduled runs. Prefect holds the cadence: each
Sync schedule has one Prefect schedule on the deployment `infrahub-sync-service/run`, with a
`CronSchedule(cron, timezone)` and the flow parameters `schedule_id` and `stage = "sync"`.
Prefect's scheduler creates the flow runs ahead of time, and a Sync worker runs them like any
other flow run of that deployment.
[ADR 0051](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0051-a-schedule-is-a-prefect-schedule-and-the-worker-admits-each-start.md)
records why, and
[ADR 0052](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0052-a-scheduled-write-to-a-branch-that-sync-creates-needs-no-approval.md)
records why a run with branch review needs no Sync approval.

- **The Sync API stores, then matches Prefect.** `ScheduleService` (`service/schedules.py`)
  validates a request, writes the schedule to the product store, then calls
  `match_prefect_schedules`. That step creates, updates, deactivates or deletes Prefect schedules
  until Prefect holds one schedule for each stored schedule, active only while the stored
  schedule is `active`. A schedule that Prefect does not hold as stored keeps `prefect_synced`
  false, and the request answers `503 orchestration-unavailable`. The step runs again on each
  schedule request, at the start of the API process (`ScheduleService.start`) and in the
  bootstrap job.
- **The API process checks what the worker cannot.** At start-up, `ScheduleService.start` stops
  each active schedule whose owner's actor no longer exists, or that writes to the default
  branch while `INFRAHUB_SYNC_REQUIRE_APPROVAL` is on, and writes the `schedule.stopped` event.
  The worker has no API token and cannot check the owner's actor. It does read
  `INFRAHUB_SYNC_REQUIRE_APPROVAL` from its own environment: `decide_start` refuses a start of a
  schedule that writes to the default branch while it is on, and the admission stops the schedule
  with the reason `approval-required` (research R6 of spec 021). When the stop step of
  `ScheduleService.start` fails on a store error, it is tried three times, then logged.
- **The worker admits the start.** A flow run that carries `schedule_id` and no `run_id` takes
  the second way into `service_sync_run` (`service/flow.py`). The flow refuses it unless Prefect's
  scheduler created it (`auto_scheduled`). `admit_scheduled_start`
  (`service/schedule_admission.py`) then decides on the start from the stored schedule and the
  flow run's `expected_start_time`: it records the starts missed since the last one, skips a
  start that is late or not the most recent, fails a start whose version can no longer be run,
  applies the overlap policy with `run_in_progress`, and, for branch review, reads the state of
  the schedule's earlier proposed changes. The product store saves the decision in one
  transaction (`ProductProjection.admit_scheduled_start`): either the events of a start that
  creates no run, or the run with the owner as actor, its accepted receipt, its write admission,
  its execution claimed by this worker and, for branch review, a `pending` row in
  `schedule_branches`. A unique index on `(schedule_id, scheduled_start_at)` allows one run for
  each start, so a retried or second flow run of the same start creates none.
- **The flow runs the `sync` stage.** The admission passes `confirm_left_out` (always true for
  branch review, else the schedule's left-out setting), `branch_review` and the schedule's
  earlier branches to the stage. The stage then runs as a `sync` that a person starts.

With branch review, the stage does more under the write guard, then after the commit
(research R11 and R15):

1. Create the run's branch from `main` (`service/branch_review.py`, `create_branch`), after
   checking that the destination's default branch is `main`.
2. Plan, verify and apply against that branch. When the plan has no operation, delete the branch
   and end with `no-change`; the row moves to `removed`.
3. After the stage result is committed, outside the scope whose failures make a write ambiguous,
   open the proposed change and record the row as `open`. Then close the proposed change of each
   earlier `pending` or `open` row with a comment that names the new one, delete its branch, and
   move the row to `replaced`. A proposed change that a person merged or closed moves to `kept`,
   and one that a person approved while the run was writing is left in place.

A failure in step 3 is recorded as `branch_review.failure` in the stage result and as the event
`proposed_change_failed`; it does not change the verdict of the write. The store saves the end of
a scheduled run and the schedule's count of failed runs in one transaction, and stops the
schedule at the fifth failed run in a row. The records are described in
[Durable product records](../../reference/durable-product-records.mdx#schedule-records).

#### When a start gets no flow run

<!-- Extracted from dev/specs/archive/021-scheduled-runs on 2026-11-18 -->

Prefect's scheduler creates the flow runs of a schedule ahead of time: at least 3 runs and at
least one hour of runs. It creates them only for the start times after the moment its loop runs,
about once a minute. A change or a deletion of one Prefect schedule deletes the future flow runs
of every schedule of the deployment that have not run, and a deletion also deletes the late ones.
For that reason `match_prefect_schedules`, after it creates, changes or deletes a Prefect schedule,
calls `PrefectOrchestration.create_scheduled_runs` (`POST /deployments/{id}/schedule`), which
creates the flow runs of every active schedule at once, with the idempotency key of the scheduler. A
failure of that call is logged; Prefect's scheduler then creates the flow runs at its next loop.

A start can still have no flow run:

- when that call failed, for a start within the next minute;
- when a schedule is deleted while the worker is stopped, for the late starts of the other
  schedules;
- when the Prefect server is stopped for longer than the flow runs it created ahead.

The worker records such a start as missed, with the cause `missed-skipped`, when it admits the
next start of the schedule. A start that passes while the worker or the Prefect server is stopped
keeps its flow run; when the worker takes it, the admission counts a start up to 5 minutes late
(`ON_TIME_SECONDS` in `service/schedule_admission.py`) as on time. It runs the most recent start
that is later than that but within the schedule's late-start duration, with a
`missed-started-late` event, and records every other late start as missed, with no run. The bootstrap job keeps the Prefect schedules: applying the deployment again does not
remove schedules that have a slug, so its match step only repairs differences.

Prefect's scheduler needs SQLAlchemy below 2.1 while the `prefect` pin is 3.8.6; see the traps in
[Prefect orchestration](orchestration-prefect.md#prefect-specific-traps).

#### A start that waits

Two cases make a start wait, with no run created:

- the overlap policy `queue`, while another run of the configuration is in progress;
- a destination that does not answer yet. Before the admission, `_destination_not_ready`
  (`service/flow.py`) reads the destination schema of the schedule's version. Only the read
  reasons `timeout` and `unreachable` make the start wait; a refused token, a missing permission
  or a missing branch does not.

The flow sets the flow run's state to `AwaitingRetry`, 60 seconds later
(`QUEUED_START_RECHECK_SECONDS`, `DESTINATION_RECHECK_SECONDS`), through the Prefect client, then
raises Prefect's `Pause` signal (`_run_again_later`). The worker submits the flow run again at
that time, with the same `expected_start_time`, so the admission applies the late-start rules to
the original start. A destination wait lasts only while the start can still run one minute later
within its late-start duration; after that, the run starts and records the destination failure.
A wait is not a failed run and does not count toward the five failed runs that stop a schedule.
[ADR 0053](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0053-a-waiting-scheduled-start-ends-its-flow-run-with-the-pause-signal.md)
records why the flow does not return the state.

### Persistence and deployment limits

Sync retains configuration versions, product runs and execution links in PostgreSQL.
It retains artifacts and internal checkpoints in object storage. Each worker stage creates
its own temporary directory and removes it when the stage ends; a later stage retrieves
its plan from object storage, not from a shared cache directory.

Replacing an API or worker process preserves the database and object-store volumes.
Retained records do not guarantee that an interrupted write resumes. If the outcome of a write
is uncertain, inspect its evidence and the destination before creating a fresh plan;
follow the [uncertain-write procedure](../../compose-deployment.mdx#when-the-outcome-of-a-write-is-uncertain).
Follow the [stop, restart and reset procedure](../../compose-deployment.mdx#stop-restart-and-reset)
to choose between replacing processes and deleting durable state.

The qualified deployment is one Linux amd64 host. Process workers and separate storage
services do not establish support for multi-host deployment or high availability.
There is no verified backup, restore or in-place migration procedure.
See [supported platforms and limits](../../operations/supported-platforms-and-limits.mdx)
for the qualification boundary, and [durable product records](../../reference/durable-product-records.mdx)
for storage settings and retained data.

### The foundation classes

[DiffSync](https://github.com/networktocode/diffsync) represents each object as a model
instance with a stable identity:

- **Adapter** — loads objects from one system and implements its supported destination operations.
- **Model** — a typed record with `_identifiers` (the natural key) and `_attributes`
  (the comparable fields). DiffSync matches objects by identifiers and compares their attributes.

`DiffSyncMixin` and `DiffSyncModelMixin` extend these base classes for Sync.
See [adapter anatomy](adapter-anatomy.md) for their contract.

### Source and destination

A configuration names one source and one destination. The direction is fixed for a run:
the source is read-only, and writes target the destination. An adapter can serve either
role only if it implements that role's operations.

The worker resolves registered adapters from installed code and checks their declared
capabilities. See [configuration foundation](configuration-foundation.md) for package
validation and credential references, and [schema mapping](schema-mapping.md) for resource
and field comparisons.

### The sync engine

`Potenda`, in `infrahub_sync/potenda/`, loads adapter data, computes the DiffSync comparison
and writes saved plans. The service applies those plans through the destination's
planned-write methods. Deletes are recorded for review but are not executed.
See [planned writes and apply](planned-write-and-apply.md) for the write operations and
relationship handling.

### The code-generation path

Registered runs build DiffSync models in memory from the destination schema through
`infrahub_sync/runtime_schema/`. They do not depend on generated Python files.

For adapter development and the direct configuration-directory path,
`infrahub_sync/generator/` renders models from Jinja2 templates and `plugin_loader.py`
resolves adapter classes. Filesystem adapter paths belong to that development route;
a registered package cannot declare one. See [local adapters](../../adapters/local-adapters.mdx).

### See also

- [Repository tour](repository-tour.md) — locate the module responsible for each stage or record.
- [Prefect orchestration](orchestration-prefect.md) — distinguish service execution from the direct Prefect integration.
- [Incremental extraction and cache](incremental-and-cache.md) — understand retained extraction data and cache behavior.
- [The saved plan artifact](plan-artifact.md) — inspect the plan's manifest and operations.
- [Adding an adapter](../guides/adding-an-adapter.md) — implement and test a connector.
