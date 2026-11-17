# Research: Scheduled runs that write without a person present

Each entry states a decision, why, and what was rejected. File and line references are to
`feature/v3-develop` at `7987596f`.

## R1. The Sync API process starts scheduled runs; Prefect schedules are not used

**Decision**: A schedule loop runs in the Sync API process, next to the liveness loop that
`create_app` already starts in its lifespan (`infrahub_sync/service/app.py:334-356`). At each
pass it reads the schedules whose next start time has passed and starts a run for each through
`RunService`, by the same steps as `POST /runs`.

**Rationale**:

- A flow run that Prefect creates from a deployment schedule is refused today. The flow claims
  its execution with `claim_execution`, which updates a `prefect_executions` row that only
  `RunService._submit` creates (`service/flow.py:518-533`, `product_store/store.py:1302-1327`).
  The refusal text names this case: "a run started by hand from the deployment" is refused.
- A run needs its `product_runs` row, its receipt and its write admission before the flow run
  exists (`service/service.py:404-422`). Only the Sync API writes them.
- ADR 0019 makes Prefect an internal component, and ADR 0024 makes the stored run record the
  source of what Prefect shows. A schedule that Prefect owns would put the decision to start
  outside the run record.
- The loop needs no new container. The API process already runs one periodic task.

**Alternatives considered**:

- A `cron` on the `infrahub-sync-service/run` deployment. Rejected for the reasons above, and
  because one deployment cannot hold one schedule for each configuration version with different
  parameters without Sync writing Prefect deployment schedules through the Prefect API.
- A separate scheduler container. Rejected: one more service to deploy and monitor, with no gain
  while the API process is a single process (`service/serve.py:132-138`).

## R2. A start is started once: one run for each schedule and start time, then a compare-and-set advance

**Decision**: For the start at time `T` of schedule `S`, the loop:

1. Starts the run. The run row holds `schedule_id = S` and `scheduled_start_at = T`, and a unique
   index on that pair allows one run. The receipt's idempotency key is
   `sha256("schedule:" + S + ":" + T)`, and its request fingerprint is computed from `S` and `T`
   only. Everything else about the run is derived from the stored schedule and from `T`,
   including the branch name (R11), so a repeated attempt builds the same request.
2. Then advances the schedule: one store call sets `next_start_at` to the following start only if
   the stored value still equals `T`, and saves the events of the start in the same transaction.

Before step 1, the loop reads whether a run with `(S, T)` exists:

- It exists and was submitted: go to step 2.
- It exists and was not submitted, because the process stopped or Prefect could not be reached:
  submit it again. `reserve_mutation` returns the stored receipt on the unique `(actor,
  key_digest)` and `_submit` reuses its stored Prefect key (`product_store/store.py:2103-2109`,
  `service/service.py:1565`). The loop retries on each pass, with a wait that doubles from 5
  seconds to 5 minutes, whatever the age of the start. Such a start is never recorded as missed.
- It does not exist: create it.

`started_at` of the run is the time the run was created, not `T`, so the run list keeps its
order (`product_store/store.py:195`).

**Rationale**:

- A stop of the process between step 1 and step 2 loses nothing: the next pass finds the run and
  advances (SC-002).
- A change of the schedule or a take-over between two attempts does not create a second run or an
  `idempotency-conflict`, because the fingerprint holds only `S` and `T` and the unique index
  holds whatever the actor is. Today `_require_matching_receipt` compares the whole request body
  (`service/service.py:1734-1771`).
- Two API processes that run the loop create one run and record a skipped or missed start once.
  The deployment runs one process (`service/serve.py:132-138`), so this is a test case.

**Alternatives considered**: advance first, then start. Rejected: a stop between the two loses
the start with no record.

## R3. The cron expression is read with `cronsim`, and time zones with `zoneinfo` and `tzdata`

**Decision**: Declare `cronsim` and `tzdata` in the `service` extra of `pyproject.toml`. Accept a
standard five-field cron expression. Compute start times with `cronsim` on a datetime that
carries the schedule's `zoneinfo.ZoneInfo`, and store them in UTC.

**Rationale**:

- Both packages are already in `uv.lock`: `cronsim` 2.7 through `pydocket`, which `prefect`
  3.8.6 requires, and `tzdata` 2026.5. Declaring them makes the dependency explicit; it installs
  nothing new. `croniter` and `apscheduler` are not in the lock.
- `cronsim` handles a start that falls in the hour a daylight-saving change removes or repeats,
  which the specification lists as an edge case.
- `zoneinfo` is in the standard library of every Python version the service supports (3.11 to
  3.13). Declaring `tzdata` removes the dependency on a time zone database in the image.

