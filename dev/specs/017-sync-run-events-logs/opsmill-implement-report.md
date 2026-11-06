# Implementation report: Sync run events and logs

## Baseline

- Date: 2026-11-06
- Branch: `017-sync-run-events-logs`
- Base commit: `a76be45264498a70160f7f0031d3ea6743fa8a5a`
- Unit tier (`uv run invoke tests.tests-unit`), exit code 0:
  `6529 passed, 4 skipped, 246 deselected, 1 xfailed, 7 warnings in 154.43s (0:02:34)`
- Ruff (`uv run ruff check .`), exit code 0: `All checks passed!`

## Tests changed by the failure exit (T026)

The unit tier was run once with the new failure exit in `service_sync_run` (T031) and before any test was
updated. It reported 9 failures. Eight are `pytest.raises` checks on `service_sync_run` whose error class has a
recorded cause, so the flow now returns a `Failed` state instead of raising:

- `tests/service/test_flow_and_prefect.py`:
  - `test_a_plan_records_a_403_as_a_missing_permission[destination_read]`
  - `test_a_plan_records_a_403_as_a_missing_permission[lineage_resolution]`
  - `test_an_apply_records_a_403_during_lineage_resolution_as_a_missing_permission`
  - `test_a_plan_records_a_missing_named_node_with_its_cause_and_next_action`
  - `test_an_apply_records_an_account_mismatch_with_both_accounts_and_its_next_action`
- `tests/service/test_registered_schema_guard.py`:
  - `test_a_failed_schema_read_refuses_with_only_its_short_reason`
  - `test_an_ambiguous_live_member_name_refuses_before_any_write[duplicate-attribute-name]`
  - `test_an_ambiguous_live_member_name_refuses_before_any_write[attribute-relationship-conflict]`

Each one now asserts the returned `Failed` state. In `test_flow_and_prefect.py`, `_record_plan_failure` and
`_record_apply_failure` take `known=True` for these cases and check that the state message is the recorded cause
followed by `Next step:` and the recorded next action. The checks on unknown error classes that use the same two
helpers keep `pytest.raises(RuntimeError)`. In `test_registered_schema_guard.py`, the new `_apply_ends_failed`
helper returns the state message, and the existing assertions on the message text are unchanged.

The ninth failure, `tests/service/test_worker_claim.py::test_claim_refusal_is_logged_at_error_before_the_flow_fails`,
is not a known error class: the refused claim still raises. It failed because the refusal line now carries the
`infrahub_sync.service.flow | ` prefix (T030), and its expected line was updated.

Because `service_sync_run` now returns `dict[str, Any] | State[Any]`, ty reported 12 new diagnostics in tests that
read the result of a successful stage. Each one now checks `isinstance(result, dict)` before reading it:
`tests/integration/isolated_worker_program.py`, `tests/service/test_apply_checkpoint_order.py`,
`tests/service/test_flow_and_prefect.py` (3), `tests/service/test_managed_write_guard.py` (2),
`tests/service/test_plan_checkpoint.py`, `tests/service/test_registered_plan_apply.py`,
`tests/service/test_registered_schema_guard.py` and `tests/service/test_sync_checkpoint_order.py`.

The last log line and the run summary artifact (spec 016) are still written on the new exit, after the ERROR
line and before the secrets are cleared: `tests/service/test_failure_without_traceback.py` checks both under the
Prefect test harness.

## Final checks (T051)

Run on 2026-11-06 after every change of phase 7, including the `runs logs --follow` change that T052 led to.

