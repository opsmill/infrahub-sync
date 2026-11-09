# Implementation report: Read state for each side of a Plan

- **Status**: DONE
- **Spec directory**: `dev/specs/018-plan-read-progress`
- **Branch**: `018-plan-read-progress`, created from `origin/017-sync-run-events-logs`, in the worktree
  `.claude/worktrees/agent-af610086f3509ea48`. Not pushed, no pull request.
- **Base commit**: `90178305`, the last preparation commit.
- **Head commit**: the commit that adds this report, after `a2d253de`.
- **Wall-clock time**: about 1 hour 5 minutes, from 16:10 to 17:15 UTC on 2026-11-09.
- **Tasks**: 29 of 29 done.
- **Unit tier**: before `6815 passed, 4 skipped, 267 deselected, 1 xfailed`; after the review fixes
  `6879 passed, 4 skipped, 267 deselected, 1 xfailed`.

## Chunk ledger

| Chunk | Tasks | ✅ / ⚠️ / ❌ | Commit | Flagged by the chunk |
| --- | --- | --- | --- | --- |
| Phase 1: Setup (run by the orchestrator) | T001 | 1 / 0 / 0 | `0300de42` | None |
| Phase 2: Potenda's report and the stored columns | T002–T010 | 9 / 0 / 0 | `d75f6b83` | A failed node count logs a WARNING and reports empty counts; the read still succeeds. Counting first failed 36 existing tests whose fake adapters have no store. A raising observer logs one WARNING per failed call. Node counting was measured offline only (see "Node counting"). |
| Phase 3: US1, read state in the run response | T011–T019 | 9 / 0 / 0 | `c9c9ea70` | The `sync` stage was wired here too. `_execute_stage` takes `flow_run_id` as a required keyword, and two direct test callers were updated. `stage_reads` takes `run_id` so that its WARNING names the run. |
| Phase 4: US2, the CLI | T020–T021 | 2 / 0 / 0 | `5c1a115d` | `nodes` is printed sorted by kind; times keep the ISO `T`. The `--help` text did not change at this point. |
| Phase 5: US3, the `sync` stage | T022–T023 | 2 / 0 / 0 | `af5f422c` | No code change. Breaking the `sync` wiring made both new tests fail; the code was then restored. |
| Phase 6: Polish | T024–T029 | 6 / 0 / 0 | `fdac2d04` | The `runs show` docstring names the new fields, so `docs.generate` added 3 lines to `cli.mdx`. The live checks were not run: no preview stack was running. |
| Review fixes | — | — | `a2d253de` | See "Review findings". |

## Tasks not completed

None. T029 is done with its live checks recorded as not run, for the reason in "Live checks".

## Local-pass evidence

Environment for every row: offline, macOS, Python 3.11.8, the worktree's uv virtual environment with the dev, Prefect
and service extras. No row needs any other context. Every test also passed in the final unit tier
(`6879 passed`, started 2026-11-09T17:07:57Z), and the orchestrator re-ran the three feature test files on the final
code at 2026-11-09T17:11:14Z: `56 passed in 0.92s`.

