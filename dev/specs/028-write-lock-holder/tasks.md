# Tasks: Name the run that holds a configuration's write lock

**Input**: Design documents from `dev/specs/028-write-lock-holder/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/contended-write-failure.md](contracts/contended-write-failure.md),
[quickstart.md](quickstart.md), [critique](critiques/critique-20261123.md)

**Tests**: Requested. The spec's acceptance needs a real PostgreSQL test that runs on every pull request (User
Stories 1 and 2, FR-013), unit tests for every "not known" case, and Vitest cases for the web interface link
(FR-014).

**Work directory**: `/Users/bkohler/automation/opsmill/infrahub-sync/.worktrees/028-write-lock-holder`, branch
`028-write-lock-holder` (built on `fix/write-guard-names-active-run`, commits `cf9f13b6` and `a50d5780`).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2 or US3 from spec.md

## Phase 1: Setup

- [X] T001 Run `uv sync --extra dev --extra prefect --extra service` in the work directory (a bare `uv run` created
  a base-only `.venv` there) and `pnpm install --frozen-lockfile` in `frontend/`. Confirm
  `uv run pytest tests/service/test_apply_guard.py tests/service/test_managed_write_guard.py -q` and
  `pnpm test` in `frontend/` pass before any change (baseline).

## Phase 2: Foundational (blocks every user story)

- [X] T002 In `infrahub_sync/service/apply_guard.py`, add the label constants and parser: `GUARD_LABEL_PREFIX =
  "infrahub-sync-guard:"`, `MAX_GUARD_LABEL_BYTES = 62`, a run ID pattern equal to
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (import it from `infrahub_sync/product_store/models.py` if it can be
  imported without a cycle, otherwise duplicate it with a comment naming the source), `guard_label(run_id) -> str |
  None` (returns the label only when the run ID matches the pattern and the encoded label is at most 62 bytes) and
  `_holder_run_id(label: object) -> str | None` (returns the run ID only when `label` is a `str`, its UTF-8 length
  is at most 62 bytes, it starts with the exact prefix, and the rest matches the pattern; the raw label never
  leaves this function).
- [X] T003 In `infrahub_sync/service/apply_guard.py`, add `holder_run_id: str | None = None` to
  `ApplyGuardContentionError` (keep its message `_CONTENDED` unchanged) and let `_guard_failure` callers set it.
- [X] T004 [P] Unit tests for T002 in `tests/service/test_apply_guard.py`: exact prefix and a generated run ID
  (`20261123T1012-3fa1b2c4`) give the run ID; wrong prefix, empty, `None`, a non-`str`, a run ID with a space or
  `=`, and a 63-byte label give `None`; `guard_label` returns `None` for a run ID of 43 or more characters.

## Phase 3: User Story 1 — an operator sees which run blocked their write (P1) 🎯 MVP

**Goal**: A contended `sync`, `apply` or `runs resume` names the run whose session holds the lock, in the CLI and
in the web interface, and the real PostgreSQL test runs on every pull request.

**Independent test**: real PostgreSQL, A holds, B and C wait, C names A (T012), run by the pull-request CI job
(T016); the web failure notice links to A (T019).

### Service

- [X] T005 [US1] In `infrahub_sync/service/apply_guard.py`, add the keyword `run_id: str | None = None` to
  `hold_apply_guard` and `_acquire`. In `_acquire`, right after `connect()` and before `_SET_DEADLINE`, when
  `guard_label(run_id)` is not `None`, run `SELECT set_config('application_name', %s, false)` with the label. A
  failure here is classified by the existing `except Exception` branch as `ApplyGuardUnavailableError` and happens
  before the lock request (critique E4).
- [X] T006 [US1] In `infrahub_sync/service/apply_guard.py`, add `_HOLDER_TIMEOUT = "SELECT
  set_config('statement_timeout', %s, false)"` with value `"2000"` and the `_HOLDER` query from plan.md Design
  step 2 with `LIMIT 2`, filtered on `locktype = 'advisory'`, `granted`, the current database's OID, the
  `_lock_columns(key)` halves, `objsubid = 1` and `pid <> pg_backend_pid()`. Add `_read_holder(connection, key) ->
  str | None`: set the timeout, run the query, return `_holder_run_id(row[0])` only when exactly one row is
  returned; catch every `Exception`, log at debug level only the outcome class (`named`, `no-row`,
  `several-rows`, `label-rejected`, `lookup-failed`), never the label, and return `None`.
