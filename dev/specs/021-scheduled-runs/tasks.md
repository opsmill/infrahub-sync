# Tasks: Scheduled runs that write without a person present

**Input**: Design documents from `dev/specs/021-scheduled-runs/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md) (R1 to R14),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: included. Constitution principle V asks for a test with each change, and each test
task comes before the implementation task it covers.

**Format**: `- [ ] [ID] [P?] [Story] Description`

- **[P]**: the task touches different files from the other open tasks of its phase and has no
  dependency on them.
- **[Story]**: the user story of the task (US1, US2, US3).
- **BLOCKED (INFP-655)**: the task cannot start before the two prerequisite capabilities of the
  specification exist. It is then written against their real interface.
- **GATED (principle I)**: the task must not be merged before the product owner confirms the
  amendment of constitution principle I for user story 2.

## What can start today

| Phase | State |
| --- | --- |
| 1, 2, 3 | Can start. |
| 4 | Blocked until the prerequisite exists. |
| 5 | Can be built. Its last task, which turns the write target on, is gated. |
| 6 | Can start after phase 3. |
| 7 | Follows the phases that are delivered. |

Phase 3 alone gives a team nothing to use. Both write targets are refused with
`schedule-setting-not-available` until they are delivered: branch review by the last task of
phase 4, the default branch by the last task of phase 5. Until one of the two is merged, no
schedule can be created outside the unit tests.

---

## Phase 1: Setup (dependencies and release note)

- [ ] T001 Add `cronsim` and `tzdata` to the `service` extra in `pyproject.toml`, each with the marker `python_version >= '3.11'` and a version range that holds the versions already in `uv.lock`; run `uv lock` and commit `uv.lock` (R3)
- [ ] T002 [P] Create the news fragment `changelog/+scheduled-runs.added.md`: schedules exist, and a product database of an earlier build must be replaced (R12)

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the records, the models and the cadence that every user story uses.

### Tests

- [ ] T003 [P] Write `tests/service/test_schedule_cadence.py`: next start from a cron expression and a time zone; a start in the hour that a daylight-saving change removes and in the hour it repeats gives at most one start; an invalid expression and an unknown time zone raise the module's own error; the list of starts between two times is bounded (R3)
- [ ] T004 [P] Write `tests/product_store/test_schedules.py`, parameterized as `("sqlite", pytest.param("postgresql", marks=pytest.mark.integration))`: create, read, list with cursor and filters, change, state transitions of data-model.md, unique name among schedules that are not deleted, the compare-and-set advance with its events in one transaction (two callers, one wins), `schedule_branches` states, the unique index on `(schedule_id, scheduled_start_at)` of `product_runs`
- [ ] T005 [P] Extend `tests/product_store/test_contract.py`, `tests/product_store/test_run_approval.py` and `tests/product_store/test_postgresql_isolation.py`: new public names, the receipt kind `schedule`, the new columns of `product_runs` and `audit_events` (critique E9)
- [ ] T006 [P] Extend `tests/client/test_models.py` and `tests/client/test_public_surface.py`: schedule request, resource and page models; `trigger_type`, `schedule_id` and `scheduled_start_at` on the run resource; `details` on the event resource; the client package imports with `cronsim` absent from `sys.modules`

### Implementation

- [ ] T007 Create `infrahub_sync/service/schedule_cadence.py`: `next_start(cron, timezone, after)` and `starts_between(cron, timezone, after, until, limit)` in UTC, with `cronsim` and `zoneinfo`, and one typed error for an invalid expression or time zone. No other module imports `cronsim` (plan.md, Python 3.10 note)
- [ ] T008 Add to `infrahub_sync/product_store/models.py`: `ScheduleRecord`, `ScheduleBranch`, `ScheduleFilters`; `trigger_type`, `schedule_id` and `scheduled_start_at` on `ProductRun` and `RunFilters`; the segment `schedule` in `EVENT_TYPE_PATTERN`; the kind `schedule` in `RelatedResource`, `PrimaryResource`, `_RELATED_KIND_ORDER` and `primary_resource`; the closed `details` model on `AuditEvent`; the kind `schedule` in `MutationReceipt.resource_kind` and its invariants (data-model.md, R9)
- [ ] T009 Add to `infrahub_sync/product_store/store.py`: tables `schedules` and `schedule_branches`; columns `trigger_type`, `schedule_id`, `scheduled_start_at` on `product_runs` and `details` on `audit_events`; the unique index and the `schedule_id` index; the receipt `CHECK` constraint and SQLite triggers with the kind `schedule`; a startup refusal, with a message, of a database whose tables lack the new columns (R12)
- [ ] T010 Add the schedule methods to the store protocol and both stores in `infrahub_sync/product_store/store.py`: create, get, list, update, `advance_schedule(schedule_id, expected_next_start_at, next_start_at, events)`, the `schedule_branches` reads and writes, and `run_of_start(schedule_id, scheduled_start_at)`; change `complete_mutation` and `commit_claimed_execution` to take a sequence of events (R2, R9)
- [ ] T011 Add `run_in_progress(config_id, now, receipt_age_limit)` to the store in `infrahub_sync/product_store/store.py`, with its tests in `tests/product_store/test_schedules.py`: an execution with `terminal_at IS NULL`, or an unresolved receipt of a stage operation younger than the limit; a run in phase `planned`, an interrupted run and an abandoned execution do not count (R7)
- [ ] T012 [P] Add to `infrahub_sync/client/models.py`: `CreateScheduleRequest`, `ChangeScheduleRequest`, `ScheduleActionRequest`, `ScheduleResource`, `SchedulePage`; the three run fields; the event `details`. Cron and time zone are text with no validation here. Re-export them and add the emitted variants in `infrahub_sync/service/models.py` (R13)
- [ ] T013 [P] Add `principal_of(actor)` to `PrincipalResolver` and `EnvironmentPrincipalResolver` in `infrahub_sync/service/auth.py`, with tests in a new `tests/service/test_principal_resolver.py` (R8)
- [ ] T014 [P] Add to `infrahub_sync/service/events.py`: the schedule event types of contracts/events.md, `schedule_decision_type`, a `schedule_event` builder, and the schedule as a related resource in `run_event` when the run holds a `schedule_id`; tests in `tests/service/test_event_transactions.py` (R9)

**Checkpoint**: Foundation ready. `uv run invoke tests.tests-unit` passes.

---

## Phase 3: User Story 1 - Scheduled sync with review in Infrahub, service side (Priority: P1)

**Goal**: an operator creates and manages a schedule, and the Sync API starts one run for each
start, skips a start when a run is in progress, and records it all.

**Independent Test**: `uv run pytest tests/service/test_schedule_routes.py tests/service/test_schedule_loop.py tests/service/test_schedule_owner.py tests/service/test_schedule_approval.py tests/cli/test_schedule_commands.py`

### Tests for User Story 1

- [ ] T015 [P] [US1] Write `tests/service/test_schedule_routes.py` for contracts/http-api.md: a write target outside the set of delivered write targets answers `422 schedule-setting-not-available`, and so does a request with no write target while branch review is not in the set; create with defaults and `next_start_at`; every refusal code of the create and change tables; list, view, change, pause, resume, delete; owner and administrator rules (acceptance scenarios 1, 10 to 13, 16); idempotency replay; one event for each accepted, refused and replayed request; a canary credential in a request never appears in a record or an event (FR-025)
- [ ] T016 [P] [US1] Write `tests/service/test_schedule_loop.py` with an injected clock and a fake orchestration: a start creates one run with `trigger_type`, `schedule_id`, `scheduled_start_at`, the owner as actor and version (scenario 2); a second pass and a second loop create no second run; a change or a take-over between two attempts creates no second run; an orchestration that is unavailable leaves a run that later passes submit, with a growing wait, and the start is never recorded as missed; `reject` skips and names the run in progress (scenario 7); a start more than 60 seconds late is recorded as missed with no run; more than 500 missed starts give 500 events and one summary; a configuration version that cannot be run records `start_failed`; 30 starts give 30 runs or recorded starts (SC-002)
- [ ] T017 [P] [US1] Write `tests/service/test_schedule_owner.py`: the owner's actor is removed, the schedule stops with `owner-not-found` and an event (scenario 14); a resume is refused while the reason holds and succeeds when the actor exists again (scenario 15); a paused or stopped schedule records no missed start
- [ ] T018 [P] [US1] Write `tests/service/test_schedule_approval.py`, with both write targets in the set of delivered write targets: with required approval, a schedule that writes to the default branch is refused at creation and at a change (scenario 8); a run with branch review is accepted with no approval (scenario 9); a restart with approval turned on stops a default-branch schedule with `approval-required`
- [ ] T019 [P] [US1] Write `tests/service/test_schedule_branch_review.py`, service part: branch review is refused for a version that declares a destination branch (scenario 16); the run's destination branch is the derived name, the same for two attempts; a `schedule_branches` row in the state `pending` exists with the run; `verify`, `apply` and `resume` on the run answer `409 scheduled-run-not-appliable` (scenario 17)
- [ ] T020 [P] [US1] Extend `tests/service/test_http_api.py` (route set, 401 and 422 on every schedule route, run fields and filters) and `tests/service/test_events_route.py` (`related=schedule:<id>` returns the schedule's events and its runs' events, with `details`; scenario 18)
- [ ] T021 [P] [US1] Write `tests/client/test_schedules.py` and `tests/cli/test_schedule_commands.py` for contracts/cli.md, and add the eight commands to the matrix in `tests/cli/test_parity_and_closure.py`

### Implementation for User Story 1

- [ ] T022 [US1] Create `infrahub_sync/service/schedules.py`: a `ScheduleService` with create, change, pause, resume and delete; cadence validation through `schedule_cadence.py`; the owner or administrator rule; the refusals `schedule-name-in-use`, `approval-required-for-schedule`, `schedule-branch-declared`, `schedule-setting-not-available` (for `queue`, the late-start duration, and a write target that is not in the set of delivered write targets that the constructor receives; `serve.py` passes an empty set, the unit tests pass both; the approval refusal is checked first; a request with no write target is refused while branch review is not delivered), `schedule-stopped`, `schedule-deleted`; receipts of the kind `schedule`; one event for each decision in the transaction of its answer
- [ ] T023 [US1] Create `infrahub_sync/service/schedule_routes.py` with a `schedule_router(service, authenticate, idempotency_key)` factory and mount it in `_include_routers` of `infrahub_sync/service/app.py`; build `ScheduleService` in `infrahub_sync/service/serve.py`
- [ ] T024 [US1] Add `start_scheduled_run(schedule, start_at)` to `RunService` in `infrahub_sync/service/service.py`: the steps of `create_run` with the owner's principal, the key and fingerprint of R2, `trigger_type`, `schedule_id`, `scheduled_start_at`, the derived branch name and the `pending` branch row for branch review, `confirm_left_out`, `branch_review` and `earlier_branches` as flow parameters, which this task also adds to the signature of `service_sync_run` in `infrahub_sync/service/flow.py` together with a typed refusal of a run that sets `branch_review`; reuse `_submit`; submit again a run of the start that exists and was not submitted (R2, R4, R11)
- [ ] T025 [US1] Refuse `verify`, `apply` and `resume` of a scheduled run with branch review in `infrahub_sync/service/service.py` with `409 scheduled-run-not-appliable` (FR-028)
- [ ] T026 [US1] Create `infrahub_sync/service/schedule_loop.py`: `ScheduleLoop.run_once()` with an injected clock; for each active schedule whose `next_start_at` has passed: the owner check, the approval check, the on-time check of 60 seconds, `run_in_progress` and the `reject` policy, `start_scheduled_run`, then `advance_schedule` with the events; the retry wait for a run that was not submitted; named error classes only, and one schedule's failure does not stop the pass (R2, R6, R7, R8, R10)
- [ ] T027 [US1] Start the schedule loop as a second task in the lifespan of `create_app` in `infrahub_sync/service/app.py`, with a cadence of 5 seconds, cancelled on shutdown; build it in `infrahub_sync/service/serve.py`; re-enter it in `infrahub_sync/service/web.py` as the liveness loop is
- [ ] T028 [US1] Add the filters `trigger_type` and `schedule_id` to `GET /runs` and the three run fields to the run projections in `infrahub_sync/service/service.py` and `infrahub_sync/service/models.py`; return `details` in `GET /events`
- [ ] T029 [P] [US1] Add to `SyncClient` in `infrahub_sync/client/client.py`: `create_schedule`, `list_schedules`, `iter_schedules`, `get_schedule`, `change_schedule`, `pause_schedule`, `resume_schedule`, `delete_schedule`; the new filters of `list_runs` and the `related` filter of `list_events`; export them in `infrahub_sync/client/__init__.py`
- [ ] T030 [US1] Add the `schedules` command group to `infrahub_sync/cli.py` (create, list, show, change, pause, resume, delete), `_resolve_schedule` (identifier, then name), the stopped warning, `--trigger-type` and `--schedule` on `runs list`, the three fields on `runs show`, and `--schedule` and the `details` on `events` (contracts/cli.md)

**Checkpoint**: the unit tests of user story 1 pass. A schedule exists and starts runs; a run with
branch review fails in the worker until phase 4.

---

## Phase 4: User Story 1 - Scheduled sync with review in Infrahub, worker side (Priority: P1)

**Goal**: a scheduled run writes to its own branch and opens a proposed change that replaces the
schedule's earlier one.

**Independent Test**: section 4 of [quickstart.md](quickstart.md), on the development stack.

**Every task of this phase is BLOCKED (INFP-655).** See
[contracts/branch-review.md](contracts/branch-review.md).

### Tests for User Story 1, worker side

- [ ] T031 [US1] BLOCKED (INFP-655): extend `tests/service/test_schedule_branch_review.py` with the worker part, against a fake of the prerequisite: the branch is created before the plan; a plan with no operation deletes the branch and ends `no-change` (scenario 6); the proposed change is opened after the write is committed (scenario 3); an unapproved earlier one is closed with the comment and its branch deleted (scenario 4); an approved, merged or closed earlier one is kept (scenario 5); a `pending` branch of a failed run is deleted by the next successful run; a failure to open or replace does not change the write verdict and records `proposed_change_failed`
- [ ] T032 [US1] BLOCKED (INFP-655): write `tests/dev_stack/smoke/test_scheduled_run.py` with the `smoke` marker for section 4 of quickstart.md

### Implementation for User Story 1, worker side

- [ ] T059 [US1] BLOCKED (INFP-655): check in `infrahub_sync/service/flow.py` and `infrahub_sync/service/apply_guard.py` whether the worker can call Infrahub and the store after the stage result is committed and the write guard is released (R11 steps 6 and 7, research "Facts not known"); record the answer in `research.md` before T034
- [ ] T033 [US1] BLOCKED (INFP-655): in the `sync` branch of `_execute_stage` in `infrahub_sync/service/flow.py`, for a run with `branch_review`: create the branch, plan on it, delete it and end when the plan has no operation, then verify and apply on it; save `branch_review.branch` and `branch_state` with the stage result (R11 steps 1 to 5)
- [ ] T034 [US1] BLOCKED (INFP-655): after the stage result is committed in `infrahub_sync/service/flow.py`, open the proposed change and handle `earlier_branches` (R11 steps 6 and 7); save the second part of `branch_review` and its events with one store call; add the failure classes of these steps to `infrahub_sync/service/failure_cause.py`
- [ ] T035 [US1] BLOCKED (INFP-655): update `schedule_branches` from the two parts of `branch_review` in `infrahub_sync/product_store/store.py`, and return `open_branch` on the schedule resource
- [ ] T058 [US1] BLOCKED (INFP-655): add `branch-review` to the set of delivered write targets in `infrahub_sync/service/serve.py` and remove the flow's refusal of `branch_review` in `infrahub_sync/service/flow.py`. This is the last task of the phase

**Checkpoint**: user story 1 works end to end on the development stack.

---

## Phase 5: User Story 2 - Scheduled sync straight to the default branch (Priority: P2)

**Goal**: on a Sync API that does not require approval, a schedule writes to the default branch,
and the team chooses whether it writes when records are left out.

**Independent Test**: `uv run pytest tests/service/test_schedule_left_out.py`, then section 3 of
[quickstart.md](quickstart.md).

### Tests for User Story 2

- [ ] T036 [P] [US2] Write `tests/service/test_schedule_left_out.py`: the setting on makes a `sync` stage write the remaining records and record `details.left_out` in its result event (scenario 2); the setting off stops after the plan with `left-out-review-required` (scenario 3); turning it on records who and when, and turning it off clears both (scenario 4); a run that a person starts is not changed
- [ ] T037 [P] [US2] Extend `tests/service/test_schedule_routes.py` and `tests/service/test_schedule_loop.py`: a schedule with the write target `default-branch` is created when approval is not required (scenario 1), its run writes to the branch that a run of a person writes to for that version (FR-027), and it has no `schedule_branches` row

### Implementation for User Story 2

- [ ] T038 [US2] In the `sync` branch of `_execute_stage` in `infrahub_sync/service/flow.py`: when `confirm_left_out` is true, skip `_left_out_stop` and pass `confirm_left_out=True` to the apply step; carry the plan's `left_out` count into the stage result and into `details.left_out` of the result event (R5, FR-005)
- [ ] T039 [US2] Record `left_out_set_by` and `left_out_set_at` in `ScheduleService` in `infrahub_sync/service/schedules.py`, and add `--write-when-left-out` and `--no-write-when-left-out` to `schedules create` and `schedules change` in `infrahub_sync/cli.py`
- [ ] T040 [US2] BLOCKED (INFP-655): for a run with branch review whose plan leaves out records and whose setting is off, delete the branch, end with `left-out-review-required` and the next action "correct the source records or turn the setting on", in `infrahub_sync/service/flow.py`; cover scenarios 5 and 6 in `tests/service/test_schedule_branch_review.py`
- [ ] T041 [US2] GATED (principle I): add `default-branch` to the set of delivered write targets in `infrahub_sync/service/serve.py` and add `--write-to-default-branch` and `--write-to-branch-review` to `infrahub_sync/cli.py`. Do this only after the amendment of constitution principle I is merged

**Checkpoint**: user story 2 works on the development stack once T041 is merged.

---

## Phase 6: User Story 3 - Operating a schedule over time (Priority: P3)

**Goal**: the `queue` policy, a late run after a missed start, a warning for a newer version, and
a take-over by an administrator.

**Independent Test**: `uv run pytest tests/service/test_schedule_missed_starts.py tests/service/test_schedule_takeover.py`

### Tests for User Story 3

- [ ] T042 [P] [US3] Write `tests/service/test_schedule_missed_starts.py`: one late run inside the duration (scenario 1); none outside it, with `missed-skipped` (scenario 2); four missed starts give one run and four events (scenario 3); `queue` makes one run wait and start when the run in progress ends (scenario 4); a further start while one waits is skipped (scenario 5); a late run follows the overlap policy (scenario 8, FR-019)
- [ ] T043 [P] [US3] Write `tests/service/test_schedule_takeover.py`: an administrator takes over a stopped schedule and the next start produces a run (scenario 7); a caller who is not an administrator is refused; `newer_version` is set when a higher version is registered and the schedule still runs its own (scenario 6)
- [ ] T044 [P] [US3] Extend `tests/cli/test_schedule_commands.py`, `tests/client/test_schedules.py` and `tests/cli/test_parity_and_closure.py`: `schedules take-over`, `--overlap-policy queue`, `--start-late-by-at-most`, and the newer-version warning on the standard error stream

### Implementation for User Story 3

- [ ] T045 [US3] In `infrahub_sync/service/schedule_loop.py`: replace the 60 seconds with `start_late_by_at_most_seconds` for the most recent missed start, record the earlier ones, and write `missed-started-late` with the run (R10, FR-017, FR-018)
- [ ] T046 [US3] In `infrahub_sync/service/schedule_loop.py` and `infrahub_sync/product_store/store.py`: the `queue` policy with `waiting_start_at`, the events `start_queued` and `skipped-start-waiting`, and the start of the waiting run when no run is in progress (R7, FR-016)
- [ ] T047 [US3] In `infrahub_sync/service/schedules.py` and `infrahub_sync/service/schedule_routes.py`: accept `queue` and the late-start duration, return `start_late_by_at_most_seconds`, `waiting_start_at` and `newer_version`, and add `POST /schedules/{schedule_id}/take-over` with its event (FR-007, FR-026)
- [ ] T048 [P] [US3] Add `take_over_schedule` to `infrahub_sync/client/client.py`, and to `infrahub_sync/cli.py` the `schedules take-over` command, `--overlap-policy`, `--start-late-by-at-most` with its duration parser, and the newer-version warning

**Checkpoint**: all user stories that are not blocked work.

---

## Phase 7: Polish & cross-cutting concerns

- [ ] T049 [P] Rewrite `docs/docs/orchestration.mdx` around the built-in schedule: create a schedule, review its proposed change, the two write targets, the left-out setting, a stopped schedule, missed starts, and the warning that a person's edit on a schedule's branch is deleted by the next run. Describe only what is delivered
- [ ] T050 [P] Remove or correct "no built-in schedule" in `docs/docs/operations/supported-platforms-and-limits.mdx`, `docs/docs/readme.mdx`, `docs/docs/migrating-from-netbox-or-nautobot.mdx` and `docs/docs/reference/prefect-remote-run.mdx`
- [ ] T051 [P] Update `docs/docs/reference/sync-http-api.mdx` (routes, the CLI and API action table, schedule events and `details`, the approval section, the run fields) and `docs/docs/reference/durable-product-records.mdx` (schedule and schedule branch records); keep `tests/test_run_events_docs.py` passing
- [ ] T052 [P] Add the schedule loop, the start of a scheduled run and the branch records to `docs/docs/development/knowledge/sync-architecture.md` and `docs/docs/development/knowledge/repository-tour.md`
- [ ] T053 Run `uv run invoke docs.generate` and commit `docs/docs/reference/cli.mdx`
- [ ] T054 Regenerate `frontend/src/api/types.generated.ts` with `pnpm codegen:openapi` against a running Sync service. Change no interface file
- [ ] T055 Write the smoke test of section 3 of quickstart.md in `tests/dev_stack/smoke/test_scheduled_run.py` (after T041), or of section 4 (after phase 4), whichever is delivered first
- [ ] T056 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, `uv run invoke tests.tests-product-store-postgresql`, the three `--help` checks of `AGENTS.md`, and `uv run invoke docs.docusaurus`
- [ ] T057 Run the sections of [quickstart.md](quickstart.md) that are not blocked and record the results in the implementation report

---

## Dependencies & execution order

- Phase 1 → Phase 2 → Phase 3. Phase 3 blocks phases 4, 5 and 6.
- Phase 4 needs phase 3 and the prerequisite of the specification.
- Phase 5 needs phase 3. T040 also needs phase 4. T041 needs the amendment of principle I.
- Phase 6 needs phase 3. It does not need phases 4 or 5.
- Phase 7 follows the phases that are delivered.

Within a phase:

- T008 → T009 → T010 → T011. T007, T012, T013 and T014 are independent of them, except that T014
  needs T008.
- T022 → T023. T024 → T026 → T027. T025 and T028 need T024. T030 needs T029.
- T059 → T033 → T034 → T035 → T058.
- T045 and T046 touch the same file: do them in that order.

### Parallel examples

- Phase 2 tests: T003, T004, T005 and T006 together.
- Phase 3 tests: T015 to T021 together, then T022 and T024 by two people.
- Phase 7: T049 to T052 together.

## Implementation strategy

1. **Foundation and the service side of user story 1** (phases 1 to 3). Nothing is delivered to a
   team yet.
2. **The first delivery is one of two**, and the product owner chooses:
    - phase 4, when the prerequisite exists: scheduled runs with review in Infrahub;
    - phase 5 with T041, when principle I is amended: scheduled runs to the default branch.
3. **User story 3** (phase 6) after either.
4. **Decision records** are written when this specification is extracted: the Sync API process
   starts scheduled runs, and a write to a branch that Infrahub Sync created for a run needs no
   Sync approval.

## Task counts

| Phase | Tasks | Blocked or gated |
| --- | --- | --- |
| 1 Setup | 2 | 0 |
| 2 Foundational | 12 | 0 |
| 3 User story 1, service side | 16 | 0 |
| 4 User story 1, worker side | 7 | 7 blocked |
| 5 User story 2 | 6 | 1 blocked, 1 gated |
| 6 User story 3 | 7 | 0 |
| 7 Polish | 9 | 0 |
| Total | 59 | 8 blocked, 1 gated |
