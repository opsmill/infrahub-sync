# Tasks: Automatic resume of an interrupted apply

**Input**: Design documents from `dev/specs/030-automatic-resume/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: included. Constitution principle V requires tests with each change, and SC-002, SC-003 and SC-005 are
test matrices.

**Organization**: by user story, after a foundational phase that adds the stored decision and the reconciler step
that every story needs.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1, US2 or US3 from [spec.md](./spec.md)

## Path Conventions

Paths are relative to the repository root. The product store has one relational implementation
(`_RelationalRunStore` in `infrahub_sync/product_store/store.py`) used by `SQLiteRunStore` and `PostgreSQLRunStore`,
behind `ProductProjection`.

---

## Phase 1: Setup

- [X] T001 Add the setting `INFRAHUB_SYNC_AUTOMATIC_RESUME` to `infrahub_sync/service/settings.py`: a constant
      `AUTOMATIC_RESUME_ENV` and a function `automatic_resume_enabled()` that uses `_parse_flag`, like
      `approval_required()`, default `false`; an unknown value raises `ValueError` naming the variable (research R9).
- [X] T002 [P] Test T001 in `tests/service/test_settings_automatic_resume.py`: unset, `true`, `false`, and an
      unknown value, parametrized.

---

## Phase 2: Foundational (blocks every story)

### The stored decision

- [X] T003 Add the record `AutomaticResume` to `infrahub_sync/product_store/models.py` with the fields of
      [data-model.md](./data-model.md): `run_id`, `interrupted_flow_run_id`, `cause` (`Literal["rule-6",
      "rule-8"]`), `worker_name` (nullable), `state` (`Literal["waiting-check", "checking", "waiting-resume",
      "resuming", "ended"]`), `decision` (`Literal["resume", "no-resume"] | None`), `reason` (nullable, one of the
      reason codes `not-resumable`, `approval-missing`, `no-progress`, `limit-reached`, `check-failed`,
      `check-found-differs`, `not-picked-up`, `refused`, `person-took-over`), `reason_detail` (nullable JSON, codes
      and counts only), `operations_not_checked`, `start_after`, `check_flow_run_id`, `resume_flow_run_id`,
      `decided_at`, `updated_at`. Validate that `decision` is `None` unless `state` is `resuming` or `ended`, and that
      `reason` is set exactly when `decision` is `no-resume`.
- [X] T004 Add the table `automatic_resumes` to `_SCHEMA` in `infrahub_sync/product_store/store.py`, primary key
      `(run_id, interrupted_flow_run_id)`, with an index on `state` for the rows that are not `ended` (research R14).
      No change to existing tables.
- [X] T005 Add to `_RunStore`, `_RelationalRunStore` and `ProductProjection` in
      `infrahub_sync/product_store/store.py`: `automatic_resumes_of(run_id)`, `open_automatic_resumes()` (rows not
      `ended`, oldest first), and `move_automatic_resume(run_id, interrupted_flow_run_id, *, from_state, to_state,
      **fields) -> bool`, a compare-and-set on `(run_id, interrupted_flow_run_id, state)` that returns `False` when
      another process moved the row first (data-model.md, "State transitions").
- [X] T006 Change `interrupt_execution` in `infrahub_sync/product_store/store.py` to take an optional
      `automatic_resume: AutomaticResume | None` and insert it in the same transaction as the interruption, only when
      the transition commits (FR-013). When the inserted row is not `ended`, call `_settle_scheduled_run_end` with
      `unchanged` instead of `failed` (research R11).
- [X] T007 [P] Tests for T003 to T006 in `tests/product_store/test_automatic_resumes.py`: the record's validation; the
      insert happens only with a committed interruption; a second interruption of the same execution inserts nothing;
      a compare-and-set from a stale state returns `False`; SQLite and, with `PRODUCT_STORE_TEST_POSTGRESQL_DSN`,
      PostgreSQL.

### The live worker rule and the decision

- [X] T008 Move the freshness rule of `_owner_is_fresh` in `infrahub_sync/service/liveness.py` into
      `live_workers(pool, now) -> tuple[...]`: workers with status `online` and a heartbeat within
      `max(3 * heartbeat_interval_seconds, 30)` seconds. `_owner_is_fresh` uses it. A snapshot with
      `detail_available` false has no live worker (research R8, critique E3).
- [X] T009 Add to `infrahub_sync/service/liveness.py` the constants `AUTOMATIC_RESUME_ACTOR = "sync:automatic-resume"`
      and `AUTOMATIC_RESUME_LIMIT = 3`, and a pure function `decide_automatic_resume(run, link, cause, worker_name,
      previous_rows, now) -> AutomaticResume | None`. It returns `None` when `automatic_resume_enabled()` is false or
      `link.purpose` is not a write (`apply`, `sync`, `resume`). Otherwise it returns a row inserted as `ended`,
      `no-resume` with the first reason that applies, in this order: `not-resumable` (no saved plan, cancelled, or a
      scheduled run with branch review, the condition of `_refuse_scheduled_branch_review`), `approval-missing`
      (`approval_required()` and no recorded approval for the plan's checksum), `no-progress` (the previous row's
      resume ended without the run's highest marker passing that row's `start_after`), `limit-reached` (3 earlier rows
      with decision `resume`). Else a row in `waiting-check` (research R10, R11, R12).
- [X] T010 Change `reconcile_execution` in `infrahub_sync/service/liveness.py`: at rule 6, only when the observed state
      is `crashed`, and at rule 8, build the row with `decide_automatic_resume` and pass it to
      `interrupt_execution` (cause `rule-6` or `rule-8`; for rule 8, the claiming worker's name from the pool
      snapshot when present). A rule 6 ending with `failed`, `cancelled` or `completed` passes no row (FR-001,
      research R2).
- [X] T011 [P] Tests for T008 to T010 in `tests/service/test_liveness_policy.py` and
      `tests/service/test_automatic_resume.py`: `live_workers` with online, offline, stale and missing heartbeats;
      every reason of T009 in its order; rule 6 with each Prefect state; rule 8; a plan or `verify` execution gets no
      row; the setting off writes no row and no event (FR-004).

### The reconciler step and the submitter

- [X] T012 Define the protocol `AutomaticResumeSubmitter` in `infrahub_sync/service/liveness.py` with
      `async submit_check(run_id: str, idempotency_key: str) -> SubmissionOutcome` and `async submit_resume(run_id:
      str, idempotency_key: str) -> SubmissionOutcome`, where the outcome is the flow run identifier or a refusal
      code. `RunLivenessReconciler.__init__` takes an optional submitter (research R3, critique E2).
- [X] T013 Implement the protocol on `RunService` in `infrahub_sync/service/service.py`: `submit_check` calls
      `verify_run` and `submit_resume` calls `resume_run` with a `Principal(actor=AUTOMATIC_RESUME_ACTOR)`, the
      idempotency keys `automatic-check:<run_id>:<interrupted_flow_run_id>` and
      `automatic-resume:<run_id>:<interrupted_flow_run_id>`, the saved plan's checksum, and `confirm_left_out` copied
      from the receipt that admitted the interrupted write (research R4, R6). A `ServiceAPIError` becomes a refusal
      outcome with its code.
- [X] T014 Change `_owned_run` in `infrahub_sync/service/service.py` to accept `AUTOMATIC_RESUME_ACTOR` only for a run
      with a row in `waiting-check` or `waiting-resume`; any other run refuses it with `forbidden` as today (research
      R5).
- [X] T015 Add `step_automatic_resumes(pool, now)` to `RunLivenessReconciler` in `infrahub_sync/service/liveness.py`
      and call it at the end of `reconcile_once` only, never from `reconcile_run` (critique E4). For each row of
      `open_automatic_resumes()`:
      `waiting-check` with a live worker → move to `checking` with `check_flow_run_id`, then `submit_check`;
      `checking` whose check execution has a verdict → read `results["verification"]` and move to `waiting-resume`, or
      end `no-resume` with `check-failed` (failed check, unreachable destination, failed execution) or
      `check-found-differs` (count in `reason_detail`), storing `operations_not_checked` (research R7, FR-006, FR-007);
      `waiting-resume` with a live worker → store `start_after` from `resume_start_after`, move to `resuming` with
      decision `resume`, then `submit_resume`; `resuming` whose resume execution has a verdict → end. An abandoned
      check or resume ends `not-picked-up`; a refusal ends `refused` with the code (research R8). Each move writes one
      structlog line with the run identifier, the state and the reason code.
- [X] T016 Wire the submitter in `infrahub_sync/service/serve.py`, where `RunLivenessReconciler` is built: pass the
      `RunService` instance.
- [X] T017 [P] Tests for T012 to T016 in `tests/service/test_automatic_resume.py`, with a scripted pool snapshot and
      fake orchestration: each state move; no submission without a live worker; a restart between the move to
      `checking` and the submission submits once (same idempotency key replays the receipt, SC-005); two reconcilers
      stepping the same row submit once; `reconcile_run` never submits; the reserved actor is refused for a run with
      no waiting row.
- [X] T018 [P] Add the two-reconciler case to `tests/product_store/test_postgresql_isolation.py`: two connections
      compare-and-set the same row, one wins (FR-014).

**Checkpoint**: an interrupted run with the setting on gets a row, and the background loop drives it to a resume.

---

## Phase 3: User Story 1 - A stopped worker is replaced and the apply finishes with no one acting (Priority: P1) 🎯 MVP

**Goal**: the run ends applied with one automatic resume, shown with "Sync (automatic resume)" and its cause.

**Independent Test**: [quickstart.md](./quickstart.md), "Preview stack (SC-001)".

### Tests for User Story 1

- [X] T019 [P] [US1] End-to-end unit test in `tests/service/test_automatic_resume.py`: an apply interrupted at marker
      6,000 of 10,000 under rule 8, a live worker, a passing check with 0 `differs` → one resume execution that starts
      after 6,000, run phase `applied`, `reconciliation_required` cleared, `automatic_resume_count` 1 (spec US1
      scenarios 1 and 3).
- [X] T020 [P] [US1] Test in `tests/service/test_account_presentation.py` that the account name middleware shows
      `sync:automatic-resume` as `Sync (automatic resume)` in `actor` fields and event related resources.

### Implementation for User Story 1

- [X] T021 [P] [US1] Add `AUTOMATIC_RESUME_DISPLAY_NAME = "Sync (automatic resume)"` and the rule that shows it for
      `AUTOMATIC_RESUME_ACTOR` to `infrahub_sync/service/presentation.py`; no account lookup for that value.
- [X] T022 [P] [US1] Add the event types `infrahub_sync.run.automatic_resume_started` and
      `infrahub_sync.run.automatic_resume_not_started` to `infrahub_sync/service/events.py`, operation
      `automatic-resume`, with the details of [contracts/http-api.md](./contracts/http-api.md). T015 writes them on
      the move to `resuming` and on every end as `no-resume` (FR-017).
- [X] T023 [US1] Add `automatic_resumes` and `automatic_resume_count` to the run resource and the run summary in
      `infrahub_sync/service/models.py` and the run rendering in `infrahub_sync/service/service.py`, with
      `cause_text` and `reason_text` built from the codes as [contracts/http-api.md](./contracts/http-api.md) states
      (FR-016).
- [X] T024 [P] [US1] Add the same fields to `infrahub_sync/client/models.py`, and test them in
      `tests/client/test_resume.py`.
- [X] T025 [US1] Print one block per row in `runs show` and the `auto resumes` column in `runs list` in
      `infrahub_sync/cli.py`, as [contracts/cli-and-web.md](./contracts/cli-and-web.md) shows; test in
      `tests/cli/test_runs_resume.py`.
- [X] T026 [US1] Regenerate `frontend/src/api/types.generated.ts` with `pnpm codegen:openapi` against a local Sync API,
      then show the rows as notices in `frontend/src/features/runs/run-notices.tsx` and the display name on resume
      entries in `frontend/src/features/runs/RunTimeline.tsx`; hide the resume action in
      `frontend/src/features/runs/RunActions.tsx` while a row is not `ended`. Add Vitest cases next to the changed
      files.

**Checkpoint**: User Story 1 works on its own.

---

## Phase 4: User Story 2 - An automatic resume that does not start, and why (Priority: P2)

**Goal**: every "no resume" case is recorded with its reason, and a person can take over.

**Independent Test**: [quickstart.md](./quickstart.md), "Refusal cases (SC-002)".

### Tests for User Story 2

- [ ] T027 [P] [US2] Parametrized test in `tests/service/test_automatic_resume.py` over every reason code of
      data-model.md: no resume execution starts, the run stays `interrupted`, the row ends `no-resume` with the code,
      and one `automatic_resume_not_started` event is written (SC-002, spec US2 scenarios 1 to 7).
- [ ] T028 [P] [US2] Test the limits in `tests/service/test_automatic_resume.py`: a resume that ends interrupted with
      no marker progress gets no further row decision `resume`; a fourth interruption after 3 resumes with progress
      ends `limit-reached`; a manual resume after either limit is accepted and creates no row (SC-003, FR-011).
- [ ] T029 [P] [US2] Test a person taking over in `tests/service/test_resume_run.py`: `verify` or `runs resume` on a
      run with a row in `waiting-check` or `waiting-resume` ends the row as `person-took-over` and proceeds; with a
      row in `checking` or `resuming`, the request gets `run-execution-conflict` (FR-019).

### Implementation for User Story 2

- [ ] T030 [US2] In `verify_run` and `resume_run` of `infrahub_sync/service/service.py`, for a principal that is not
      `AUTOMATIC_RESUME_ACTOR`, end a waiting row as `no-resume`, `person-took-over` with `move_automatic_resume`
      before the receipt is reserved, and write the `automatic_resume_not_started` event (FR-019, research R15).
- [ ] T031 [US2] Add the `next:` line of [contracts/cli-and-web.md](./contracts/cli-and-web.md) for rows that ended
      `no-resume` in `infrahub_sync/cli.py`, and the same advice on the run page notice in
      `frontend/src/features/runs/run-notices.tsx`.

**Checkpoint**: User Stories 1 and 2 work on their own.

---

## Phase 5: User Story 3 - A scheduled run on the default branch (Priority: P3)

**Goal**: schedules count the run by its final result and skip starts while an automatic resume waits or runs.

**Independent Test**: spec US3 scenarios 1 to 4 in the unit tier.

### Tests for User Story 3

- [ ] T032 [P] [US3] Tests in `tests/service/test_schedule_admission.py`: a run with a row not `ended` makes the
      overlap policy skip (`reject`) or queue (`queue`) the next start (spec US3 scenario 2).
- [ ] T033 [P] [US3] Tests in a new `tests/service/test_schedule_automatic_resume.py`: an interrupted scheduled run
      whose automatic resume ends applied sets the failure count to zero; one whose row ends `no-resume` adds one;
      one whose automatic resume ends with a worker-reported failure adds one; a branch-review run gets
      `not-resumable` (spec US3 scenarios 1, 3 and 4, FR-018).

### Implementation for User Story 3

- [ ] T034 [US3] Change `_run_in_progress` (`_SELECT_RUN_WITH_OPEN_EXECUTION`) in
      `infrahub_sync/product_store/store.py` to also return a run of the configuration that has a row in
      `automatic_resumes` not in state `ended` (research R11).
- [ ] T035 [US3] Settle the schedule count later in `infrahub_sync/product_store/store.py`: `failed` when a row ends
      `no-resume` (in the transaction of `move_automatic_resume`), and, for a resume execution started by
      `AUTOMATIC_RESUME_ACTOR`, `succeeded` or `failed` from its verdict in the transaction that records its end,
      instead of `unchanged` (research R11).

**Checkpoint**: all user stories work on their own.

---

## Phase 6: Polish and governance

- [ ] T036 Amend Principle I in `.specify/memory/constitution.md`: add a bullet after the schedule case that names
      automatic resume, with the instruction (the interrupted run's own write and the site's setting), the target
      servers (those of the saved plan) and the approval (that of the interrupted run); bump the version to 1.3.0 and
      `Last Amended` to the date of the change (FR-020, research R13).
- [ ] T037 Write `dev/adr/0055-a-lost-worker-resumes-automatically-when-the-site-allows-it.md` (check that 0055 is
      still free on every branch first): context (ADR 0027, the liveness rule "never replayed"), decision (rule 6
      `crashed` and rule 8 only; the check; the limits; the reserved actor; exactly once through receipts),
      consequences, alternatives (research R1 to R5). Add it to `dev/adr/README.md`. No internal links.
- [ ] T038 Update the liveness docstring of `RunLivenessReconciler` in `infrahub_sync/service/liveness.py`: an
      execution is never replayed, except an automatic resume after rule 6 (`crashed`) or rule 8 when the site turned
      it on (ADR 0055).
- [ ] T039 [P] Write the section "Automatic resume" in `docs/docs/operations/day-2-operations.mdx`: the setting, when
      Sync resumes, each reason it does not, the limits, and how a person takes over.
- [ ] T040 [P] Document the new fields and events in `docs/docs/running-a-sync.mdx` and
      `docs/docs/reference/sync-http-api.mdx`, and the setting in `docs/docs/compose-deployment.mdx` and
      `docs/docs/kubernetes-deployment.mdx`.
- [ ] T041 [P] Add the changelog fragment `changelog/+automatic-resume.added.md` with
      `uv run --extra dev towncrier create -c "…" +automatic-resume.added.md`.
- [ ] T042 Add `infrahub-sync runs resume` to the list of CLI commands in `AGENTS.md` and mention automatic resume in
      it (spec 016 `EXTRACTED.md` noted the missing entry).
- [ ] T043 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`,
      the frontend checks in `frontend/` (`pnpm lint`, `pnpm test`), and the CLI sanity commands of `AGENTS.md`.