**Alternatives considered**: `croniter` (a new package, and its maintenance status has changed
more than once); Prefect's own cron schedule classes (they belong to deployments, see R1).

**Not verified**: whether `python:3.13-slim-bookworm` ships a system time zone database. With
`tzdata` declared, the answer does not matter.

## R4. A scheduled run is a `sync` run with three extra facts

**Decision**: `RunService` gains one method that the loop calls. It follows `create_run`
(`service/service.py:343-444`) with these differences:

- The principal is the schedule's owner, built from the actor name (R8).
- `operation` is `sync` and `confirm_writes` is true.
- The run row holds `trigger_type = "scheduled"`, `schedule_id` and `scheduled_start_at` (R2).
- The flow receives `confirm_left_out` when the schedule's left-out setting is on.
- The refusal `approval-required-for-sync` does not apply when the write target is branch review
  (R6).

`POST /runs` is not changed. Its runs record `trigger_type = "manual"`.

**Rationale**: The specification asks for the same lifecycle, history and events as a run that a
person starts. Reusing the receipt, the run row, the write admission and `_submit` gives that.

**Trigger types**: this specification writes `manual` and `scheduled`. INFP-656 also names
`system`, for a run that another system starts. Nothing distinguishes such a run from a manual
one today, so `system` is left to INFP-658.

## R5. The left-out setting reaches the worker as the existing `confirm_left_out` parameter

**Decision**: The `sync` branch of `_execute_stage` (`service/flow.py:930-998`) skips
`_left_out_stop` and passes `confirm_left_out=True` to its apply step when the flow parameter is
true. `CreateRunRequest` gets no new field.

**Rationale**: The flow parameter exists (`service/flow.py:1421`) and reaches the engine for
`apply` and `resume` (`flow.py:895`). The `sync` branch ignores it today. A person who wants this
for a manual run already has `apply --confirm-left-out`; adding it to `POST /runs` is not part of
this specification.

## R6. The approval setting and the two write targets

**Decision**:

- `POST /schedules` and a change of the write target are refused with `409
  approval-required-for-schedule` when `approval_required()` is true and the write target is the
  default branch.
- The loop checks the same condition before each start, because the setting comes from the
  environment of the process (`service/settings.py:47-53`) and a restart can turn it on. A schedule that no
  longer passes is moved to the state `stopped` with the reason `approval-required`, and records
  an event.
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

## R7. Overlap: one store question, asked before each start

**Decision**: A new store method answers "which run of configuration X is in progress". A run is
in progress when either holds:

- It has a `prefect_executions` row with `terminal_at IS NULL`.
- It has a receipt that is not resolved, for an operation that starts a stage (the set the store
  already arbitrates, `product_store/store.py:299-305`), and the receipt is younger than the
  admission time limit `INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS` (default 300).

The loop asks before each start:

- `reject`: when a run is in progress, the start is skipped and the event names that run.
- `queue` (user story 3): the schedule stores the waiting start. A later pass starts it when no
  run is in progress. A further start while one waits is skipped.

**Rationale**:

- Phase alone does not tell: an `apply` leaves the run in phase `planned` while it runs. A run in
  phase `planned` that waits for a person, an interrupted run and an abandoned execution all have
  `terminal_at` set, so they do not block a schedule.
- The age limit is needed because nothing resolves a receipt that has no execution: the liveness
  loop reads only `prefect_executions` (`service/liveness.py:151-156`). Without the limit, a
  request of a person that stopped half-way would block every later start.
- "Any run" includes a `plan` or a `verify` of a person. FR-014 asks for that.
- The question and the start are not one transaction. A person can start a run between the two.
  SC-006 does not depend on this check: the write guard of ADR 0010 still serializes writers
  (`service/apply_guard.py:42-52`), and the scheduled run that loses waits 30 seconds and fails.
  The check is what makes the common case a recorded skip and not a failed run.
- A `sync` of a person is not refused today while an earlier run of the configuration needs
  reconciliation. A scheduled run is not refused either.

**Alternatives considered**: take the write guard in the API process. Rejected: the guard is a
session lock that the worker must hold for the whole write.

## R8. The owner is an actor name; the resolver says whether that actor still has a token

**Decision**: A schedule stores its owner's actor name and no token. `PrincipalResolver` gains
one method that returns the `Principal` of a named actor, or `None`. The loop calls it before each
start. `None` moves the schedule to `stopped` with the reason `owner-not-found`. A resume
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
- `commit_claimed_execution`, `complete_mutation` and the new advance method of R2 take a
  sequence of events. Today the first two take one (`product_store/store.py:2548-2556,
  3464-3475`), and a scheduled run can write three in one transaction.
- An event that concerns a run sets `run_id`: the run that a start created, or the run in
  progress that caused a skipped start.
