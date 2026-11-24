# Implementation report: name the run that holds a configuration's write lock

## 1. Header

- **Feature**: Name the run that holds a configuration's write lock (INFP-647, acceptance criterion 7)
- **Spec directory**: `dev/specs/028-write-lock-holder`
- **Branch**: `028-write-lock-holder`, built on `fix/write-guard-names-active-run` (`cf9f13b6`, `a50d5780`)
- **Base commit**: `33780c7f` (tasks rebuilt after clarification)
- **Head commit**: `0ee204ee` before this report
- **Wall-clock time**: about 2 hours 10 minutes (2026-11-23, 19:45 to 21:50 UTC)
- **Status**: DONE. All 34 tasks are complete, every new or changed test passed locally, and the review ran in
  `command` mode.

## 2. Chunk ledger

| # | Chunk | Tasks | ✅ / ⚠️ / ❌ | Commit | Points raised by the subagent |
|---|---|---|---|---|---|
| 1 | Setup | T001 | 1 / 0 / 0 | `c02bf2f5` | The `frontend/vendor/infrahub` submodule was empty; `git submodule update --init` checked out the recorded commit. Baseline: 108 pytest and 410 Vitest tests passed. |
| 2 | Foundational | T002–T004 | 3 / 0 / 0 | `c7015517` | The run ID pattern is imported from `product_store.models`, not copied. `re.fullmatch` rejects a trailing newline. `holder_run_id` is keyword-only, so pickling the error drops it. |
| 3 | US1, service | T005–T010 | 6 / 0 / 0 | `95c91b11` | `_read_holder` runs inside `try/finally` before the session is closed. On PostgreSQL 16, a holder without a label has an empty `application_name`, which is rejected and gives "not known". |
| 4 | US1, service tests | T011–T014 | 4 / 0 / 0 | `08a80e14` | T014 needed no new test, because the existing tests already covered its cases. The three-session test passed 20 times in 30.5 s on PostgreSQL 16. |
| 5 | US1, pull-request CI | T015–T016 | 2 / 0 / 0 | `9fcb22e5` | The product store tests and the write-lock tests can share one server: the write-lock tests end only the session that holds their own key, and the store tests use their own schema. The task takes about 2.5 minutes. |
| 6 | US1, web interface | T017–T020 | 4 / 0 / 0 | `e794e372` | T017 changed no file: the OpenAPI schema types a run's results as a free-form object, so no generated type exists for the failure record. The link sets `--notice-tone` so its outline shows inside the danger notice. |
| 7 | US2, run not known | T021–T024 | 4 / 0 / 0 | `1a3887d3` | **On PostgreSQL 16, a session of another account read the label** (`track_activities=on`). The spec assumed the opposite. The documentation was corrected in chunk 9. |
| 8 | US3, holder's write has ended | T025–T027 | 3 / 0 / 0 | `c145e055` | Added a shared failure case for the "still writing" variant. |
| 9 | Documentation and checks | T028–T034 | 7 / 0 / 0 | `2381eab4` | ADR 0021 now lists 25 failure types: the original 17, 7 types that scheduled runs added without amending it, and `write_guard_contended`. One integration test failed once because it read `pg_locks` before the server had ended the closed session; it now waits for the release. |
| — | Review fixes | — | — | `0ee204ee` | See §5. |

## 3. Tasks not completed

None. All 34 tasks are `[X]`.

## 4. Local-pass evidence

All runs on macOS, Python 3.11.8, pytest 9.1.1; PostgreSQL runs on a disposable `postgres:16-alpine` (16.14)
container; web tests on Node 26.8.1, pnpm 10.34.5, Vitest 5.1.1. Times are UTC on 2026-11-23.

