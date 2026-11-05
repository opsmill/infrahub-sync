# Implementation report: Compose stacks run the Prefect server from the Sync image (PR 1)

- Spec dir: `dev/specs/010-prefect-from-sync-image`
- Branch: `010-prefect-from-sync-image` (worktree `.claude/worktrees/agent-a1ae0124638af9832`)
- Base commit: `13bf22cb`. Head commit before this report: `396551c3` (37 commits, 88 files changed)
- Scope: the first pull request. The tasks marked PR 2 are deferred by product-owner decision.
- Wall-clock time: about 4.5 hours (2026-10-22 19:45Z to 2026-10-23 00:30Z)
- Status: DONE for the PR 1 scope. No local-pass evidence is missing.

## Chunk ledger

| # | Chunk | Tasks | Outcome | Commits | Flagged upward |
|---|-------|-------|---------|---------|----------------|
| 1 | Phase 1 Setup | T001–T003 | 3 ✅ | 0fc900c6, f9629860 | T001 ran on amd64 under OrbStack Rosetta emulation, not on a native amd64 host. The `infrahub-sync:3.0.0a5` image is not in the registry, so the stack ran on an image built from the checkout. `uv lock` moved slurpit-sdk to 0.9.156, which uses httpx 0.28. |
| 2 | Phase 2 Foundational | T004–T006 | 3 ✅ | 243574c9 | The tests reset Prefect's application cache for each case. The UI tests check what the server serves. `_compose_available` is renamed to `compose_cli_version`. |
| 2b | Product-owner instructions (2026-10-22) | not in tasks.md | 2 ✅ | 13d9bb09, 4008861a | The testcontainers plugin stays enabled. `tests/conftest.py` wraps `psutil.cpu_freq`, including SystemError. T054, T057, FR-025 and R4 are reworded. `uv lock --upgrade` changed 95 packages. New caps: `pydantic<2.13`, so numeric secrets stay masked, and `ujson<6`. FastAPI is pinned explicitly at 0.141.1. |
| 3a | US1 tests | T007–T013 | 7 ✅ | ab1228d8 | Target-state tests used strict xfail until their implementation landed. T009 already passed. |
| 3b | US1 root file | T014–T020 | 7 ✅ | c60828dc, 61401808, c1bfce4b | T015 had nothing to remove. T017 needed only docstring and test changes. New optional setting `INFRAHUB_SYNC_COMPOSE_PROJECT_PREFIX`. The image without the `service` extra is simulated by deleting `prefect` from the image. |
| 3c | US1 docs and measurement | T021–T024 | 4 ✅ | 552d2127, 273dfde8 | The docs say "an earlier alpha", because of the candidate-version test. `quickstart-compose.mdx` is updated too. |
| 4a | US2 development file | T025, T027, T029–T033 | 7 ✅ | d1441347, c3d00eac | `build_inputs_hash` and `rebuild_decision` are implemented early. The included Infrahub ports are overridden in the main file, which relies on Compose v5.5 behaviour. `COMPOSE_PROFILES=infrahub` is set in `preview.env`. |
| 4b | US2 `dev` namespace | T034, T035, T038, T040 | 4 ✅ | 844d7c40, 18531ec4 | New module `tasks/smoke_dataset.py`. Incident: a test reached the spec 012 preview stack, and the `infrahub-sync:dev` tag was taken from another image. The coordinator closed the incident with no harm. |
| 5 | US3 host processes | T041–T045 | 5 ✅ | 9d391938, e6bc6067, a45a1d87 | New `SYNC_IMAGE` variable. `dev.deps` runs `sync-bootstrap` once in a container. The task detects dependencies mode from `compose ps`. |
| 6 | US4 Prefect UI snippet | T046–T050 | 5 ✅ | 10ea2e7a, 370d031a, 6d4464f7 | `PREFECT_SERVER_UI_API_URL` followed the bind address, which broke FR-006. Fixed in chunk 8a. |
| 7a | US5 package | T051–T057 | 7 ✅ | 6030a2e3, 10e6768a, 912860f2 | The packaged copy is committed, not force-included. Fixture names follow the parent class: `infrahub_sync_compose` is written but not started, and `infrahub_sync_app` is the started project. The 3.10 lint profile excludes the new package. |
| 7b | US5 end-to-end and CI | T058–T060 | 3 ✅ | 22b37656, 7ca13256, 12136bfc | The fixtures worked on their first live run. The package syncs a tag from a branch to `main`. Infrahub 1.11.9. |
| 8a | Polish: documentation | T065, T066, T067 and four fixes | 2 ✅, 1 ⚠️ | d10413a1, b76a42b1, a68d6599, 0bfa9ec1, b493ddac, a348a998, 301a6137 | `invoke lint` ran `ruff check --diff`, so it skipped violations Ruff cannot fix. It now runs `ruff check .`, which is a behaviour change. `compose()` takes a `ComposeTarget`. The Prefect UI API address is always loopback. The httpx note on the Slurp'it page is removed. |
| 8b | Polish: final checks | T068, T069 | 2 ✅ | 6ec86240, 9ec376b5, 396551c3 | Two type-check findings fixed. All five quickstart sections pass. The deviations found are recorded in tasks.md. |
| 9 | Product-owner fixes (2026-10-22) | not in tasks.md | 3 ✅ | cf3cf1a7, 518249e8, 059f30ae | The test project's `.env` is single-quoted, and a value Compose cannot read single-quoted is refused. New `bench.run --infrahub-url` and `INFRAHUB_SYNC_DEV_INFRAHUB_HOST_URL`; research.md has no U1 section, so the name is this chunk's choice. The included Infrahub defaults to host port 8000. |
| 10 | Product-owner decision (2026-10-23): Sync API on host port 8001 | not in tasks.md | 1 ✅ | 680341de, 0aa51bda | The root file, its packaged copy and the development stack publish the Sync API on host port 8001; inside the container it still listens on 8000. The CLI has no default Sync API URL (`INFRAHUB_SYNC_API_URL` has no fallback), so nothing changed there. The development stack and the root deployment now share host port 8001, so running both at once needs `SYNC_API_PORT` or `INFRAHUB_SYNC_API_PORT` moved; the development stack page says so. |

