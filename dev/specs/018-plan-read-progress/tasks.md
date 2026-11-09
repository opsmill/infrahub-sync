# Tasks: Read state for each side of a Plan

**Input**: Design documents from `dev/specs/018-plan-read-progress/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Required. The feature is delivered test-first: every functional requirement has a test task that must
fail before its implementation task starts. Run one test file with `uv run pytest <file> -q -p no:cacheprovider`.
Run the offline tier with `uv run invoke tests.tests-unit`. Before each commit also run `uv run ruff check .`,
because `invoke lint` does not check `tests/` with Ruff.

**Base**: Implementation starts after pull request #402 (spec 017) and the spec 016 branch are merged into
`feature/v3-develop`, and after this branch is rebased onto it (plan.md, "Dependencies on other work").

**Order**: Potenda's report (Phase 2) and the stored columns (Phase 2) come first, because the worker, the response
and the CLI all need them. US1 then wires the worker and the response. US2 (CLI) and US3 (`sync`) depend on US1.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: the user story in spec.md: US1 read state in the run response, US2 CLI, US3 `sync` stage.

## Phase 1: Setup

- [X] T001 Confirm the baseline: on branch `018-plan-read-progress`, run `uv run invoke tests.tests-unit` and `uv run ruff check .`, and record the pass count under a "Baseline" heading in `dev/specs/018-plan-read-progress/opsmill-implement-report.md` (create the file).

---

## Phase 2: Foundational - Potenda's report and the stored columns

**Purpose**: Potenda reports each side's read to an optional observer and logs the end line; the store keeps one
document for each side. Every user story needs both.

### Tests for Phase 2

- [X] T002 [P] Write failing tests in `tests/test_potenda_read_progress.py` with two fake DiffSync adapters and a recording observer: a successful read reports `read_started("source")` then `read_ended("source", report)` with `succeeded=True`, `read_mode="full"` and `nodes_by_kind` holding every top-level kind (a kind with no node shows `0`) sorted by name (FR-001, FR-002, FR-003, FR-008; research R1, R5); a failing destination reports `succeeded=False` and `error_class` equal to the raised exception's class name, never its message, while the source reports its own end (FR-004, FR-013; research R8); an incremental read reports `read_mode="incremental"` (FR-008).
- [X] T003 [P] Add failing tests to `tests/test_potenda_read_progress.py`: an observer whose methods raise does not change the read's result and logs one WARNING with the error class (FR-007; research R2); with no observer, `load_both_sides` behaves as before; one INFO line `Load: source read succeeded (full): 5 nodes; DcimDevice 3, LocationSite 2` and one `Load: destination read failed (ConnectionError)` are logged on `infrahub_sync.potenda`, with no exception text (FR-010; contracts/cli-output.md).
- [X] T004 [P] Write failing tests in `tests/product_store/test_side_read_columns.py`: a store created before this feature gains `source_read` and `destination_read` on start-up and reads existing rows with both `None`; `record_side_read` writes one side's document and leaves the other side unchanged; a write to an execution whose `terminal_at` is set changes nothing; an unknown side name is refused before any SQL runs; both sides written from two threads at once on SQLite are both stored (research R3; critique R2).
- [X] T005 [P] Add a failing test to `tests/test_execution_surface.py`: `execute_run(..., operation="plan", _read_observer=observer)` sets `ptd.read_observer` on the engine the factory returned, and the factory still receives its eight pinned keyword arguments (research R2).

### Implementation for Phase 2

- [X] T006 Create `infrahub_sync/potenda/read_progress.py` with the `SideReadObserver` protocol (`read_started(side)`, `read_ended(side, report)`), the `Side` literal (`"source"`, `"destination"`) and the frozen `SideReadReport` dataclass (data-model.md).
- [X] T007 In `infrahub_sync/potenda/__init__.py`, add `read_observer: SideReadObserver | None = None` to `Potenda`, and change `_load_side` to report the start and the end to it, map side `A` to `source` and `B` to `destination`, compute `nodes_by_kind` and `read_mode` after the snapshot is written, guard each observer call with `try`/`except Exception` and a WARNING naming the error class, and log the end line (research R1, R2, R5, R7, R8). Make T002 and T003 pass.
- [X] T008 In `infrahub_sync/execution.py`, add the private keyword-only seam `_read_observer: SideReadObserver | None = None` to `execute_run` and set `ptd.read_observer` after the factory call, next to `ptd.force_full_extract` (research R2). Make T005 pass.
- [X] T009 In `infrahub_sync/product_store/store.py`, add `_PREFECT_EXECUTION_READ_COLUMNS` (`source_read TEXT`, `destination_read TEXT`) to `_migrate_prefect_execution_columns`, add both columns to `_SELECT_PREFECT_EXECUTIONS` and `_INSERT_PREFECT_EXECUTION` and their row mapping, and add `record_side_read(run_id, flow_run_id, side, document)` with the `terminal_at IS NULL` guard; add `source_read` and `destination_read` to `PrefectExecutionLink` in `infrahub_sync/product_store/models.py`, and the method to the projection protocol (research R3; data-model.md). Make T004 pass.
- [X] T010 Measure the node-counting time on the largest example configuration with the store the example uses, and record it, with the side's read time, under a "Node counting" heading in `dev/specs/018-plan-read-progress/opsmill-implement-report.md`. If counting takes more than 5% of the read, count in one pass in `infrahub_sync/potenda/__init__.py` and keep T002 passing (research R5; critique R3).

**Checkpoint**: Potenda reports each read, logs its end, and the store keeps each side's document.

---

## Phase 3: User Story 1 - Read state for each side, while the stage runs and after it ends (Priority: P1) 🎯 MVP

**Goal**: The run response returns each side's read state for a Plan execution, while it runs and after it ends.

**Independent Test**: Run a Plan with fake adapters whose reads can be paused or made to fail, and read the run
during and after each read.

### Tests for User Story 1

- [ ] T011 [P] [US1] Write failing tests in `tests/service/test_read_progress.py` for `StoreSideReadObserver`: `read_started` writes `{"state": "reading", "started_at": ...}`; `read_ended` writes `succeeded` with `started_at`, `ended_at`, `read_mode` and `nodes_by_kind`, or `failed` with `error_class`; a store write that raises is logged as a WARNING and not raised (FR-002, FR-007; research R10; data-model.md).
- [ ] T012 [P] [US1] Add failing tests to `tests/service/test_read_progress.py` for the response derivation: no document gives `reads=None`; an execution with no terminal state returns the stored states; a terminal state other than `completed` returns `waiting` as `not_started` and `reading` as `interrupted`, both with `ended_at=None`; a `completed` execution returns a side still `waiting` or `reading` as `None` and logs one WARNING; a document that does not parse returns `None` for that side and logs one WARNING (FR-005, FR-006, FR-009; research R6; contracts/run-response.md).
- [ ] T013 [P] [US1] Add failing tests to `tests/service/test_read_progress.py` for the stage wiring: for a `plan` stage, `_execute_stage` records both sides as `waiting` before `execute_run` runs and passes a `StoreSideReadObserver` for the execution's `flow_run_id` to `execute_run` through `_plan`; for `verify` and `apply` stages, nothing is recorded and no observer is passed; a lineage refusal before the reads leaves both sides `waiting`, returned as `not_started` once the execution failed (FR-001, FR-011; research R4, R9, R10).
- [ ] T014 [P] [US1] Add failing tests to `tests/service/test_http_api.py`: `GET /runs/{run_id}` returns `reads` on each orchestration entry as in contracts/run-response.md, with the source `succeeded` and the destination `reading` while a fake destination read is paused; an execution recorded before this feature returns `reads: null`; no field of `reads` contains the text of a raised exception (FR-005, FR-013).

### Implementation for User Story 1

- [ ] T015 [US1] Add `SideRead`, `StageReads` and `OrchestrationSummary.reads: StageReads | None = None` to `infrahub_sync/client/models.py` (data-model.md).
- [ ] T016 [US1] Create `infrahub_sync/service/read_progress.py` with `StoreSideReadObserver(projection, run_id, flow_run_id)`, a `record_waiting` helper that writes both `waiting` documents, and `stage_reads(link)`, which derives `StageReads` from a `PrefectExecutionLink` (research R6, R10). Make T011 and T012 pass.
- [ ] T017 [US1] In `infrahub_sync/service/flow.py`, pass `flow_run_id` from `service_sync_run` to `_execute_stage`; for `plan` and `sync` stages, call `record_waiting` and build a `StoreSideReadObserver` before `_plan`; add an `observer` parameter to `_plan` and pass it as `_read_observer` to `execute_run` (research R4, R9, R10). Make T013 pass.
- [ ] T018 [US1] In `infrahub_sync/service/service.py`, set `reads=stage_reads(link)` in both `OrchestrationSummary` constructions of `_resource_with_observations` (research R6). Make T014 pass.
- [ ] T019 [US1] Update existing tests that compare a full run response or an `OrchestrationSummary` dictionary for the new `reads` key, in `tests/service/` and `tests/client/`, without changing what they check otherwise (critique R4).

**Checkpoint**: User Story 1 works on its own: the run response shows each side's read state for a Plan.

---

## Phase 4: User Story 2 - The CLI shows the same information (Priority: P2)

**Goal**: `infrahub-sync runs show` prints the read state of the run's latest `plan` or `sync` execution.

**Independent Test**: Run `runs show` against a fake Sync API response for a run with a Plan execution followed by a
`verify` execution.

### Tests for User Story 2

- [ ] T020 [P] [US2] Write failing tests in `tests/cli/test_client_commands.py`: `runs show` prints `source_read` and `destination_read` after `prefect_flow_run_url`, with keys in the order `state`, `mode`, `started_at`, `ended_at`, `nodes`, `error` and keys with no value left out; a run whose Plan execution is followed by a `verify` execution still prints the Plan's read state; a run without `reads` prints neither field and keeps every other field and the exit code (FR-012; contracts/cli-output.md; critique M1).

### Implementation for User Story 2

- [ ] T021 [US2] In `infrahub_sync/cli.py`, change `_echo_run` to select the latest orchestration entry whose `purpose` is `plan` or `sync` for the read fields, and print `source_read` and `destination_read` when it has `reads` (research R11). Make T020 pass.

**Checkpoint**: User Stories 1 and 2 both work.

---

## Phase 5: User Story 3 - The same for the `sync` stage (Priority: P3)

**Goal**: A `sync` execution records and returns the same read state as a Plan.

**Independent Test**: Run the `sync` stage with fake adapters and read the run.

### Tests for User Story 3

- [ ] T022 [P] [US3] Add failing tests to `tests/service/test_read_progress.py`: the `sync` stage records both sides as `waiting`, passes the observer to `_plan(..., composed_sync=True)`, and its run response returns the same `reads` as a Plan for the same fake reads (research R9).

### Implementation for User Story 3

- [ ] T023 [US3] Make T022 pass in `infrahub_sync/service/flow.py`, if T017 did not already cover the `sync` path.

**Checkpoint**: All user stories work.

---

## Phase 6: Polish and cross-cutting concerns

- [ ] T024 [P] Document `reads` in each orchestration entry, with the state rules, in `docs/docs/reference/sync-http-api.mdx` (contracts/run-response.md).
- [ ] T025 [P] Document the `source_read` and `destination_read` fields of `runs show` in `docs/docs/reference/cli.mdx`, and what the operator views during a Plan in `docs/docs/running-a-sync.mdx` (contracts/cli-output.md).
- [ ] T026 [P] Check `docs/docs/reference/durable-product-records.mdx` for the list of what a stage execution records, and add the read state there if it lists execution fields (critique R5).
- [ ] T027 [P] Add the news fragment `changelog/+plan-read-progress.added.md` with `uv run --extra dev towncrier create -c "<text>" +plan-read-progress.added.md`, describing what the operator views during a Plan and a `sync`.
- [ ] T028 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, `uv run infrahub-sync --help`, `uv run infrahub-sync runs --help`, and `uv run invoke docs.generate` if the CLI help changed; record the results in `dev/specs/018-plan-read-progress/opsmill-implement-report.md`.
- [ ] T029 Run the live checks in `dev/specs/018-plan-read-progress/quickstart.md` sections 2 to 5 when the preview stack is available, and record each result or the reason it was not run in `dev/specs/018-plan-read-progress/opsmill-implement-report.md`.

---

## Dependencies and execution order

- **Phase 1** has no dependency.
- **Phase 2** depends on Phase 1. T006 comes before T007 and T008. T009 is independent of T006 to T008.
- **US1 (Phase 3)** depends on Phase 2. T015 comes before T016; T016 before T017 and T018; T019 after T018.
- **US2 (Phase 4)** depends on T015 (the client model) and on US1 for live data.
- **US3 (Phase 5)** depends on T017.
- **Phase 6** depends on the user stories it documents.

## Parallel opportunities

- T002, T003, T004 and T005 are in different files and can be written together.
- T011 to T014 can be written together once Phase 2 is done.
- T024 to T027 can run together.

```text
Phase 2 tests together:   T002 | T004 | T005   (T003 shares T002's file)
US1 tests together:       T011 | T014          (T012 and T013 share T011's file)
Polish together:          T024 | T025 | T026 | T027
```

## Implementation strategy

1. **MVP**: Phases 1 to 3. The run response returns each side's read state for a Plan, which is what the frontend
   team needs.
2. **Then**: Phase 4 (CLI) and Phase 5 (`sync`), each a separate increment.
3. **Then**: Phase 6.
