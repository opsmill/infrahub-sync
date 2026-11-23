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
integration test proves "A holds, B and C wait, C names A" and the "not known" cases.

## Technical Context

**Language/Version**: Python 3.11–3.13 (Sync service profile; the guard module is service-only)

**Primary Dependencies**: psycopg 3 (guard session), the product store (`infrahub_sync/product_store/store.py`),
Typer CLI (`infrahub_sync/cli.py`), Pydantic client models (`infrahub_sync/client/models.py`)

**Storage**: PostgreSQL for the write lock; the run records are in the product store (SQLite or PostgreSQL). The
write lock always needs PostgreSQL (`service_guard_session`).

**Testing**: pytest. Unit tier with the scripted fake session in `tests/service/test_apply_guard.py`;
flow tests in `tests/service/test_managed_write_guard.py`; failure template tests in
`tests/service/test_failure_cause.py` and the shared secret-leak cases in `tests/service/failure_cases.py`; CLI
rendering in `tests/cli/test_failure_output.py`; opt-in real PostgreSQL tests in
`tests/integration/test_apply_guard_integration.py` (`-m integration`, `APPLY_GUARD_TEST_POSTGRESQL_DSN`).

**Target Platform**: The Sync service worker (Linux container) and the CLI.

**Project Type**: Single Python package with a service, a worker and a CLI.

**Performance Goals**: The holder lookup is one query, run only after the wait has already failed (SC-004).

**Constraints**: No secret, database address, account name or raw label may reach the failure record, a log line
or the CLI output. A lookup failure never replaces the contention error. The label must be set before the lock
request.

**Scale/Scope**: About six source files and six test files; one ADR decision; three documentation pages.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Read-only and dry-run by default | The lookup only reads PostgreSQL system views and the run records. It writes nothing. | Pass |
| II. Sync idempotency and safety | The lock is taken and released as before; a write still changes nothing when its wait fails. | Pass |
| III. Pattern consistency | Reuses the guard's existing key halves (`_lock_columns`), sanitized failures (`_guard_failure`), the fixed failure templates and the existing integration tier. | Pass |
| IV. Type safety and explicit contracts | The candidate run ID is a typed attribute of `ApplyGuardContentionError`; `active_run_id` keeps its client model type. | Pass |
| V. Test discipline | Unit tests for parsing and every "not known" case; a real PostgreSQL test for the three-session case (FR acceptance). | Pass |
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
infrahub_sync/product_store/store.py      # replace other_write_in_progress with run_write_in_progress(run_id)
infrahub_sync/cli.py                      # unchanged output line; check texts only
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

   Any exception from this query is swallowed (logged by type name only) and gives no candidate (FR-006).
3. **Parse the label (FR-003, FR-004).** `_holder_run_id(label)` returns a run ID only when the label is shorter
   than 63 bytes, starts with the exact prefix, and the rest matches the run ID pattern; otherwise `None`. The
   raw label never leaves this function (FR-005).
4. **Carry the candidate.** `ApplyGuardContentionError` gets `holder_run_id: str | None`, set by
   `_acquisition_failure`. Its message is unchanged.
5. **Confirm in the run records (FR-003, FR-007).** `_active_write_run` in `flow.py` reads
   `exc.holder_run_id`; rejects it when it equals the failing run's ID; calls `projection.lookup_run(candidate)`
   and accepts it only when the run exists and its `config_id` equals the failing run's `config_id`. It then
   calls `projection.run_write_in_progress(candidate)` to learn whether that run still has a claimed, open write.
   It returns `(run_id, ended)` or `(None, False)`. Any lookup exception gives `(None, False)`.
6. **Remove the guess (FR-008).** `other_write_in_progress` and its SQL are replaced by
   `run_write_in_progress(run_id) -> bool` (same WHERE clause restricted to one run), on the store protocol, the
   relational store and `ProductProjection`.
7. **Templates (FR-007, FR-009).** `_guard_contended_template` gets a third variant when the holder's write has
   ended: the cause names the run and says its write has ended but its session still held the lock; the next
   action says to ask the administrator of the Sync service to check that run's worker, then plan again. The
   "ended" flag is passed to `failure_record` as a keyword and is not stored as a new field (research R6).
8. **Documentation (FR-010, FR-011).** Correct `secret-redaction.md` and `apply-guard.md`; add the "ended" output
   example to `running-a-sync.mdx`; record the ADR 0021 decision.

## Complexity Tracking

No constitution violations.