| Test id | Type | Run command | Passed at | Environment | Pass line |
|---|---|---|---|---|---|
| `tests/service/test_apply_guard.py` — 27 label and parser tests (`test_holder_run_id_*`, `test_guard_label_*`, `test_contention_error_has_no_holder_run_id_by_default`, `test_guard_failure_carries_a_holder_run_id_on_contention_only`) | unit | `uv run pytest tests/service/test_apply_guard.py -v -k "holder_run_id or guard_label or guard_failure_carries"` | 19:49:38Z | n/a | `27 passed, 89 deselected in 0.24s` |
| `tests/service/test_managed_write_guard.py` (whole file), `test_read_progress.py::test_a_sync_stage_records_waiting_before_it_waits_for_the_write_guard`, `test_verify_destination.py::test_verify_takes_no_configuration_write_guard` | unit | `uv run pytest -q -p no:cacheprovider -rA <node IDs>` | 20:10:21Z | n/a | `23 passed, 2 skipped in 1.76s` |
| `tests/product_store/test_write_admission.py::test_write_holder_state_*[sqlite\|postgresql]` | unit + PostgreSQL | `uv run pytest tests/product_store/test_write_admission.py -rA -k write_holder_state` | 20:17:42Z | `PRODUCT_STORE_TEST_POSTGRESQL_DSN`, 127.0.0.1:55434 | `PASSED ...[sqlite]`, `PASSED ...[postgresql]` (4 tests) |
| `test_apply_guard.py::test_acquisition_labels_the_session_then_bounds_the_deadline_then_locks_then_proves_ownership`, `test_acquisition_without_a_writable_label_sends_no_label_statement[None\|run 1]`, `test_a_lock_timeout_reads_the_holder_on_the_same_session_before_closing_it`, `test_one_holder_row_with_a_valid_label_names_the_holding_run`, `test_a_write_that_takes_the_lock_never_looks_up_a_holder` | unit | `uv run pytest tests/service/test_apply_guard.py -rA -k "<names>"` | 20:17:40Z | n/a | `PASSED` for each |
| `test_managed_write_guard.py::test_the_configuration_write_guard_receives_the_stage_run_id[apply\|sync]`, `test_a_contended_failure_looks_up_the_holder_in_the_run_records_exactly_once[apply\|sync]` | unit | same as above | 20:17:40Z | n/a | `PASSED` for each |
| `tests/integration/test_apply_guard_integration.py::test_the_run_whose_wait_ends_names_the_holder_and_never_the_other_waiter[0..19]` | integration | `APPLY_GUARD_TEST_POSTGRESQL_DSN=... uv run pytest -m integration <node> -rA` | 20:16:28Z | 127.0.0.1:55434 | `20 passed in 30.51s` |
| `tasks/tests.py::tests_product_store_postgresql` (selection extended; no new test function) | integration | `uv run invoke tests.tests-product-store-postgresql` | 20:27:03Z | both DSNs, 127.0.0.1:55435 | `426 passed, 1399 deselected`, 0 skipped |
| `frontend/src/domain/run-state.test.ts` — `failureDetail activeRunId` (5 cases) | unit (web) | `pnpm exec vitest run src/domain/run-state.test.ts --reporter=verbose` | 20:33:44Z | Node 26.8.1 | `Tests  5 passed (5)` |
| `test_apply_guard.py::test_a_holder_that_cannot_be_proven_leaves_the_contention_error_unchanged[7 cases]`, `test_managed_write_guard.py::test_a_candidate_the_run_records_do_not_confirm_is_recorded_as_not_known[3 cases]`, `test_failure_cause_credentials.py::*[write-guard-contended-holder-confirmed\|rejected\|lookup-raised]` | unit | `uv run pytest -rA <5 files> -k "cannot_be_proven or do_not_confirm or holder"` | 20:49:40Z | n/a | `47 passed` |
| `test_apply_guard_integration.py::test_a_holder_without_a_label_is_not_known`, `test_a_holder_label_cut_to_63_bytes_is_not_known`, `test_a_holder_that_releases_before_the_lookup_is_not_known`, `test_a_holder_of_another_account_is_named_correctly_or_not_known` | integration | `uv run pytest -m integration tests/integration/test_apply_guard_integration.py -rA -k "not_known or named_correctly"` | 20:50:48Z | 127.0.0.1:55437, `track_activities=on` | `4 passed, 31 deselected in 3.37s` |
| `test_failure_cause.py::test_a_contended_write_guard_has_one_cause_and_next_action_per_holder_case[4 cases]`, `..._whose_holder_write_ended_never_says_to_wait_for_it`, `test_a_resume_contended_by_a_holder_whose_write_ended_ends_with_verify_then_resume`, `test_failure_output.py::test_a_contended_write_whose_holder_write_ended_prints_how_to_release_the_guard[sync\|apply]`, `test_failure_cause_credentials.py::*[write-guard-contended-holder-writing]` | unit | `uv run pytest <4 files> -rA` | 20:56:39Z | n/a | `241 passed` |
| `test_apply_guard_integration.py::test_a_holder_label_cut_to_63_bytes_is_not_known` (made to wait for the release) | integration | `uv run invoke tests.tests-product-store-postgresql` | chunk 9 | 127.0.0.1:55439, PostgreSQL 16.14 | `430 passed, 1399 deselected`, 0 skipped |
| Review fixes: `test_write_admission.py::test_write_holder_state_reports_when_the_run_last_ended_a_claimed_write[sqlite\|postgresql]`, `test_managed_write_guard.py::test_a_holder_whose_write_ended_before_the_wait_began_is_reported_as_ended`, `..._ended_during_the_wait_is_reported_as_writing`, `test_a_contention_error_without_a_wait_start_assumes_the_largest_deadline`, `test_a_holder_with_no_open_write_and_no_write_end_time_is_reported_as_writing`, `test_apply_guard.py::test_a_lock_timeout_records_when_the_wait_started_from_the_deadline_it_used[30.0\|120.0]`, and the reworded template, CLI and credential tests | unit + PostgreSQL | `uv run pytest -p no:cacheprovider -rA <nodes>` | 21:45:34Z–21:45:54Z | 127.0.0.1:55440 for the store test | `PASSED` for each |
| Whole suites after the review fixes | all | `uv run pytest tests/service tests/product_store tests/cli -q`; `uv run invoke tests.tests-product-store-postgresql` | 21:36:05Z; 21:42:45Z | 127.0.0.1:55440 | `4944 passed, 424 skipped`; `431 passed, 1400 deselected`, 0 skipped |

