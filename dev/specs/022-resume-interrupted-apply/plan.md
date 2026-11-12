# Implementation Plan: Resume an interrupted apply

**Branch**: `022-resume-interrupted-apply` | **Date**: 2026-11-12 | **Spec**: [spec.md](./spec.md)

## Summary

Save a progress marker on the execution while a saved plan is applied, show it with per-kind counts, extend `verify`
on an interrupted run with a per-operation report for the operations after the marker, and add a `resume` stage that
applies the same saved plan from the marker in a new execution on the same run. A new decision record replaces ADR
0010's "an uncertain run is terminal" for writes proven safe to repeat. Design decisions are in
[research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11–3.13 (service profile); the CLI and client also run on 3.10.

**Primary Dependencies**: FastAPI, Pydantic, Typer, HTTPX, Prefect client, Infrahub SDK (no new dependency).

**Storage**: the product store on SQLite (tests, local) and PostgreSQL (service). One nullable JSON column on
`prefect_executions` (`apply_progress`) through the existing start-up column migration, and one new table
`resume_admissions`. No backfill.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`): `tests/test_potenda*.py`, `tests/service/`,
`tests/product_store/`, `tests/client/`, `tests/cli/`. PostgreSQL isolation test for the resume reservation. One
integration test against the preview stack that kills the worker during an apply (SC-001), marked opt-in.

**Target Platform**: Linux server (Sync API and worker); CLI on any platform.

**Project Type**: web service with a CLI client.

**Performance Goals**: marker saves add at most 2% to apply time on tiers M and L (SC-003); `verify` of an
interrupted tier L run under 1 minute (SC-004).

**Constraints**:

- The API stays `v3-unstable`; all response changes are additive.
- An apply that is never interrupted behaves as today apart from the marker saves; every existing test passes
  unchanged.
- A saved marker never runs ahead of a commit (FR-002).
- `verify` never reaches the write loop.
- No secret in markers, verify reports, audit events, errors or logs (`secret-redaction.md`): the verify report
  holds operation identifiers, kinds, actions and states only, never values.
- The vendored `opsmill_prefect_extras` stays byte-identical.

**Scale/Scope**: about 12 source files and 12 test files; 3 user documentation pages; 1 ADR.

## Constitution Check

| Principle | Status |
| --- | --- |
| I. Read-only and dry-run by default | Pass. `resume` is a write, needs `confirm_writes`, the checksum and a passed `verify`; `verify` stays read-only. |
| II. Sync idempotency and safety | Pass. Resume relies on convergent upserts (ADR 0012, 0013); repeating up to 500 operations is safe. Deletes are never executed. |
| III–V (connector consistency, typing, secrets) | Pass. No adapter contract change; new code typed; reports carry no values. |

No violation; Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
dev/specs/022-resume-interrupted-apply/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── cli.md
├── checklists/requirements.md
├── critiques/
├── alignment-check.md
└── tasks.md
```

### Source Code (repository root)

```text
infrahub_sync/
├── potenda/
│   ├── __init__.py              # apply_plan: progress observer, start_after, should_stop check
│   └── apply_progress.py        # NEW: ApplyProgressObserver protocol, ApplyProgressLostError
├── plan/
│   └── destination_check.py     # NEW: per-operation matches/differs/missing comparison
├── adapters/infrahub.py         # read nodes by id or HFID, grouped by kind, for the verify report
├── execution.py                 # pass observer and start_after through the shared execution surface
├── product_store/
│   ├── store.py                 # apply_progress column, resume_admissions table, reservation, clearing
│   └── models.py                # ApplyProgress, ResumeAdmission
├── service/
│   ├── apply_progress.py        # NEW: StoreApplyProgressObserver (throttle, failure policy, 5-minute stop)
│   ├── service.py               # resume_run, resumable-run checks, verify report wiring
│   ├── app.py                   # POST /runs/{run_id}/resume
│   └── flow.py                  # resume purpose, start_after from the latest marker
├── client/
│   ├── client.py                # resume()
│   └── models.py                # ResumeRunRequest, ApplyProgress, VerifyOperationReport
└── cli.py                       # runs resume; runs show progress and resumes; verify report lines

dev/adr/0026-an-interrupted-apply-resumes-from-its-saved-position.md   # NEW

docs/docs/
├── running-a-sync.mdx           # resume after an interruption
├── reference/sync-http-api.mdx  # resume route and new fields (generated where applicable)
└── development/knowledge/planned-write-and-apply.md   # marker and resume

tests/
├── test_potenda_apply_progress.py
├── product_store/test_resume_admissions.py
├── service/test_resume_run.py
├── service/test_apply_progress_observer.py
├── service/test_verify_destination_report.py
├── client/test_resume.py
└── cli/test_runs_resume.py
```

## Implementation notes

1. **Engine first** (R3, R4, R6): the observer protocol and `start_after` are pure engine changes, testable with
   the existing fake destination.
2. **Store** (R2, R5, R8): the column, the table, the conditional marker update, the resume reservation and the
   clearing on success. The reservation is one transaction on the locked run row; add a PostgreSQL isolation test
   with two concurrent resumes.
3. **Service and flow** (R1, R9, R10): `resume_run` reuses `apply_run`'s checks in the same order (ownership,
   non-secret parameters, `confirm_writes`, cancelled, approval, configuration, checksum, left-out), then the
   resumable-run check and the verify-after-interruption check, then the reservation.
4. **Verify report** (R7): runs only for a resumable run and only after every pre-write check passed.
5. **Surface**: client, CLI, docs, changelog fragment (`+resume-interrupted-apply.added.md`), ADR 0026.
6. **Benchmark** (SC-003, SC-004): run the existing benchmark runner on tiers M and L with the marker on; record
   the result in the implementation report.

## Complexity Tracking

None.