- [X] T007 [US1] In `_acquire` / `_acquisition_failure` in `infrahub_sync/service/apply_guard.py`, when the
  failure is a lock timeout (`55P03`), call `_read_holder` on the same connection before `_discard` and set
  `holder_run_id` on the `ApplyGuardContentionError`. The contention error is raised whatever `_read_holder`
  returns.
- [X] T008 [US1] In `infrahub_sync/service/flow.py`, change `_configuration_write_guard(configuration_id)` to
  `_configuration_write_guard(configuration_id, run_id)` and pass `run_id` at both call sites in `_execute_stage`
  (`flow.py:1254` apply, `flow.py:1311` sync) and on to `hold_apply_guard(..., run_id=run_id)`.
- [X] T009 [US1] In `infrahub_sync/product_store/store.py`, replace `other_write_in_progress(config_id, *,
  run_id) -> str | None` with `write_holder_state(run_id: str, config_id: str) -> HolderState | None` on the store
  protocol (~line 1221), the relational store (~line 4432) and `ProductProjection` (~line 5817). One query: it
  returns `None` when the run does not exist or its `config_id` differs; otherwise a `HolderState` (a frozen
  dataclass with `writing: bool`) where `writing` applies the open-write rule of
  `_SELECT_OTHER_RUN_WITH_OPEN_WRITE` ("an execution whose purpose is in `WRITE_PURPOSES`, that a worker claimed
  and that has no terminal verdict") to that one run. Rename the SQL constant accordingly (SC-004: one read of the
  run records).
- [X] T010 [US1] In `infrahub_sync/service/flow.py`, rewrite `_active_write_run(projection, stored, exc)` to
  return `tuple[str | None, bool]` (run ID, ended): read `exc.holder_run_id` only for `ApplyGuardContentionError`;
  reject it when it equals `stored.run_id`; call `projection.write_holder_state(candidate, stored.config_id)` once;
  accept the candidate only when it returns a state; `ended = not state.writing`. Any exception gives
  `(None, False)` and logs only the exception's type name. Update the caller at `flow.py:1761`.

### Service tests

- [X] T011 [US1] Update `tests/service/test_apply_guard.py`: every existing scripted acquisition now expects the
  `set_config('application_name', ...)` statement first when a run ID is passed; update each script deliberately
  (critique E6). Add: the label statement is sent before `_SET_DEADLINE` and `_ACQUIRE`; a `55P03` on `_ACQUIRE`
  runs `_HOLDER_TIMEOUT` then `_HOLDER` before the session is closed; one row with a valid label sets
  `holder_run_id`; a write that takes the lock runs neither `_HOLDER_TIMEOUT` nor `_HOLDER` (SC-004).
- [X] T012 [US1] In `tests/integration/test_apply_guard_integration.py` (`-m integration`,
  `APPLY_GUARD_TEST_POSTGRESQL_DSN`), add the three-session test: run A (a separate OS process, as the existing
  holder test does) holds the guard with `run_id="20261123T1012-aaaaaaaa"`; runs B and C wait with
  `deadline_seconds` of 5 and 1; C's `ApplyGuardContentionError.holder_run_id` equals A's run ID and never B's.
  Repeat 20 times (SC-001).
- [X] T013 [P] [US1] Update `tests/service/test_managed_write_guard.py`: replace the `other_write_in_progress`
  cases with: a contention error carrying a candidate of the same configuration names it for `apply` and `sync`;
  `_configuration_write_guard` receives the stage's run ID (critique E1); `write_holder_state` is called exactly
  once per contended failure.
- [X] T014 [P] [US1] Update `tests/product_store/test_write_admission.py`: replace the two
  `other_write_in_progress` tests with `write_holder_state` tests (unknown run → `None`; run of another
  configuration → `None`; open claimed write → `writing=True`; terminal or unclaimed write → `writing=False`), on
  SQLite and on PostgreSQL when `PRODUCT_STORE_TEST_POSTGRESQL_DSN` is set.

### Pull-request CI (FR-013)

- [X] T015 [P] [US1] In `tasks/tests.py`, extend `tests_product_store_postgresql`: refuse to run when
  `APPLY_GUARD_TEST_POSTGRESQL_DSN` is unset (same message style as the existing DSN check), add
  `tests/integration/test_apply_guard_integration.py` to the pytest selection, and update its docstring. The task
  still fails on any skip, an unset DSN or an empty selection.