The run-wide checks in chunk 9 also passed:

- `invoke tests.tests-unit`: 11210 passed, 9 skipped, 2 xfailed.
- `pnpm test` (415 of 415), `pnpm check`, `typecheck` and `build`.
- `invoke format`, `invoke lint` and `ruff check .`.
- `invoke docs.rumdl` (183 files) and the documentation site build.

## 5. Review findings

Review mode: command (/code-review), level `high`, over `33780c7f..2381eab4`.

| Severity | File | Summary | Outcome |
|---|---|---|---|
| important | `infrahub_sync/service/flow.py:1696` | "Ended" was decided after the lock timeout, so a holder that finished in that gap made the failure tell the operator to restart a healthy worker. | Fixed in `0ee204ee`: the same run-record read returns when the holder's write ended. The "ended" text is used only when that write ended before this write began to wait. The wait start is recorded on the contention error. |
| nit | `infrahub_sync/service/apply_guard.py:86` | "Held its write guard for the whole wait" can be false if the first holder released and another waiter took the lock just before the lookup. | Fixed: all three texts now say "when this write's wait for the guard ended". |
| nit | `infrahub_sync/service/apply_guard.py:183` | Run IDs longer than 42 characters, and holders on workers without this change, carry no label and are never named. | Documented in `apply-guard.md` and the changelog fragment; run ID limits are unchanged. |
| nit | `infrahub_sync/service/failure_cause.py:606` | Docstrings still described the removed lookup. | Fixed, plus one more docstring with the same wording. |
| nit | `infrahub_sync/service/apply_guard.py:59` | New logger uses `logging`, AGENTS.md asks for structlog. | Deferred: the modules on the worker's write path use the standard library, and structlog is not configured, so switching would hide the line from the test that checks the label is never logged. See §6. |

## 6. Autonomous decisions

- **Chunking:** User Story 1 has 16 tasks, so it was split into four chunks: service, service tests, CI and web interface.
- **T017 changed no file.** The generated web API types have no failure-record type to extend (§2, chunk 6).
- **Label visibility:** on PostgreSQL 16, another account read the label. The spec's assumption, research R4, the secret-redaction guideline and the ADR 0021 amendment now record this, and the label must hold nothing but a run ID.
- **ADR 0021 now lists 25 failure types, not 18.** It had not been amended when scheduled runs added 7 types.
- **"Ended" rule (review fix):**
  - It compares the holder worker's clock with the waiting worker's clock. If the two hosts' clocks differ, the wrong variant can be shown.
  - When the wait start is unknown, the rule assumes the largest allowed deadline plus the lookup limit, which errs toward the "still writing" text.
  - The store test checks one write execution only, because the test helpers do not allow a second write execution on the same run.
- **Logger:** kept the standard library (§5, last row). Say if you want structlog anyway.
- **Review scope:** the review covered this run's commits only. The two earlier commits (`cf9f13b6`, `a50d5780`) were reviewed when that branch was built and are rewritten by this work.

## 7. Suggested next steps

1. Open the draft pull request into `feature/v3-develop` and watch CI, as requested on 2026-11-23.
2. Decide on the logger: keep the standard library, or switch the service write path to structlog in a separate change.
3. After merge, update INFP-647 to say criterion 7 is met, and close the earlier branch `fix/write-guard-names-active-run`, which this branch includes.