- An event that the loop writes has the schedule's owner as its actor, because `actor` is
  required (`models.py:734-780`).

The event types are listed in [contracts/events.md](contracts/events.md).

**Rationale**: ADR 0033 saves an event in the transaction of its decision. The start time and the
left-out count are facts of the event, not a person's reason, and the reason of a stage result is
read from the receipt before the plan exists (`service/flow.py:1562-1577`).

**Bound on missed starts**: one event for each missed start, up to 500 for one schedule in one
pass. Older ones are recorded in one event whose `details` hold their count and their first and
last start times. A schedule that starts every minute and a service that was not running for a
week would otherwise write 10,080 events in one transaction.

## R10. Missed starts in user story 1 and in user story 3

**Decision**:

- A start is on time when the loop processes it within 60 seconds of its time. The loop's cadence
  is 5 seconds, so this only fails when the process was not running or was blocked.
- User story 1: a start that is not on time is a missed start. It is recorded and no run starts.
  A restart of the Sync API that lasts more than 60 seconds across a start time therefore loses
  that start until user story 3 is delivered. The specification accepts this.
- User story 3: the schedule's `start_late_by_at_most_seconds` (default 3600) replaces the 60
  seconds for the most recent missed start. Earlier missed starts are recorded and never run.

**Rationale**: the specification delivers the late run with user story 3, and FR-017 asks for one
run however many starts were missed.

## R11. Branch review runs in the worker: the branch first, the proposed change after the write

**Decision**: The service derives the branch name when it creates the run, stores it as the run's
destination branch, and writes a `schedule_branches` row in the state `pending` in the same
transaction. In the worker, the `sync` stage of a run with branch review does this under the
write guard:

1. Create the branch through the prerequisite.
2. Plan, against the new branch.
3. If the plan has no operation, delete the branch and end with the outcome `no-change`.
4. If the plan leaves out records and the left-out setting is off, delete the branch and end with
   the outcome `left-out-review-required`.
5. Verify and apply against the new branch.

After the write is committed as the stage result, and outside the scope whose failures make a
write ambiguous (`service/flow.py:1502-1508`):

6. Open the proposed change through the prerequisite.
7. Read the schedule's earlier branches in the states `open` and `pending`. For each one that
   nobody approved, close its proposed change, if it has one, with a comment that names the new
   one, and delete its branch (FR-020, FR-021).

A failure of step 6 or 7 does not change the verdict of the write. It is recorded in the run's
result and as an event, and the next successful run of the schedule does step 7 again.

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
- A failed run replaces nothing, because step 7 comes after the write and the new proposed
  change.

**A run with branch review is never applied or resumed by a person.** `verify`, `apply` and
`resume` requests on such a run are refused. Its branch is deleted when it stops after the plan,
and a run interrupted during its write leaves a `pending` branch that the next successful run
deletes. Nothing of it reached the default branch.

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
- A schedule has a generated identifier and a unique name. The CLI accepts either, as
  `_resolve_config` does for configurations (`cli.py:373`).
- A deleted schedule keeps its row in the state `deleted`, so that a run's `schedule_id` and the
  schedule's events still resolve. Its name becomes free.

The routes are in [contracts/http-api.md](contracts/http-api.md) and the commands in
[contracts/cli.md](contracts/cli.md).

## R14. Time is injected

**Decision**: The loop takes a `clock`, as `RunLivenessReconciler` does (`service/liveness.py:138`).
The new `RunService` method takes the start time as an argument, stores it as
`scheduled_start_at`, and reads `started_at` from the injected clock (R2). Tests pass a clock that
they advance.

**Rationale**: `create_run` reads `datetime.now` directly (`service/service.py:355`). The tests
of this feature need a start time they control. The project uses no time-freezing library.

## Facts not known at the time of writing

- Whether a schedule should start a run while one of its proposed changes is approved and not
  merged. This plan starts it, as the specification says, and the two proposed changes then hold
  the same changes.
- Whether the worker can open the proposed change and replace earlier ones after the stage result
  is committed (R11, steps 6 and 7). At that point the execution is terminal and the write guard
  is released. This was not checked against `service/flow.py`; phase 4 of the tasks checks it
  first.
- Whether Prefect refuses a flow run that carries a parameter the flow does not declare. The
  tasks add the new flow parameters to the flow's signature in phase 3 so that the answer does
  not matter.
- The interface of the two prerequisite capabilities of INFP-655. The contract in
  [contracts/branch-review.md](contracts/branch-review.md) is what this plan needs; the
  prerequisite may deliver it in another form.
- How Infrahub reports that a person approved a proposed change, and whether the token of the
  worker may close a proposed change and delete a branch. The prerequisite answers both.
- Whether constitution principle I will be amended for user story 2.
