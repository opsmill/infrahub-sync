# Research: Scheduled runs that write without a person present

Each entry states a decision, why, and what was rejected. File and line references are to
`feature/v3-develop` at `7987596f`.

## R1. Prefect holds the cadence: one Prefect schedule for each Sync schedule

**Decision**: Each Sync schedule has one Prefect schedule on the existing deployment
`infrahub-sync-service/run`. The Sync API creates it with
`create_deployment_schedules`, with a `CronSchedule(cron, timezone)`, the slug of the Sync
schedule's identifier, and the parameters `schedule_id` and `stage = "sync"`. A pause, a resume, a
change of the cadence and a deletion update or delete that Prefect schedule
(`update_deployment_schedule`, `delete_deployment_schedule`). Prefect's scheduler creates the flow
runs. Infrahub Sync has no loop of its own.

**What was checked** in the installed Prefect 3.8.6:

- `DeploymentScheduleCreate` has the fields `schedule`, `active`, `parameters`, `slug` and
  `replaces`, so each schedule of one deployment carries its own parameters.
- `CronSchedule` has `cron` and `timezone`.
- A flow run holds `auto_scheduled` and `expected_start_time`.
- The scheduler service is on by default. It creates at least 3 runs and at least one hour of
  runs ahead for each schedule, at most 100 runs, and handles a new schedule within 5 seconds.
  The late-runs service marks a run as late after 15 seconds.
- Sync's Prefect server starts Prefect's own services with their defaults
  (`service/prefect_server.py:34-38`).

**What the other OpsMill products do**:

- Infrahub puts a `cron` on a workflow definition, and `to_deployment` turns it into a
  `DeploymentScheduleCreate(schedule=CronSchedule(cron=...))`
  (`infrahub/backend/infrahub/workflows/models.py:52, 86-87`). Six system workflows use it, for
  example `git_repositories_sync` every minute. Infrahub has no schedule that a user creates.
- The Prefect package vendored in this repository has the same `cron` field on its
  `WorkflowDefinition` (`opsmill_prefect_extras/workflows/definitions.py:129, 284-285`).
- Styrmin has a `BackupSchedule` record that a user creates, with a `cron_expression` and a
  `prefect_deployment_id` column. Its service only stores the record
  (`styrmin/backend/styrmin_backend/services/backup/scheduler.py`); no code sets
  `prefect_deployment_id` or starts a backup from it. The intended design is the one of this
  decision, and it is not built there.

**Rationale**:

- Prefect already runs a scheduler in Sync's deployment, with cron and time zone handling, a
  pause for each schedule, and the list of coming runs in its interface. AD-1 gives Prefect
  "dispatch, scheduling, retries, cancellation, and workers".
- It needs no periodic task in the API process, no new package, and no rule for two API processes.
- The product owner asked for it.

**What it changes in the worker**: a flow run that Prefect's scheduler created has no run record,
and the flow refuses such a flow run today (`service/flow.py:518-533`). R2 adds the admission of a
scheduled start.

**Alternatives considered**:

- A loop in the Sync API process that starts runs through `RunService`. It was the first design
  of this plan. Rejected: it builds a second scheduler next to the one Prefect runs.
- One Prefect deployment for each Sync schedule. Rejected: the worker admits only the deployment
  `run` (`service/worker.py:118-130`), and one deployment with several schedules needs no change
  there.

## R2. A scheduled flow run is admitted by the worker: one run for each schedule and start time

**Decision**: `service_sync_run` gains a second way in. When the flow run is `auto_scheduled` and
carries `schedule_id`, the flow calls one store method before anything else:
`admit_scheduled_start(schedule_id, scheduled_start_at, flow_run_id, worker_id)`, with
`scheduled_start_at` = the flow run's `expected_start_time`. In one transaction it:

1. Reads the schedule. A schedule that is not `active` ends the flow run with no run record.
2. Records, as missed starts, the starts of the cadence between the schedule's last admitted start
   and this one that have no run (R10).
3. Decides whether this start runs: the lateness check (R10) and the overlap check (R7). A start
   that does not run is recorded as an event and ends the flow run in a completed state.
4. Otherwise creates the run row (`trigger_type`, `schedule_id`, `scheduled_start_at`, the owner
   as actor, the version and branch from the schedule), its accepted receipt, its write admission
   and its execution row, already claimed by this worker.

