# Implementation report: Resume an interrupted apply

**Status**: INCOMPLETE. All code tasks are done. Two tasks need infrastructure (T034, T035), and the Phase 6 review
did not run (see "Review findings").

- **Spec directory**: `dev/specs/022-resume-interrupted-apply/`
- **Branch**: `022-resume-interrupted-apply`, stacked on `020-plan-problem-reporting` (PR #409)
- **Base commit**: `5f005776`
- **Head commit**: `2cc75957`
- **Change**: 11 commits, 53 files, 6,554 insertions, 196 deletions
- **Unit tier**: 7,569 passed at baseline, 7,739 passed at head (4 skipped, 3 xfailed at both)

## Chunk ledger

| Chunk | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged upward |
| --- | --- | --- | --- | --- |
| A. Engine progress marker | T001–T005 (5) | 5 / 0 / 0 | `67422c7a`, `d06ea24c` | `execute_run` takes private `_apply_progress` and `_start_after`. A raising observer is only logged. `start_after` outside the plan raises `ValueError` before any write. The fake engine in `tests/plan/test_destination_binding.py` was changed. |
| B. Marker in store and service, `verify` admission | T006–T012 (7) | 7 / 0 / 0 | `477210ba` | The refusal for `verify` sat in `_reservation_refused` and also in `_require_append_owner`; both got the exception. A save that returns `False` counts as failed. 12 existing test fakes changed. The commit message has no blank line after the subject. |
| C. Resume admission in the store, models | T013–T016 (4) | 3 / 1 / 0 | `ce4a1853` | Clearing `reconciliation_required` happens in `_settle_resume` (from `commit_claimed_execution`), because `_terminalize_execution` never writes `succeeded`. Any cancelled or abandoned execution on a run that needs reconciliation returns it to interrupted. T015: the PostgreSQL concurrency test did not run (no test database). |
| D. Resume through service, flow, API, client, CLI | T017–T023 (7) | 7 / 0 / 0 | `17288ddb`, `d8690f18` | Positions are 0-based. A resume's own counts cover only the operations it sent. New event type `infrahub_sync.run.resumed`. A failed resume ends in phase `resume-failed`. After a successful resume, the run summary holds only the resume's counts. `GET /runs/{id}` reads the whole saved plan for a run that has written. |
| E. Per-operation `verify` report (US2) | T024–T028 (5) | 5 / 0 / 0 | `2af6d000`, `fc7feaac` | The destination filters (`__values`, `<rel>__ids`, `include`) were tested only against the fake client. SC-004 was not measured. When the destination read fails, the next action says "then plan again", which is wrong for `verify`. |
| F. Resume of `sync` runs (US3) | T029–T030 (2) | 2 / 0 / 0 | `5b717709` | `resume_run` now refuses a run with `reconciliation_required=false` before reading the plan. A `sync` killed during its plan part is marked as needing reconciliation and still returns 503 `plan-unavailable` on resume. |
| G. ADR, docs, changelog, checks | T031–T036 (6) | 4 / 2 / 0 | `a4c4a0ed`, `2cc75957` | ADR 0026 added; ADR 0010 marked partly superseded. Five more docs pages corrected that called an interrupted run terminal. The HTTP reference is written by hand. `docs.docusaurus` not run, so new page links are unchecked. |

## Tasks not completed

- **T015 (partial)**: all 18 SQLite cases pass. The 27 PostgreSQL cases, including the concurrent-resume isolation
  test, skip without `PRODUCT_STORE_TEST_POSTGRESQL_DSN`.
- **T034**: the integration test `tests/preview/test_resume_interrupted_apply.py` is written and skips cleanly. It
  was not run, because no preview stack was running and the chunk was told not to start one. Run it with:
  `INFRAHUB_SYNC_RESUME_CHECK_CONFIG="<config_id>@<version>" INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN="<token>" uv run pytest -m integration tests/preview/test_resume_interrupted_apply.py`
  (add `INFRAHUB_SYNC_SMOKE_STACK=dev` for the dev stack).
- **T035**: the SC-003 and SC-004 benchmarks were deferred; they need the tier M and L datasets and take hours. The
  runner commands are under "Phase 6 results" in `tasks.md`. Nothing turns the marker off, so the baseline must run
  at commit `5f005776`. The runner does not time `verify`; SC-004 is a manual step on tier L.

## Local-pass evidence

Rows are grouped by test file. The verbose per-test PASSED lines are in the chunk logs in the session scratchpad.

| Test id | Type | Run command | Passed at (UTC) | Environment | Verbatim pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/test_potenda_apply_progress.py` (11 tests) | unit | `uv run pytest tests/test_potenda_apply_progress.py tests/plan/test_destination_binding.py -v` | 2026-11-12T22:42:58Z | Python 3.11.8, macOS | `test_start_after_dispatches_only_the_operations_after_that_position PASSED` (39 passed) |
| `tests/plan/test_destination_binding.py` (changed fake) | unit | same | 2026-11-12T22:42:58Z | same | `test_a_matching_destination_applies_without_ceremony PASSED` |
| `tests/product_store/test_apply_progress.py`, `tests/service/test_apply_progress_observer.py` | unit | `uv run pytest tests/product_store/test_apply_progress.py tests/service/test_apply_progress_observer.py … -v -p no:randomly` | 2026-11-12T22:59:49Z | SQLite | `43 passed, 9 skipped in 1.67s` |
| `tests/product_store/test_resume_admissions.py` (SQLite cases) | unit | `uv run pytest tests/product_store/test_resume_admissions.py -v -p no:cacheprovider` | 2026-11-12T23:24:05Z | SQLite, no PostgreSQL DSN | `test_exactly_one_of_two_concurrent_resumes_is_admitted[sqlite] PASSED` (28 passed) |
| `tests/product_store/test_resume_admissions.py` (PostgreSQL cases) | integration | same, with `PRODUCT_STORE_TEST_POSTGRESQL_DSN` set | MISSING — see chunk C | PostgreSQL required | 27 skipped |
| `tests/service/test_flow_and_prefect.py`, `tests/runtime_schema/test_worker_path.py`, `tests/service/test_read_progress.py`, `tests/product_store/test_contract.py` (changed) | unit | `uv run invoke tests.tests-unit` | 2026-11-12T23:08:59Z | SQLite | `7621 passed, 4 skipped` |
| `tests/service/test_resume_run.py`, `tests/client/test_resume.py`, `tests/cli/test_runs_resume.py`, two tests in `tests/test_execution_surface.py`, `tests/service/test_http_api.py::test_confirmation_schema_errors_and_openapi_contract` | unit | `uv run pytest -v -p no:cacheprovider tests/service/test_resume_run.py tests/client/test_resume.py tests/cli/test_runs_resume.py …` | 2026-11-13 (chunk D) | Python 3.11.8, macOS | `48 passed` |
| `tests/test_destination_check.py`, `tests/service/test_verify_destination_report.py`, two tests in `tests/cli/test_client_commands.py` | unit | `uv run pytest -v -p no:cacheprovider tests/test_destination_check.py tests/service/test_verify_destination_report.py …` | 2026-11-13T00:30:26Z | Python 3.11.8, macOS | `48 passed` |
| `tests/service/test_resume_run.py` (sync-run cases) | unit | `uv run pytest tests/service/test_resume_run.py -v -p no:cacheprovider` | 2026-11-13T00:46Z | Python 3.11.8, macOS | `test_an_interrupted_sync_run_resumes_from_its_marker_with_the_checksum_of_its_saved_plan PASSED` (31 passed) |
| `tests/preview/test_resume_interrupted_apply.py::test_a_killed_apply_resumes_to_the_state_of_an_uninterrupted_apply` | e2e | see T034 above | deferred — local E2E not supported | needs the preview stack | `SKIPPED (preview environment not running)` |
| Full unit tier | unit | `uv run invoke tests.tests-unit` | 2026-11-13T00:57:46Z | Python 3.11.8, macOS | `7739 passed, 4 skipped, 339 deselected, 3 xfailed` |

## Review findings

The Phase 6 review (`speckit-review-run`: code, errors, tests, types, comments) **did not run**. Auto mode could not
check the review agents, because the context-mode plugin changes each agent's input after the check. The call was
refused twice and not retried.

Items the chunks flagged that a review should look at first:

| Severity (orchestrator's estimate) | File | Summary |
| --- | --- | --- |
| Medium | `infrahub_sync/service/resume_view.py` | `GET /runs/{id}` reads the whole saved plan on every request for a run that has written; slow on tier L plans. |
| Medium | `infrahub_sync/service/flow.py` | After a resume, the run summary holds only the resume's counts, not the whole plan's. |
| Medium | `infrahub_sync/service/flow.py` | A `sync` killed during its plan part is marked as needing reconciliation and returns 503 `plan-unavailable` on resume. |
| Low | `infrahub_sync/service/flow.py` | A failed destination read in `verify` gives the next action "then plan again". |
| Low | `infrahub_sync/adapters/infrahub.py` | The verify read's filters are tested only against the fake client. |

## Autonomous decisions

- Chunks ran one at a time, split along the phases of `tasks.md`: Phase 2 into A and B, US1 into C and D.
- A cancelled `verify` on an interrupted run keeps the run resumable (given to chunk C).
- T034 was written but not run, and T035 was deferred: no Sync stack was running, and starting one was out of scope
  for this run.
- The review was not run after the second refusal, as the harness instructs.

## Suggested next steps

1. Produce the missing evidence: run `tests/product_store/test_resume_admissions.py` with
   `PRODUCT_STORE_TEST_POSTGRESQL_DSN` set.
2. Run the Phase 6 review: turn off the context-mode plugin or leave auto mode, then run `/speckit-review-run` in
   the worktree.
3. Run T034 on the preview stack, and SC-003 / SC-004 (T035) with the benchmark runner.
4. Decide on the medium items above.
5. Open a stacked PR on #409 when you are ready.