- [X] T016 [US1] In `.github/workflows/workflow-tests.yml`, job `product-store-postgresql`, set
  `APPLY_GUARD_TEST_POSTGRESQL_DSN` to the same value as `PRODUCT_STORE_TEST_POSTGRESQL_DSN`, and update the job's
  comment to say it also runs the write-lock integration tests. Keep the job name, the trigger
  (`needs.files-changed.outputs.sync`) and the service container unchanged. Leave `.github/scripts/nightly_e2e.py`
  as it is (research R10). Depends on T015.

### Web interface (FR-014)

- [X] T017 [P] [US1] Make the web interface's API types know `active_run_id` on the stage failure: regenerate
  `frontend/src/api/types.generated.ts` with `pnpm codegen:openapi` against a running Sync API of this branch; if
  no API can be started, add the field by hand exactly as the OpenAPI schema of `StageFailureResource` in
  `infrahub_sync/client/models.py` declares it (`string | null`), with a comment that the next regeneration
  replaces it.
  Outcome: no change to `types.generated.ts`. The Sync API's OpenAPI schema types a run's `results` as
  `dict[str, Any]` and no route returns `StageFailureResource`, so a regeneration from this branch's app is
  identical to the committed file and has no `active_run_id`. `failureDetail` reads the failure record as loose
  JSON, so it needs no generated type.
- [X] T018 [US1] In `frontend/src/domain/run-state.ts`, add `activeRunId?: string` to `FailureDetail` and set it
  in `failureDetail` only when the failure record's `active_run_id` is a non-empty string. Depends on T017.
- [X] T019 [US1] In `frontend/src/features/runs/run-notices.tsx`, in the failure notice, show the existing
  "Open run" link (`<Link to="/runs/$runId" params={{ runId: f.activeRunId }} className={OPEN_RUN}>`, as in the
  notice that already names another run) when `f.activeRunId` is set. Add no web-only wording; the stored cause and
  next action carry the "not known" and "has ended" cases (contracts/contended-write-failure.md, "Web interface").
  Depends on T018.
- [X] T020 [P] [US1] Add `frontend/src/domain/run-state.test.ts` (Vitest): `failureDetail` sets `activeRunId` for a
  failure record with `active_run_id: "20261123T1012-aaaaaaaa"`, and leaves it unset when the field is `null`,
  absent, an empty string or a number. Depends on T018.

## Phase 4: User Story 2 — the run is not known when it cannot be proven (P1)

**Goal**: Every case where the holder cannot be proven records `active_run_id = null` and the "not known" text; the
web interface then shows no link.

**Independent test**: real PostgreSQL, a holder without a label gives "not known" (T024).

- [X] T021 [US2] In `tests/service/test_apply_guard.py`, add unit cases where `_read_holder` returns `None` and
  the contention error is still raised unchanged: no row, two rows, a label with another prefix, a 63-byte label,
  the timeout statement raising, the query raising (FR-006, FR-006a).
- [X] T022 [US2] In `tests/service/test_managed_write_guard.py`, add flow cases that store `write_guard_contended`
  with `active_run_id` `None`: candidate equals the failing run; `write_holder_state` returns `None` (unknown run,
  or a run of another configuration); `write_holder_state` raises.
- [X] T023 [P] [US2] In `tests/service/failure_cases.py`, extend the shared contended case so the secret-leak
  checks run with a candidate that is confirmed, one that is rejected, and a lookup that raised (SC-003).
- [X] T024 [US2] In `tests/integration/test_apply_guard_integration.py`, add: a holder without a label → `None`; a
  holder whose label is set by hand to 63 bytes → `None`; a holder that releases the lock between the waiter's
  timeout and its lookup → `None` (best effort: assert never a wrong run ID); a holder connected with a second,
  non-superuser account created by the test, and a waiter connected with a third account → `None` or A's run ID,
  never another value, and print the server's `track_activities` setting with the result (critique E7). These
  tests run in the pull-request job through T015 and T016.

## Phase 5: User Story 3 — the named run has already ended (P2)

**Goal**: A holder whose write has ended is named, and the operator is not told to wait for it, in the CLI and in
the web interface.

**Independent test**: unit test with a confirmed candidate whose `write_holder_state` has `writing=False` (T026).