The flow then continues as the `sync` stage of that run. A unique index on
`(schedule_id, scheduled_start_at)` allows one run: a flow run that Prefect retries, or a second
flow run for the same start, finds the run and is refused as today.

A flow run with `schedule_id` that is not `auto_scheduled`, for example one started by hand from
the Prefect interface, is refused.

**Rationale**:

- The stored run record still exists before any work, as ADR 0024 requires. It is written by the
  worker, which already has the product store, instead of by the Sync API.
- Prefect gives each scheduled flow run an idempotency key from its schedule and start time, and
  the unique index gives the same guarantee in the product store.
- The worker cannot ask the Sync API: it has no API token, and adding one would add a credential.

**What the worker cannot check**: whether the owner's actor still exists and whether the Sync API
requires approval. Both are settings of the API process. R6 and R8 check them in the API process
when it starts, and deactivate the Prefect schedule.

## R3. Prefect validates the cron expression and the time zone; no new package

**Decision**: The Sync API validates a cadence by building the Prefect `CronSchedule` it will
send, and computes `next_start_at` with the server-side schedule class of the installed Prefect
(`prefect.server.schemas.schedules.CronSchedule.get_dates`), which is what the scheduler uses.
`pyproject.toml` gets no new dependency.

**Rationale**: the cadence that the operator views and the cadence that Prefect runs are computed
by the same code, including a start that falls in the hour a daylight-saving change removes or
repeats.

**Alternatives considered**: declare `cronsim` and compute start times in Sync. Rejected with the
loop of R1.

**Checked** in the installed Prefect 3.8.6, on 2026-11-17:

- An invalid cron expression and an unknown time zone name raise a Pydantic `ValidationError`
  when the `CronSchedule` is built.
- Prefect accepts a six-field expression, whose first field is seconds. The Sync API refuses any
  expression that does not have five fields, before it builds the `CronSchedule`, so the shortest
  cadence is one minute.
- `get_dates` runs with no database session.
- For `30 2 * * *` in `Europe/Paris`, the night that has no 02:30 gives one start, at 01:30 local
  time, and the night that has 02:30 twice gives one start, the first. A start is never doubled.

## R4. A scheduled run is a `sync` run with three extra facts

**Decision**: The run that the admission of R2 creates holds what `create_run` writes
(`service/service.py:343-444`), with these differences:

- The actor is the schedule's owner, read from the stored schedule (R8).
- `operation` is `sync` and `confirm_writes` is true.
- The run row holds `trigger_type = "scheduled"`, `schedule_id` and `scheduled_start_at` (R2).
- The admission returns the schedule's left-out setting and write target to the flow. They are
  not flow parameters: the Prefect schedule carries only `schedule_id` and `stage`.
- The refusal `approval-required-for-sync` does not apply when the write target is branch review
  (R6).

`POST /runs` is not changed. Its runs record `trigger_type = "manual"`.

**Rationale**: The specification asks for the same lifecycle, history and events as a run that a
person starts. Writing the same receipt, run row, write admission and execution row gives that.

**Trigger types**: this specification writes `manual` and `scheduled`. INFP-656 also names
`system`, for a run that another system starts. Nothing distinguishes such a run from a manual
one today, so `system` is left to INFP-658.

## R5. The left-out setting reaches the engine as the existing `confirm_left_out` argument

**Decision**: The `sync` branch of `_execute_stage` (`service/flow.py:930-998`) skips
`_left_out_stop` and passes `confirm_left_out=True` to its apply step when the schedule's
left-out setting, which the admission of R2 returns, is on. `CreateRunRequest` gets no new field.

**Rationale**: The flow parameter exists (`service/flow.py:1421`) and reaches the engine for
`apply` and `resume` (`flow.py:895`). The `sync` branch ignores it today. A person who wants this
for a manual run already has `apply --confirm-left-out`; adding it to `POST /runs` is not part of
this specification.

## R6. The approval setting and the two write targets

**Decision**:

- `POST /schedules` and a change of the write target are refused with `409
  approval-required-for-schedule` when `approval_required()` is true and the write target is the
  default branch.
- The setting comes from the environment of the API process (`service/settings.py:47-53`), and a
  restart can turn it on. When the API process starts, it checks every active schedule (R8). A
  schedule that no longer passes is moved to the state `stopped` with the reason
  `approval-required`, its Prefect schedule is deactivated, and an event is recorded.
