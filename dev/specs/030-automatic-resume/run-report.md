# Run report: automatic resume (spec 030)

This report records which checks of [quickstart.md](./quickstart.md) ran for tasks T043 and T044, with the
commands and their results. All commands ran on 2026-11-23 (UTC) on branch `030-automatic-resume`, in the full
Python 3.11-3.13 development profile, on macOS (Apple silicon).

## Summary

- The unit tier, the PostgreSQL tier and the frontend checks ran and passed.
- The steps of SC-001 and SC-002 that the test fixtures reproduce in one process ran and passed. The tests
  replace Prefect and the worker with a fake orchestration whose work pool is scripted to be live or empty.
- The preview stack check (SC-001 steps 1-7) and the refusal cases on that stack (SC-002) did **not** run. They are
  deferred: the only development stack on this machine belongs to another session, and starting a second one would
  collide with its ports. The commands to run them on a free machine are in
  [Deferred: preview stack check](#deferred-preview-stack-check).

## Quality gates (T043)

| Command | Final line |
| --- | --- |
| `uv run invoke format` | `- [INFRAHUB-SYNC] All formatters have been executed!` (no file changed) |
| `uv run invoke lint` | `- [INFRAHUB-SYNC] All linters have been executed!` (exit 0) |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ty check .` | `Found 33 diagnostics` (exit 0; all 33 are `warning[unused-ignore-comment]`, see below) |
| `uv run invoke tests.tests-unit` | `11313 passed, 9 skipped, 653 deselected, 2 xfailed, 16 warnings in 418.12s` |
| `PRODUCT_STORE_TEST_POSTGRESQL_DSN=postgresql://postgres:…@127.0.0.1:55431/t043 uv run invoke tests.tests-product-store-postgresql` | `418 passed, 1438 deselected, 1 warning in 108.81s` |
| `uv run invoke tests.check-310` | `- [INFRAHUB-SYNC-TEST] check-310 passed all three Python 3.10 legs` (ty leg passed; unit legs `8011 passed, 100 skipped` with Prefect and `7747 passed, 108 skipped` for the base install) |
| `pnpm lint` (in `frontend/`) | `Checked 126 files in 52ms. No fixes applied.` |
| `pnpm check` (in `frontend/`) | `Checked 126 files in 33ms. No fixes applied.` |
| `pnpm typecheck` (in `frontend/`) | `tsc --noEmit` exit 0, no output |
| `pnpm test` (in `frontend/`) | `Tests  431 passed (431)` |
| `uv run infrahub-sync --help` | exit 0 |
| `uv run infrahub-sync configs --help` | exit 0 |
| `uv run infrahub-sync runs --help` | exit 0; the list includes `resume` |

The PostgreSQL tier ran against a throw-away `postgres:16-alpine` container started for this run on
`127.0.0.1:55431` and removed afterwards. It includes the SC-005 test from quickstart.md:
`tests/product_store/test_postgresql_isolation.py::test_postgresql_two_reconcilers_moving_one_automatic_resume_record_move_it_once PASSED`.

The 33 `ty` warnings report `# ty: ignore[unresolved-import]` comments on optional imports such as `psycopg` that
resolve in the full profile. They are warnings and do not fail `invoke lint`. Spec 030 added 2 of them, in
`tests/product_store/test_automatic_resumes.py` and `tests/product_store/test_postgresql_isolation.py`, with the same
comment as the existing PostgreSQL tests; the other 31 come from earlier work. The comments are needed where
`psycopg` is not installed, for example in the Python 3.10 environment. No change was made.

## SC-001 and SC-002 steps reproduced in process (T044)

Command:

```bash
uv run pytest -v tests/service/test_automatic_resume.py -k "test_an_apply_whose_worker_stopped_ends_applied_with_one_automatic_resume or test_the_setting_off_gives_no_record or test_the_setting_off_writes_no_record_and_no_event or test_a_check_that_reports_differs_ends_the_record or test_a_required_approval_that_is_missing_ends_the_record or test_rule_8_writes_an_ended_record_for_a_run_without_a_required_approval or test_a_run_that_runs_resume_refuses_is_not_resumable or test_rule_6_decides_only_for_a_crashed_flow_run or test_every_reason_code_has_a_not_started_case or test_the_reason_text_names_the_reason_with_its_codes_and_counts or test_the_cause_text_names_the_rule_and_the_worker"
```

Result: `25 passed, 58 deselected, 1 warning in 1.61s`. The 25 include every parametrized case of the selected tests. The whole spec 030 test set
(`tests/service/test_automatic_resume.py`, `tests/service/test_schedule_automatic_resume.py`,
`tests/service/test_settings_automatic_resume.py`, `tests/product_store/test_automatic_resumes.py`) gave
`138 passed, 15 skipped, 1 warning in 7.27s`; the 15 skipped tests need PostgreSQL and passed in the PostgreSQL tier
above.

| Quickstart step | What the test does | Test (all PASSED) |
| --- | --- | --- |
| SC-001 steps 3-6 | An apply saved the marker of its 6,000th operation of 10,000; the worker stops sending heartbeats; the liveness reconciler ends the apply under rule 8; once a worker is live, the Sync API submits the check and then one resume from the saved marker; the run ends `applied` with `automatic_resume_count` 1 and the actor `Sync (automatic resume)` | `test_an_apply_whose_worker_stopped_ends_applied_with_one_automatic_resume` |
| SC-001 step 6, the cause text | The run detail names rule 8 and the worker | `test_the_cause_text_names_the_rule_and_the_worker` (3 cases) |
| SC-002, `INFRAHUB_SYNC_AUTOMATIC_RESUME=false` | No record and no event | `test_the_setting_off_gives_no_record`, `test_the_setting_off_writes_no_record_and_no_event` |
| SC-002, one node changed in Infrahub | A check result with 2 `differs` ends the record with the reason `check-found-differs`, the count 2, and no resume submitted; the reason text for a count of 1 reads "the check found 1 node that differs from the plan" | `test_a_check_that_reports_differs_ends_the_record`, `test_the_reason_text_names_the_reason_with_its_codes_and_counts` (8 cases) |
| SC-002, approval required and missing | With `INFRAHUB_SYNC_REQUIRE_APPROVAL=true`, the decision and the liveness reconciler end the record with the reason `approval-missing` and record one "not started" event; the text reads "the site requires approval and the run's plan has no recorded approval" | `test_a_required_approval_that_is_missing_ends_the_record`, `test_rule_8_writes_an_ended_record_for_a_run_without_a_required_approval` |
| SC-002, `runs cancel` instead of a stopped worker | Partly covered, see the note below the table | `test_rule_6_decides_only_for_a_crashed_flow_run[cancelled-False]` |

Note on the cancellation case. The test above shows that a flow run that Prefect reports as cancelled gets no
record. No test cancels a running apply with `runs cancel` and then checks that no record exists. A separate test,
`test_a_run_that_runs_resume_refuses_is_not_resumable[cancelled]`, shows that if a decision is made for a run whose
phase is already `cancelled`, the decision writes an ended record with the reason `not-resumable`, not "no row". Which
of the two a real `runs cancel` produces depends on whether the liveness reconciler sees the cancelled apply as
interrupted. That is not known from the in-process tests; the preview stack check answers it.
| SC-002, every reason | Each reason code has a "not started" case | `test_every_reason_code_has_a_not_started_case` |

Not reproduced in process:

- SC-001 step 2: a plan of at least 10,000 creates and updates on a real Infrahub. The test uses a recorded marker,
  not a real write.
- SC-001 step 4: stopping a real worker container. The test stops the heartbeats of a fake worker.
- SC-001 step 7: a new `infrahub-sync plan` that reports 0 creates and 0 updates. This needs a real destination.

## Deferred: preview stack check

The preview stack check did not run. The machine runs another session's development stack (the
`infrahub-sync-dev-*` containers) and its NetBox, and the task did not allow starting, stopping or changing them, or
starting a second stack next to them.

