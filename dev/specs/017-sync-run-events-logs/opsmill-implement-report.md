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