- A scheduled run with branch review is accepted whatever the setting is. The service generates
  the branch name, so the caller cannot name the default branch or an existing branch.

**Rationale**: FR-002. The check lives where `approval_required()` is already read: the service,
not the worker (`service/service.py:389`).

**A write target that is not delivered yet** is refused with `422
schedule-setting-not-available`: branch review until the worker side of user story 1 exists, and
the default branch until user story 2 is delivered. The approval refusal is checked first. When
branch review is not delivered and the request names no write target, the request is refused: a
write to the default branch is never chosen for the operator (FR-003).

**Decision record**: the rule amends ADR 0037 and needs its own ADR, written when the
specification is extracted.

**A third stop reason**, `repeated-failures`, is set by the worker with the end of the fifth
failed run in a row (FR-032). The count is a column of the schedule, updated in the transaction
that saves the end of the run.

## R7. Overlap: one store question, asked when a start is admitted

**Decision**: A new store method answers "which run of configuration X is in progress". A run is
in progress when either holds:

- It has a `prefect_executions` row with `terminal_at IS NULL`.
- It has a receipt that is not resolved, for an operation that starts a stage (the set the store
  already arbitrates, `product_store/store.py:299-305`), and the receipt is younger than the
  admission time limit `INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS` (default 300).

The admission of R2 asks it:

- `reject`: when a run is in progress, the start is skipped and the event names that run.
- `queue` (user story 3): the schedule stores the waiting start, and the flow run returns a
  Prefect state that schedules it again one minute later, until no run is in progress. A
  further start while one waits is skipped.

**Rationale**:

- Phase alone does not tell: an `apply` leaves the run in phase `planned` while it runs. A run in
  phase `planned` that waits for a person, an interrupted run and an abandoned execution all have
  `terminal_at` set, so they do not block a schedule.
- The age limit is needed because nothing resolves a receipt that has no execution: the liveness
  loop reads only `prefect_executions` (`service/liveness.py:151-156`). Without the limit, a
  request of a person that stopped half-way would block every later start.
- Prefect's own concurrency limit of a deployment is not used: it counts flow runs of one
  deployment, and the rule here is one run for each configuration, whoever started it.
- "Any run" includes a `plan` or a `verify` of a person. FR-014 asks for that.
- The question and the start are not one transaction. A person can start a run between the two.
  SC-006 does not depend on this check: the write guard of ADR 0010 still serializes writers
  (`service/apply_guard.py:42-52`), and the scheduled run that loses waits 30 seconds and fails.
  The check is what makes the common case a recorded skip and not a failed run.
- A `sync` of a person is not refused today while an earlier run of the configuration needs
  reconciliation. A scheduled run is not refused either.

**Alternatives considered**: take the write guard in the API process. Rejected: the guard is a
session lock that the worker must hold for the whole write.

## R8. The owner is an actor name; the API process checks it when it starts

**Decision**: A schedule stores its owner's actor name and no token. `PrincipalResolver` gains
one method that returns the `Principal` of a named actor, or `None`. The API process calls it for
every active schedule when it starts, in the same step that makes the Prefect schedules match
the stored ones (R13). Actors change only at a restart, so no later check is needed. `None` moves the schedule to `stopped` with the reason `owner-not-found`. A resume
succeeds when the actor exists again. A take-over by an administrator (user story 3) sets the
owner to that administrator and the state to `active`.

An actor name is not a fixed identity: a changed token under the same name keeps the schedule
running, and a name given to another person makes that person the owner. The specification states
both as assumptions.

**Rationale**:

