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

## R2. A start is started once: a deterministic idempotency key, then a compare-and-set advance

**Decision**: For the start at time `T` of schedule `S`, the loop:

1. Starts the run with the idempotency key `sha256("schedule:" + S + ":" + T)`. `RunService`
   derives the Prefect idempotency key from the receipt, as it does today
   (`service/service.py:1722-1752`).
2. Then advances the schedule: one store call sets `next_start_at` to the following start only if
   the stored value still equals `T`, and saves the events of the start in the same transaction.

**Rationale**:

- If the process stops between step 1 and step 2, the next pass repeats step 1 with the same key.
  The receipt replays the accepted answer, and no second run is created. No start is lost
  (SC-002).
- If two API processes run the loop, both may do step 1; the receipt makes it one run. Only one
  of them wins step 2, so a skipped or missed start is recorded once.
- It needs no lock held across a network call to Prefect.

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
- The run row holds `trigger_type = "scheduled"` and `schedule_id`.
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
- The loop checks the same condition before each start, because the setting is read when the
  process starts (`service/settings.py:47-53`) and a restart can turn it on. A schedule that no
  longer passes is moved to the state `stopped` with the reason `approval-required`, and records
  an event.
- A scheduled run with branch review is accepted whatever the setting is. The service generates
  the branch name, so the caller cannot name the default branch or an existing branch.

**Rationale**: FR-002. The check lives where `approval_required()` is already read: the service,
not the worker (`service/service.py:389`).

**Decision record**: the rule amends ADR 0037 and needs its own ADR, written when the
specification is extracted.

## R7. Overlap: one store question, asked before each start

**Decision**: A new store method answers "which run of configuration X is in progress". A run is
in progress when it has a `prefect_executions` row with `terminal_at IS NULL`, or a receipt that
is not resolved. The loop asks before each start:

- `reject`: when a run is in progress, the start is skipped and the event names that run.
- `queue` (user story 3): the schedule stores the waiting start. A later pass starts it when no
  run is in progress. A further start while one waits is skipped.

**Rationale**:

- Phase alone does not tell: an `apply` leaves the run in phase `planned` while it runs. The
  execution rows and the receipts are the reliable signals, and `pending_executions()` already
  uses the first (`product_store/store.py:1929-1939`).
- The question and the start are not one transaction. A person can start a run between the two.
  SC-006 does not depend on this check: the write guard of ADR 0010 still serializes writers
  (`service/apply_guard.py:42-52`), and the scheduled run that loses waits 30 seconds and fails.
  The check is what makes the common case a recorded skip and not a failed run.

**Alternatives considered**: take the write guard in the API process. Rejected: the guard is a
session lock that the worker must hold for the whole write.

## R8. The owner is an actor name; the resolver says whether that actor still has a token

**Decision**: A schedule stores its owner's actor name and no token. `PrincipalResolver` gains
one method that returns the `Principal` of a named actor, or `None`. The loop calls it before each
start. `None` moves the schedule to `stopped` with the reason `owner-token-invalid`. A take-over
by an administrator sets the owner to that administrator and the state to `active`.

**Rationale**:

- Tokens and actors come from `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, read once when the process
  starts (`service/auth.py:12, 46-75`). The resolver holds the actor names in a private
  attribute, and `RunService` receives only its secret values (`service/serve.py:90-96`). One
  read method is the smallest change.
- Storing a token would put a credential in the product store (constitution principle VI).
- `_owned_run` then works with no change: the owner or an administrator can cancel a scheduled
  run (`service/service.py:1773-1786`).

## R9. Schedule events use a new resource segment, and the schedule is a related resource

**Decision**:

- `EVENT_TYPE_PATTERN` becomes `^infrahub_sync\.(run|config|schedule)\.[a-z_]+$`
  (`product_store/models.py:710`).
- `RelatedResource.kind` and `PrimaryResource.kind` gain `schedule`.
- Every schedule event names the schedule as a related resource, so `GET /events` filters on it
  with the existing `related` filter. The events table gets no new column.
- An event that concerns a run sets `run_id`: the run that a start created, or the run in
  progress that caused a skipped start. That event then also appears in the run's own list.
- An event that the loop writes has the schedule's owner as its actor, because `actor` is
  required (`models.py:734-780`). The start time is in `reason`, in the fixed form
  `start=<ISO 8601 time>`.

The event types are listed in [contracts/events.md](contracts/events.md).

**Rationale**: ADR 0033 saves an event in the transaction of its decision. The receipt methods
already take an event (`complete_mutation`), and the new advance method of R2 takes the events of
a start.

**Bound on missed starts**: one event for each missed start, up to 500 for one schedule in one
pass. Older ones are recorded in one event whose `reason` holds the count and the first and last
start times. A schedule that starts every minute and a service that was not running for a week
would otherwise write 10,080 events in one transaction.

## R10. Missed starts in user story 1 and in user story 3

**Decision**:

- A start is on time when the loop processes it within 60 seconds of its time. The loop's cadence
  is 5 seconds, so this only fails when the process was not running or was blocked.
- User story 1: a start that is not on time is a missed start. It is recorded and no run starts.
- User story 3: the schedule's `start_late_by_at_most_seconds` (default 3600) replaces the 60
  seconds for the most recent missed start. Earlier missed starts are recorded and never run.

**Rationale**: the specification delivers the late run with user story 3, and FR-017 asks for one
run however many starts were missed.

## R11. Branch review runs in the worker, and creates the branch after the plan

**Decision**: The service generates the branch name when it starts the run and stores it as the
run's destination branch. In the worker, the `sync` stage of a run with branch review does this
under the write guard:

1. Plan. The branch does not exist yet, so the plan reads the default branch.
2. If the plan has no operation, end with the outcome `no-change`. No branch is created
   (FR-022).
3. Create the branch through the prerequisite.
4. Verify and apply against the new branch.
5. Open the proposed change through the prerequisite.
6. Read the schedule's previous branch and proposed change. If nobody approved it, close it with
   a comment that links the new one, and delete its branch (FR-020, FR-021).

The stage result carries what happened, and `commit_claimed_execution` saves it with its events
in one transaction (`product_store/store.py:3464`).

**Rationale**:

- Only the worker holds Infrahub credentials (ADR 0031, ADR 0039).
- A branch created from the default branch holds the same data at that moment, so a plan computed
  against the default branch is valid for it. The verify step still runs against the new branch.
- A failed run replaces nothing, because step 6 comes after the write and the new proposed
  change.

**Risk**: step 1 asks the prerequisite to let a run plan against the default branch and write to
another. If the prerequisite cannot, the fallback is to create the branch first and delete it
when the plan has no operation. FR-022 is then met only at the end of the run. The contract is in
[contracts/branch-review.md](contracts/branch-review.md).

**The branch name** is `sync/<schedule name>-<YYYYMMDD>-<6 hexadecimal characters>`, after the
`mcp/session-YYYYMMDD-<hex>` pattern of Infrahub MCP. It is fixed; a schedule cannot set its own
pattern.

## R12. New tables are created at startup; new columns reach only a new database

**Decision**: Add the tables `schedules` and `schedule_branches` to `_SCHEMA`, and the columns
`trigger_type` and `schedule_id` to the `CREATE TABLE` of `product_runs`
(`product_store/store.py:72-155`).

**Rationale**: The product store has no migration mechanism. Commit `2531593f` removed the helper
that added columns to an existing table, with the message "Startup no longer upgrades a product
database that an earlier build created". `CREATE TABLE IF NOT EXISTS` creates the two new tables
on an existing database, and does not add the two columns.

**Consequence**: a deployment that keeps an earlier product database must replace it, as for
every other column added since that commit. The user documentation already states that there is
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
The new `RunService` method takes the start time as an argument and uses it for `started_at`.
Tests pass a clock that they advance.

**Rationale**: `create_run` reads `datetime.now` directly (`service/service.py:355`). The tests
of this feature need a start time they control. The project uses no time-freezing library.

## Facts not known at the time of writing

- The interface of the two prerequisite capabilities of INFP-655. The contract in
  [contracts/branch-review.md](contracts/branch-review.md) is what this plan needs; the
  prerequisite may deliver it in another form.
- How Infrahub reports that a person approved a proposed change, and whether the token of the
  worker may close a proposed change and delete a branch. The prerequisite answers both.
- Whether constitution principle I will be amended for user story 2.