| Test id | Type | Run command | Passed at (UTC) | Verbatim pass line |
| --- | --- | --- | --- | --- |
| `tests/test_potenda_read_progress.py::test_a_successful_read_reports_its_start_then_its_end_with_counts[True\|False]` | unit | `uv run pytest tests/test_potenda_read_progress.py -v -p no:cacheprovider` | 2026-11-09T16:26:42Z | `PASSED` (each case) |
| `tests/test_potenda_read_progress.py::test_a_failing_destination_reports_its_error_class_and_the_source_reports_its_own_end[True\|False]` | unit | same | 2026-11-09T16:26:42Z | `PASSED` (each case) |
| `tests/test_potenda_read_progress.py::test_an_incremental_read_reports_its_mode` | unit | same | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/test_potenda_read_progress.py::test_a_raising_observer_does_not_change_the_read_and_logs_one_warning_per_call` | unit | same | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/test_potenda_read_progress.py::test_a_raising_observer_does_not_hide_a_failed_read` | unit | same | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/test_potenda_read_progress.py::test_without_an_observer_the_reads_behave_as_before` | unit | same | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/test_potenda_read_progress.py::test_each_read_end_logs_one_info_line_without_exception_text` | unit | same | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/test_potenda_read_progress.py::test_a_store_that_cannot_be_counted_does_not_fail_the_read` | unit | same | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/test_execution_surface.py::test_the_read_observer_seam_reaches_the_engine_without_changing_the_factory_call` | unit | `uv run pytest "<id>" -v -p no:cacheprovider` | 2026-11-09T16:26:42Z | `PASSED` |
| `tests/product_store/test_side_read_columns.py::test_a_store_created_before_the_feature_gains_both_columns_and_reads_old_rows_as_none[local\|postgresql-emulated]` | unit | `uv run pytest tests/product_store/test_side_read_columns.py -v -p no:cacheprovider` | 2026-11-09T17:07:51Z | `PASSED` (each case) |
| `tests/product_store/test_side_read_columns.py::test_recording_one_side_leaves_the_other_side_unchanged[local\|postgresql-emulated]` | unit | same | 2026-11-09T17:07:51Z | `PASSED` (each case) |
| `tests/product_store/test_side_read_columns.py::test_a_write_to_a_terminal_execution_changes_nothing[local\|postgresql-emulated]` | unit | same | 2026-11-09T17:07:51Z | `PASSED` (each case) |
| `tests/product_store/test_side_read_columns.py::test_an_unknown_side_is_refused_before_any_sql_runs[...]` (8 cases) | unit | same | 2026-11-09T17:07:51Z | `PASSED` (each case) |
| `tests/product_store/test_side_read_columns.py::test_both_sides_written_from_two_threads_at_once_are_both_stored[local\|postgresql-emulated]` | unit | same | 2026-11-09T17:07:51Z | `PASSED` (each case) |
| `tests/product_store/test_side_read_columns.py::test_a_stored_read_that_is_not_a_json_object_reads_as_none_and_logs_one_warning[...]` (4 cases) | unit | same | 2026-11-09T17:07:51Z | `PASSED` (each case) |
| `tests/service/test_read_progress.py` observer tests: `test_read_started_writes_a_reading_document_with_its_start_time`, `test_a_successful_read_ended_writes_succeeded_with_times_mode_and_sorted_counts`, `test_a_failed_read_ended_writes_failed_with_the_error_class_only`, `test_a_store_write_that_raises_is_logged_as_a_warning_and_not_raised`, `test_record_waiting_writes_both_sides_as_waiting`, `test_record_waiting_logs_a_store_failure_and_does_not_raise` | unit | `uv run pytest tests/service/test_read_progress.py -q -rA -p no:cacheprovider` | 2026-11-09T16:39:24Z | `PASSED` (each test) |
| `tests/service/test_read_progress.py` derivation tests: `test_an_execution_without_documents_has_no_reads`, `test_an_execution_still_running_returns_the_stored_states`, `test_an_execution_that_did_not_complete_returns_waiting_as_not_started_and_reading_as_interrupted[failed\|interrupted\|cancelled]`, `test_an_execution_that_did_not_complete_keeps_a_side_that_ended`, `test_a_completed_execution_returns_an_unfinished_side_as_none_and_logs_one_warning[waiting\|reading]`, `test_a_document_that_does_not_parse_returns_none_for_that_side_and_logs_one_warning[4 cases]`, `test_a_stored_field_outside_the_document_is_not_returned` | unit | same | 2026-11-09T16:39:24Z | `PASSED` (each case) |
| `tests/service/test_read_progress.py` wiring tests: `test_a_plan_stage_records_both_sides_waiting_then_passes_the_observer_to_execute_run`, `test_a_verify_stage_records_nothing_and_passes_no_observer`, `test_an_apply_stage_records_nothing_and_passes_no_observer`, `test_a_lineage_refusal_before_the_reads_leaves_both_sides_waiting_then_not_started` | unit | same | 2026-11-09T16:39:24Z | `PASSED` (each test) |
| `tests/service/test_read_progress.py::test_a_sync_stage_records_both_sides_waiting_then_passes_the_observer_to_its_composed_plan` | unit | `uv run pytest "<id>" -v -p no:cacheprovider` | 2026-11-09T16:48:02Z | `PASSED` |
| `tests/service/test_read_progress.py::test_a_sync_stage_returns_the_same_reads_as_a_plan_stage_for_the_same_reads` | unit | same | 2026-11-09T16:48:02Z | `PASSED` |
| `tests/service/test_read_progress.py::test_each_execution_of_a_run_keeps_and_returns_its_own_read_state` | unit | `uv run pytest "<id>" -v -p no:cacheprovider` | 2026-11-09T17:07:51Z | `PASSED` |
| `tests/service/test_http_api.py::test_run_response_returns_each_sides_read_while_the_destination_read_is_paused` | unit | `uv run pytest "<id>" -q -rA -p no:cacheprovider` | 2026-11-09T16:39:24Z | `PASSED` |
| `tests/service/test_http_api.py::test_an_execution_recorded_before_the_read_state_returns_reads_null` | unit | same | 2026-11-09T16:39:24Z | `PASSED` |
| `tests/runtime_schema/test_worker_path.py::test_a_registered_stage_reaches_execution_with_its_models_bound` (changed call site) | unit | same | 2026-11-09T16:39:24Z | `PASSED` |
| `tests/service/test_registered_schema_guard.py` (one changed call site; whole file run) | unit | `uv run pytest tests/service/test_registered_schema_guard.py -v -p no:cacheprovider` | 2026-11-09T16:39:15Z | `60 passed, 1 warning in 1.64s` |
| `tests/cli/test_client_commands.py::test_runs_show_prints_each_side_read_after_the_prefect_flow_run_link` | unit | `uv run pytest "<id>" -q -p no:cacheprovider` | 2026-11-09T16:45:19Z | `1 passed in 0.12s` |
| `tests/cli/test_client_commands.py::test_runs_show_prints_only_the_state_of_a_side_that_has_not_started_reading` | unit | same | 2026-11-09T16:45:21Z | `1 passed in 0.12s` |
| `tests/cli/test_client_commands.py::test_runs_show_prints_the_plan_read_state_when_a_verify_execution_follows_it` | unit | same | 2026-11-09T16:45:22Z | `1 passed in 0.12s` |
| `tests/cli/test_client_commands.py::test_runs_show_prints_no_read_fields_for_a_run_without_read_state` | unit | same | 2026-11-09T16:45:24Z | `1 passed in 0.11s` |
| `tests/cli/test_client_commands.py::test_runs_show_prints_no_read_fields_when_the_newest_plan_execution_has_no_read_state` | unit | `uv run pytest "<id>" -v -p no:cacheprovider` | 2026-11-09T17:07:51Z | `PASSED` |