- Tokens and actors come from `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, read once when the process
  starts (`service/auth.py:12, 46-75`). The resolver holds the actor names in a private
  attribute, and `RunService` receives only its secret values (`service/serve.py:90-96`). One
  read method is the smallest change.
- Storing a token would put a credential in the product store (constitution principle VI).
- `_owned_run` then works with no change: the owner or an administrator can cancel a scheduled
  run (`service/service.py:1773-1786`).

## R9. Schedule events use a new resource segment, a related resource and a `details` field

**Decision**:

- `EVENT_TYPE_PATTERN` becomes `^infrahub_sync\.(run|config|schedule)\.[a-z_]+$`
  (`product_store/models.py:710`).
- `RelatedResource.kind` and `PrimaryResource.kind` gain `schedule`, with its place in
  `_RELATED_KIND_ORDER` and in the `primary_resource` property (`models.py:713, 771-780`).
- Every schedule event names the schedule as a related resource, so `GET /events` filters on it
  with the existing `related` filter (`service/app.py:283-294`).
- `run_event` adds the schedule as a related resource when the run holds a `schedule_id`
  (`service/events.py:108-122`). Every event of a scheduled run then names its schedule, including
  the events that the liveness loop and the failure path write.
- `AuditEvent` and the public event gain an optional `details` field: a small object with closed
  keys (`start_at`, `starts`, `first_start_at`, `last_start_at`, `left_out`, `branch`,
  `proposed_change_id`, `replaced_run_id`, `cause`). It is stored in a new nullable column of
  `audit_events`. `reason` keeps its meaning: the text of the person who made the request.
- `commit_claimed_execution`, `complete_mutation` and the admission method of R2 take a
  sequence of events. Today the first two take one (`product_store/store.py:2548-2556,
  3464-3475`), and a scheduled run can write three in one transaction.
- An event that concerns a run sets `run_id`: the run that a start created, or the run in
  progress that caused a skipped start.
- An event that the admission writes has the schedule's owner as its actor, because `actor` is
  required (`models.py:734-780`).

The event types are listed in [contracts/events.md](contracts/events.md).

**Rationale**: ADR 0033 saves an event in the transaction of its decision. The start time and the
left-out count are facts of the event, not a person's reason, and the reason of a stage result is
read from the receipt before the plan exists (`service/flow.py:1562-1577`).

**Bound on missed starts**: one event for each missed start, up to 500 for one schedule in one
pass. Older ones are recorded in one event whose `details` hold their count and their first and
last start times. A schedule that starts every minute and a service that was not running for a
week would otherwise write 10,080 events in one transaction.

## R10. Missed and late starts follow from how Prefect creates runs

**Decision**:

- Prefect creates the flow runs of a schedule ahead of time (R1). When the worker or the Prefect
  server was not running at a start time, the flow run waits and starts when the worker returns.
  The admission of R2 then compares the time with `expected_start_time`:
    - User story 1: a start that is more than 5 minutes late does not run. It is recorded as a
      missed start.
    - User story 3: the schedule's `start_late_by_at_most_seconds` (default 3600) replaces the 5
      minutes for the most recent start. An earlier late start never runs.
- When the Prefect server was not running for longer than the runs it had created ahead, some
  starts have no flow run at all. The admission of the next start records them: it computes the
  starts of the cadence since the last admitted start (R3) and writes one missed-start event for
  each one that has no run.
- A restart of the Sync API does not lose a start. The API process is not involved in a start.

**Rationale**: FR-017 asks for one run however many starts were missed, and FR-018 for a record
of every missed start. The late check must be ours: Prefect runs a late flow run whatever its
age.

**Consequence**: missed starts are recorded when the next start is admitted, not when the
service returns. A schedule that is paused or stopped records none.

## R11. Branch review runs in the worker: the branch first, the proposed change after the write

**Decision**: The admission of R2 derives the branch name when it creates the run, stores it as
the run's destination branch, and writes a `schedule_branches` row in the state `pending` in the
same transaction. In the worker, the `sync` stage of a run with branch review does this under the
write guard:

1. Create the branch (R15).
2. Plan, against the new branch.
3. If the plan has no operation, delete the branch and end with the outcome `no-change`.
4. Verify and apply against the new branch. A plan that leaves out records does not stop the
   run: with branch review the remaining records are always written, because the proposed
   change is the review. The schedule's left-out setting applies only to a write to the
   default branch.

After the write is committed as the stage result, and outside the scope whose failures make a
write ambiguous (`service/flow.py`, `_failure_exit`):

5. Open the proposed change (R15). Its description states the count of records left out.
6. For each earlier branch of the schedule in the states `open` and `pending`, close its
   proposed change, if it has one, with a comment that names the new one, and delete its branch
   (FR-020, FR-021).

A failure of step 5 or 6 does not change the verdict of the write. It is recorded in the run's
result and as an event, and the next successful run of the schedule does step 6 again.

**Before the admission**, for a schedule with branch review, the flow reads from Infrahub the
state of the schedule's earlier proposed changes (R15) and passes the states to the admission of
R2:

- One of them is approved and open: the start does not run. It is recorded as skipped with the
  outcome `skipped-approved-change-open` (FR-031). Step 6 therefore never meets an approved
  proposed change, except one that a person approved while the run was writing; that one is
  left in place.
- Merged or closed by a person: its row moves to the state `kept`, and the start runs.
- Infrahub cannot be reached: the start is recorded as failed, and no run is created.

**Rationale**:

- Only the worker holds Infrahub credentials (ADR 0031, ADR 0039).
- The plan must be made on the branch it is applied to. The plan's manifest records the
  destination address and branch, and the apply step refuses a manifest that differs from the
  live adapter, with no override (`plan/verify.py:462-515`, `plan/models.py:566`). The worker
  also uses one branch for the schema read, the plan and the apply (`service/flow.py:932-987`),
  and the schema read fails on a branch that does not exist. Planning on the default branch and
  writing to another would need a second branch parameter and a change of the manifest and its
  checksum.
- The `pending` row names a branch before the worker creates it, so a run that fails, is
  interrupted or is cancelled never leaves a branch that no record names.
- A failed run replaces nothing, because step 6 comes after the write and the new proposed
  change.

**A run with branch review is never applied or resumed by a person.** `verify`, `apply` and
`resume` requests on such a run are refused. A run interrupted during its write leaves a
`pending` branch that the next successful run deletes. Nothing of it reached the default
branch.

**A configuration version that declares a destination branch** cannot have a schedule with branch
review. `effective_destination_branch` puts the declared branch before the run's branch
(`configuration/runtime.py:27-30`), so the run would write to the declared branch with no review.
The request is refused. For the write target `default-branch`, the run writes to the branch that
a run of a person writes to: the declared branch, else `main`.

**The branch name** is `sync/<schedule name>-<YYYYMMDD of T>-<6 hexadecimal characters>`, after
the `mcp/session-YYYYMMDD-<hex>` pattern of Infrahub MCP. The six characters are the start of
`sha256(schedule_id + ":" + T)`, so a repeated attempt derives the same name (R2). The pattern is
fixed; a schedule cannot set its own.

**Alternatives considered**: create the branch only when the plan holds an operation. Rejected
for the manifest reason above. The cost of the decision is one branch that exists for the time of
a plan when nothing changed.

## R12. New tables are created at startup; new columns reach only a new database

**Decision**: Add the tables `schedules` and `schedule_branches` to `_SCHEMA`, and the columns
`trigger_type`, `schedule_id` and `scheduled_start_at` to the `CREATE TABLE` of `product_runs`
(`product_store/store.py:72-155`).

**Rationale**: The product store has no migration mechanism. Commit `2531593f` removed the helper
that added columns to an existing table, with the message "Startup no longer upgrades a product
database that an earlier build created". `CREATE TABLE IF NOT EXISTS` creates the two new tables
on an existing database, and does not add the new columns.

The receipt kinds are also named in a `CHECK` constraint, in SQLite triggers and in model
invariants (`product_store/store.py:231-251`, `models.py:544-550`), and `audit_events` gains the
column `details` (R9). `GET /runs` filters on `schedule_id`, so the run listing indexes gain one
(`store.py:195-200`), next to the unique index of R2.

**Consequence**: a deployment that keeps an earlier product database must replace it, as for
every other column added since that commit. At startup the service refuses an earlier database
with a message that says so, instead of failing at the first insert. The user documentation already states that there is
no in-place upgrade (`docs/docs/operations/supported-platforms-and-limits.mdx:48`). The changelog
fragment states it again.

## R13. The API follows the existing conventions

**Decision**:

- Routes are added by a router factory, as `configuration_router` is
  (`service/config_routes.py:449`), and every mutation is a `POST` with an `Idempotency-Key`.
  The API has no `PATCH` or `DELETE` route today, so a change is `POST /schedules/{id}/change`
  and a deletion is `POST /schedules/{id}/delete`.
- `GET /schedules` pages with a cursor (ADR 0032), newest first by `(created_at, schedule_id)`.
- Receipts gain the resource kind `schedule` (`product_store/models.py:522`).
- Request and resource models go in `infrahub_sync/client/models.py`, which the service re-exports.
- The bootstrap job removes the Prefect schedules of the deployment each time it runs. It applies
  the deployment with `apply_deployments`, which treats `schedules` as a field it owns: the
  definition has none, so a deployment that holds schedules does not match, and the update sends
  an empty list (`opsmill_prefect_extras/deployments.py:325-346, 399-417`). The vendored package
  is frozen and is not changed. The bootstrap job therefore runs the step that makes Prefect
  match the store, after it applies the deployment (`service/bootstrap.py:304-368`).
- The store is written first and Prefect second. When the Prefect call fails, the request
  answers `503` and the stored schedule keeps a flag that it is not yet in Prefect. One step
  makes the Prefect schedules of the deployment match the stored schedules, by slug: it runs
  at the end of the bootstrap job, when the API process starts, and after each schedule request. A Prefect schedule that
  someone changed in the Prefect interface is put back by that step.
- A schedule has a generated identifier and a unique name. The CLI accepts either, as
  `_resolve_config` does for configurations (`cli.py:373`).
- A deleted schedule keeps its row in the state `deleted`, so that a run's `schedule_id` and the
  schedule's events still resolve. Its name becomes free.

The routes are in [contracts/http-api.md](contracts/http-api.md) and the commands in
[contracts/cli.md](contracts/cli.md).

## R14. Time is injected

**Decision**: `admit_scheduled_start` takes the current time and the start time as arguments, and
the flow reads the current time from one function that tests replace. Tests of the admission pass
both times. The project uses no time-freezing library.

**Rationale**: `create_run` and `flow.py` read `datetime.now` directly
(`service/service.py:355`, `service/flow.py:814`). The tests of this feature need a start time
and a lateness they control.

## R15. The branch and the proposed change are made with the Infrahub SDK, in the worker

**Decision**: One new module of the service holds five operations, called by the scheduled
`sync` stage. The module builds its own `InfrahubClientSync` from the same destination settings
as the destination adapter: the address, and the token that `select_runtime_credential(settings,
"token", ("INFRAHUB_API_TOKEN",))` returns (`adapters/infrahub.py:984-1008`), with the run's
branch as its default branch. The flow builds it before the destination adapter, because the
adapter reads the schema of its branch when it is built and that branch must exist
(`adapters/infrahub.py:1023`), and keeps it until the end of steps 5 and 6, because
`_execute_stage` returns no reference to the adapter's client (`flow.py:887, 1155`). The
requests about the proposed change are sent to the run's branch, so the token needs the object
permissions of other branches and not `edit_default_branch` (checked from the source code, see
below):

| Operation | How |
| --- | --- |
| Create a branch | `client.branch.create(branch_name=..., sync_with_git=False, wait_until_completion=True)`. A branch of that name that exists counts as created. |
| Delete a branch | `client.branch.delete(...)`. A branch that no longer exists counts as deleted. |
| Open a proposed change | Create a `CoreProposedChange` node with `name`, `description`, `source_branch` and `destination_branch`. |
| Read a proposed change | Read the node's `state` and its `approved_by` peers. The result is `approved` when the state is `open` and `approved_by` holds at least one account, else the state itself: `open`, `merging`, `merged`, `closed` or `canceled`. |
| Close a proposed change | Read the state first. Only when it is `open`: add a `CoreChangeComment` to the node, then set its `state` to `closed`. A state of `closed`, `canceled` or `merged` counts as done; `merging` counts as merged and the proposed change is kept. Infrahub refuses an update of a proposed change that is not open. |

Other facts from the source code that the operations follow:

- Infrahub accepts a proposed change only to the branch `main`, and refuses a second open
  proposed change for the same source branch. The default branch of the destination must be
  `main` for branch review. The Sync API cannot read the destination, so the worker checks it: a
  run with branch review on a destination whose default branch is not `main` fails before it
  creates its branch, with a failure class that says so.
- Deleting a branch sets its proposed changes to `canceled`. The close comes before the delete,
  so the comment of a replaced proposed change is kept.
- Two runs of one configuration can overlap during steps 5 and 6. `update_schedule_branch` locks
  the row and refuses a row in a final state, so only one of them records a replacement; the
  other skips it. No second write guard is needed.

**Rationale**:

- These are the calls that Infrahub MCP makes for its session branch and its proposed change
  (`infrahub-mcp/src/infrahub_mcp/utils.py:307-338`, `tools/write.py:397-400`), and that this
  repository's tests and tasks already make for a branch (`tasks/smoke_dataset.py:50-60`).
- `CoreProposedChange` holds `source_branch`, `destination_branch`, `state`, `approved_by` and
  `comments` (`infrahub/backend/infrahub/core/schema/definitions/core/propose_change.py`).
- Only the worker holds the Infrahub token (ADR 0031, ADR 0039).
- The product owner decided on 2026-11-17 that this specification builds these two parts of
  INFP-655. Only a scheduled run uses them here.

**Failures** are mapped to failure classes in `service/failure_cause.py`, with no text from
Infrahub in the record (ADR 0021).

**Corrected on 2026-11-18** after the source check of task T059: the first version of this
decision used the destination adapter's client, read four states, and closed a proposed change
with no read of its state.

## Facts not known at the time of writing

- Whether Prefect deletes the flow runs it created ahead when a schedule is changed, paused or
  deleted. The admission of R2 does not depend on it: a flow run of a schedule that is not
  active, or of a cadence that no longer holds its start time, creates no run.
- Which Prefect state a flow returns to be scheduled again later (the `queue` policy, R7).

Checked since, for R11 and R15, from the source code only on 2026-11-18 (task T059). None of the
three answers was checked against a running Infrahub. The Infrahub source read is the checkout at
commit `0dcbe157dd` (`git describe`: `infrahub-v1.12.0b0-31-g0dcbe157dd`; its generated
`backend/infrahub/_version.py` names an earlier build and is out of date). The SDK source is
`infrahub-sdk-python` at commit `b31f5f42`. Infrahub paths below are relative to `backend/infrahub/`.

- **Permissions of the worker's token.** Infrahub checks a permission against the branch that
  the GraphQL request is sent to. A mutation sent to the default branch needs the global
  permission `edit_default_branch` and object permissions that allow the default branch; a
  mutation sent to another branch needs object permissions that allow other branches
  (`graphql/auth/query_permission_checker/default_branch_checker.py:42-51`,
  `object_permission_checker.py:32-37`). The client that the destination adapter builds sends to
  the run's branch by default (`infrahub_sync/adapters/infrahub.py:995-1000`).
    - Create a branch: no permission check in the mutation, and `BranchCreate` is exempt from
      `edit_default_branch` (`graphql/mutations/branch.py:78-132`, `default_branch_checker.py:20-29`).
      A branch name that exists is refused with a validation error (`branch.py:103-107`).
    - Delete a branch: the global permission `delete_branch` only when another account created
      the branch (`branch.py:202-208`). Deleting a branch sets every open and closed proposed
      change of that branch to the state `canceled` (`core/branch/delete_coordinator.py:74-77`,
      `proposed_change/tasks.py:365-395`).
    - Create a `CoreProposedChange`: object permission `create` on it. Infrahub refuses a
      destination other than `main` and a second open proposed change for the same source branch
      (`graphql/mutations/proposed_change.py:89-129`).
    - Update its `state` to `closed`: object permission `update` on it. Only a change to `merged`
      checks a global permission, `merge_proposed_change` (`proposed_change.py:180-189`).
    - Create a `CoreChangeComment`: object permission `create` on it, and `update` on the
      `CoreProposedChange` it names, because a mutation needs `update` on every related kind that
      is not its root (`graphql/analyzer.py:694-701`).
    - The default group "Infrahub Users" holds all of these for other branches, and `update` on
      `CoreProposedChange` with `edit_default_branch` for the default branch
      (`core/initialization.py:427-492`).
- **Closing a proposed change** is an update of its `state` with the generated update mutation,
  as R15 states; there is no close mutation (`proposed_change.py:147-227`). An update of a
  proposed change that is no longer open is refused, so closing one that is already closed fails
  in Infrahub (`proposed_change/constants.py:32-36`, `proposed_change.py:168-169`). Rule 4 of
  [contracts/branch-review.md](contracts/branch-review.md) therefore needs a read of the state
  first. The states are `open`, `merged`, `merging`, `closed` and `canceled`
  (`proposed_change/constants.py:14-19`); the contract's read result names only four of them.
- **Work after the stage result is committed.** The `sync` stage leaves the write guard at the
  end of its `with` block, before the commit (`infrahub_sync/service/flow.py:1075-1145`,
  `apply_guard.py:310-315`). After `_commit_success` the flow run still writes a log line and
  calls the store (`flow.py:1710-1714`). The liveness reconciler does nothing with a terminal
  execution: it reads only executions with no `terminal_at`, and rule 0 ends at a terminal one
  (`liveness.py:99, 151-156, 195-196`). The store refuses only the writes tied to a live
  execution (`product_store/store.py:1612, 1625, 1647`); `update_schedule_branch`,
  `record_audit` and `record_results` have no such condition (`store.py:3614-3649, 3041-3043,
  3837-3842`). `record_results` replaces the whole `results` document.
    - The flow run holds no reference to the destination adapter's Infrahub client after the
      commit: `_execute_stage` returns only the result and the writeback (`flow.py:887, 1155`).
      Steps 5 and 6 need a client that the flow keeps, or one built for them.
    - The branch cannot be created with that client either: the adapter reads the schema of its
      branch when it is built (`adapters/infrahub.py:1023`).
    - No second write guard is needed. Steps 5 and 6 touch only the run's own branch and the
      branches and proposed changes that earlier `schedule_branches` rows name, never the
      destination data. Runs of the configuration can overlap at that point, because R7 counts
      only executions with no `terminal_at`; `update_schedule_branch` locks the row and refuses a
      row in a final state (`store.py:3626-3640`), so two runs that replace the same row cannot
      both record it.

Checked since, for R15, from the source code only on 2026-11-18 while writing
`infrahub_sync/service/branch_review.py` (task T062), in the Infrahub checkout above and at the
tag `infrahub-v1.11.11`, and in the installed `infrahub-sdk` 1.23.2. None of it was checked
against a running Infrahub.

- Infrahub answers 404 to a GraphQL request sent to a branch that does not exist, before it reads
  the request (`graphql/app.py:133-138`; line 128 at `infrahub-v1.11.11`). The SDK raises
  `URLNotFoundError` for it (`infrahub_sdk/client.py:2484-2485`).
- The SDK sends every request to the client's default branch unless the call names another
  one (`client.py:2460`), and `client.branch.create`, `delete`, `get` and `all` name none
  (`infrahub_sdk/branch.py:182-252`). On a client whose default branch is the run's branch,
  they fail while that branch does not exist. The module therefore sends its own branch list,
  `BranchCreate` and `BranchDelete` documents to `main` with `execute_graphql(...,
  branch_name="main")`. `BranchCreate` and `BranchDelete` are exempt from
  `edit_default_branch` (`default_branch_checker.py:20-29`). Both accept
  `wait_until_completion` at `infrahub-v1.11.11` too.
- The branch check of rule 6 reads that 404 to the branch list sent to `main` as "the default
  branch is not `main`". An address that reaches a server on a wrong path also answers 404 and
  is recorded the same way.
- A read of the schedule's earlier proposed changes before the run's branch exists (T063)
  must name `branch="main"`; `CoreProposedChange` is branch-agnostic
  (`core/schema/definitions/core/propose_change.py:26`).

Checked since, for R3, in the installed Prefect 3.8.6 on 2026-11-17 while writing
`infrahub_sync/service/schedule_cadence.py`:

- Prefect accepts an empty time zone name and an alias such as `@daily`. The module refuses both:
  the empty name itself, and the alias because it does not have five fields.
- Prefect accepts an expression whose date never comes, such as `0 2 30 2 *`, and `get_dates`
  then raises a `ValueError` of the cron library. The module reports it as an invalid expression.
- An unknown time zone raises a `ValidationError` with no field location; an invalid expression
  raises one with the location `cron`. The module's error names the field from that difference.
- `get_dates` returns a start that is equal to its `start` argument, and awaits nothing.
- The start of the night that has no 02:30 depends on the Python version, because Prefect computes
  it with different libraries. For `30 2 * * *` in `Europe/Paris` on 29 March 2026, Python 3.11.8
  gives 01:30 local time (00:30 UTC), as R3 records, and Python 3.13 gives 03:30 local time
  (01:30 UTC). Both give one start. The night that has 02:30 twice gives the first one on both.
  Python 3.12 was not checked.