- [ ] T044 Run the preview stack check and the refusal cases of [quickstart.md](./quickstart.md) (SC-001, SC-002) and
      record the result in `dev/specs/030-automatic-resume/run-report.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependency.
- Phase 2 depends on Phase 1 and blocks every story.
- Phases 3, 4 and 5 depend on Phase 2. Phase 4's T031 and Phase 3's T025 and T026 touch the same files, so run Phase 3
  before Phase 4.
- Phase 6 depends on the stories it documents; T036 to T038 can start after Phase 2.

### User Story Dependencies

- US1 needs only Phase 2.
- US2 needs Phase 2; its output reuses the fields of T023.
- US3 needs Phase 2 only.

### Within Each User Story

Tests first, then store changes, then service, then CLI and web interface.

### Parallel Opportunities

- T002, T007, T011, T017 and T018 are test files with no shared file.
- In US1: T020, T021, T022 and T024.
- In US2: T027, T028 and T029.
- In US3: T032 and T033.
- In Phase 6: T039, T040 and T041.

## Parallel Example: User Story 1

```text
T020 presentation test   T021 presentation rule   T022 event types   T024 client models
```

## Implementation Strategy

### MVP First (User Story 1 Only)

Phases 1 and 2, then Phase 3, then T036 to T038 (the constitution and the ADR must ship with the first write that
nobody starts). Check with the preview stack.

### Incremental Delivery

US1, then US2 (the refusal reasons and the take-over), then US3 (schedules), then the documentation.

### Parallel Team Strategy

After Phase 2, one person on US1 and the web interface, one on US2, one on US3 and the governance tasks.

## Notes

- Commit with explicit paths; the main checkout is shared.
- The two open questions of the spec (the issue key, the limit of 3) do not block any task.