No integration or E2E test was added. The live checks in `quickstart.md` are recorded under "Live checks".

## Review findings

Five read-only review agents (code, tests, error handling, types, comments and docs) ran over `90178305..HEAD`,
then a simplify pass. The orchestrator confirmed each fixed finding against the code before the fix.

| Severity | File | Finding | Outcome |
| --- | --- | --- | --- |
| High | `infrahub_sync/product_store/store.py` (`_run_from_rows`) | Text that is not a JSON object in `source_read` or `destination_read` made `lookup_run` raise. That broke `GET /runs/{run_id}`, `runs show` and the worker's own lookup before it commits a stage. | Fixed in `a2d253de`: such a column reads as `None`, with one WARNING naming the run, the column and the error class. |
| High | `docs/docs/reference/sync-http-api.mdx` | Said `GET /runs` returns `reads`; its items have no orchestration entries. | Fixed in `a2d253de`. |
| Medium | docs and docstrings | `reads` is also `null` before the worker reaches the planning step. | Fixed in `a2d253de`. |
| Medium | docs | `read_mode` is `full` only when every top-level kind was read in full. | Fixed in `a2d253de`. |
| Medium | tests | No test for FR-011 (each execution keeps its own read state). | Fixed in `a2d253de`: one store test, one CLI test. |
| Medium | tests | The column tests ran on SQLite only. | Fixed in `a2d253de`: also run on the emulated PostgreSQL store. |
| Low | docstrings and `running-a-sync.mdx` | Four wording errors. | Fixed in `a2d253de`. |
| Medium | `infrahub_sync/potenda/__init__.py` (`_counted_nodes`) | A failed node count reads as a side with no nodes. | Deferred: telling "unknown" from "zero" needs a new response field. |
| Medium | `infrahub_sync/client/models.py` (`SideRead`), `product_store/models.py` | The read types do not enforce the fields of each state; the stored document is not validated on write. | Deferred: a strict validator on the client model could make a client fail to parse a whole run response. A validated stored type is a possible follow-up. |
| Low | `infrahub_sync/service/read_progress.py` | A write that changes no row leaves no trace; the broad catch in `_store` also covers a programming error; stdlib `logging` is used, like the code around it. | Deferred. |
| Low | `infrahub_sync/product_store/store.py` | A SQLite lock can drop one read-state write; the side then shows no state. | Deferred: the result degrades safely. |
| Low | several | Simplify suggestions: use `SideRead.model_fields` for the stored fields, remove a repeated side check, import `Side` in the store, call `_store` directly. | Deferred: advisory only. |

## Autonomous decisions

- The work ran in a separate worktree, because the main checkout holds uncommitted 016 extraction changes.
- The orchestrator ran T001 itself, because it records the baseline and changes no feature code.
- The five read-only review agents ran in parallel rather than one after another, because they change nothing.
- The broken-JSON case was rated High, not Medium, because it also stops the worker from recording a stage's result.
- The deferred findings above were left out of this run, for the reasons given in the table.
- The live checks were not run, because no preview stack was running and the run does not start containers.