| Check | Exit code | Result |
| --- | --- | --- |
| `uv run invoke format` | 0 | All formatters ran. |
| `uv run invoke lint` | 0 | rumdl: `No issues found in 140 files`; Ruff: `All checks passed!`; Pylint passed; yamllint passed; ty: `Found 20 diagnostics`, all `unused-ignore-comment` warnings in files this phase did not change, 0 errors. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run invoke tests.tests-unit` | 0 | `6798 passed, 4 skipped, 265 deselected, 1 xfailed, 7 warnings in 167.04s (0:02:47)` |
| `vale` on the four changed pages | 0 errors | 4 warnings, all sentence-case warnings on headings this phase did not change. |

The first unit-tier run of this phase failed two tests in `tests/cli/test_parity_and_closure.py`: they refuse
`--run-id` anywhere in the documentation, because it is a retired option of the earlier commands. `events --run-id`
(T044) is a current option, and the generated CLI reference now lists it. The test now removes the
`events ... --run-id` usages and the `infrahub-sync events` section of the generated reference before the check,
the way it already removed `configs list --name`; a new parametrized test shows that `plan --run-id` and
`runs show --run-id` are still refused.

## Development stack scenarios (T052)

Run on 2026-11-06 from 13:35 to 13:44 UTC against the development stack (`infrahub-sync-dev`). The `sync-api` and
`sync-worker` containers were restarted with `docker compose -f development/docker-compose.yml --profile sync
restart sync-api sync-worker`; they mount `infrahub_sync/` from this checkout, so they ran this branch's code. No
image was rebuilt and no other container was changed. The CLI ran on the host with
`INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001` and the stack's development token. The configuration is
`from-netbox` (`20261105T1514-9d8722ad`, version 1).

### Scenario 1: missing destination branch

- `infrahub-sync plan --config from-netbox --version 1 --branch demo-test-017`: run `20261106T1335-72e1565b`,
  exit code 1, `phase: plan-failed`, and the lines
  ``cause: Branch `demo-test-017` does not exist in Infrahub.`` and
  ``hint: Create it, or start the run with `--branch <existing branch>`.``
- `infrahub-sync runs logs 20261106T1335-72e1565b` printed two lines: one ERROR line,
  ``Branch `demo-test-017` does not exist in Infrahub. Next step: Create it, or start the run with `--branch <existing branch>`.``,
  then the last INFO line `service run ... stage=plan outcome=failed; next step: ...`. A case-insensitive search of
  that output for `traceback`, `prefect`, `4200`, `4230` and `prefect-server` found 0 matches.
- `infrahub-sync events --run-id 20261106T1335-72e1565b`: `infrahub_sync.run.accepted`, then
  `infrahub_sync.run.failed` with outcome `failed`.
- Differs from quickstart step 2: `infrahub-sync runs show` prints no `plan_failure`, `cause:` or `hint:` line. The
  `plan` command printed both, and `runs results` holds `plan_failure.cause` and `plan_failure.next_action`.

### Scenario 2: timeline

Scenario 2 needs an apply, so it ran on a throwaway Infrahub branch, `test-017-timeline`, created for it from the
worker container with the Infrahub SDK. The branch still exists; delete it when it is no longer needed.

- The first apply (run `20261106T1336-92bb217b`) was submitted without `--branch` and failed before any write with
  `PlanVerificationError`: the plan was made for `test-017-timeline` and the apply targets `main` by default. Its
  ERROR line holds only the class name, `PlanVerificationError`; the next action is on the last INFO line and in
  `apply_failure.next_action`. This is the existing behavior for an error class with a next action and no cause.
- Run `20261106T1337-b3c280e8`: plan of 1,689 operations (1,688 creates, 1 delete that apply does not execute), then
  `infrahub-sync apply ... --branch test-017-timeline`. The run ended `applied`.
- `infrahub-sync events --run-id 20261106T1337-b3c280e8`: `run.accepted`, `run.planned`, `run.accepted`,
  `run.applied`, in this order.
- `GET /events?run_id=20261106T1337-b3c280e8` returned the same four events. Each has the primary resource
  `run:20261106T1337-b3c280e8` and the related resources `account:dev`, `config:20261105T1514-9d8722ad`,
  `config_version:20261105T1514-9d8722ad@1` and `branch:test-017-timeline`.

### Scenario 3: polling, SC-003 delay and log size (critique E6)

The gap is the time the line was printed minus its `timestamp`, measured by a script that runs
`infrahub-sync runs logs RUN_ID --follow` and timestamps each printed line.

- Plan followed from its start (run `20261106T1342-98e06dcd`): 60 lines, each printed once, gap 0.46 s minimum,
  1.91 s median, 2.41 s maximum. The command exited 0 after the run ended (29.2 s in all). SC-003 (under 5 s) holds.
- Apply followed from its start, same run: the 3 apply lines were printed once each, gaps 1.53 s to 1.58 s.
- Log size of the largest available sync (run `20261106T1337-b3c280e8`, `from-netbox` plan and apply of 1,689
  operations): Prefect holds 80 lines for its two flow runs; `GET /runs/{run_id}/logs` returns 70 Sync lines (67
  plan, 3 apply) in a 15,322-byte response. Five requests took 0.023 s to 0.041 s each. The plan-only run
  `20261106T1336-92bb217b` has 72 Prefect lines and 67 Sync lines, read in 0.020 s to 0.024 s.

Defect found and fixed: the first follow of an apply (runs `20261106T1336-92bb217b`, `20261106T1337-b3c280e8`,
`20261106T1339-81a5c868`) printed none of the apply's lines. Two causes:

- An apply of a planned run keeps `run_phase` `planned` until it ends, so the first empty read stopped the command.
  The same is true of a verify, which keeps its phase after it ends (the behavior chunk 8 flagged; not run here).
- Prefect stores an execution's last lines in batches, after Sync records that the execution ended: the apply's
  last line has the timestamp 13:40:09.014 and its execution ended at 13:40:08.998.

`runs logs --follow` (`infrahub_sync/cli.py`) now reads the latest execution's `terminal_state` from
`GET /runs/{run_id}` instead of the phase, and stops after three empty reads in a row once the run has ended.
`tests/cli/test_client_commands.py` has two new tests for these cases, and `contracts/cli-output.md` and the HTTP
reference describe the rule. The two apply follows above ran with this change.

### Scenario 4: Prefect cannot be reached

The task allows a restart of `sync-api` and `sync-worker` only, so `prefect-server` was not stopped. Instead, a
second Sync API ran on the host on port 8099 with the `sync-api` container's settings moved onto loopback
(`tasks/dev.py` `host_environment`), the same PostgreSQL and object store, and `PREFECT_API_URL` set to a closed
port, `http://127.0.0.1:9/api`. It was stopped after the check. For run `20261106T1337-b3c280e8`:

| Request | Status | Time |
| --- | --- | --- |
| `GET /runs/{run_id}/logs` | 503, `log-unavailable`, `the log of this run cannot be read now; retry later` | 1.53 s |
| `GET /runs/{run_id}` | 200 | 1.55 s |
| `GET /events?run_id=...` | 200 | 0.01 s |
| `GET /runs/{run_id}/results` | 200 | 0.01 s |

No response held `127.0.0.1:9`, `prefect-server`, `open Prefect` or `in Prefect`.

The `runs show` difference noted in scenario 1 was fixed after this run by review finding 1 (commit `5cba699d`):
`runs show` now prints `<stage> failed: <type>`, `cause:` and `hint:` for each failed stage.

## Run summary

- Feature: Sync run events and logs, read from the Sync API (spec 017)
- Spec directory: `dev/specs/017-sync-run-events-logs/`
- Base commit: `a76be45264498a70160f7f0031d3ea6743fa8a5a`
- Head commit before this report: `5cba699d`
- Wall-clock time: about 2 h 30 min (11:48 to 14:20 UTC, 2026-11-06)
- Status: DONE. 52 of 52 tasks are checked. Every new or changed test passed locally at least once.
- Nothing was pushed and no pull request was opened.

## Chunk ledger

| # | Chunk (tasks.md phase) | Tasks | Done / partial / blocked | Commits | Points flagged by the chunk |
| --- | --- | --- | --- | --- | --- |
| 1 | Phase 1: Setup | 1 (T001) | 1 / 0 / 0 | `dc15f08e` | The optional speckit git hooks were not run. |
| 2 | Phase 2: event storage | 8 (T002–T009) | 8 / 0 / 0 | `77b20199` | The backfill ran only in the start-up that added the column (later fixed, review 4). `since` includes its time and `until` excludes it. Several `related` filters must all match. |
| 3 | Phase 3, US1 part 1: events written | 6 (T010–T012, T015–T017) | 6 / 0 / 0 | `71c030d3` | New `service/events.py` builder. `acknowledge_execution_cancellation` gained `event=`. `run.cancelled` is written in `liveness.py`. A stage success has the outcome `succeeded`. The stage receipt is found by flow run ID and operation. |
| 4 | Phase 3, US1 part 2: `GET /events` and client | 4 (T013, T014, T018, T019) | 4 / 0 / 0 | `f11829df` | An unknown `run_id` or `config_id` returns an empty page, not 404. `since` and `until` need a time zone. The route is a module-level router because `create_app` hit Ruff's complexity limit. |
| 5 | Phase 4, US3 part 1: failure cause | 6 (T020–T022, T027–T029) | 6 / 0 / 0 | `e7682738` | Infrahub SDK 1.23.2 turns any HTTP 400 on the schema endpoint into `BranchNotFoundError`. The Infrahub server answers 400 there only for a missing branch. A test runs the real SDK client to hold this. |
| 6 | Phase 4, US3 part 2: flow lines and wording | 7 (T023–T026, T030–T032) | 7 / 0 / 0 | `a1a9030a` | `service_sync_run` returns `dict | State`, so 9 test files gained an `isinstance` check. An unknown failure also writes one ERROR line with its class name. One harness run printed "database is locked" once; three later runs were clean. |
| 7 | Phase 5, US2: run log | 8 (T033–T040) | 8 / 0 / 0 | `0a61dbe2` | New `OrchestrationUnavailableError`. Lines are ordered by their entry's position, then time, then log ID, because attempts are counted per stage. Log page: default 200, maximum 1000. |
| 8 | Phase 6, US4: CLI | 5 (T041–T045) | 5 / 0 / 0 | `00983571` | A verify run keeps `run_phase` `accepted` after it ends, so `--follow` also reads the latest execution's end state. `events --follow` pages by `offset`. |
| 9 | Phase 7: polish | 7 (T046–T052) | 7 / 0 / 0 | `c7bc530b`, `81e4d942` | The development stack showed that `runs logs --follow` stopped too early for an apply; fixed in `c7bc530b`. The Infrahub branch `test-017-timeline` was created for scenario 2 and still exists. |
| R | Review fixes | 13 findings | 13 / 0 / 0 | `5cba699d` | See "Review findings". |