- [X] T025 [US3] In `infrahub_sync/service/failure_cause.py`, add a keyword `active_run_ended: bool = False` to
  `failure_record` (not stored as a field) and a third variant of `_guard_contended_template` with the cause and
  next action from `contracts/contended-write-failure.md` ("held this configuration's write guard for the whole
  wait although its write has ended"; "The guard is released only when the worker of run `<id>` stops. Ask the
  administrator of the Sync service to stop or restart that worker, then plan again."). For a `runs resume`
  failure, keep the existing verify-then-resume ending. Pass the flag from `_failure_evidence` in
  `infrahub_sync/service/flow.py`.
- [X] T026 [P] [US3] In `tests/service/test_failure_cause.py`, add tests for the three template variants and the
  resume ending of the "ended" variant.
- [X] T027 [P] [US3] In `tests/cli/test_failure_output.py`, add a rendering test of the "ended" variant: the CLI
  prints `active run: <id>`, the cause and the next action, with no "wait for it to finish".

## Phase 6: Polish and cross-cutting

- [ ] T028 [P] Amend `dev/adr/0021-a-failure-record-holds-a-structured-cause.md` with a dated section
  "Amendment, 2026-11-23" (follow the format of `dev/adr/0029-the-development-stack-is-one-compose-file-with-profiles.md`):
  the fixed list gains `write_guard_contended` (18 names); the allowed fields gain `active_run_id`, a run ID read
  from the label of the session that holds the configuration's write lock and confirmed in the run records; the
  accepted risk that a session of the same database account can set a label (research R5, R7). Do not cite
  internal sources.
- [ ] T029 [P] Correct `docs/docs/development/guidelines/secret-redaction.md`: replace the line added by
  `cf9f13b6` ("the ID of the run the product store records as writing to the same configuration") with the new
  source of the run ID; state that the guard session's label is read, that PostgreSQL shows a session's full
  details only to the same account, a superuser or `pg_read_all_stats`, and that only the run ID is stored.
- [ ] T030 [P] Update `docs/docs/development/knowledge/apply-guard.md`: the session label, the holder lookup (at
  most two reads after a failed wait, 2-second limit, exactly one row) and the removal of the run-record guess.
- [ ] T031 [P] Update `docs/docs/running-a-sync.mdx` with the "ended" output example and the web interface's
  "Open run" link, and `docs/docs/reference/sync-http-api.mdx` if the description of `active_run_id` mentions the
  run records.
- [ ] T032 [P] Update `docs/docs/development/guidelines/testing-tiers.md`: the "Product store on PostgreSQL"
  pull-request job also runs the write-lock integration tests and needs `APPLY_GUARD_TEST_POSTGRESQL_DSN`.
- [ ] T033 [P] Reword `changelog/+write-guard-names-active-run.changed.md`: the failure names the run whose session
  holds the configuration's write lock, or says it is not known, in the CLI and in the web interface.
- [ ] T034 Run the gates in the work directory: `uv run invoke format`, `uv run invoke lint`,
  `uv run ruff check .`, `uv run invoke tests.tests-unit` (large output through `ctx_execute` with `| tail -80`),
  the CLI help checks from AGENTS.md, `pnpm check`, `pnpm typecheck`, `pnpm test` and `pnpm build` in `frontend/`,
  then `uv run invoke tests.tests-product-store-postgresql` against a disposable PostgreSQL 16 server as in
  [quickstart.md](quickstart.md). Record the counts and the server's `track_activities` value in the commit
  message body.

## Dependencies and execution order

- Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) and Phase 5 (US3) → Phase 6.
- T005–T007 depend on T002–T003. T010 depends on T009. T012 and T024 depend on T005–T007.
- T016 depends on T015. T018 depends on T017; T019 and T020 depend on T018.
- US2 depends on US1's code (it tests the "not known" paths of the same lookup). US3 depends on T010 (the
  `ended` flag).

## Parallel examples

- Phase 2: T004 alongside T003 once T002 is written.
- US1: T013 and T014 in parallel after T009–T010; T015 and T017 in parallel with the service tasks (different
  files); T019 and T020 in parallel after T018.
- US3: T026 and T027 in parallel after T025.
- Phase 6: T028–T033 in parallel.

## Implementation strategy

- MVP: Phases 1–3 (US1), proved by T012 on real PostgreSQL in the pull-request job, and by the web link.
- Then US2 (the "not known" cases, which the spec treats as P1 too), then US3, then documentation and gates.
- Commit after each phase with explicit paths; messages end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`.
