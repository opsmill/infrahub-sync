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
