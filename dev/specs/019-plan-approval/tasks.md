# Tasks: Approve a plan without applying it, and a `verify` that checks the destination

**Input**: Design documents from `dev/specs/019-plan-approval/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/http-api.md](contracts/http-api.md), [contracts/cli.md](contracts/cli.md), [quickstart.md](quickstart.md)

**Tests**: included. The constitution (principle V) requires tests for new behaviour, and SC-002 to SC-004 are
defined as test outcomes.

**Organization**: one phase per user story. US1 (approval), US2 (`verify` checks the destination), US3
(approval-required setting) and US4 (withdraw by cancelling) can be shipped independently, except that US3's `apply`
check reads the approval that US1 stores, and US4's `approve` refusal extends US1's `approve_run`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: the user story the task belongs to (US1, US2, US3)

## Phase 1: Setup

- [X] T001 Record the unit-test baseline: run `uv run invoke tests.tests-unit` and `uv run ruff check .` in the worktree and note the pass/skip counts in `dev/specs/019-plan-approval/tasks.md` under "Baseline" (no source change)

## Phase 2: Foundational (blocks US1 and US3)

- [X] T002 Add the `RunApproval` model (fields `approved_by`, `approved_at` with time zone, `plan_checksum` 64-hex, `reason`) and `ProductRun.approval: RunApproval | None = None` with a validator refusing a partial record, in `infrahub_sync/product_store/models.py`
- [X] T003 Add nullable columns `approved_by`, `approved_at`, `approved_checksum`, `approval_reason` to `product_runs` through the existing start-up nullable-column migration list, and map them in the row-to-`ProductRun` and `ProductRun`-to-row code for SQLite and PostgreSQL, in `infrahub_sync/product_store/store.py`
- [X] T004 [P] Test that a pre-existing `product_runs` table gains the four columns idempotently, that existing rows read `approval is None`, and that a stored approval round-trips, in `tests/product_store/test_run_approval.py`

**Checkpoint**: the store carries an optional approval; nothing writes it yet.

## Phase 3: User Story 1 - Approve a plan without applying it (Priority: P1) 🎯 MVP

**Goal**: an operator approves a planned run with its checksum; the run records who, when and which checksum; nothing is written to the destination.

**Independent Test**: plan a run, `approve RUN_ID --expected-checksum CHECKSUM`, read the run, confirm no destination write and no Prefect flow run.

### Tests for User Story 1

- [X] T005 [P] [US1] Test `record_approval` on SQLite: conditional update succeeds once, a second call reports already-approved without changing the record, a run not in phase `planned` is not updated, and the mutation receipt is completed in the same transaction with the given response body, in `tests/product_store/test_run_approval.py`
- [X] T006 [P] [US1] Test concurrent `record_approval` on PostgreSQL: two transactions approving the same run, exactly one stores, in `tests/product_store/test_postgresql_isolation.py`
- [X] T007 [P] [US1] Test `POST /runs/{run_id}/approve` per [contracts/http-api.md](contracts/http-api.md): success body (run with `approval` and `plan_summary`), 403 for another non-admin actor, 409 `run-not-approvable` for a failed, applied or still-running run (message names the phase or the active stage), 409 `plan-checksum-unverified`, 409 `checksum-mismatch` showing both checksums, 409 `already-approved` with the existing approval in `details`, replay with the same idempotency key returns the stored body, no Prefect submission, audit events with the outcomes in [data-model.md](data-model.md), in `tests/service/test_approval.py`
- [X] T008 [P] [US1] Test the client `approve_run`/`approve` request and the `approval` field on `RunResource` and `PublicRunResource` (present and absent), in `tests/client/test_client.py` and `tests/client/test_models.py`
- [X] T009 [P] [US1] Test the CLI `approve` command output (run lines, approval lines, `approved_creates`, `approved_updates`, `approved_deletes_recorded`) and the `runs show` approval lines, present and absent, in `tests/cli/test_client_commands.py`

### Implementation for User Story 1

- [X] T010 [US1] Check whether `mutation_receipts.operation` is constrained (SQL check or a `Literal` type in `infrahub_sync/product_store/models.py`) and add `approve`; add `record_approval(run_id, approval, receipt, response)` running the conditional update `approved_by IS NULL AND phase = 'planned'` and the receipt completion in one transaction, returning whether it stored, in `infrahub_sync/product_store/store.py`
- [X] T011 [US1] Add `ApproveRunRequest` (`expected_checksum`, optional `reason`, extra fields forbidden), `RunApprovalResource`, the `approval` field on the run resources and the `plan_summary` field on the approve response, in `infrahub_sync/client/models.py` (and re-export in `infrahub_sync/client/__init__.py` and `infrahub_sync/service/models.py` as the existing models are)
- [X] T012 [US1] Implement `SyncService.approve_run(run_id, request, principal, idempotency_key)` with the evaluation order of [contracts/http-api.md](contracts/http-api.md): `_owned_run`, receipt lookup and replay, approvable state (phase `planned`, no active execution; message names the phase or active stage), `_plan(run_id)`, `checksum_ok`, checksum equality, reserve receipt, `record_approval`; audit every outcome; return the run resource with `approval` and `plan_summary`, in `infrahub_sync/service/service.py`
- [X] T013 [US1] Return `approval` on `GET /runs/{run_id}` and `GET /runs` from `ProductRun.approval`, in `infrahub_sync/service/service.py`
- [X] T014 [US1] Add the route `POST /runs/{run_id}/approve` (status 200, `Idempotency-Key` required as for the other run mutations), in `infrahub_sync/service/app.py`
- [X] T015 [US1] Add `approve_run` and the short alias `approve` to the HTTP client, in `infrahub_sync/client/client.py`
- [X] T016 [US1] Add the `approve` CLI command (`RUN_ID`, `--expected-checksum`, `--reason`, `--idempotency-key`; not interactive; prints run lines, approval lines and approved counts) and print `approved_by`, `approved_at`, `approved_checksum` in `_echo_run` when present, in `infrahub_sync/cli.py`

**Checkpoint**: US1 complete; `approve` works with the setting absent.

## Phase 4: User Story 2 - `verify` checks the destination (Priority: P2)

**Goal**: `verify` runs every pre-write check `apply` runs and writes nothing.

**Independent Test**: plan a run, break one pre-write condition, run `verify`, confirm it fails on that check and the destination is unchanged.

### Tests for User Story 2

- [X] T017 [P] [US2] Test `Potenda.check_plan`: returns the parsed plan and the check list on a valid plan; raises the same error types and check names as `apply_plan` for each gate failure; never calls `ownership.before_operation` or any destination write method, in `tests/test_potenda_check_plan.py`
- [X] T018 [P] [US2] Parametrized parity test over the check names in [data-model.md](data-model.md) (`run_binding`, `plan_checksum`, `source_snapshot`, `config_version`, `format_version`, `destination_schema`, `destination_binding`, `write_surface`, `planned_fields`, `lineage`): for each, one fixture makes the service `verify` stage and the `apply` stage both fail and name the same check (SC-004), in `tests/service/test_verify_destination.py`
- [X] T019 [P] [US2] Test the `verify` stage: success merges `results.verification.checks` and `destination_checked: true`; a failure merges `verify_failure` and leaves the phase unchanged; an unreachable destination fails with the destination error and reports no destination check as passed; the stage does not take the configuration write guard; it resolves no source credential; a legacy run reports `destination_checked: false`, in `tests/service/test_verify_destination.py`
- [X] T020 [P] [US2] Test that `verify_run` passes the run's stored `destination_branch` as the stage branch, in `tests/service/test_http_api.py`
- [X] T021 [P] [US2] Test the CLI `verify` success output prints one `check: NAME passed` line per check, in `tests/cli/test_client_commands.py`

### Implementation for User Story 2

- [X] T022 [US2] Split `Potenda.apply_plan` into `check_plan(*, run_id, config_version, artifact, expected_checksum=None) -> PlanCheck` (gate: `verify_plan` with the write-surface check, optional `expected_checksum_refusal`, `parse_plan_artifact`, `_require_applyable_format`, `_validate_infrahub_plan_payload`, `resolve_destination_lineage`) and the write loop; `apply_plan` reads the artifact once and calls `check_plan`; keep the ordering comments with the code they describe, in `infrahub_sync/potenda/__init__.py`
- [X] T023 [US2] Add `PlanApplier.check_plan(*, config_version, allow_destination_change=False) -> PlanCheck` running `_require_recorded_destination` and `engine.check_plan` on one artifact read, in `infrahub_sync/utils.py`
- [X] T024 [US2] In the `verify` stage of `_execute_stage`, for a registered run and after the existing file verification: rebuild the instance with the `apply` runtime-model scope (one destination schema read), call `_require_planned_schema`, open `PlanApplier.open_existing(...)` with the stage branch and call `check_plan`; merge `checks` and `destination_checked` into `results.verification`; no write guard. Change `_worker_execution_context` so `verify` resolves no source credential (`stage not in ("apply", "verify")`) and so its runtime scope is built only when requested, in `infrahub_sync/service/flow.py`
- [X] T025 [US2] Pass `branch=run.destination_branch` in `verify_run`'s stage parameters, in `infrahub_sync/service/service.py`
- [X] T026 [US2] Print one `check: NAME passed` line per entry of `results.verification.checks` after a successful `verify`, and change the `verify` help text to "Check that the named run's saved plan would apply now, without writing.", in `infrahub_sync/cli.py`

**Checkpoint**: US2 complete; `verify` and `apply` share one gate.

## Phase 5: User Story 3 - The site requires approval (Priority: P3)

**Goal**: with `INFRAHUB_SYNC_REQUIRE_APPROVAL` on, only an approved plan is applied and `sync` is refused; with it off, nothing changes.

**Independent Test**: start the service with the setting on; apply an unapproved plan (refused), an approved plan (proceeds), run `sync` (refused); repeat with the setting off (unchanged).

### Tests for User Story 3

- [X] T027 [P] [US3] Test setting parsing: unset is off; `true`/`1`/`yes`/`on` and `false`/`0`/`no`/`off` in any case; any other value raises at start-up with a message naming the variable, in `tests/service/test_approval_required.py`
- [X] T028 [P] [US3] Test admission with the setting on: `apply` of an unapproved run → 409 `approval-required`; approval checksum differing from `expected_checksum` → 409 `approval-checksum-mismatch`; approved run with the matching checksum → accepted; `POST /runs` with `operation = "sync"` → 409 `approval-required-for-sync` and no run record; `operation = "plan"` unaffected; an idempotent replay of an `apply` accepted before the setting was turned on still replays; nothing is submitted on a refusal; audit outcome `refused-approval-required`, in `tests/service/test_approval_required.py`
- [X] T029 [P] [US3] Test `GET /me` returns `approval_required` and `whoami` prints it, in `tests/service/test_http_api.py` and `tests/cli/test_client_commands.py`

### Implementation for User Story 3

- [X] T030 [US3] Create the setting module: parse `INFRAHUB_SYNC_REQUIRE_APPROVAL`, validate it when the app is created (fail closed), and expose `approval_required() -> bool`, in `infrahub_sync/service/settings.py`; call the validation from the app factory in `infrahub_sync/service/app.py`
- [X] T031 [US3] In `apply_run`, after authorization and receipt replay and before reserving a receipt, refuse when the setting is on and the run has no approval or a different approved checksum; in `create_run`, refuse `operation == "sync"` when the setting is on, before the run record is created; audit both refusals, in `infrahub_sync/service/service.py`
- [X] T032 [US3] Add `approval_required` to `PrincipalResource`, return it from `GET /me`, and print it in `whoami`, in `infrahub_sync/client/models.py`, `infrahub_sync/service/app.py` and `infrahub_sync/cli.py`

**Checkpoint**: all three stories complete.

## Phase 6: User Story 4 - Withdraw a plan by cancelling the run (Priority: P3)

**Goal**: `runs cancel` on a finished, unapplied plan sets the run's phase to `cancelled`; `approve` and `apply` refuse it afterwards.

**Independent Test**: plan a run, approve it, `runs cancel` it, then try to approve and to apply it; both are refused and the destination is unchanged.

### Tests for User Story 4

- [X] T033 [P] [US4] Test `cancel_planned_run` on SQLite: sets `phase`/`outcome` to `cancelled` and `finished_at`, keeps the approval columns, completes the receipt with `200` and the run body; completes it with `409 run-execution-conflict` instead when a write was admitted, another non-`cancel` receipt is unresolved or an execution is unfinished; does nothing for a phase other than `planned`; and `_reservation_refused` refuses an `apply` reservation once the phase is `cancelled`, in `tests/product_store/test_write_admission.py`
- [X] T034 [P] [US4] Test on PostgreSQL that a concurrent `cancel_planned_run` and `apply` reservation, and a concurrent `cancel_planned_run` and `record_approval`, never both succeed, in `tests/product_store/test_postgresql_isolation.py`
- [X] T035 [P] [US4] Test `POST /runs/{run_id}/cancel` per [contracts/http-api.md](contracts/http-api.md): a planned run (approved and unapproved) is withdrawn with `200`, `phase: cancelled`, approval kept, no Prefect call; an active execution is cancelled as today; an already-cancelled run → `409 run-already-cancelled`; an applied run and a plan-failed run → `409 execution-terminal`; same-key replay returns the stored `200`; another non-admin actor → 403; audit outcomes `accepted-withdrawn` and `refused-cancelled`, in `tests/service/test_cancel_planned_run.py`
- [X] T036 [P] [US4] Test that `apply` and `approve` refuse a run in phase `cancelled` with `409 run-cancelled` before any receipt is reserved, including a planned run whose `verify` execution was cancelled, and that an approved-then-cancelled run cannot be applied with the setting on or off (SC-006), in `tests/service/test_cancel_planned_run.py`
- [X] T037 [P] [US4] Test the `runs cancel` help text, confirmation prompt and `phase: cancelled` output after a withdrawal, in `tests/cli/test_client_commands.py`

### Implementation for User Story 4

- [X] T038 [US4] Add `cancel_planned_run(run_id, receipt, cancelled_at, response)` using the write-first row lock and the `_reservation_refused` checks, and make `_reservation_refused` refuse when the locked run's phase is `cancelled`, for SQLite and PostgreSQL, in `infrahub_sync/product_store/store.py` (and the projection protocol near `reserve_mutation` in the same file)
- [X] T039 [US4] In `cancel_run`, where no execution is cancellable: for phase `planned`, reserve the `cancel` receipt and call `cancel_planned_run`, returning its stored response; for phase `cancelled`, refuse with `409 run-already-cancelled`; keep `execution-terminal` for every other phase and every other branch unchanged; audit `accepted-withdrawn` and `refused-cancelled`, in `infrahub_sync/service/service.py`
- [X] T040 [US4] Refuse `phase == "cancelled"` with `409 run-cancelled` in `apply_run` (after replay, before the checksum check) and in `approve_run` (before the approvable-state check), audited `refused-cancelled`; update the existing tests that expected `execution-terminal` when cancelling a planned run, naming each in the commit message, in `infrahub_sync/service/service.py` and `tests/service/test_http_api.py`
- [X] T041 [US4] Change the `runs cancel` help text and confirmation prompt to the wording in [contracts/cli.md](contracts/cli.md), in `infrahub_sync/cli.py`

**Checkpoint**: an approval can be withdrawn; a cancelled run is never approved or applied.

## Phase 7: Polish and cross-cutting

- [ ] T042 [P] Rewrite the steps after `plan` in `docs/docs/running-a-sync.mdx`: `verify` (all pre-write checks, needs the destination), `approve` (records the decision, writes nothing, optional unless the setting is on), `apply`; answer "is `verify` an approval?"; state that `runs cancel` withdraws a finished plan, approved or not, and replace "cancels a run that has not finished"
- [ ] T043 [P] Document `POST /runs/{run_id}/approve`, the run `approval` field, the `verify` checks and results, the setting's refusals, `GET /me approval_required`, the cancel route's withdraw path (route table line, 409 list with `run-cancelled` and `run-already-cancelled`, recovery table), in `docs/docs/reference/sync-http-api.mdx`
- [ ] T044 [P] Document `INFRAHUB_SYNC_REQUIRE_APPROVAL` in the service environment of `docs/docs/compose-deployment.mdx`
- [ ] T045 Regenerate the CLI reference with `uv run invoke docs.generate` (updates `docs/docs/reference/cli.mdx`) and update the `verify`, `approve` and `whoami` lines of the CLI list in `AGENTS.md`
- [ ] T046 [P] Add changelog fragments: `changelog/+plan-approval.added.md` (approval, the setting, withdrawing a finished plan with `runs cancel`) and `changelog/+verify-destination-checks.changed.md` (`verify` checks the destination and fails when it cannot reach it; `apply` refuses every run in phase `cancelled`)
- [ ] T047 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, and the CLI sanity commands from `AGENTS.md`; record the results against the T001 baseline in `dev/specs/019-plan-approval/tasks.md`
- [ ] T048 Run the live scenarios of `dev/specs/019-plan-approval/quickstart.md` against the development stack and record the outcome in `dev/specs/019-plan-approval/quickstart.md` under "Live results", or record why they could not run

## Dependencies and execution order

- Phase 1 → Phase 2 → US1, US2, US3, US4.
- US1 (Phase 3) needs Phase 2.
- US2 (Phase 4) needs only Phase 1; it touches no approval code and can run in parallel with Phase 2 and US1.
- US3 (Phase 5) needs Phase 2 and T012 (the stored approval it reads); T027, T030 and T032 can start after Phase 2.
- US4 (Phase 6) needs Phase 2 and US1's `approve_run` (T012) and `record_approval` (T010); T038 and T040 edit `store.py` and `service.py` after US1 and US3 have.
- Phase 7 after the stories it documents.

Within each story, tests first (they fail), then models, store, service, route, client, CLI.

## Parallel opportunities

- US2 as a whole in parallel with Phase 2 + US1 (different files except `service.py` T025 vs T012/T013; serialize those two edits).
- Within US1: T005–T009 together; T011 alongside T010.
- Within US2: T017–T021 together.
- Within US3: T027–T029 together.
- Within US4: T033–T037 together.
- Phase 7: T042, T043, T044, T046 together.

## Implementation strategy

1. MVP: Phases 1–3 (approval). It already answers the customer's "the only way to accept a plan is to apply it".
2. Add US2 (`verify` checks the destination), which gives the "validate" step.
3. Add US3 (mandatory approval) for sites that want it.
4. Add US4 (withdraw by cancelling), so an approval can be taken back.
5. Polish: docs, changelog, full gates, live check.

## Baseline

Recorded by T001 on commit `b46ecd79`, Python 3.11.8, after
`uv sync --extra dev --extra prefect --extra service`:

- `uv run invoke tests.tests-unit`: 6449 passed, 4 skipped, 1 xfailed, 246 deselected,
  0 failed, 6 warnings, in 180.65 s.
- `uv run ruff check .`: all checks passed.
