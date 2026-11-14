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

### Second review (`/code-review xhigh`)

A second review ran over the branch after the report. Its fixes are in the commit that follows `003f32a8`.

| # | File | Finding | Outcome |
| --- | --- | --- | --- |
| 1 | `infrahub_sync/service/flow.py` | Both sides were recorded as `waiting` only at `_plan`, after the live schema read and, for `sync`, after the write guard. A stage that failed earlier returned `reads: null` instead of both sides `not_started`. | Fixed: `_execute_stage` records `waiting` as its first step for `plan` and `sync`. Tests cover a schema-read failure for both stages and a `sync` that waits for the guard. |
| 2 | `infrahub_sync/service/read_progress.py` | A side stayed `reading` while Prefect already reported the flow run as `Crashed`, until liveness recorded the verdict. | Fixed: `stage_reads` takes the observed Prefect state; `failed`, `crashed` or `cancelled` with no verdict ends the unfinished sides. |
| 3 | `infrahub_sync/service/read_progress.py` | A write that changed no row left no trace; the broad catch was not explained. | Fixed: a write that changes no row logs a WARNING naming the execution. The catch stays broad, with the reason in the docstring: the store raises the errors of two drivers that share no base class. |
| 4 | `infrahub_sync/potenda/__init__.py` | `_nodes_by_kind` counts with `adapter.count`, which scans a configured store again for each kind and miscounts on one store type. | No change needed: the v3 service has no store (`execution.py` and `configuration/runtime.py` refuse a configured store). Counting from the snapshot records instead is not a small change, because the snapshot covers only top-level kinds and is written only when a run directory is set. |
| 5 | `infrahub_sync/cli.py` | `_echo_run` printed the read lines for `plan`, `sync`, `verify`, `apply` and `runs cancel` too. | Fixed: only `runs show` prints them. |
| 6 | `infrahub_sync/client/models.py` | A closed `Literal` on `SideRead.state` and `read_mode` would make a client fail on a state a later server adds. | Fixed: both are strings on the client model. The Sync API still returns only the states it stores and derives. |
| 7 | `read_progress.py`, `store.py` | The "still reading after completed" and "unreadable column" warnings repeat on every read of the run. | Deferred: logging each bad document once needs state shared across requests and workers. |
| 8 | `infrahub_sync/service/read_progress.py` | `record_waiting` wrote the two columns in two transactions. | Fixed: the store method is now `record_side_reads`, which writes one or both sides in one `UPDATE`. |
| 9 | `read_progress.py`, `cli.py` | The node counts were sorted three times. | Fixed: Potenda sorts them; the observer and the CLI keep that order. |
| 10 | `infrahub_sync/product_store/store.py` | The side name was checked twice, and the column names were listed twice. | Fixed: the store checks the side once, and the migration tuple is built from `_SIDE_READ_COLUMNS`. |
| 11 | `infrahub_sync/product_store/store.py` | `source_read` and `destination_read` were missing from `CREATE TABLE prefect_executions`. | Fixed. |
| 12 | `infrahub_sync/service/service.py` | Each client applies the "latest `plan` or `sync` entry" rule itself. | Deferred: a run-level field changes the API, which is a design decision for the maintainers. |
| 13 | `potenda/__init__.py`, `read_progress.py` | Three broad `except Exception` catches. | Partly fixed through #3; the two Potenda catches stay, because an observer or a count must never fail a read (FR-007). |
| 14 | `store.py`, `client/models.py` | `Side` and `ReadMode` were retyped. | Fixed in the store, which imports `Side` for typing only. The client model now uses strings (#6). |
| 15 | `infrahub_sync/product_store/store.py` | The read-state `UPDATE` does not require `claiming_worker_id`. | Deferred: the worker ID is not passed to `_execute_stage`; adding it changes the store method, the observer and every caller. |

## Autonomous decisions

- The work ran in a separate worktree, because the main checkout holds uncommitted 016 extraction changes.
- The orchestrator ran T001 itself, because it records the baseline and changes no feature code.
- The five read-only review agents ran in parallel rather than one after another, because they change nothing.
- The broken-JSON case was rated High, not Medium, because it also stops the worker from recording a stage's result.
- The deferred findings above were left out of this run, for the reasons given in the table.
- The live checks were run later on a preview stack started for them; see Live checks.

## Suggested next steps

1. Review the branch, then rebase the stack as planned: 017 onto the 016 branch, then 018 onto 017.
2. Find a live input that makes a read fail after it started, if the `failed` state needs a live check.
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

Run on 2026-11-09 from 19:12Z to 19:21Z, on branch `018-plan-read-progress` at `d3da45c2`, against a preview stack
started from this worktree with `uv run invoke preview.up`. The stack ran on macOS, with Infrahub 1.11.9. The Sync API
and the Prefect worker ran on the host from the worktree's virtual environment. Infrahub was published on port 8180
through a git-ignored `development/preview.local.env`, because port 8080 was in use. `uv run invoke preview.seed`
loaded the smoke dataset: one `InfraDevice` on `main` and the `preview-smoke` branch.

Each check registered its own package, with Infrahub `main` as the source and the `preview-smoke` branch as the
destination, mapping `InfraDevice` (`name`, `type`). Each check started a Plan with `infrahub-sync plan --config-id
<id> --version 1 --branch preview-smoke --no-wait`, then read `infrahub-sync runs show <run_id>` every 0.15 s. The
driver script is not committed. Tokens are left out below.

| Check | Run | Result |
| --- | --- | --- |
| Section 2, a Plan that succeeds | `20261109T1916-fa2ddf78` | Pass |
| Section 3, a side that cannot be reached | `20261109T1917-a9f02677`, `20261109T1917-d0f5133b`, `20261109T1917-1c764fd5` | Pass, with the change described below |
| Section 4, an interrupted read | `20261109T1918-54885be6` | Not observed: the reads took 30–60 ms |
| Section 5, response shape | `GET /runs/20261109T1916-fa2ddf78` | Pass |

### Section 2: a Plan that succeeds

The states that `runs show` printed, in order:

```text
19:16:59Z execution_state=scheduled                       (no read lines)
19:17:03Z execution_state=running    source_read: state=waiting / destination_read: state=waiting
19:17:04Z execution_state=running    both sides state=reading started_at=...
19:17:04Z execution_state=running    source_read: state=succeeded ... / destination_read: state=reading
19:17:05Z phase=planned execution_state=completed
source_read: state=succeeded mode=full started_at=2026-11-09T19:17:04.053135+00:00 ended_at=2026-11-09T19:17:04.221735+00:00 nodes=InfraDevice:1
destination_read: state=succeeded mode=full started_at=2026-11-09T19:17:04.053401+00:00 ended_at=2026-11-09T19:17:04.910973+00:00 nodes=InfraDevice:1
```

`infrahub-sync runs logs 20261109T1916-fa2ddf78` printed one end line for each side:

```text
2026-11-09T19:17:04.228309+00:00  plan  INFO  Load: source read succeeded (full): 1 nodes; InfraDevice 1
2026-11-09T19:17:04.920256+00:00  plan  INFO  Load: destination read succeeded (full): 1 nodes; InfraDevice 1
```

A first `runs logs` call, made right when the execution ended, did not yet show the destination line. A second call
showed it. The logs reach the Sync API a short time after the execution ends.

### Section 3: a side that cannot be reached

In each of these Plans, the failure came before either read started:

- The source URL `http://127.0.0.1:1`: `plan failed: ServerNotReachableError`. The worker looks up the source account
  before the reads.
- The destination URL `http://127.0.0.1:1`: `plan failed: DestinationSchemaUnavailableError`, raised while the worker
  read the destination schema.
- A source branch that does not exist: `plan failed: BranchNotFoundError`.

In all three runs, both sides showed `state=not_started`, which is the spec's edge case "The Plan stops before either
read". Before the second-review fix #1, these runs would have shown no read lines.

`quickstart.md` expects `destination_read: state=failed` for an unreachable destination. That does not happen,
because the destination's reachability is checked before the reads. A Plan that maps an attribute the source kind
does not have also did not fail: the run ended `no-change` and both sides `succeeded`. No live input was found that
makes a read fail after it started. The `failed` state is covered by the unit tests in
`tests/test_potenda_read_progress.py` and `tests/service/test_read_progress.py`.

### Section 4: an interrupted read

The driver was set to stop the Prefect worker process (the host process `prefect-worker`; the preview stack runs no
worker container) as soon as a side showed `state=reading`. The smoke dataset has one node, so both reads ended within
60 ms, and no `runs show` call saw `state=reading`. The worker was not stopped. The `interrupted` state is covered by
the unit tests.

### Section 5: response shape

`GET /runs/20261109T1916-fa2ddf78` returned 200, with this entry in `orchestration`:

```json
{"purpose": "plan", "state": "completed", "reads": {
  "source": {"state": "succeeded", "started_at": "2026-11-09T19:17:04.053135Z", "ended_at": "2026-11-09T19:17:04.221735Z",
             "read_mode": "full", "nodes_by_kind": {"InfraDevice": 1}, "error_class": null},
  "destination": {"state": "succeeded", "started_at": "2026-11-09T19:17:04.053401Z", "ended_at": "2026-11-09T19:17:04.910973Z",
                  "read_mode": "full", "nodes_by_kind": {"InfraDevice": 1}, "error_class": null}}}
```

`GET /runs` returned no `reads` field, as the docs state. No execution recorded before this feature existed in this
fresh store, so `reads: null` for an older execution was not checked live.

### Environment note

The first Plan, `20261109T1912-9b334708`, crashed before it started: the Prefect worker had been started from a
sandbox whose temporary directory no longer existed (`FileNotFoundError` in `tempfile.TemporaryDirectory`). The stack
was restarted from a normal shell, and every later run used that restarted stack. This was not caused by the feature.