## Tasks not completed

- Deferred to PR 2 by product-owner decision: T026, T028, T036, T037, T039, T061, T062, T063 and T064.
- T066 is partly done. The repository-tour part is done. The `check-310` to `tests.check-310` rename depends on T036, so it moves to PR 2. The task stays `[ ]` with an inline note.

## Local-pass evidence

All runs were on macOS arm64 with Docker 29.8.2 (OrbStack), unless the row says otherwise.

| Test id | Type | Run command | Passed at (ISO 8601) | Environment context | Verbatim pass line |
|---|---|---|---|---|---|
| tests/compose/test_fixture_prerequisites.py (3) | unit | `uv run pytest tests/compose/test_fixture_prerequisites.py -q` | 2026-10-22T19:58:42Z | Python 3.11 | `3 passed in 1.87s` |
| tests/service/test_prefect_server.py (4) | unit | `uv run invoke tests.tests-unit` | 2026-10-22T20:14:04Z | Python 3.11.8 | `PASSED` on each; `5677 passed` |
| tests/compose/test_stacks.py (3) | unit | `uv run invoke tests.tests-unit` | 2026-10-22T20:14:04Z | Python 3.11.8 | `PASSED` on each |
| tests/test_unit_tier.py cpu_freq wrapper (7) | unit | `uv run pytest tests/test_unit_tier.py tests/compose/test_fixture_prerequisites.py -v` | 2026-10-22T20:21Z | infrahub-testcontainers 1.11.9 plugin active | `10 passed in 3.20s` |
| tests/compose/test_compose_contract.py, tests/compose/test_image_pins.py (T007–T013) | unit (contract) | `uv run pytest tests/compose/test_compose_contract.py tests/compose/test_image_pins.py tests/test_release_pin.py -q` | 2026-10-22T20:51Z | Compose CLI only | `117 passed, 5 xfailed in 2.62s` |
| tests/test_release_pin.py (T017) | unit | `uv run pytest tests/test_release_pin.py -q` | 2026-10-22T20:52Z | n/a | `29 passed in 0.07s` |
| tests/compose/test_prefect_server_start.py (2), test_bootstrap_idempotence.py::test_on_an_empty_silo_volume_..., full compose suite | compose (integration) | `INFRAHUB_SYNC_COMPOSE_PROJECT_PREFIX=s010-infrahub-sync INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s010-compose-test uv run pytest -m compose tests/compose -x -rA` | 2026-10-22T21:04:03Z | arm64 image built from the checkout | `40 passed, 247 deselected in 524.28s` |
| tests/compose/test_documentation.py Silo and bounded-wait tests (2) | unit | `uv run pytest tests/compose/test_documentation.py -k 'silo or bounded_wait' -v` | 2026-10-22T21:11Z | n/a | `PASSED` on each |
| tests/dev_stack/test_profiles.py (15), test_build_inputs.py (10), updated dev_stack tests | unit | `uv run pytest -q tests/dev_stack tests/compose/test_image_pins.py tests/compose/test_compose_contract.py tests/test_benchmark_runner.py` | 2026-10-22T21:32:12Z | Compose v5.5.0 CLI | `317 passed, 1 xfailed in 16.67s` |
| tests/dev_stack/test_dev_tasks.py (23) | unit | `uv run pytest -m "not integration and not preview and not docker and not compose" tests/dev_stack/test_dev_tasks.py -q` | 2026-10-22T22:03Z | n/a | `23 passed in 0.07s` |
| tests/test_benchmark_runner.py, tests/preview (not live) | unit | same marker filter over those paths | 2026-10-22T22:03:08Z | n/a | `266 passed, 23 deselected in 14.05s` |
| Preview smoke suite through `dev.smoke` (container mode) | e2e | `uv run invoke dev.smoke` | 2026-10-22T21:55:02Z | project `s010-dev` | `23 passed, 70 deselected in 187.30s` |
| tests/dev_stack/test_deps_mode.py (14) | unit | `uv run pytest tests/dev_stack/test_deps_mode.py tests/dev_stack/test_dev_tasks.py -v -m "not integration and not preview and not docker and not compose"` | 2026-10-22T22:19:18Z | n/a | `36 passed in 0.08s` |
| `dev.smoke` in dependencies mode, with the worker stopped and with the API stopped | e2e | `uv run invoke dev.smoke` | 2026-10-22T22:17:46Z to 22:18:25Z | project `s010-deps` | `23 passed`; the two failures named `dev.worker` and `dev.api` as expected |
| tests/compose/test_compose_contract.py snippet tests (2), test_documentation.py snippet tests (2) | unit | `uv run pytest tests/compose/test_compose_contract.py tests/compose/test_documentation.py -k "snippet or prefect_ui_section" -v` | 2026-10-22T22:32:34Z | Compose CLI | `4 passed, 164 deselected in 0.16s` |
| tests/testcontainers/test_packaging.py, test_image_selection.py, tests/test_unit_tier.py, tests/test_release_pin.py, tests/test_linter_tasks.py and related tests | unit | `uv run pytest <paths> --no-cov -q -rA` | 2026-10-22T22:58:56Z | Python 3.11 | `69 passed, 2 warnings in 0.97s` |
| tests/compose/test_testcontainers_fixture.py (6) | e2e (compose) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s010-qs uv run pytest -m compose tests/compose/test_testcontainers_fixture.py -rA` | 2026-10-23T00:19:10Z | Infrahub 1.11.9 | `6 passed, 15 warnings in 201.37s` |
| tests/test_workflow_contracts.py | unit | `uv run pytest tests/test_workflow_contracts.py` | 2026-10-22T23:17:48Z | n/a | `204 passed in 12.30s` |
| tests/test_development_stack_docs.py (8 new), tests/cli/test_parity_and_closure.py | unit | `uv run pytest tests/test_development_stack_docs.py tests/cli/test_parity_and_closure.py -q` | 2026-10-22T23:44:59Z | n/a | `21 passed in 0.39s` |
| test_compose_contract.py::test_the_prefect_ui_api_address_stays_loopback_with_another_bind_address | unit | `uv run pytest tests/compose/test_compose_contract.py -k prefect_ui -v` | 2026-10-22T23:27:59Z | Compose CLI | `5 passed, 73 deselected in 0.40s` |
| tests/test_unit_tier.py::test_patch_cpu_freq_replaces_the_module_function | unit | `uv run pytest tests/test_unit_tier.py -rA` | 2026-10-22T23:55:49Z | Python 3.11 | `13 passed` |
| Full unit tier, Python 3.11 profile | unit | `uv run invoke tests.tests-unit` | 2026-10-23T00:22:44Z | n/a | `5832 passed, 4 skipped, 212 deselected, 1 xfailed` |
| tests/testcontainers/test_image_selection.py `.env` quoting tests (7 new), tests/test_benchmark_runner.py host URL tests (5 new), tests/dev_stack/test_profiles.py port tests (2 new), updated dev_stack and benchmark tests | unit | `uv run pytest tests/testcontainers tests/test_benchmark_runner.py tests/dev_stack -m "not integration and not preview and not docker and not compose" -q` | 2026-10-23T15:11:33Z | Compose v5.5.0 CLI | `294 passed, 2 warnings in 16.15s` |
| tests/compose/test_testcontainers_fixture.py (7), including `TestSettingsReachTheProject::test_an_exported_value_with_a_dollar_sign_reaches_the_worker_unchanged` | e2e (compose) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s010-c9 uv run pytest -m compose tests/compose/test_testcontainers_fixture.py -rA` | 2026-10-23T15:13:42Z | arm64 image `infrahub-sync:s010-c9` built from the checkout | `7 passed, 15 warnings in 201.62s (0:03:21)` |
| Full unit tier after chunk 9 | unit | `uv run invoke tests.tests-unit` | 2026-10-23T15:09:41Z | Python 3.11 | `5846 passed, 4 skipped, 213 deselected, 1 xfailed, 6 warnings` |
| tests/compose/test_compose_contract.py::test_the_api_publishes_on_host_port_8001_by_default (new), tests/dev_stack/test_profiles.py::test_the_sync_api_publishes_on_loopback, test_dev_tasks.py, test_deps_mode.py, tests/test_benchmark_runner.py (updated to 8001) | unit | `uv run pytest -m "not integration and not preview and not docker and not compose" tests/compose tests/dev_stack tests/testcontainers tests/test_benchmark_runner.py tests/preview tests/test_development_stack_docs.py -rA` | 2026-10-23T18:21:27Z | Compose v5.5.0 CLI | `627 passed, 75 deselected, 1 xfailed, 2 warnings in 26.56s` |
| Full unit tier after chunk 10 | unit | `uv run invoke tests.tests-unit` | 2026-10-23T18:19:08Z | Python 3.11 | `5847 passed, 4 skipped, 213 deselected, 1 xfailed, 6 warnings` |
| tests/compose/test_testcontainers_fixture.py (7) | e2e (compose) | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s010-c10 uv run pytest -m compose tests/compose/test_testcontainers_fixture.py -rA` | 2026-10-23T18:26:43Z | arm64 image `infrahub-sync:s010-c10` built from the checkout | `7 passed, 15 warnings in 210.43s (0:03:30)` |
| Full compose suite after chunk 10 | compose (integration) | `INFRAHUB_SYNC_COMPOSE_PROJECT_PREFIX=s010-infrahub-sync INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s010-c10 uv run pytest -m compose tests/compose -rA` | 2026-10-23T18:39:01Z | arm64 image `infrahub-sync:s010-c10` | `47 passed, 259 deselected, 15 warnings in 729.81s (0:12:09)` |
| Full unit tier, Python 3.10 profile | unit | same command in a 3.10 venv (dev and prefect extras) | 2026-10-22T23:55:59Z | Python 3.10.21 | `4846 passed, 82 skipped, 183 deselected, 1 xfailed` |
| Full unit tier after the Infrahub 1.11.9 pin (1cd95c71, 08334b2d) | unit | `uv run invoke tests.tests-unit` | 2026-11-02T15:26:22Z | Python 3.11, infrahub-testcontainers 1.11.9 | `5847 passed, 4 skipped, 213 deselected, 1 xfailed, 6 warnings` |
| Full compose suite after the Infrahub 1.11.9 pin, including tests/compose/test_testcontainers_fixture.py | compose (integration) | `INFRAHUB_SYNC_COMPOSE_PROJECT_PREFIX=s010b-infrahub-sync INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=s010b-1119 uv run pytest -m compose tests/compose -rA` | 2026-11-02T15:36:18Z | arm64 image `infrahub-sync:s010b-1119` built from 08334b2d; Infrahub fixture `registry.opsmill.io/opsmill/infrahub:1.11.9` | `47 passed, 259 deselected, 15 warnings in 789.19s (0:13:09)` |

The compose rows for chunks 7b to 10 ran before commit 1cd95c71 pinned Infrahub 1.11.9. At that time the compose fixture used Infrahub 1.10.6 and the lock file held infrahub-testcontainers 1.11.8. The two rows dated 2026-11-02 are the runs on Infrahub 1.11.9. The run left no containers, volumes or networks, and the `infrahub-sync:s010b-1119` image was removed afterwards. The Python 3.10 unit tier was not run again.

The one remaining xfail is the preview-file case in `tests/compose/test_image_pins.py`. It passes once PR 2 deletes the preview file (T039).

## Review findings

The review (`speckit-review-run` over `13bf22cb..HEAD`) found nothing critical or high. Nothing was fixed inline. Chunk 9 fixed the two medium findings; the low findings are deferred.

| Severity | File | Summary | Handling |
|---|---|---|---|
| medium | `infrahub_sync/testcontainers/helpers.py:170` | The generated `.env` holds values without quotes, so Compose changes an exported value that contains `$`. | Fixed in cf3cf1a7 |
| medium | `tasks/bench.py:107` | The benchmark runner follows `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` from the shell and then targets an Infrahub the host cannot reach. | Fixed in 518249e8 |
| low | `infrahub_sync/testcontainers/helpers.py:244-248` | When log collection fails during a failed start, `stop()` does not run. | Deferred |
| low | `tasks/dev.py:414` | `_print_urls` prints the default bearer token even when `BEARER_TOKENS` is overridden. | Deferred |
| low | `pyproject.toml` | The `cpu_freq` patch applies to every test run. Report the crash upstream and mark the patch as temporary. | Deferred |
| low | `pyproject.toml` | No comment explains the `testcontainers>=4.8,<4.9` range. | Deferred |
| low | `docker-compose.yml:104,125` | `object-store-init` needs `/bin/sh` in the Silo image. The live compose tier checks it, and it passed locally. | Deferred. The product owner confirmed the Silo image (decision D-5). |

## Autonomous decisions

- The chunks were split more finely than the phases: US1 into 3a, 3b and 3c; US2 into 4a and 4b; US5 into 7a and 7b; Polish into 8a and 8b. Chunk 2b ran the product-owner instructions.
- Each target-state contract test was committed with a strict xfail marker, so the unit tier stays green. Every marker was removed when its implementation landed, except the preview-file marker kept for PR 2.
- In PR 1, T065 keeps `preview-stack.md` and adds a note that the development stack replaces it. T066 leaves the `check-310` rename to PR 2.
- T001 counts as passed under Rosetta emulation. A native amd64 run is still to do in CI.
- The amd64 first-start times are not measured. R11 holds the CI commands.
- The medium review findings were deferred under the high-severity-only rule, then fixed in chunk 9 on product-owner instruction.
- The included Infrahub now defaults to `${INFRAHUB_PORT:-8000}`, matching branch `012-v2-configuration-conversion`, so the ports agree.
- The benchmark runner's host Infrahub URL comes from `--infrahub-url`, else `INFRAHUB_SYNC_DEV_INFRAHUB_HOST_URL`. Research U1 names no variable (research.md has no U1 section), so chunk 9 chose this name. Either one also replaces the published port when no Infrahub network is selected.
- In a test project's `.env`, a value is single-quoted with `'` written as `\'`. Compose cannot read a single-quoted value that ends with a backslash or holds a backslash before `'`, so such a value is refused, naming the variable but not the value.
- `invoke lint` now runs `ruff check .`, the same command as CI. This makes the gate stricter.
- Product-owner decision 2026-10-23: the Sync API defaults to host port 8001 in the root deployment and in the development stack, and host port 8000 is for Infrahub. The port the Sync API listens on inside the container stays 8000, because only the host side conflicts with Infrahub; the in-network address `http://sync-api:8000` and the health checks are unchanged. Spec FR-006a records the decision.

## Measured first-start times (FR-008, arm64 laptop)

| Stack | Warm start (images already pulled) | Cold start (images pulled during the run) |
|---|---|---|
| Baseline, Prefect from `prefecthq/prefect` | 20.5 s | 42.2 s |
| This feature | 20.6 s | 28.8 s |

In both cold runs the Sync image was built locally from the checkout and kept, so only the third-party images were pulled. Times on an amd64 GitHub runner have not been measured; the commands are in R11.

## Suggested next steps

1. Run T001 and the R11 measurements on a native amd64 runner.
2. Review the branch and open PR 1. After it merges, start PR 2 with T026, T028, T036, T037, T039, T061–T064 and the `check-310` part of T066.
