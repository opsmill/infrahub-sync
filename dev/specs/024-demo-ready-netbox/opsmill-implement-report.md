# Implementation report: 024-demo-ready-netbox

**Status**: INCOMPLETE. T016 and T022 are not done: their live Docker scenarios were not run (§3).

- Spec directory: `dev/specs/024-demo-ready-netbox`
- Branch: `024-demo-ready-netbox`, worktree `.worktrees/024-demo-ready-netbox`. Not pushed, no pull request.
- Base commit: `4ce055ec`. Head commit: the commit that adds this report.
- Wall-clock time: about 1 h 20 min, from 15:50Z to 17:15Z on 2026-11-18.

## 1. Summary

- 27 of 29 tasks are done.
- Unit tier: 9003 passed, 4 skipped, 2 xfailed (2026-11-18T17:11:55Z).
- `invoke lint` exits 0, `ruff check .` passes, and `docs.lint` passes.
- `tests.check-310` passed all three Python 3.10 legs.
- The CLI sanity commands pass, including `demo --help`.

## 2. Chunk ledger

| Chunk | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged |
| --- | --- | --- | --- | --- |
| 1. Setup and the `demo prepare` command | T001–T010 | 10 / 0 / 0 | `d95ceefb` | `--reason` 409 on re-run; the contract said `--url`, the CLI option is `--api-url` |
| 2. User Story 1: `demo` preset and `dev.demo` | T011–T016 | 5 / 1 / 0 | `8894a6ba`, `8640d3c1` | live scenarios not run; rebuilt the shared `infrahub-sync:dev` tag; `dev.demo` fetches the `frontend/vendor/infrahub` submodule |
| 3. User Story 2: assets in the wheel and the image | T017–T022 | 5 / 1 / 0 | `7660164d` | ADR 0048; `.github/file-filters.yml` updated; `build_inputs_hash` does not hash the asset paths |
| 4. User Stories 3 and 4: documentation | T023–T026 | 4 / 0 / 0 | `f25ee9d4`, `4d40963e` | `main` has no `ci-docker-image.yml`, so a dispatch uses `--ref feature/v3-develop`; Vale exceptions for `Kubernetes` and `Styrmin` |
| Review fixes | review items 1–9 | 8 / 1 / 0 | `2f85d702`, `b95f236b` | item 9 finished by the orchestrator |
| 5. Polish, done by the orchestrator | T027–T029 | 3 / 0 / 0 | `fb50811d` | `.yamllint.yml` now ignores `/.dev` |

## 3. Tasks not completed

The live parts of T016 (quickstart scenarios 1 and 2) and T022 (scenarios 3 and 4) were not run.

- **Why:** this machine holds stopped Compose projects named `infrahub-sync-dev` and `infrahub-sync-netbox`, and this run did not create them. The NetBox tasks attach NetBox's network to the worker of the fixed project `infrahub-sync-dev` (`tasks/netbox.py:472` and `:747`). The NetBox project name is set in `development/netbox/netbox.env`. A live run would therefore change those stacks, and the clean-up tasks would delete their data.
- **Unknown until they run:** the elapsed time of SC-001 (15 minutes or less), the plan counts, and the image path against a live Sync API.
- The appendix has the details from chunk 2.

## 4. Local-pass evidence

All runs are on macOS with Python 3.11.8 through `uv run`, from the worktree. The image tests ran on Docker 29.8.2 (OrbStack) against `infrahub-sync:demo-024-test`, which was removed afterwards.

