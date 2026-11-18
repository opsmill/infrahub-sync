# Tasks: Scheduled runs that write without a person present

**Input**: Design documents from `dev/specs/021-scheduled-runs/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md) (R1 to R15),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: included. Constitution principle V asks for a test with each change, and each test
task comes before the implementation task it covers.

**Format**: `- [ ] [ID] [P?] [Story] Description`

- **[P]**: the task touches different files from the other open tasks of its phase and has no
  dependency on them.
- **[Story]**: the user story of the task (US1, US2, US3).

## Order of delivery

The product owner decided the order on 2026-11-17. No task is blocked or gated.

| Phase | Delivered to a team |
| --- | --- |
| 1, 2, 3 | No. Both write targets are refused with `schedule-setting-not-available` until they are delivered. |
| 4 | **Yes, first delivery: scheduled runs with review in Infrahub.** Merge phases 1 to 4 together. |
| 5 | Yes: a write to the default branch, and the left-out setting. |
| 6 | Yes: the `queue` policy, the late run, the newer-version warning, the take-over. |
| 7 | Follows each delivery. |

---

## Phase 1: Setup (bootstrap test and release note)

- [X] T001 Write `tests/service/test_schedule_bootstrap.py`: after the bootstrap job applies the deployment again, which removes its Prefect schedules, the Prefect schedule of every stored active schedule exists again (R13)
- [X] T002 [P] Create the news fragment `changelog/+scheduled-runs.added.md`: schedules exist, and a product database of an earlier build must be replaced (R12)

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the records, the models and the cadence that every user story uses.

### Tests

- [X] T003 [P] Write `tests/service/test_schedule_cadence.py`: a valid cadence gives its next start in UTC; a start in the hour that a daylight-saving change removes and in the hour it repeats gives at most one start; an invalid expression, an expression that does not have five fields and an unknown time zone raise the module's own error; the list of starts between two times is bounded (R3)
- [X] T004 [P] Write `tests/product_store/test_schedules.py`, parameterized as `("sqlite", pytest.param("postgresql", marks=pytest.mark.integration))`: create, read, list with cursor and filters, change, state transitions of data-model.md, unique name among schedules that are not deleted, `admit_scheduled_start` (the run, receipt, write admission, claimed execution and events in one transaction; two calls for one start, one creates the run), `schedule_branches` states, the unique index on `(schedule_id, scheduled_start_at)` of `product_runs`
- [X] T005 [P] Extend `tests/product_store/test_contract.py`, `tests/product_store/test_run_approval.py` and `tests/product_store/test_postgresql_isolation.py`: new public names, the receipt kind `schedule`, the new columns of `product_runs` and `audit_events` (critique E9)
- [X] T006 [P] Extend `tests/client/test_models.py` and `tests/client/test_public_surface.py`: schedule request, resource and page models; `trigger_type`, `schedule_id` and `scheduled_start_at` on the run resource; `details` on the event resource; the client package imports with `prefect` absent from `sys.modules`

### Implementation

- [X] T007 Create `infrahub_sync/service/schedule_cadence.py`: `validate(cron, timezone)`, `next_start(cron, timezone, after)` and `starts_between(cron, timezone, after, until, limit)` in UTC, built on Prefect's `CronSchedule`, with one typed error for an invalid expression or time zone. No module outside `infrahub_sync/service/` imports it (R3)
- [X] T008 Add to `infrahub_sync/product_store/models.py`: `ScheduleRecord`, `ScheduleBranch`, `ScheduleFilters`; `trigger_type`, `schedule_id` and `scheduled_start_at` on `ProductRun` and `RunFilters`; the segment `schedule` in `EVENT_TYPE_PATTERN`; the kind `schedule` in `RelatedResource`, `PrimaryResource`, `_RELATED_KIND_ORDER` and `primary_resource`; the closed `details` model on `AuditEvent`; the kind `schedule` in `MutationReceipt.resource_kind` and its invariants (data-model.md, R9)
- [X] T009 Add to `infrahub_sync/product_store/store.py`: tables `schedules` and `schedule_branches`; columns `trigger_type`, `schedule_id`, `scheduled_start_at` on `product_runs` and `details` on `audit_events`; the unique index and the `schedule_id` index; the receipt `CHECK` constraint and SQLite triggers with the kind `schedule`; a startup refusal, with a message, of a database whose tables lack the new columns (R12)
- [X] T010 Add the schedule methods to the store protocol and both stores in `infrahub_sync/product_store/store.py`: create, get, list, update, the `schedule_branches` reads and writes, and `admit_scheduled_start(schedule_id, scheduled_start_at, flow_run_id, worker_id, now, decision)` which writes the run, its accepted receipt, its write admission, its claimed execution and the events of the start in one transaction, or only the events when the start does not run; change `complete_mutation` and `commit_claimed_execution` to take a sequence of events (R2, R9); update `consecutive_failures` and stop the schedule at 5 in the store call that saves the end of a scheduled run, with its event (FR-032)
- [X] T011 Add `run_in_progress(config_id, now, receipt_age_limit)` to the store in `infrahub_sync/product_store/store.py`, with its tests in `tests/product_store/test_schedules.py`: an execution with `terminal_at IS NULL`, or an unresolved receipt of a stage operation younger than the limit; a run in phase `planned`, an interrupted run and an abandoned execution do not count (R7)
- [X] T012 [P] Add to `infrahub_sync/client/models.py`: `CreateScheduleRequest`, `ChangeScheduleRequest`, `ScheduleActionRequest`, `ScheduleResource`, `SchedulePage`; the three run fields; the event `details`. Cron and time zone are text with no validation here. Re-export them and add the emitted variants in `infrahub_sync/service/models.py` (R13)
- [X] T013 [P] Add `principal_of(actor)` to `PrincipalResolver` and `EnvironmentPrincipalResolver` in `infrahub_sync/service/auth.py`, with tests in a new `tests/service/test_principal_resolver.py` (R8)
- [X] T014 [P] Add to `infrahub_sync/service/events.py`: the schedule event types of contracts/events.md, `schedule_decision_type`, a `schedule_event` builder, and the schedule as a related resource in `run_event` when the run holds a `schedule_id`; tests in `tests/service/test_event_transactions.py` (R9)

**Checkpoint**: Foundation ready. `uv run invoke tests.tests-unit` passes.

---

## Phase 3: User Story 1 - Scheduled sync with review in Infrahub, service side (Priority: P1)

**Goal**: an operator creates and manages a schedule, Prefect starts one flow run for each start,
and the worker turns it into one run, or skips it when a run is in progress, and records it all.

**Independent Test**: `uv run pytest tests/service/test_schedule_routes.py tests/service/test_schedule_admission.py tests/service/test_schedule_prefect.py tests/service/test_schedule_owner.py tests/service/test_schedule_approval.py tests/cli/test_schedule_commands.py`

### Tests for User Story 1

- [X] T015 [P] [US1] Write `tests/service/test_schedule_routes.py` for contracts/http-api.md: a write target outside the set of delivered write targets answers `422 schedule-setting-not-available`, and so does a request with no write target while branch review is not in the set; create with defaults and `next_start_at`; every refusal code of the create and change tables; list, view, change, pause, resume, delete; owner and administrator rules (acceptance scenarios 1, 10 to 13, 16); idempotency replay; one event for each accepted, refused and replayed request; a canary credential in a request never appears in a record or an event (FR-025); a second schedule for a configuration answers `409 schedule-exists-for-configuration` with the name of the first (FR-006); a delete and a change to the default branch answer with `open_branch` (FR-034); `schedules show` fields of FR-033
- [X] T016 [P] [US1] Write `tests/service/test_schedule_admission.py`, with a flow run that the test describes and times that it passes: a scheduled flow run creates one run with `trigger_type`, `schedule_id`, `scheduled_start_at`, the owner as actor and the version (scenario 2); a second flow run of the same start, and a retried flow run, create no second run; a flow run with `schedule_id` that is not `auto_scheduled` is refused; a schedule that is paused, stopped or deleted creates no run; `reject` skips and names the run in progress (scenario 7); a start more than 5 minutes late is recorded as missed with no run; starts of the cadence with no flow run since the last admitted start are recorded as missed, with 500 events and one summary beyond that; a configuration version that cannot be run records `start_failed`; 30 starts give 30 runs or recorded starts (SC-002); the fifth consecutive failed run stops the schedule with `repeated-failures` and an event, a successful or no-change run sets the count to zero, a cancelled run and a skipped start leave it, and a resume sets it to zero (scenario 21, FR-032)
- [X] T017 [P] [US1] Write `tests/service/test_schedule_owner.py` and `tests/service/test_schedule_prefect.py`: when the API process starts and the owner's actor is gone, the schedule stops with `owner-not-found`, its Prefect schedule is deactivated and an event is written (scenario 14); a resume is refused while the reason holds and succeeds when the actor exists again (scenario 15); create, change, pause, resume and delete call the fake orchestration with the right Prefect schedule (cron, time zone, slug, parameters, active); a failed Prefect call answers `503`, leaves `prefect_synced` false, and the next match repairs it; a Prefect schedule that differs from the store is put back
- [X] T018 [P] [US1] Write `tests/service/test_schedule_approval.py`, with both write targets in the set of delivered write targets: with required approval, a schedule that writes to the default branch is refused at creation and at a change (scenario 8); a run with branch review is accepted with no approval (scenario 9); a restart with approval turned on stops a default-branch schedule with `approval-required`
- [X] T019 [P] [US1] Write `tests/service/test_schedule_branch_review.py`, service part: branch review is refused for a version that declares a destination branch (scenario 16); the run's destination branch is the derived name, the same for two attempts; a `schedule_branches` row in the state `pending` exists with the run; `verify`, `apply` and `resume` on the run answer `409 scheduled-run-not-appliable` (scenario 17)
- [X] T020 [P] [US1] Extend `tests/service/test_http_api.py` (route set, 401 and 422 on every schedule route, run fields and filters) and `tests/service/test_events_route.py` (`related=schedule:<id>` returns the schedule's events and its runs' events, with `details`; scenario 18)
- [X] T021 [P] [US1] Write `tests/client/test_schedules.py` and `tests/cli/test_schedule_commands.py` for contracts/cli.md, and add the eight commands to the matrix in `tests/cli/test_parity_and_closure.py`

### Implementation for User Story 1

- [X] T022 [US1] Create `infrahub_sync/service/schedules.py`: a `ScheduleService` with create, change, pause, resume and delete; cadence validation through `schedule_cadence.py`; the owner or administrator rule; the refusals `schedule-name-in-use`, `approval-required-for-schedule`, `schedule-branch-declared`, `schedule-setting-not-available` (for `queue`, the late-start duration, and a write target that is not in the set of delivered write targets that the constructor receives; `serve.py` passes an empty set, the unit tests pass both; the approval refusal is checked first; a request with no write target is refused while branch review is not delivered), `schedule-stopped`, `schedule-deleted`; receipts of the kind `schedule`; one event for each decision in the transaction of its answer; after the store commit, the call that makes the Prefect schedule match, with `503 orchestration-unavailable` and `prefect_synced` false when it fails (R13); the refusal `schedule-exists-for-configuration`; a resume of a schedule stopped for `repeated-failures` always succeeds and sets `consecutive_failures` to zero
- [X] T023 [US1] Create `infrahub_sync/service/schedule_routes.py` with a `schedule_router(service, authenticate, idempotency_key)` factory and mount it in `_include_routers` of `infrahub_sync/service/app.py`; build `ScheduleService` in `infrahub_sync/service/serve.py`
- [X] T024 [US1] Add to `ServiceOrchestration` and `PrefectOrchestration` in `infrahub_sync/service/orchestration.py`: read the schedules of the deployment `infrahub-sync-service/run`, and create, update and delete one by slug with `CronSchedule(cron, timezone)`, `active`, and the parameters `schedule_id` and `stage`; add the same methods to the fake orchestrations of the service tests (R1)
- [X] T025 [US1] Refuse `verify`, `apply` and `resume` of a scheduled run with branch review in `infrahub_sync/service/service.py` with `409 scheduled-run-not-appliable` (FR-028)
- [X] T026 [US1] In `infrahub_sync/service/flow.py`: add the parameter `schedule_id` to `service_sync_run` and make `run_id` optional; for a flow run that is `auto_scheduled` and carries `schedule_id`, decide the start (schedule state, the 5 minutes of lateness, `run_in_progress` and the `reject` policy, the missed starts since `last_start_at`, the derived branch name and the `pending` branch row for branch review) and call `admit_scheduled_start`; end the flow run with no run record when the start does not run; refuse a flow run with `schedule_id` that is not `auto_scheduled`; refuse a run with branch review with a typed failure until phase 4 (R2, R7, R10, R11)
- [X] T027 [US1] In `infrahub_sync/service/schedules.py` and the lifespan of `create_app` in `infrahub_sync/service/app.py`: one step, run once when the API process starts and after each schedule request, that makes the Prefect schedules of the deployment match the stored schedules by slug; at startup it also stops a schedule whose owner's actor no longer exists or whose write target now needs approval, with its event; build it in `infrahub_sync/service/serve.py`; call the same step at the end of the bootstrap job in `infrahub_sync/service/bootstrap.py`, after it applies the deployment (R6, R8, R13)
- [X] T028 [US1] Add the filters `trigger_type` and `schedule_id` to `GET /runs` and the three run fields to the run projections in `infrahub_sync/service/service.py` and `infrahub_sync/service/models.py`; return `details` in `GET /events`
- [X] T029 [P] [US1] Add to `SyncClient` in `infrahub_sync/client/client.py`: `create_schedule`, `list_schedules`, `iter_schedules`, `get_schedule`, `change_schedule`, `pause_schedule`, `resume_schedule`, `delete_schedule`; the new filters of `list_runs` and the `related` filter of `list_events`; export them in `infrahub_sync/client/__init__.py`
- [X] T030 [US1] Add the `schedules` command group to `infrahub_sync/cli.py` (create, list, show, change, pause, resume, delete), `_resolve_schedule` (identifier, then name), the stopped warning, `--trigger-type` and `--schedule` on `runs list`, the three fields on `runs show`, and `--schedule` and the `details` on `events` (contracts/cli.md)

**Checkpoint**: the unit tests of user story 1 pass. A schedule exists and starts runs; a run with
branch review fails in the worker until phase 4.

---

## Phase 4: User Story 1 - Scheduled sync with review in Infrahub, worker side (Priority: P1)

**Goal**: a scheduled run writes to its own branch and opens a proposed change that replaces the
schedule's earlier one. This phase also builds the five branch and proposed change operations
(FR-029, FR-030).

**Independent Test**: `uv run pytest tests/service/test_branch_review_operations.py tests/service/test_schedule_branch_review.py`, then section 4 of [quickstart.md](quickstart.md) on the development stack.

### Facts to check first

- [X] T059 [US1] Check against a running Infrahub and the code, and record the answers in `dev/specs/021-scheduled-runs/research.md` under "Facts not known": the permissions that the worker's token needs to create and delete a branch and to create, update and comment on a `CoreProposedChange`; whether closing a proposed change is an update of its `state` or a mutation of its own; whether the worker can call Infrahub and the store after the stage result is committed and the write guard is released (`infrahub_sync/service/flow.py`, `infrahub_sync/service/apply_guard.py`)

### Tests for User Story 1, worker side

- [X] T060 [P] [US1] Write `tests/service/test_branch_review_operations.py` with a fake Infrahub client, for contracts/branch-review.md: each of the five operations; a branch that exists counts as created, a branch that is missing counts as deleted, a proposed change that is closed counts as closed; `approved` only when the state is open and `approved_by` holds an account; each failure maps to a failure class with no text from Infrahub
- [X] T061 [P] [US1] Write `tests/integration/test_branch_review_operations_integration.py` with the `integration` marker: the five operations against a running Infrahub, with a branch that the test deletes in `finally`
- [X] T031 [US1] Extend `tests/service/test_schedule_branch_review.py` with the worker part, against the fake Infrahub client: an earlier proposed change that is approved and open stops the start with `skipped-approved-change-open` and no run (scenario 5); one that a person merged or closed moves to `kept` and the start runs (scenario 19); the branch is created before the plan; a plan with no operation deletes the branch and ends `no-change` (scenario 6); a plan that leaves out records still writes the remaining ones, and the description of the proposed change states the count (scenario 20); the proposed change is opened after the write is committed (scenario 3); an earlier unapproved one is closed with the comment and its branch deleted (scenario 4); a `pending` branch of a failed run is deleted by the next successful run; a failure to open or replace does not change the write verdict and records `proposed_change_failed`; Infrahub out of reach before the admission records `start_failed`
- [ ] T032 [US1] Write `tests/dev_stack/smoke/test_scheduled_run.py` with the `smoke` marker for section 4 of quickstart.md

### Implementation for User Story 1, worker side

- [X] T062 [US1] Create `infrahub_sync/service/branch_review.py` with the five operations of research R15 on the Infrahub client that the destination adapter builds, and add their failure classes to `infrahub_sync/service/failure_cause.py`
- [X] T063 [US1] In the admission in `infrahub_sync/service/flow.py`: for a schedule with branch review, read the state of the schedule's earlier proposed changes before `admit_scheduled_start`; skip the start when one is approved and open, with the event `start_skipped` and the outcome `skipped-approved-change-open`; move a merged or closed one to `kept`; record `start_failed` when Infrahub cannot be reached (FR-031, R11)
- [X] T033 [US1] In the `sync` branch of `_execute_stage` in `infrahub_sync/service/flow.py`, for a run with branch review: create the branch, plan on it, delete it and end when the plan has no operation, then verify and apply on it with `confirm_left_out` true; save `branch_review.branch` and `branch_state` with the stage result (R11 steps 1 to 4)
- [X] T034 [US1] After the stage result is committed in `infrahub_sync/service/flow.py`, open the proposed change with the description of contracts/branch-review.md and replace the schedule's earlier branches (R11 steps 5 and 6); save the second part of `branch_review` and its events with one store call
- [X] T035 [US1] Update `schedule_branches` from the two parts of `branch_review` in `infrahub_sync/product_store/store.py`, and return `open_branch` on the schedule resource
- [ ] T058 [US1] Add `branch-review` to the set of delivered write targets in `infrahub_sync/service/serve.py` and remove the flow's refusal of a run with branch review in `infrahub_sync/service/flow.py`. This is the last task of the phase

**Checkpoint**: user story 1 works end to end on the development stack. This is the first
delivery.

---

## Phase 5: User Story 2 - Scheduled sync straight to the default branch (Priority: P2)

**Goal**: on a Sync API that does not require approval, a schedule writes to the default branch,
and the team chooses whether it writes when records are left out.

**Independent Test**: `uv run pytest tests/service/test_schedule_left_out.py`, then section 3 of
[quickstart.md](quickstart.md).

### Tests for User Story 2

- [X] T036 [P] [US2] Write `tests/service/test_schedule_left_out.py`: for the default branch, the setting on makes a `sync` stage write the remaining records and record `details.left_out` in its result event (scenario 2); the setting off stops after the plan with `left-out-review-required` (scenario 3); turning it on records who and when, and turning it off clears both (scenario 4); turning it on for a schedule with branch review answers `422 schedule-left-out-not-applicable`, and a change of the write target to branch review sets it to false (scenario 5); a run that a person starts is not changed
- [X] T037 [P] [US2] Extend `tests/service/test_schedule_routes.py` and `tests/service/test_schedule_admission.py`: a schedule with the write target `default-branch` is created when approval is not required (scenario 1), its run writes to the branch that a run of a person writes to for that version (FR-027), and it has no `schedule_branches` row

### Implementation for User Story 2

- [X] T038 [US2] In the `sync` branch of `_execute_stage` in `infrahub_sync/service/flow.py`: when the admission returns `confirm_left_out` true, skip `_left_out_stop` and pass `confirm_left_out=True` to the apply step; carry the plan's `left_out` count into the stage result and into `details.left_out` of the result event (R5, FR-005)
- [X] T039 [US2] Record `left_out_set_by` and `left_out_set_at` in `ScheduleService` in `infrahub_sync/service/schedules.py`, and add `--write-when-left-out` and `--no-write-when-left-out` to `schedules create` and `schedules change` in `infrahub_sync/cli.py`
- [X] T040 [US2] In `ScheduleService` in `infrahub_sync/service/schedules.py`: refuse `write_when_left_out` true for the write target `branch-review` with `422 schedule-left-out-not-applicable`, and set it to false when the write target changes to `branch-review` (FR-004)
- [ ] T041 [US2] Add `default-branch` to the set of delivered write targets in `infrahub_sync/service/serve.py` and add `--write-to-default-branch` and `--write-to-branch-review` to `infrahub_sync/cli.py`. This is the last task of the phase

**Checkpoint**: user story 2 works on the development stack.

---

## Phase 6: User Story 3 - Operating a schedule over time (Priority: P3)

**Goal**: the `queue` policy, a late run after a missed start, a warning for a newer version, and
a take-over by an administrator.

**Independent Test**: `uv run pytest tests/service/test_schedule_missed_starts.py tests/service/test_schedule_takeover.py`

### Tests for User Story 3

- [X] T042 [P] [US3] Write `tests/service/test_schedule_missed_starts.py`: one late run inside the duration (scenario 1); none outside it, with `missed-skipped` (scenario 2); four missed starts give one run and four events (scenario 3); `queue` makes one run wait and start when the run in progress ends (scenario 4); a further start while one waits is skipped (scenario 5); a late run follows the overlap policy (scenario 8, FR-019)
- [X] T043 [P] [US3] Write `tests/service/test_schedule_takeover.py`: an administrator takes over a stopped schedule and the next start produces a run (scenario 7); a caller who is not an administrator is refused; `newer_version` is set when a higher version is registered and the schedule still runs its own (scenario 6)
- [X] T044 [P] [US3] Extend `tests/cli/test_schedule_commands.py`, `tests/client/test_schedules.py` and `tests/cli/test_parity_and_closure.py`: `schedules take-over`, `--overlap-policy queue`, `--start-late-by-at-most`, and the newer-version warning on the standard error stream

### Implementation for User Story 3

- [X] T045 [US3] In the admission in `infrahub_sync/service/flow.py`: replace the 5 minutes with `start_late_by_at_most_seconds` for the most recent start, never run an earlier late start, and write `missed-started-late` with the run (R10, FR-017, FR-018)
- [X] T046 [US3] In the admission in `infrahub_sync/service/flow.py` and in `infrahub_sync/product_store/store.py`: the `queue` policy with `waiting_start_at`, the events `start_queued` and `skipped-start-waiting`, and a flow run that is scheduled again one minute later until no run is in progress (R7, FR-016)
- [X] T047 [US3] In `infrahub_sync/service/schedules.py` and `infrahub_sync/service/schedule_routes.py`: accept `queue` and the late-start duration, return `start_late_by_at_most_seconds`, `waiting_start_at` and `newer_version`, and add `POST /schedules/{schedule_id}/take-over` with its event (FR-007, FR-026)
- [X] T048 [P] [US3] Add `take_over_schedule` to `infrahub_sync/client/client.py`, and to `infrahub_sync/cli.py` the `schedules take-over` command, `--overlap-policy`, `--start-late-by-at-most` with its duration parser, and the newer-version warning

**Checkpoint**: all three user stories work.

---

## Phase 7: Polish & cross-cutting concerns

- [ ] T049 [P] Rewrite `docs/docs/orchestration.mdx` around the built-in schedule: create a schedule, review its proposed change, the two write targets, the left-out setting, a stopped schedule, missed starts, and the warning that a person's edit on a schedule's branch is deleted by the next run. Describe only what is delivered. State that Infrahub Sync sends no notification for a scheduled run and give the commands to check a schedule (FR-033)
- [ ] T050 [P] Remove or correct "no built-in schedule" in `docs/docs/operations/supported-platforms-and-limits.mdx`, `docs/docs/readme.mdx`, `docs/docs/migrating-from-netbox-or-nautobot.mdx` and `docs/docs/reference/prefect-remote-run.mdx`
- [ ] T051 [P] Update `docs/docs/reference/sync-http-api.mdx` (routes, the CLI and API action table, schedule events and `details`, the approval section, the run fields) and `docs/docs/reference/durable-product-records.mdx` (schedule and schedule branch records); keep `tests/test_run_events_docs.py` passing
- [ ] T052 [P] Add the schedule loop, the start of a scheduled run and the branch records to `docs/docs/development/knowledge/sync-architecture.md` and `docs/docs/development/knowledge/repository-tour.md`
- [ ] T053 Run `uv run invoke docs.generate` and commit `docs/docs/reference/cli.mdx`
- [ ] T054 Regenerate `frontend/src/api/types.generated.ts` with `pnpm codegen:openapi` against a running Sync service. Change no interface file
- [ ] T055 Add the smoke test of section 3 of quickstart.md to `tests/dev_stack/smoke/test_scheduled_run.py` (after T041), and state the permissions of the worker's token in Infrahub in `docs/docs/compose-deployment.mdx` (from T059); check on the development stack that a run starts within 1 minute of its start time (SC-007)
- [ ] T056 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, `uv run invoke tests.tests-product-store-postgresql`, the three `--help` checks of `AGENTS.md`, and `uv run invoke docs.docusaurus`
- [ ] T057 Run every section of [quickstart.md](quickstart.md) and record the results in the implementation report

---

## Dependencies & execution order

- Phase 1 → Phase 2 → Phase 3 → Phase 4. Phases 1 to 4 are merged together.
- Phase 5 needs phase 3. It does not need phase 4, and is delivered after it.
- Phase 6 needs phase 3. It does not need phases 4 or 5.
- Phase 7 follows each delivery.

Within a phase:

- T008 → T009 → T010 → T011. T007, T012, T013 and T014 are independent of them, except that T014
  needs T008.
- T024 → T022 → T023 → T027. T026 needs T010 and T011. T025 and T028 need T026. T030 needs T029.
- T059 → T062 → T063 → T033 → T034 → T035 → T058. T060 and T061 come before T062.
- T045 and T046 touch the same function: do them in that order.

### Parallel examples

- Phase 2 tests: T003, T004, T005 and T006 together.
- Phase 3 tests: T015 to T021 together, then T024 and T026 by two people.
- Phase 7: T049 to T052 together.

## Implementation strategy

1. **Foundation and the service side of user story 1** (phases 1 to 3). Nothing is delivered to a
   team yet.
2. **First delivery: phase 4.** Scheduled runs with review in Infrahub. Phases 1 to 4 are one
   pull request, or a series merged together.
3. **Second delivery: phase 5.** Scheduled runs to the default branch.
4. **User story 3** (phase 6).
5. **Decision records** are written when this specification is extracted: Prefect holds the
   cadence and the worker admits a scheduled flow run, and a write to a branch that Infrahub Sync
   created for a run needs no Sync approval.

## Task counts

| Phase | Tasks |
| --- | --- |
| 1 Setup | 2 |
| 2 Foundational | 12 |
| 3 User story 1, service side | 16 |
| 4 User story 1, worker side | 11 |
| 5 User story 2 | 6 |
| 6 User story 3 | 7 |
| 7 Polish | 9 |
| Total | 63 |