On a machine with no other development stack, an operator runs these commands from the repository root. They were
not executed for this report. The argument forms of `plan` and `apply` were checked against `--help`. The
development Compose file passes `AUTOMATIC_RESUME` and `REQUIRE_APPROVAL` from the shell to
`INFRAHUB_SYNC_AUTOMATIC_RESUME` and `INFRAHUB_SYNC_REQUIRE_APPROVAL`. The worker is stopped and started with
`docker kill` and `docker start` on its container, so that the Sync API and its settings stay as they are; the
container name assumes the default project name `infrahub-sync-dev`.

```bash
# SC-001 step 1: start the preview stack with automatic resume on.
AUTOMATIC_RESUME=true uv run invoke dev.preview

# Step 2: register a configuration whose plan has at least 10,000 creates and updates
# (the tier M benchmark dataset). Step 3:
uv run infrahub-sync plan --config CONFIGURATION --version VERSION
uv run infrahub-sync apply RUN_ID --expected-checksum CHECKSUM --no-wait

# Step 4: when the progress marker is above 0, stop the worker.
uv run infrahub-sync runs show RUN_ID
docker kill infrahub-sync-dev-sync-worker-1

# Step 5: start it again.
docker start infrahub-sync-dev-sync-worker-1

# Step 6: wait for the phase `applied` and one "Automatic resume 1: started" block with the rule 8 cause.
uv run infrahub-sync runs show RUN_ID

# Step 7: expect 0 creates and 0 updates.
uv run infrahub-sync plan --config CONFIGURATION --version VERSION
```

For the refusal cases of SC-002, repeat steps 3 and 4 once per condition, and check that `runs show RUN_ID` reports
"Automatic resume 1: not started" with the expected reason:

- Automatic resume off: restart the stack with `AUTOMATIC_RESUME=false uv run invoke dev.restart`; expect no
  automatic resume block.
- One node changed: after step 4, change one node of the plan in Infrahub; expect "the check found 1 node that
  differs from the plan".
- Approval missing: restart with `AUTOMATIC_RESUME=true REQUIRE_APPROVAL=true uv run invoke dev.restart` and do not
  run `infrahub-sync approve`; expect "the site requires approval and the run's plan has no recorded approval".
- Cancellation: run `infrahub-sync runs cancel RUN_ID` instead of stopping the worker; expect no automatic resume
  block.

Record the results of that run in this file, under a new heading with the date and the machine.