| Test id | Type | Run command | Passed at | Environment | Pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/demo/test_assets.py` (6 tests), `tests/demo/test_package.py` (12), `tests/demo/test_prepare.py` (original 11), `tests/cli/test_demo_prepare.py` (original 5), and 2 changed in `tests/test_netbox_tasks.py` | unit | `uv run pytest tests/demo tests/cli/test_demo_prepare.py "tests/test_netbox_tasks.py::…" -v -p no:randomly` | 2026-11-18T15:53:18Z | n/a | `36 passed in 0.97s` |
| `tests/demo/test_prepare.py`, review additions (reason, A/B switching, `same_name` paging, userinfo ×3, timeout rounding, non-convergence ×2, URL userinfo in errors) | unit | `uv run pytest -v -p no:cacheprovider tests/demo/test_prepare.py tests/cli/test_demo_prepare.py` | 2026-11-18T16:55:30Z | n/a | each test line `PASSED` |
| `tests/cli/test_demo_prepare.py::test_the_registered_package_holds_neither_token` and `::test_a_sync_api_failure_after_the_schema_load_says_a_re_run_completes_the_registration[unauthenticated\|connection-error]` | unit | same command | 2026-11-18T16:55:30Z | n/a | `PASSED` |
| `tests/cli/test_demo_prepare.py::test_an_address_with_userinfo_exits_2_naming_the_option_and_not_the_value[…]` (6 cases) | unit | `uv run pytest -v -p no:cacheprovider tests/cli/test_demo_prepare.py -k userinfo` | 2026-11-18T17:06:27Z | n/a | `PASSED` for all 6 |
| `tests/dev_stack/test_dev_tasks.py`, `test_deps_mode.py`, `test_netbox_presence.py` and `tests/test_task_inventory.py`: the demo tests listed in the appendix | unit | see the appendix | 2026-11-18T16:06:37Z and 16:06:46Z | n/a | `174 passed, 35 deselected`; `8 passed, 138 deselected` |
| `tests/dev_stack/test_dev_tasks.py::test_dev_demo_shows_the_last_lines_of_a_preparation_that_printed_no_json_object` | unit | `uv run pytest -rA -q -p no:cacheprovider <id>` | 2026-11-18T16:55:38Z | n/a | `PASSED` |
| `tests/demo/test_wheel_assets.py::test_the_wheel_carries_the_demo_schema_and_package` | unit (builds a wheel) | `uv run pytest tests/demo/test_wheel_assets.py -v` | 2026-11-18T16:25:04Z | `uv` on `PATH` | `PASSED` |
| `tests/image/test_demo_assets.py` (2 tests) and `tests/image/test_build_context.py::test_the_build_context_holds_only_what_the_image_needs` | image (`docker` marker) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=demo-024-test uv run pytest -m docker tests/image/test_demo_assets.py tests/image/test_build_context.py -v` | 2026-11-18T16:17:45Z | Docker 29.8.2, image `infrahub-sync:demo-024-test` | `3 passed in 3.49s` |
| Whole unit tier on the final tree | unit | `uv run invoke tests.tests-unit` | 2026-11-18T17:11:55Z | n/a | `9003 passed, 4 skipped, 385 deselected, 2 xfailed` |
| Python 3.10 legs | unit | `uv run invoke tests.check-310` | about 2026-11-18T16:58Z, before `b95f236b` | `.dev/check-310-venv` | `check-310 passed all three Python 3.10 legs` (6645 passed, 101 skipped) |
| Quickstart scenarios 1–4 (`dev.demo`, a re-run, the image against a live stack, a schema conflict) | e2e, manual | [quickstart.md](quickstart.md) | deferred: they would touch Compose projects that are not ours (§3) | — | — |

## 5. Review findings

Four review agents covered code, error handling, tests, and comments with types. Simplify was not run (§6). None of the findings was critical.

| Severity | File | Finding | Outcome |
| --- | --- | --- | --- |
| High | `infrahub_sync/demo/prepare.py` | A re-run with another `--reason` got 409 `idempotency-conflict` | Fixed: the key digest now includes the reason; the fake replays answers like the service |
| High | `prepare.py` | A schema that had not converged only warned, then the package was registered | Fixed: `schema.in_sync()` is checked and raises; the timeout is rounded up |
| Medium | `prepare.py` | A replayed 201 reported `versioned` | Fixed: the state comes from the version before and after the call |
| Medium | `prepare.py`, `cli.py` | `user:pass@` in an address reached output and errors | Fixed: refused with exit 2; userinfo stripped from error text |
| Medium | tests | No test of the package canary (FR-010) or of Sync API errors at the CLI | Fixed |
| Medium | `tasks/dev.py` | Unreadable output from `demo prepare` was dropped | Fixed: the last 4 lines go into the error |
| Medium | tutorial page | Claims about the admin token and the re-run | Fixed |
| Low | `prepare.py` | `same_name` counted only the first page | Fixed |
| Low | docstrings and the `build_inputs_hash` comment | Inaccurate wording | Fixed |
| Low | `tests/demo/test_wheel_assets.py` | Runs `uv build` in the unit tier with no marker; the isolated build may download | Deferred: follows the existing `tests/testcontainers/test_packaging.py` pattern |
| Low | `tests/demo/test_prepare.py` | The approval test reads the source text | Deferred |
| Low | `PreparationResult` | Repeats the fields of `Registration`; one invariant is not enforced | Deferred |

## 6. Autonomous decisions

- Spec number 024 in its own worktree, because numbers 020 to 023 were taken and other sessions use the main checkout.
- Scope is this repository only. The Styrmin drivers and the chart are a later spec in `styrmin`.
- Feedback goes through the channel named in the invitation to the round, not a public tracker (critique P2).
- `dev.demo` runs `demo prepare` on the host, because the development stack's source mount hides the bundled assets (critique E1).
- The review ran before chunk 5, and the orchestrator did chunk 5 (a news fragment and the verification) itself.
- Simplify review not run. It is advisory, it edits code, and the run was unattended.
- Live scenarios skipped, on the advisor's ruling (see `overnight-log.md`).
- `.yamllint.yml` now ignores `/.dev`. Without that, the Python 3.10 check environment holds a copy of the bundled `package.yml` that fails yamllint.
- Vale exceptions added for `Kubernetes` and `Styrmin`.

## 7. Suggested next steps

1. Run quickstart scenarios 1 to 4 on a machine without the `infrahub-sync-dev` and `infrahub-sync-netbox` projects, or after their owner removes them. Record the time for SC-001 and the plan counts.
2. Check which version the web interface's **Start run** uses after the addresses go A, then B, then A.
3. Open a pull request into `feature/v3-develop` with the `opsmill-dev-pr` skill, then run `/speckit.opsmill.extract`.
4. Dispatch a pre-release image (for example `3.0.0-rc.1`) as the publishing guide describes. Maintainer action.
5. Start the Styrmin spec in `styrmin`: an `infrahub-sync` driver that calls `infrahub-sync demo prepare`, and a NetBox driver.


## Appendix: evidence of chunk 2

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