## Tasks not completed

None. Every task in `tasks.md` is checked.

## Local-pass evidence

Each row covers one test file. It lists the new or changed tests in that file, the command and the runner's
summary line. Environment is local macOS, Python 3.11.8, unless the row says otherwise.

| Test file and tests | Type | Run command | Passed at (UTC) | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/product_store/test_audit_event_migration.py`: model tests (type, related kinds, malformed values), migration tests (older store typed, opening twice changes nothing, read back), `list_events` order, filters (12 cases), paging, bad limit and offset, filter validation; review: `test_opening_a_store_whose_columns_were_added_but_not_filled_types_its_events` | unit + integration | `PRODUCT_STORE_TEST_POSTGRESQL_DSN=postgresql://postgres:test@localhost:55432/sync uv run pytest tests/product_store/test_audit_event_migration.py tests/service/test_event_transactions.py -q -p no:cacheprovider` | 2026-11-06T14:14:20Z | SQLite, emulated PostgreSQL, and PostgreSQL 16 in a temporary Docker container (`postgres:16`, port 55432, removed after the run) | `112 passed in 5.25s` |
| `tests/service/test_event_transactions.py`: event insert fails and the decision is not saved, decision and event saved together, for receipts, stage results, cancellations, cancel acknowledgements; review: late cancel refusal event (3 tests) | unit + integration | same run as the row above | 2026-11-06T14:14:20Z | same as above | `112 passed in 5.25s` (same run) |
| `tests/product_store/test_contract.py` (public names, fake PostgreSQL catalog) | unit | `uv run pytest tests/product_store/test_contract.py -q -p no:cacheprovider` | 2026-11-06T12:07Z (chunk 2) | n/a | `417 passed, 34 skipped` |
| `tests/service/test_http_api.py`: 5 event tests (accepted, replayed, refused, unavailable, cancel), 3 orchestrator wording tests, OpenAPI path set; review: `test_an_interrupted_execution_records_a_run_failed_event_with_its_verdict`, `test_cancelling_a_claimed_write_answers_with_durable_ambiguity` | unit | `uv run pytest tests/service/test_http_api.py -q -p no:cacheprovider` | 2026-11-06T13:02:45Z; review tests 14:13:13Z | n/a | `126 passed, 1 warning in 4.89s`; review run `28 passed, 40 deselected, 1 warning in 1.80s` |
| `tests/service/test_config_routes.py`: 2 configuration event tests; fake gained `read_logs` | unit | `uv run pytest tests/service/test_config_routes.py -q -p no:cacheprovider -k configuration` | 2026-11-06T12:22:35Z; 13:14:14Z | n/a | `2 passed, 73 deselected`; combined run `477 passed, 1 warning in 11.14s` |
| `tests/service/test_flow_and_prefect.py`: stage events (4 stages), failed or interrupted (2 cases), verify read-only test changed, 5 known-failure tests changed to assert `Failed` (T026) | unit | `uv run pytest tests/service/test_flow_and_prefect.py -q -p no:cacheprovider` | 2026-11-06T13:02:52Z; review 14:13:13Z | n/a | `60 passed, 1 warning in 2.97s` |
| `tests/service/test_events_route.py`: contract shape, each query parameter (11 cases), paging, same-time order, 422 (13 cases), limit bounds, token required and no refusal listed, independent timeline test | unit | `uv run pytest tests/service/test_events_route.py -q -p no:cacheprovider` | 2026-11-06T12:33:47Z; review 14:13:17Z | n/a | `32 passed, 1 warning in 1.57s`; review run `43 passed, 1 warning in 10.05s` |
| `tests/client/test_client.py`: `list_events`, `iter_events` (4 tests, 10 refusal cases), `read_run_logs` (13 tests); review: 2 indentation tests | unit | `uv run pytest tests/client/test_client.py -q -p no:cacheprovider` | 2026-11-06T13:14:14Z; review 14:13:17Z | n/a | `477 passed, 1 warning in 11.14s` (combined); review run `43 passed` |
| `tests/client/test_models.py`: event page reads a newer server, event time needs a time zone | unit | `uv run pytest tests/client/test_models.py -q -p no:cacheprovider` | 2026-11-06T12:33:47Z | n/a | `127 passed, 1 warning in 1.64s` (combined) |
| `tests/configuration/test_schema_validation.py`: missing branch is `nobranch`, HTTP 400 is a missing branch (real SDK client), one existing test changed | unit | `uv run pytest tests/configuration/test_schema_validation.py -q -p no:cacheprovider` | 2026-11-06T12:46:03Z | Infrahub SDK 1.23.2 | `59 passed in 0.17s` |
| `tests/runtime_schema/test_destination_branch_missing.py` (new): typed subclass, 7 reasons, permission text | unit | `uv run pytest tests/runtime_schema/test_destination_branch_missing.py -q -p no:cacheprovider` | 2026-11-06T12:46:03Z | n/a | `9 passed in 1.08s` |
| `tests/service/test_failure_cause.py` (new): missing branch, 7 reasons, distinct causes, wrapped error, redaction, cap, unknown reason | unit | `uv run pytest tests/service/test_failure_cause.py -q -p no:cacheprovider` | 2026-11-06T12:46:03Z | n/a | `28 passed in 0.14s` |
| `tests/service/test_registered_schema_guard.py`: 3 tests changed to assert `Failed` and the new cause text | unit | `uv run pytest tests/service/test_registered_schema_guard.py -q -p no:cacheprovider` | 2026-11-06T13:02:57Z | n/a | `34 passed in 1.29s` |
| `tests/service/test_failure_without_traceback.py` (new): missing branch writes one ERROR line and ends `Failed`; unknown error still raises | unit | `uv run pytest tests/service/test_failure_without_traceback.py -q -p no:cacheprovider` | 2026-11-06T13:02:35Z | Prefect test harness | `2 passed in 6.78s` |
| `tests/service/test_run_log_next_step.py`: 5 prefix tests, 7 last-line tests changed, broken-logger test changed; review: lost claim, receipt lookup fails (2 tests) | unit | `uv run pytest tests/service/test_run_log_next_step.py -q -p no:cacheprovider` | 2026-11-06T13:02:43Z; review 14:13:11Z | n/a | `32 passed in 0.60s`; review run `7 passed, 1 skipped, 83 deselected in 0.63s` (the skip is the PostgreSQL case, passed in the 14:14:20Z run) |
| `tests/service/test_worker_claim.py`: refused-claim line test changed for the prefix | unit | `uv run pytest tests/service/test_worker_claim.py -q -p no:cacheprovider` | 2026-11-06T13:03:00Z | n/a | `16 passed in 1.38s` |
| `tests/service/test_run_log.py` (new): Sync lines kept, stage and attempt, order, unknown flow run, control characters and cap, paging; review: secret across the cap | unit | `uv run pytest tests/service/test_run_log.py -q -p no:cacheprovider` | 2026-11-06T13:14:14Z; review 14:13:17Z | n/a | `477 passed, 1 warning in 11.14s` (combined); review run `43 passed` |
| `tests/service/test_orchestration_logs.py` (new): paging of 200, short first page, expired flow run, no flow runs, client error or timeout (4 cases) | unit | `uv run pytest tests/service/test_orchestration_logs.py -q -p no:cacheprovider` | 2026-11-06T13:14:14Z; review 14:13:17Z | fake Prefect client | `477 passed` (combined); review run `43 passed` |
| `tests/service/test_run_logs_route.py` (new): independent test, stage filter, paging, 422, limit 1000, 404, empty run, 410, 503 while record and events stay readable, no Prefect wording, token required | unit | `uv run pytest tests/service/test_run_logs_route.py -q -p no:cacheprovider` | 2026-11-06T13:14:14Z; review 14:13:17Z | n/a | `477 passed` (combined); review run `43 passed` |
| `test_liveness_policy.py`, `test_status_observation_budget.py`, `test_artifact_confidentiality.py` (fakes gained `read_logs`); `test_sync_store_policy.py` (unchanged, uses a changed fake) | unit | combined run of 11 files (chunk 7) | 2026-11-06T13:14:14Z | n/a | `477 passed, 1 warning in 11.14s` |
| `tests/cli/test_client_commands.py`: `runs logs` (6 tests, 2 changed and 2 added in chunk 9), `events` (2 tests); review: `test_runs_show_prints_the_recorded_cause_and_next_action_of_a_failed_stage` | unit | `uv run pytest tests/cli/test_client_commands.py -q -p no:cacheprovider -k "runs_logs or events"` | 2026-11-06T13:47:34Z; review 14:13:11Z | n/a | `8 passed, 155 deselected in 0.16s`; review run `7 passed, 1 skipped, 83 deselected` |
| `tests/cli/test_parity_and_closure.py`: 2 tests changed, `test_the_removed_option_rule_exempts_only_events_run_id` (5 cases) | unit | `uv run pytest tests/cli/test_parity_and_closure.py -q -p no:cacheprovider` | 2026-11-06T13:47:28Z | n/a | `25 passed in 0.36s` |
| `tests/service/test_prefect_wording.py` (new): no Prefect wording in messages, lines or CLI output (read, expired, unavailable); no flow run delete; guard finds a call; review: API messages checked for "prefect" | unit | `uv run pytest tests/service/test_prefect_wording.py -q -p no:cacheprovider` | 2026-11-06T13:47:16Z; review 14:13:17Z | Prefect test harness | `5 passed, 1 warning in 8.46s`; review run `43 passed` |
| `tests/test_run_events_docs.py` (new): HTTP reference (5 cases), Prefect retention settings (2), clock note, orchestration page | unit | `uv run pytest tests/test_run_events_docs.py -q -p no:cacheprovider` | 2026-11-06T13:47:26Z | n/a | `9 passed in 0.02s` |
| Type-only `isinstance` edits: `tests/integration/isolated_worker_program.py`, `test_apply_checkpoint_order.py`, `test_managed_write_guard.py`, `test_plan_checkpoint.py`, `test_registered_plan_apply.py`, `test_sync_checkpoint_order.py` | unit | `uv run invoke tests.tests-unit` | 2026-11-06T14:13Z (review chunk) | n/a | `6812 passed, 4 skipped, 1 xfailed` |
| Quickstart scenarios 1–4 on the development stack (T052) | e2e (manual) | see "Development stack scenarios (T052)" | 2026-11-06T13:35Z–13:44Z | `infrahub-sync-dev` stack, `from-netbox` configuration | recorded in that section |

