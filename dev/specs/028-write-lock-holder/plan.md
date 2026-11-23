# Implementation Plan: Name the run that holds a configuration's write lock

**Branch**: `028-write-lock-holder` | **Date**: 2026-11-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/028-write-lock-holder/spec.md`

## Summary

A write of a run labels its write-lock PostgreSQL session with `infrahub-sync-guard:<run_id>` before it requests
the configuration's advisory lock. When the lock wait times out (`55P03`), the same session reads the label of the
session that holds that lock (`pg_locks` joined to `pg_stat_activity`, current database, same key halves as
`_OWNERSHIP`), before the session is discarded. The guard module accepts only a label with the exact prefix, a
run-ID-shaped value and a length that cannot have been cut short, and attaches the candidate run ID to
`ApplyGuardContentionError`. The service flow confirms the candidate in the run records (the run exists and
belongs to the same configuration), checks whether it still has an open write, and stores only the run ID in
`active_run_id`. The guess from the run records (`other_write_in_progress`, added by `cf9f13b6`/`a50d5780`) is
removed. The failure template gets a third variant for a holder whose write has ended. A real PostgreSQL
integration test proves "A holds, B and C wait, C names A" and the "not known" cases. It runs on every pull
request in the existing "Product store on PostgreSQL" CI job, which already starts a PostgreSQL service
container, and stays in the nightly job (FR-013). The web interface's failure notice links to the named run with
the same "Open run" link the run page already uses for other runs (FR-014).

## Technical Context

**Language/Version**: Python 3.11–3.13 (Sync service profile; the guard module is service-only)

**Primary Dependencies**: psycopg 3 (guard session), the product store (`infrahub_sync/product_store/store.py`),
Typer CLI (`infrahub_sync/cli.py`), Pydantic client models (`infrahub_sync/client/models.py`); the web
interface (React and TanStack Router, `frontend/`); GitHub Actions (`.github/workflows/workflow-tests.yml`)

**Storage**: PostgreSQL for the write lock; the run records are in the product store (SQLite or PostgreSQL). The
write lock always needs PostgreSQL (`service_guard_session`).

**Testing**: pytest. Unit tier with the scripted fake session in `tests/service/test_apply_guard.py`;
flow tests in `tests/service/test_managed_write_guard.py`; failure template tests in
`tests/service/test_failure_cause.py` and the shared secret-leak cases in `tests/service/failure_cases.py`; CLI
rendering in `tests/cli/test_failure_output.py`; real PostgreSQL tests in
`tests/integration/test_apply_guard_integration.py` (`-m integration`, `APPLY_GUARD_TEST_POSTGRESQL_DSN`), run
by `invoke tests.tests-product-store-postgresql`, which fails on any skip, an unset DSN or an empty selection;
web interface tests with Vitest (`frontend/src/domain/*.test.ts`, `pnpm test`).

**Target Platform**: The Sync service worker (Linux container) and the CLI.

**Project Type**: Single Python package with a service, a worker and a CLI.

**Performance Goals**: At most two reads, run only after the wait has already failed: one on the lock session
(holder and label) and one of the run records (`write_holder_state`, a single
query, see Design step 6). Nothing is added to a write that takes the lock (SC-004).

**Constraints**: No secret, database address, account name or raw label may reach the failure record, a log line
or the CLI output. A lookup failure never replaces the contention error. The label must be set before the lock
request.

**Scale/Scope**: About six source files and six test files; two web interface files and one web test; one CI
workflow and one invoke task; one ADR decision; four documentation pages.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Read-only and dry-run by default | The lookup only reads PostgreSQL system views and the run records. It writes nothing. | Pass |
| II. Sync idempotency and safety | The lock is taken and released as before; a write still changes nothing when its wait fails. | Pass |
| III. Pattern consistency | Reuses the guard's existing key halves (`_lock_columns`), sanitized failures (`_guard_failure`), the fixed failure templates and the existing integration tier. | Pass |
| IV. Type safety and explicit contracts | The candidate run ID is a typed attribute of `ApplyGuardContentionError`; `active_run_id` keeps its client model type. | Pass |
| V. Test discipline | Unit tests for parsing and every "not known" case; a real PostgreSQL test for the three-session case, run on every pull request (FR-013); Vitest cases for the web link. | Pass |
| VI. Security, secrets, and input boundaries | The label is untrusted input: exact prefix, run ID pattern, length limit, then confirmation in the run records. Only the run ID is stored. The secret-redaction guideline is corrected. | Pass |
| VII. Simplicity and maintainability | No new table, no migration; the earlier guess is removed rather than kept as a fallback. | Pass |

Post-design re-check: unchanged, all pass.

## Project Structure

### Documentation (this feature)

```text
dev/specs/028-write-lock-holder/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── contended-write-failure.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (speckit-tasks)
```

### Source Code (repository root)

```text
infrahub_sync/service/apply_guard.py      # label the session; read and parse the holder's label on 55P03
infrahub_sync/service/flow.py             # pass run_id to the guard; confirm the candidate in the run records
infrahub_sync/service/failure_cause.py    # third template variant: holder whose write has ended
infrahub_sync/product_store/store.py      # replace other_write_in_progress with write_holder_state(run_id, config_id)
infrahub_sync/cli.py                      # unchanged output line; check texts only
frontend/src/domain/run-state.ts          # FailureDetail.activeRunId read from the failure record
frontend/src/features/runs/run-notices.tsx  # "Open run" link to the named run in the failure notice
frontend/src/domain/run-state.test.ts     # new Vitest cases for activeRunId
.github/workflows/workflow-tests.yml      # set APPLY_GUARD_TEST_POSTGRESQL_DSN in the PostgreSQL job
tasks/tests.py                            # add the write-lock integration tests to tests-product-store-postgresql
docs/docs/development/guidelines/testing-tiers.md
docs/docs/development/knowledge/apply-guard.md
docs/docs/development/guidelines/secret-redaction.md
docs/docs/running-a-sync.mdx
dev/adr/0021-a-failure-record-holds-a-structured-cause.md   # decision recorded in research R7
changelog/+write-guard-names-active-run.changed.md          # reworded

tests/service/test_apply_guard.py
tests/service/test_managed_write_guard.py
tests/service/test_failure_cause.py
tests/service/failure_cases.py
tests/product_store/test_write_admission.py
tests/cli/test_failure_output.py
tests/integration/test_apply_guard_integration.py
```

**Structure Decision**: The label is written and read inside `apply_guard.py`, the only module that owns the
guard session (ADR 0010: the session is never handed to an adapter or to the product store). The run-record
confirmation stays in `flow.py`, which already owns the run records at failure time. The guard module never
imports the product store.

## Design

1. **Label the session (FR-001).** `hold_apply_guard` takes a new keyword `run_id: str | None`. In `_acquire`,
   right after `connect()` and before `_SET_DEADLINE`, it runs
   `SELECT set_config('application_name', %s, false)` with `infrahub-sync-guard:<run_id>`, only when the full
   label is at most 62 bytes and the run ID matches the run ID pattern (research R2). A failure to set the label
   is classified like any other acquisition failure (`ApplyGuardUnavailableError`), because it happens before
   the lock request and must not leave an unlabelled holder behind silently.
2. **Read the holder (FR-002).** In `_acquire`, when the failure is a lock timeout (`55P03`), the same connection
   runs `_HOLDER` before `_discard`:

   ```sql
   SELECT a.application_name
   FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
   WHERE l.locktype = 'advisory' AND l.granted
     AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
     AND l.classid::bigint = %s AND l.objid::bigint = %s AND l.objsubid = 1
     AND l.pid <> pg_backend_pid()
   ```

   Before the query, the session sets `statement_timeout` to 2000 ms with `set_config` (as `_SET_DEADLINE` does);
   a timeout gives no candidate (FR-006, critique E2). The query reads at most two rows (`LIMIT 2`); a candidate
   is taken only when exactly one row is returned (FR-006a, critique E3). Any exception from these statements is
   swallowed and gives no candidate (FR-006). A debug log line records only the outcome class (named, no row,
   several rows, label rejected, lookup failed), never the label (critique E8).

   A failure of the label statement in step 1 happens before the lock request and fails the write as
   `ApplyGuardUnavailableError`, like any other acquisition failure (critique E4). `_configuration_write_guard`
   always passes the run ID; only the existing integration tests call `hold_apply_guard` without it (critique E1).
3. **Parse the label (FR-003, FR-004).** `_holder_run_id(label)` returns a run ID only when the label is shorter
   than 63 bytes, starts with the exact prefix, and the rest matches the run ID pattern; otherwise `None`. The
   raw label never leaves this function (FR-005).
4. **Carry the candidate.** `ApplyGuardContentionError` gets `holder_run_id: str | None`, set by
   `_acquisition_failure`. Its message is unchanged.
5. **Confirm in the run records (FR-003, FR-007).** `_active_write_run` in `flow.py` reads
   `exc.holder_run_id`; rejects it when it equals the failing run's ID; calls
   `projection.write_holder_state(candidate, config_id)` (step 6) and accepts it only when the run exists and its
   `config_id` equals the failing run's `config_id`. The same call tells whether that run still has a claimed, open write. It returns `(run_id, ended)` or `(None, False)`. Any
   lookup exception gives `(None, False)`.
   Note (2026-11-23, review): `HolderState` also carries `write_ended_at`, and the run counts as ended only when the run records have no claimed, open write for the run and its latest claimed write ended (`HolderState.write_ended_at`) before this write started waiting (the lock timeout minus the deadline the guard used, stamped as `ApplyGuardContentionError.wait_started_at`). A holder that released the lock and recorded its verdict during the wait gets the "writing" variant. The causes say which run held the guard when the wait ended, not "for the whole wait".
6. **Remove the guess (FR-008), one run-record read (SC-004).** `other_write_in_progress` and its SQL are
   replaced by `write_holder_state(run_id, config_id) -> HolderState | None` on the store protocol, the relational
   store and `ProductProjection`. One query returns `None` when the run does not exist or belongs to another
   configuration, otherwise whether the run still has a claimed, open write (same WHERE clause as before,
   restricted to one run). Step 5 uses this one call instead of `lookup_run` plus a second query.
7. **Templates (FR-007, FR-009).** `_guard_contended_template` gets a third variant when the holder's write has
   ended: the cause names the run and says its write has ended but its session still held the lock; the next
   action says the lock is released only when that run's worker stops, so the administrator of the Sync service
   must stop or restart that worker, then plan again (critique P1). The
   "ended" flag is passed to `failure_record` as a keyword and is not stored as a new field (research R6).
8. **Documentation (FR-010, FR-011).** Correct `secret-redaction.md` and `apply-guard.md`, including the
   accepted risk that a session of the same account can set a label (critique E5); add the "ended" output example
   to `running-a-sync.mdx`; amend ADR 0021 (research R7).
9. **Tests.** The scripted fake session's expected statement order changes in every existing acquisition test of
   `tests/service/test_apply_guard.py`; update them deliberately (critique E6). The integration test repeats the
   three-session case 20 times (SC-001) and prints the server's `track_activities` setting with any "not known"
   result (critique E7).
10. **Web interface (FR-014).** `failureDetail` in `run-state.ts` reads `active_run_id` from the failure record
    into `FailureDetail.activeRunId` (only when it is a non-empty string). The failure notice in
    `run-notices.tsx` adds the existing "Open run" link (`<Link to="/runs/$runId">`, as in the notice that already
    names another run) when `activeRunId` is set. The cause and next action are the texts the service stores, so
    the "not known" and "has ended" cases need no web-only wording. The web interface never shows a raw label;
    it only receives the run ID. Vitest cases cover a named run, an absent field and a non-string value. This is a
    link inside an existing notice, not a new screen, so no `emil-prototype` round is planned (research R11).
11. **Pull-request CI (FR-013).** The "Product store on PostgreSQL" job in `workflow-tests.yml` already runs on
    every pull request that changes Sync files, with a PostgreSQL 16 service container. It also sets
    `APPLY_GUARD_TEST_POSTGRESQL_DSN` to the same server, and `tests-product-store-postgresql` adds
    `tests/integration/test_apply_guard_integration.py` to its selection, so a skip fails the job. The job keeps
    its name and the nightly job keeps its own run (research R10).

## Complexity Tracking

No constitution violations.
