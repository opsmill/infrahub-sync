# Tasks: Resume an interrupted apply

**Input**: Design documents from `dev/specs/022-resume-interrupted-apply/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/http-api.md](contracts/http-api.md), [contracts/cli.md](contracts/cli.md), [quickstart.md](quickstart.md),
[critiques/critique.md](critiques/critique.md)

**Tests**: included. The constitution (principle V) requires tests for new behaviour, and SC-001 and SC-002 are
defined as test outcomes.

**Organization**: the progress marker and the admission of `verify` on an interrupted run are foundational, because
US1's resume needs both. US1 (resume), US2 (per-operation `verify` report) and US3 (`sync` runs) then build on them.
US2 can ship without US1; US3 extends US1's resume stage.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: the user story the task belongs to (US1, US2, US3)

## Phase 1: Setup

- [ ] T001 Record the unit-test baseline: run `uv run invoke tests.tests-unit` and `uv run ruff check .` in the worktree and note the pass/skip counts under "Baseline" at the end of this file (no source change)

## Phase 2: Foundational (blocks US1, US2 and US3)

### Progress marker in the engine

- [ ] T002 Add `ApplyProgressObserver` (a protocol with `committed(index, operation)`, `kind_finished(index)`, `stopped(index)`, `should_stop() -> bool`) and `ApplyProgressLostError` (carries the partial `ApplyRecord`) in `infrahub_sync/potenda/apply_progress.py`
- [ ] T003 Extend `Potenda.apply_plan` in `infrahub_sync/potenda/__init__.py` with keyword arguments `progress: ApplyProgressObserver | None = None` and `start_after: int | None = None`: call `committed` only after the destination call returned and `applied.append` ran (FR-002), call `kind_finished` when the next operation's kind differs, call `should_stop()` next to `ownership.before_operation()` and raise `ApplyProgressLostError` when it is true, and skip operations at index `<= start_after` without dispatching them (research R3, R4, R6)
- [ ] T004 Pass `progress` and `start_after` through `PlanApplier.apply_plan` (`infrahub_sync/utils.py`) and the shared execution surface (`infrahub_sync/execution.py`), defaulting to `None` so the CLI-local and direct Prefect paths behave as today
- [ ] T005 [P] Test in `tests/test_potenda_apply_progress.py`: `committed` is never reported for an operation whose destination call raised; `kind_finished` fires at each kind boundary; `start_after=k` dispatches only operations after `k` and still runs every pre-write check; `should_stop()` returning true raises `ApplyProgressLostError` with the partial record; with no observer the existing apply tests pass unchanged

### Progress marker in the store and the service

- [ ] T006 Add `ApplyProgress` (fields of data-model.md "Execution progress marker") to `infrahub_sync/product_store/models.py`, add the nullable `apply_progress` column to `prefect_executions` through the existing start-up column migration, and add `record_apply_progress(run_id, flow_run_id, document)` that updates only `WHERE terminal_at IS NULL`, in `infrahub_sync/product_store/store.py` (research R2)
- [ ] T007 Add `StoreApplyProgressObserver` in `infrahub_sync/service/apply_progress.py`: save after 500 commits, after 30 seconds, at each kind boundary, on `stopped` and at the end; catch store errors broadly, log a warning with the run identifier and the error class; track the first failed attempt since the last success and make `should_stop()` true once it is 5 minutes old (research R4, critique E4)
- [ ] T008 Pass a `StoreApplyProgressObserver` from the service worker's apply and `sync` stages in `infrahub_sync/service/flow.py`
- [ ] T009 [P] Test in `tests/product_store/test_apply_progress.py`: the column is added idempotently, a marker round-trips, and a write after the execution is terminal changes nothing
- [ ] T010 [P] Test in `tests/service/test_apply_progress_observer.py` with an injected clock: the save cadence (500, 30 seconds, kind boundary, end, failure); a failing store keeps writing and logs one warning per failure without the exception message; `should_stop()` turns true 5 minutes after the first failed attempt and never during a quiet period between successful saves

### `verify` on an interrupted run

- [ ] T011 Add the resumable-run rule (data-model.md "State rules": `reconciliation_required`, not cancelled, no unfinished execution) as a store query, and admit a `verify` reservation on a resumable run as the one exception to the write-admission refusal in `_reserve_existing`, in `infrahub_sync/product_store/store.py` (research R5, critique E1)
- [ ] T012 [P] Test in `tests/product_store/test_resume_admissions.py`: `verify` is admitted on a resumable run; `plan`, `apply`, `approve` and a second `verify` while one is unfinished are still refused; `verify` on an applied run is still refused

**Checkpoint**: applies save a marker; an interrupted run accepts `verify`. Nothing resumes yet.

## Phase 3: User Story 1 - Resume an interrupted apply (Priority: P1) 🎯 MVP

**Goal**: an operator resumes an interrupted apply from its marker on the same run.

**Independent Test**: interrupt an apply with a fake destination, run `verify`, resume, and check that every
operation after the marker was written once more and the run ends applied.

- [ ] T013 [US1] Add the `resume_admissions` table and `reserve_resume(run_id, receipt, ...)`: one transaction on the locked run row requiring the run to be resumable, its latest `verify` execution after its latest write execution and succeeded, and no unfinished resume; refusals `run-not-resumable`, `verify-required`, `run-execution-conflict`, in `infrahub_sync/product_store/store.py` (research R5, data model)
- [ ] T014 [US1] Derive the run's position as the highest saved position over all write executions, and clear `reconciliation_required` and set phase `applied` when a `resume` execution ends `succeeded`, inside `_terminalize_execution`, in `infrahub_sync/product_store/store.py` (research R8, critique E2)
- [ ] T015 [P] [US1] Test in `tests/product_store/test_resume_admissions.py`: each refusal; two concurrent resumes on PostgreSQL admit exactly one (isolation test); the original `write_admissions` row is kept; success clears `reconciliation_required`; an interrupted resume keeps the run resumable and makes `verify` required again
- [ ] T016 [US1] Add `ResumeRunRequest` (data-model.md) and `RunResource.apply_progress` and `RunResource.resumes` to `infrahub_sync/client/models.py`
- [ ] T017 [US1] Add `resume_run` to `infrahub_sync/service/service.py`: reuse `apply_run`'s checks in the same order (ownership, non-secret parameters, `confirm_writes`, cancelled, approval, supported configuration, checksum, left-out), then reserve through `reserve_resume`; record every request in the audit history and event list with operation `resume` and the optional reason (FR-011 to FR-014, FR-018)
- [ ] T018 [US1] Add the `resume` purpose to `infrahub_sync/service/flow.py`: read the run's saved plan, pass `start_after` from the run's position and a `StoreApplyProgressObserver`, never call the plan stage (FR-010, FR-015)
- [ ] T019 [US1] Add `POST /runs/{run_id}/resume` with the idempotency key to `infrahub_sync/service/app.py`, and derive `apply_progress` (position, `saved_at`, `resumable`, `verify_required`, `by_kind` with the uncertain window of critique E3) and `resumes` for `GET /runs/{run_id}` (FR-004, contracts/http-api.md)
- [ ] T020 [US1] Add `resume()` to `infrahub_sync/client/client.py`
- [ ] T021 [US1] Add `runs resume` to `infrahub_sync/cli.py` with the options of contracts/cli.md, the `verify-required` hint, and the progress, resumable and per-kind lines and resume history in `runs show`
- [ ] T022 [P] [US1] Test in `tests/service/test_resume_run.py`: P1 acceptance scenarios 1 to 4 with a fake destination (10,000 operations, marker at 6,000, worker stopped after 6,200); each refusal of contracts/http-api.md including approval-required and left-out; the reason is optional; idempotent replay of the same key; a destination "not found" on an id-keyed update stops the resume with a failure that names the operation
- [ ] T023 [P] [US1] Test in `tests/client/test_resume.py` and `tests/cli/test_runs_resume.py`: request shape, `runs show` output lines, the `verify-required` hint, and exit codes matching `apply`

**Checkpoint**: an interrupted apply can be resumed from the CLI and the API (SC-002 in unit tests).

## Phase 4: User Story 2 - Learn what an uncertain write did (Priority: P2)

**Goal**: `verify` on an interrupted run reports each uncertain operation as matches, differs or missing.

**Independent Test**: interrupt an apply, set the fake destination to hold some uncertain nodes as planned, some
changed and some absent, run `verify`, and compare the report.

- [ ] T024 [US2] Add the comparison in `infrahub_sync/plan/destination_check.py`: for one planned create or update and the node read from the destination (or none), return `matches`, `differs` or `missing`, comparing every planned attribute and relationship, never returning values (research R7)
- [ ] T025 [US2] Add a read of nodes by recorded identifier or complete human-friendly identifier, grouped by kind with one query per kind, to the Infrahub adapter in `infrahub_sync/adapters/infrahub.py`
- [ ] T026 [US2] In the `verify` stage, when the run is resumable and every pre-write check passed, compute the uncertain window (critique E3), read its nodes, and store `results.verification.operations` and, above 2,000 operations, `operations_truncated`, in `infrahub_sync/service/flow.py` (FR-005 to FR-008)
- [ ] T027 [US2] Print one `operation ID KIND ACTION: STATE` line per reported operation, the truncation line and the overwrite note in `verify` output, in `infrahub_sync/cli.py` (FR-006, contracts/cli.md)
- [ ] T028 [P] [US2] Test in `tests/service/test_verify_destination_report.py` and `tests/test_destination_check.py`: the three states; the window when the failing operation is known, when it is unknown with a successful last save, and when the last save failed; the report never holds an attribute value; a failed pre-write check fails `verify` and produces no report; differs and missing never fail `verify`

**Checkpoint**: `verify` explains an interruption (P2 acceptance scenarios).

## Phase 5: User Story 3 - Resume an interrupted `sync` run (Priority: P3)

- [ ] T029 [US3] Make `resume_run` and the `resume` flow accept a run whose interrupted write execution has purpose `sync`, using the checksum of the plan the `sync` run saved and never reading the source, in `infrahub_sync/service/service.py` and `infrahub_sync/service/flow.py` (FR-017, research R9)
- [ ] T030 [P] [US3] Test in `tests/service/test_resume_run.py`: an interrupted `sync` run resumes from its marker, no plan stage runs, and the source adapter is never constructed

## Phase 6: Polish and cross-cutting

- [ ] T031 [P] Write `dev/adr/0026-an-interrupted-apply-resumes-from-its-saved-position.md`, replacing ADR 0010's "an uncertain run is terminal" and its rejected "write-attempt table" alternative for writes proven safe to repeat, and mark ADR 0010 as partly superseded
- [ ] T032 [P] Update `docs/docs/running-a-sync.mdx` (what to do after an interruption: `runs show`, `verify`, `runs resume`, and that a resume overwrites nodes reported as differs), `docs/docs/development/knowledge/planned-write-and-apply.md` (marker and resume), and regenerate the CLI and HTTP reference with `uv run invoke docs.generate`
- [ ] T033 [P] Add the changelog fragment `changelog/+resume-interrupted-apply.added.md`
- [ ] T034 Opt-in integration test (`-m integration`) against the preview stack following quickstart.md: kill `sync-worker` during an apply, `verify`, `runs resume`, then a new plan shows 0 creates and 0 updates (SC-001, SC-005)
- [ ] T035 Measure SC-003 and SC-004 with the existing benchmark runner on tiers M and L, first without the marker and then with it on the same machine (critique E5), and record the results in the implementation report
- [ ] T036 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit` and the CLI sanity commands of AGENTS.md; record the results under "Phase 6 results"

## Dependencies and execution order

- Phase 2 blocks every story. T002 → T003 → T004; T006 → T007 → T008; T011 needs T006.
- US1: T013 → T014 → T017 → T018 → T019 → T020 → T021. T016 before T017.
- US2 needs Phase 2 only (T011 for the `verify` admission); T024 and T025 can run in parallel, then T026 → T027.
- US3 needs T017 and T018.
- Phase 6 after the stories it documents; T034 and T035 after US1 and US2.

## Parallel opportunities

- T005, T009, T010, T012 once their source tasks land.
- US2 (T024, T025) in parallel with US1 after Phase 2.
- T031, T032, T033 in parallel.

## Implementation strategy

1. Phase 2, then US1: the smallest change that lets an operator resume (MVP).
2. US2: the per-operation report.
3. US3, then Phase 6.

## Baseline

To be recorded by T001.