Final unit tier after the review fixes: `6812 passed, 4 skipped, 1 xfailed`. Format, lint (Pylint 10.0, ty 0
errors) and `ruff check .` exit 0.

## Review findings

Six reviewers checked `a76be452..5cba699d^`: code, tests, error handling, types, comments and simplification.

| Severity | File | Finding | Action |
| --- | --- | --- | --- |
| HIGH | `infrahub_sync/cli.py` (`_echo_run`) | `runs show` printed no failure cause or next action (SC-001, CLI contract). | Fixed in `5cba699d`. |
| HIGH | `infrahub_sync/service/flow.py` (`_record_failure`) | When the worker no longer held the execution, the failure was not saved but the flow wrote the cause and ended `Failed` as if it had been. | Fixed: WARNING line, unrecorded path. |
| HIGH | `docs/docs/reference/sync-http-api.mdx` | Said older events are not listed; the migration gives them a type. | Fixed. |
| MEDIUM | `infrahub_sync/product_store/store.py` | On SQLite, a failed first start-up could leave older events untyped for good. | Fixed: both backfills run at every start-up. |
| MEDIUM | `store.py`, `service/service.py` | A late cancel acknowledgement saved its refusal event in a separate transaction (FR-014). | Fixed. |
| MEDIUM | `infrahub_sync/service/liveness.py` | An execution interrupted by the liveness check recorded no `run.failed` event. | Fixed: `run.failed`, outcome `ambiguous`. |
| MEDIUM | `flow.py` | A failed receipt lookup inside the commit could lose the failure record or turn a success into a failure. | Fixed: event built before the commit, falls back to the run's actor. |
| MEDIUM | `service/orchestration.py`, `service/service.py` | An unreachable orchestrator gave 503 with no server log line, so an outage looked like a credentials problem. | Fixed: error class logged, response text unchanged. |
| MEDIUM | `tests/service/test_prefect_wording.py` | API messages were not checked for the word "Prefect". | Fixed. |
| MEDIUM | `sync-http-api.mdx`, `running-a-sync.mdx`, `contracts/events-route.md` | `run.accepted` row missed cancel; null `primary_resource` undocumented; a page still sent the reader to the Prefect link. | Fixed. |
| MEDIUM | `cli.py` (`events --follow`), `GET /events` | Paging by `offset` can skip or repeat one event when an event with an earlier time is saved late. | Documented as a limit; paging by `(created_at, event_id)` deferred. |
| MEDIUM | `product_store/models.py` (`AuditEvent`) | Configuration, version and branch are stored both as fields and in `related`, with no check that they agree. | Deferred. |
| MEDIUM | `flow.py`, `liveness.py` | Stage and cancellation events repeat the same receipt lookup. | Deferred (simplification). |
| MEDIUM | `cli.py` | One variable holds both "run ended" and "empty reads" in the follow loop. | Deferred (simplification). |
| LOW | `client/models.py`, `service/run_log.py` | Log messages lost their indentation; messages were capped before redaction. | Fixed. |
| LOW | `changelog/+run-events.added.md` | Implied the CLI has every `GET /events` filter. | Fixed. |
| LOW | various | `actor or "unknown"` is silent; `runs logs --follow` stops on one 503; `EventFilters` accepts `since >= until`; duplicate client models and offset helpers; shared test fake for `read_logs`; stale `worker.py` docstring; no test that a token of another account reads a log. | Deferred. |
| LOW | `infrahub_sync/configuration/capabilities.py` | Any HTTP 400 on the schema endpoint means a missing branch. | Accepted: matches the Infrahub server; a test holds the SDK behavior. |