## Suggested next steps

1. Review the branch, then rebase the stack as planned: 017 onto the 016 branch, then 018 onto 017.
2. Run the live checks in `quickstart.md` sections 2 to 5 on the preview stack.
3. Decide on the deferred findings, in particular a "counts unavailable" marker and a validated stored type.
4. Open the pull request when the stack is ready.

## Baseline

Run on branch `018-plan-read-progress` at `90178305`, in the uv virtual environment with the dev, Prefect and
service extras, on macOS, from 2026-11-09T16:10:40Z to 16:13:59Z.

- `uv run invoke tests.tests-unit`: `6815 passed, 4 skipped, 267 deselected, 1 xfailed, 7 warnings in 191.57s`
- `uv run ruff check .`: `All checks passed!`

## Node counting

Measured on 2026-11-09T16:20:24Z at `0300de42` plus the T002–T009 changes, in the uv virtual environment, on
macOS. The measurement script is not committed.

- Largest example configuration: `examples/nautobot-v1_to_infrahub/config.yml`, with 30 schema mappings over 23
  distinct kinds. It sets no `store`, so it uses DiffSync's in-memory store. `execute_run` refuses a configured
  store, so no example uses the Redis store on this path.
- No live Nautobot or Infrahub was available, so the side's real read time is not known. In its place, the script
  measured the time to add the nodes to an in-memory adapter with the example's 23 kinds. That time is a lower
  bound of a real read, which also queries the system over the network.
- The counting is `_nodes_by_kind` in `infrahub_sync/potenda/__init__.py`, averaged over 20 calls.

| Nodes | Time to add the nodes | Counting time | Counting as a share of adding |
| --- | --- | --- | --- |
| 7,659 | 0.0211 s | 0.000005 s | 0.026% |
| 76,659 | 0.2626 s | 0.000005 s | 0.002% |

With the in-memory store, `count(model)` is one `len()` for each kind, so the counting time does not grow with the
number of nodes. It stays far below 5% of the read, so the counting was not changed to a single pass.

Not measured: the Redis store, where `count(model)` scans the store's keys for each kind. No Redis server was
reachable on `localhost:6379`.

## Polish checks

Run on branch `018-plan-read-progress` at `af5f422c` plus the T024–T027 changes, in the uv virtual environment with
the dev, Prefect and service extras (re-synced after `towncrier create`), on macOS, on 2026-11-09.

- `uv run invoke format`: exit 0, `431 files left unchanged`.
- `uv run invoke lint`: exit 0. rumdl `No issues found in 140 files`; Ruff `All checks passed!`; Pylint
  `0 diagnostics, score 10.0`; yamllint passed; ty reported 20 `unused-ignore-comment` warnings and no error. The
  warnings are in files this feature does not change (for example `infrahub_sync/adapters/nautobot.py` and
  `tests/product_store/test_contract.py`): their `ty: ignore[unresolved-import]` comments are for optional
  dependencies that this profile installs.
- `uv run ruff check .`: `All checks passed!`
- `uv run invoke docs.lint`: exit 0, `No issues found in 140 files`.
- `uv run invoke tests.tests-unit`, from 16:54:35Z to 16:57:20Z: `6865 passed, 4 skipped, 267 deselected,
  1 xfailed, 7 warnings in 161.83s`. The baseline was 6815 passed; the 50 more are the tests of T002–T022.
- `uv run infrahub-sync --help` and `uv run infrahub-sync runs --help`: exit 0.
- The `runs show` help changed: its docstring now names `source_read` and `destination_read`. `uv run invoke
  docs.generate` regenerated `docs/docs/reference/cli.mdx`, which gained those three lines and nothing else.

## Live checks

Checked on 2026-11-09. `docker ps` listed only the `hivemind` containers: no Sync API, worker, Prefect, Infrahub or
NetBox container of the preview stack was running. `INFRAHUB_SYNC_API_URL` was unset, and nothing answered on
`127.0.0.1:8000`. The containers were not started, because this chunk does not start containers it does not own.

- Section 2, a Plan that succeeds: not run, because no Sync API or worker was running.
- Section 3, a destination that cannot be read: not run, for the same reason.
- Section 4, an interrupted read: not run, for the same reason; it also needs a worker container to stop.
- Section 5, response shape: not run against a live Sync API, for the same reason. The shape is covered offline by
  the T014 tests in `tests/service/test_http_api.py`, which pass in the unit tier above.
