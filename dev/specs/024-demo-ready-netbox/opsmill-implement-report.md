# Implementation report: 024-demo-ready-netbox

## Evidence (chunk 2)

Phase 3, User Story 1 (T011 to T016). Environment: macOS (Darwin 25.6.0), Python 3.11.8 through
`uv run`, Docker 29.8.2.

### Phase verification commands (2026-11-18)

- `uv run invoke format`: exit 0.
- `uv run invoke lint`: exit 0.
- `uv run ruff check .`: `All checks passed!`
- `uv run invoke tests.tests-unit`:
  `8981 passed, 4 skipped, 383 deselected, 2 xfailed, 15 warnings in 206.73s (0:03:26)`

### New and changed tests

Command, run at 2026-11-18T16:06:37Z:
`uv run pytest -v -p no:randomly tests/dev_stack/test_dev_tasks.py tests/dev_stack/test_deps_mode.py tests/dev_stack/test_netbox_presence.py tests/test_task_inventory.py -k "demo or recorded_dataset or contract_tasks or unknown_preset or local_infrahub_preset_and or inventory or task"`.
Result: `174 passed, 35 deselected in 0.69s`. A second run at 2026-11-18T16:06:46Z,
`uv run pytest -q -rA -p no:randomly tests/dev_stack/test_dev_tasks.py tests/dev_stack/test_deps_mode.py -k "demo_preset or demo_dataset or refuses_a_preparation or seeds_nothing or writes_nothing"`,
gave `8 passed, 138 deselected in 0.20s`. The tests that this chunk adds or changes all passed:

- `tests/dev_stack/test_dev_tasks.py`
  - `test_the_demo_preset_runs_infrahub_and_netbox_without_the_smoke_dataset`
  - `test_dev_start_with_the_demo_preset_starts_netbox_and_writes_nothing_into_infrahub`
  - `test_dev_demo_runs_demo_prepare_on_the_host_with_the_tokens_in_the_environment`
  - `test_dev_demo_with_a_new_netbox_says_it_loaded_the_demo_dataset`
  - `test_dev_demo_keeps_an_existing_netbox_and_prints_the_reload_command[unknown]` and `[recorded]`
  - `test_dev_demo_prints_no_token_the_user_set`
  - `test_dev_demo_prints_the_development_tokens_when_they_are_in_use`
  - `test_dev_demo_without_a_sync_token_for_bearer_tokens_stops_before_starting`
  - `test_dev_demo_refuses_a_preparation_it_cannot_read[failed]`, `[not-json]` and `[no-configuration]`
  - `test_the_dev_namespace_holds_the_contract_tasks` (changed: adds `demo`)
  - `test_an_unknown_preset_is_refused_before_anything_starts` (changed: lists `demo`)
  - `test_a_local_infrahub_preset_and_an_infrahub_network_are_refused_together[demo]` (new case)
- `tests/dev_stack/test_deps_mode.py::test_dev_deps_with_the_demo_preset_starts_netbox_and_seeds_nothing`
- `tests/dev_stack/test_netbox_presence.py::test_recorded_dataset_names_only_a_seed_load_it_recorded`,
  with the cases `[no-record]`, `[seed-tier]`, `[unreadable]` and `[not-an-object]`
- `tests/test_task_inventory.py::test_every_contract_name_exists[dev.demo]`

### Research R-9: the web interface in the development stack (T014)

`uv run invoke dev.build` built `infrahub-sync:dev`, exit 0. The image sets
`INFRAHUB_SYNC_WEB_DIRECTORY=/opt/infrahub-sync/web` and holds `index.html` there. The test ran in
that image with the development source mount. `create_site(FastAPI(), web_directory())` answered
`GET /` with `Accept: text/html` with `200 text/html; charset=utf-8` and the entry page. The full
stack was not started; the reason is under "Live scenarios" below. The build needs the
`frontend/vendor/infrahub` submodule. `dev.demo` therefore fetches the submodule when it is
missing.

### Live scenarios 1 and 2: not run

Docker was running, and ports 8000, 8001 and 8082 were free. The scenarios were not run because
containers and volumes of both Compose projects already existed on this machine, all stopped:

- `infrahub-sync-dev` (the development stack)
- `infrahub-sync-netbox` (the local NetBox)

This session did not create them. Both project names are fixed, so `dev.demo` would have
started and changed that stack, and the final `dev.destroy` and `netbox.destroy` would have
removed its data. The NetBox name cannot be isolated with an override either: `netbox.local.env`
can change the NetBox project name, but the task that reports NetBox ready attaches NetBox's
network to the worker of `infrahub-sync-dev`, stopped containers included. For the same reason, scenario 1's elapsed time and
the plan counts are not known. Run scenarios 1 and 2 on a machine with neither project, or after
the owner of that stack removes it.