## Autonomous decisions

- Phase 3 (10 tasks) and phase 4 (13 tasks) were each split in two chunks along their natural seams: events
  written and the read route for US1, the failure cause and the flow lines for US3.
- The polish chunk was allowed to restart only the `sync-api` and `sync-worker` containers of the development
  stack. The store migration ran on the development PostgreSQL. It created and applied to the Infrahub branch
  `test-017-timeline`, which still exists.
- The polish chunk changed `runs logs --follow` and `contracts/cli-output.md` after the development stack showed
  the command stopped before an apply's lines arrived.
- The review fixes went beyond the HIGH findings: every MEDIUM finding that touched a requirement or stored data
  was fixed in the same pass. Simplification, the `AuditEvent` consistency check and cursor paging were deferred.
- The PostgreSQL case of the new migration test was skipped by the review chunk; the orchestrator ran it against a
  temporary PostgreSQL 16 container, and it passed.

## Suggested next steps

1. Review the branch, then open a pull request when ready (none was opened).
2. Delete the Infrahub branch `test-017-timeline` on the development stack when it is no longer needed.
3. Decide whether `events --follow` and `GET /events` should page by `(created_at, event_id)` instead of `offset`.
4. Check whether a verify run should change `run_phase` when it ends; today it stays `accepted`.
5. Consider the deferred simplifications and the `AuditEvent` consistency validator.
6. The `--branch` advice for a missing branch does not help when the branch is set in the destination settings;
   consider a second wording for that case.
