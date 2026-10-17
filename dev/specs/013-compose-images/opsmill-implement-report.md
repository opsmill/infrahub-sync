# Implementation report: spec 013, Compose images

- **Feature:** fewer images in the Compose deployment. The Prefect server runs from the Sync
  image, a team can use its own S3-compatible store, every bundled adapter's credentials and a
  custom CA reach the worker, and the release records the image digest and SBOM.
- **Spec directory:** `dev/specs/013-compose-images`
- **Branch:** `013-compose-images`, created from `v3-mvp`. Not pushed.
- **Base commit:** `bad4e0b3`
- **Head commit before this report:** `32472281`
- **Change size:** 60 files, 5,945 lines added, 402 removed.
- **Wall-clock time:** from 2026-10-15T19:00Z to 2026-10-16T16:05Z, including a pause overnight.
- **Status:** COMPLETE for every task that can run before merge. Six tasks are open:
  - four were not done by product owner decision;
  - two can only run after merge or after a release.

## Chunk ledger

Each chunk ran in a subagent that started without this session's history. The chunks ran one
after another.

| # | Chunk | Tasks | ✅ / ⚠️ / ❌ | Commits | Points the subagent raised |
| --- | --- | --- | --- | --- | --- |
| 1 | Setup | T001, T002 | 2 / 0 / 0 | `f1f6d709` | Baseline: unit tier 5,670 passed; compose tier 37 passed on arm64. `.specify/feature.json` is missing, so speckit scripts need `SPECIFY_FEATURE_DIRECTORY`. |
| 2 | Lifecycle measurement, first half | T003, T004 dispatch | 1 / 0 / 0 | `c422cede`, `87ba5f8a`; pushed branch `ci/compose-repeat-measure` (`9438b62f`) | The sha guard accepts only commits merged into `feature/v3-develop`, so the run tested `8dc921b9` with the new workflow. `-x` is removed from the nightly run too. Each iteration's artifact is uploaded with a pinned github-script step. |
| fix | Workflow contract test | (T003 follow-up) | 1 / 0 / 0 | `1d26870a` | `test_only_the_newest_stable_release_is_called_newest` failed in two early runs and then passed. The cause is not known. |
| 3 | CA trust store | T005–T013 | 9 / 0 / 0 | `374b3774` | `worker.py`, which spec 009 owns, gets one import of `sys` and `trust_store` and one call. A docs row was added early to keep the documentation test passing. |
| 4 | US1: Prefect server from the Sync image | T014–T024 | 11 / 0 / 0 | `bd5c2400` | No V3 release tag ships a root `docker-compose.yml`. The upgrade test therefore reads the previous file from git history, and `INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF` overrides that. Three image label tests fail on any local build. |
| 5 | US2: Prefect UI closed by default | T025–T031 | 7 / 0 / 0 | `b34f5f7f` | The `prefect-ui.yml` snippet is copied into `tests/compose/conftest.py`, so the tests do not depend on the spec path. |
| 6a | US3: S3 client and bootstrap | T032, T033, T038, T039 | 4 / 0 / 0 | `05b1fbf5` | `build_s3_client` hides SDK construction errors. The review fixes later reworked the mapping from errors to families. |
| 6b | US3: Compose files | T034, T035, T040–T042 | 5 / 0 / 0 | `2ee57fbc` | Compose 2.24.0 accepts the interpolated `scale` and `required: false`. |
| 6c | US3: compose tests and docs | T036, T037, T043–T046 | 6 / 0 / 0 | `c9918c86` | The external MinIO runs over HTTPS with a test CA, in both addressing styles. |
| 7 | US5: worker credentials and custom CA | T047–T055 | 9 / 0 / 0 | `1535a0fa` | The HTTPS source for T049 is a Python HTTPS server started from the Sync image and read through the `genericrestapi` adapter. |
| 8 | US4: development and preview stacks | T056–T063 | 7 / 1 / 0 | `8a4c7db1` | T063 ⚠️: host port 8080 is taken on this machine, so the preview ran Infrahub on 8081. Both Prefect health checks returned 200. `invoke start` does not rebuild an existing development image. |
| 10 | Lifecycle measurement, second half | T004 | 1 / 0 / 0 | `7f2d565d` | Run [38806056523](https://github.com/opsmill/infrahub-sync/actions/runs/38806056523) passed 10 of 10 on amd64 and 10 of 10 on arm64. None of C1 to C5 was observed. |
| 11 | Readiness times and release image | T066, T069, T071–T073 | 5 / 0 / 0 | `7b011c62` | The readiness bound must be at least 1.5 times the slowest measured start (220.5 s), so 420 s stays. `trigger-release.yml` now grants `contents: write`, which only `record_image` uses. |
| 12 | Leak test, docs, ADRs, changelog | T074–T080 | 7 / 0 / 0 | `c808c052` | No credential was found anywhere. "Plan verification details" now says to wait for the verification before `apply`; an earlier `apply` gets 409. |
| 13 | Gates, tiers and quickstart | T081–T084 | 4 / 0 / 0 | `4ca5488c` | `test_managed_run.py` now expects the read-only CA mount. The compose tier passes, 48 tests. The quickstart differences are listed in `implementation-notes.md`. |
| review | Review fixes | 11 findings | 11 / 0 / 0 | `32472281` | See "Review findings". |

## Tasks not completed

- **T064, T065, T067, T068:** not done. The product owner decided on 2026-10-16 that no failure
  was observed in 20 iterations of run 38806056523, so the hardening for causes C1, C2 and C4 is
  not needed.
- **T070:** deferred until after the merge into `feature/v3-develop`. The nightly sha guard
  rejects unmerged commits, so a rerun now would test `feature/v3-develop` again, not this
  branch.
- **T085:** after the first pre-release that runs `record_image`, run quickstart scenario 13.
- **T063 is partial, for an environment reason only.** Port 8080 on this machine is held by an
  unrelated container. The preview ran with `PREVIEW_INFRAHUB_PORT=8081`, and both health checks
  passed.

## Local-pass evidence

Common environment: macOS on arm64, Python 3.11.8, Docker 29.4.0 (OrbStack), Compose 5.1.2. The
image is `infrahub-sync:compose-test`, rebuilt from the branch before each compose run. Rows that
cover a parametrized test list the cases in brackets. All times are UTC.

| Test id | Type | Run command | Passed at | Environment context | Verbatim pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/test_workflow_contracts.py::test_nightly_runs_the_compose_suite_against_an_image_it_builds` | unit | `uv run pytest <node> -n 0 -v` | 2026-10-15T21:14:28Z | n/a | `...test_nightly_runs_the_compose_suite_against_an_image_it_builds PASSED` |
| `tests/service/test_trust_store.py` (10 tests from chunk 3, plus 9 tests from the review fixes: indented key header, key marker anywhere, unreadable directory, unreadable file, upper-case suffixes, explicit directory wins, empty variable kept, bundle replaced with mode 0600, planted link replaced) | unit | `uv run pytest tests/service/test_trust_store.py ... -n 0 -v` | 2026-10-15T21:05:43Z and 2026-10-16T16:03:41Z | n/a | `82 passed in 1.89s` (chunk 3) and `37 passed, 60 deselected in 0.84s` (review fixes) |
| `tests/compose/test_compose_contract.py`: CA mount tests (`test_the_api_and_the_worker_share_no_writable_mount_source`, `test_a_sync_service_mounts_only_the_read_only_ca_directory[sync-bootstrap\|sync-api\|sync-worker\|cli]`, `test_only_postgresql_and_the_object_store_keep_a_named_volume`, `test_no_service_mounts_a_host_path`, `test_the_ca_directory_is_mounted_read_only_on_exactly_the_three_sync_services`, `test_the_ca_directory_setting_reaches_exactly_the_three_sync_services`, `test_without_the_ca_setting_the_source_is_the_empty_named_volume`, `test_the_ca_setting_binds_the_operator_directory`, `test_no_sync_service_takes_a_filesystem_input`) | unit | `uv run pytest tests/service/test_trust_store.py tests/compose/test_compose_contract.py -n 0 -v` | 2026-10-15T21:05:43Z | n/a | `82 passed in 1.89s` |
| Trust store at each entry point: `test_compose_bootstrap.py::test_the_trust_store_is_installed_before_anything_converges`, `::test_a_private_key_in_the_ca_directory_stops_the_bootstrap_without_its_content`; `test_environment_names.py::test_the_server_installs_the_trust_store_before_it_builds_the_app`, `::test_a_private_key_in_the_ca_directory_stops_the_server_without_its_content`; `test_service_worker.py::test_the_worker_installs_the_trust_store_before_it_reads_its_arguments`, `::test_a_private_key_in_the_ca_directory_stops_the_worker_without_its_content` | unit | `uv run pytest <3 files> -n 0 -v -k "trust_store or private_key"` | 2026-10-15T21:05:46Z | n/a | `6 passed, 34 deselected in 0.69s` |
| `tests/service/test_prefect_server.py` (4 tests), `test_compose_contract.py` US1 tests (`prefect-server` in `ALL_SYNC_SERVICES`, no stock image, command form, only two credentials), `tests/test_release_pin.py` | unit | `uv run pytest tests/compose/test_compose_contract.py tests/service/test_prefect_server.py tests/test_release_pin.py -n 0` | 2026-10-15T21:43:08Z | n/a | `111 passed in 7.30s` |
| `tests/image/test_image_artifact.py::test_the_image_puts_the_prefect_ui_files_on_a_writable_root`, `::test_the_prefect_server_module_serves_the_api_and_the_ui_under_a_read_only_root`, `::test_the_prefect_server_factory_runs_under_the_uvicorn_command` | image | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m docker tests/image/test_image_artifact.py -n 0` | 2026-10-15T21:30:44Z; repeated 2026-10-16T14:41:30Z | image `1be3f670`; later `79927274` | `3 failed, 28 passed`. The three new tests passed; the failures are the three label tests that every local build fails. |
| `tests/compose/test_lifecycle.py::test_a_plan_apply_and_separate_sync_run_through_the_replacement_worker` (version check added) | compose | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose/test_lifecycle.py -n 0` | 2026-10-15T21:31:18Z; full tier 2026-10-16T15:00:18Z | image `1be3f670`; later `79927274` | `12 passed in 320.94s`; `12 passed in 339.51s` |
| `tests/compose/test_prefect_server_upgrade.py::test_the_prefect_records_survive_the_move_to_the_sync_image_server` | compose | `... uv run pytest -m compose tests/compose/test_prefect_server_upgrade.py -n 0 --compose-zero-skip` | 2026-10-15T21:45:53Z; full tier 2026-10-16T15:13:05Z | the previous file is read from git history | `1 passed in 139.69s`; `1 passed in 163.10s` |
| US2 contract tests: `test_only_the_api_publishes_a_host_port`, `test_the_prefect_ui_calls_its_api_on_the_opt_in_loopback_port[default\|port-setting\|bind-address]`, `test_the_prefect_ui_override_publishes_prefect_on_loopback_only[None\|127.0.0.1\|0.0.0.0]`, `test_every_published_address_is_loopback`; `test_documentation.py::test_the_page_prints_the_prefect_ui_override_and_its_two_commands` | unit | `uv run pytest -n 0 -v <node ids>` | 2026-10-15T22:06:02Z | n/a | `9 passed in 0.57s` |
| `tests/compose/test_configuration_free_startup.py::test_a_started_deployment_publishes_only_the_sync_api` (and the file's modified port handling) | compose | `... uv run pytest -m compose tests/compose/test_configuration_free_startup.py -n 0 --compose-zero-skip` | 2026-10-15T21:54:30Z; full tier 2026-10-16T15:45:04Z; after the review fixes 2026-10-16T15:58:57Z | image `9d615fb1`; later `fe54956b` | `6 passed in 120.63s`; `6 passed in 130.82s`; `6 passed in 126.50s` |
| `tests/service/test_storage.py::test_build_s3_client_*` (`passes_endpoint_region_and_addressing_style[default\|path\|virtual]`, `leaves_endpoint_and_region_unset_when_absent`, `refuses_another_addressing_style_by_name_only[4]`, `refuses_an_endpoint_with_userinfo[2]`, `hides_sdk_construction_failures`), `::test_service_product_projection_builds_its_client_through_build_s3_client`, `::test_service_storage_factory_validates_settings_and_hides_startup_details` | unit | `uv run pytest tests/service/test_compose_bootstrap.py tests/service/test_storage.py -q -n 0` | about 2026-10-15T22:10Z | n/a | `100 passed` |
| `tests/service/test_compose_bootstrap.py`: bucket and create-only tests from chunk 6a, plus the review-fix tests (`test_a_bucket_check_redirected_to_another_region_is_unavailable`, `test_a_bucket_check_error_without_a_status_is_unavailable`, `test_a_refused_or_failed_bucket_create_stops_with_its_family_only[3]`, `test_an_unreachable_store_during_the_bucket_create_is_unavailable`, `test_a_failed_check_write_is_unavailable_and_still_deletes_the_key[2]`, `test_a_refused_or_unsupported_check_write_stops_with_its_family_and_deletes_the_key[2]`, `test_a_refused_check_key_delete_stops_with_the_access_refused_family`, `test_a_failed_check_write_keeps_its_own_family_when_the_delete_fails_too`, `test_each_object_store_failure_prints_its_family_only[5]`, `test_a_setting_the_shared_builder_refuses_is_named_without_its_value[4]`, `test_a_client_the_sdk_cannot_build_stops_with_the_unavailable_family`) | unit | `uv run pytest -p no:randomly -n 0 -v <files> -k ...` | 2026-10-16T16:03:41Z | n/a | `37 passed, 60 deselected in 0.84s` |
| `tests/compose/test_bootstrap_idempotence.py` (bootstrap behaviour changed) | compose | `... uv run pytest -m compose tests/compose/test_bootstrap_idempotence.py -n 0 --compose-zero-skip` | 2026-10-15T22:17:11Z; after the review fixes 2026-10-16T15:56:46Z | image `fe54956b` | `6 passed in 123.86s` |
| US3 contract tests: `test_the_object_store_settings_reach_the_three_sync_services_with_the_bundled_defaults`, `test_the_object_store_settings_follow_the_env_file`, `test_the_bundled_object_store_setting_scales_both_object_store_services[unset\|bundled\|left-out]`, `test_the_bootstrap_job_does_not_require_the_bundled_object_store[None\|0]`, `test_the_object_store_credential_reaches_exactly_the_sync_services_and_the_bundled_store[2]`, `test_object_store_uses_the_pinned_minio_build_and_migrates_existing_data` | unit | `uv run invoke tests.tests-unit` | 2026-10-15T22:34:27Z | n/a | `5754 passed, 4 skipped, 210 deselected` |
| `tests/compose/test_compose_minimum.py::test_the_minimum_release_accepts_an_interpolated_scale_and_an_optional_dependency[unset\|left-out]` | compose | `... uv run pytest -m compose tests/compose/test_compose_minimum.py -n 0 --compose-zero-skip -v` | 2026-10-15T22:28:36Z | pinned Compose 2.24.0 image | `10 passed in 16.52s` |
| `tests/compose/test_object_store_upgrade.py::test_a_plan_from_the_previous_release_is_read_after_the_move` | compose | `... uv run pytest -m compose tests/compose/test_object_store_upgrade.py -n 0 --compose-zero-skip` | 2026-10-15T22:57:26Z; full tier 2026-10-16T15:10:37Z | image `79335d20` | `1 passed in 138.28s` |
| `tests/compose/test_external_object_store.py::test_a_run_keeps_its_files_in_the_external_store[path\|virtual]`, `::test_bootstrap_refuses_an_external_endpoint_while_the_bundled_store_runs` (changed by the review fixes) | compose | `... uv run pytest -m compose tests/compose/test_external_object_store.py -n 0 --compose-zero-skip` | 2026-10-16T15:50:15Z | image `fe54956b`; external MinIO `cgr.dev/chainguard/minio:latest-dev@sha256:d7c90699...` | `3 passed in 220.77s (0:03:40)` |
| US5 contract tests: 17 names built from the registry, receiver test per credential, the three reworked source-credential tests | unit | `uv run pytest tests/compose/test_compose_contract.py tests/compose/test_documentation.py -n 0 -q -m "not compose"` | 2026-10-15T23:15:53Z | n/a | `268 passed in 5.39s` |
| `tests/compose/test_custom_ca.py::test_a_custom_ca_is_trusted_only_when_named_and_a_private_key_stops_the_services` | compose | `... uv run pytest -m compose tests/compose/test_custom_ca.py -n 0 --compose-zero-skip` | 2026-10-16T15:54:04Z | image `fe54956b`; Python HTTPS source started from the Sync image | `1 passed in 152.91s (0:02:32)` |
| `tests/compose/test_source_credentials.py::test_the_worker_resolves_every_credential_of_every_bundled_adapter` | compose | `... uv run pytest -m compose tests/compose/test_source_credentials.py -n 0 --compose-zero-skip` | 2026-10-15T23:17:46Z; full tier 2026-10-16T15:15:59Z | image `551a3ecb` | `2 passed, 19 deselected in 142.58s` |
| US4 tests: `test_no_service_runs_the_stock_prefect_image[3]`, `test_the_prefect_server_runs_the_sync_factory_module[3]`, `test_the_checkout_stacks_build_their_prefect_server_from_the_repository_dockerfile[2]`, `test_no_repository_file_names_the_retired_prefect_image_settings`, `test_every_image_other_than_sync_is_named_by_digest[3]`, `test_destination_and_preview_default_images_are_tagged_indexes[4]`, `test_preview_env_tags_match_compose_pin_defaults`, `test_image_overrides_keep_their_matching_digests`, `test_local_image_override_can_drop_the_digest`, `test_standalone_version_override_drops_the_shipped_digest`; `tests/preview/test_preview_configuration.py::test_preview_prefect_server_is_built_from_the_checkout`, `::test_preview_up_uses_a_bounded_compose_wait`, `::test_preview_rejects_tag_only_image_overrides` | unit | `uv run pytest -n 0 -v <nodes>` | 2026-10-15T23:30:39Z | n/a | `23 passed in 1.23s` |
| T063: Prefect health of the development stack (4230) and the preview stack (4210) | manual | `urlopen('http://127.0.0.1:4230/api/health')`; `urlopen('http://127.0.0.1:4210/api/health')` | 2026-10-15T23:26:37Z; 2026-10-15T23:28:15Z | preview Infrahub on port 8081 (8080 is taken) | `200 true`; `200 true` |
| `tests/compose/test_readiness_bounds.py`: `test_the_deployment_start_uses_the_declared_bound_and_a_cushioned_process`, `test_the_bound_keeps_its_margin_over_the_slowest_measured_start`, `test_each_service_time_to_healthy_is_read_from_the_events_of_one_up`, `test_no_events_means_no_service_lines`; `tests/compose/test_fixture_prerequisites.py::test_a_named_image_runs_the_fixture_through_start_seed_and_teardown` | unit | `uv run pytest tests/compose/test_readiness_bounds.py ... --no-cov` | 2026-10-16T08:48:43Z | n/a | `11 passed in 0.06s` |
| Readiness tests from the review fixes: `test_a_watcher_that_outlives_terminate_is_killed_and_reaped`, `test_a_readiness_record_failure_never_replaces_the_failure_it_wraps`, `test_a_readiness_record_failure_is_reported_when_nothing_else_failed`; `tests/dev_stack/test_compose_valid.py::test_an_exported_object_store_setting_does_not_reach_the_stack` | unit | `uv run pytest -p no:randomly -n 0 -v <files> -k ...` | 2026-10-16T16:03:41Z | n/a | `37 passed, 60 deselected in 0.84s` |
| Workflow contract tests for `record_image` and the trigger permission (7 new tests) | unit | `uv run pytest tests/test_workflow_contracts.py -q --no-cov` | 2026-10-16T08:43:24Z | n/a | `210 passed in 13.44s` |
| `tests/compose/test_credential_leaks.py::test_no_credential_reaches_output_logs_or_a_service_that_does_not_receive_it` | compose | `... uv run pytest -m compose tests/compose/test_credential_leaks.py -n 0 --compose-zero-skip -p no:cacheprovider` | 2026-10-16T14:31:31Z; full tier 2026-10-16T14:49:18Z | image `d515054b`; later `79927274` | `1 passed in 231.19s (0:03:51)` |
| `tests/compose/test_managed_run.py::test_the_deployed_worker_container_mounts_only_the_read_only_ca_directory` | compose | `... uv run pytest -m compose tests/compose/test_managed_run.py -n 0 --compose-zero-skip` | 2026-10-16T15:08:33Z | image `79927274` | `4 passed in 120.65s` |
| Full compose tier, 18 files, one file per run | compose | `... uv run pytest -m compose tests/compose/<file> -n 0 --compose-zero-skip` | 2026-10-16T14:42Z to 15:16Z | image `79927274` | 48 passed, 0 failed, 0 skipped, after the `test_managed_run.py` fix |
| Unit tier, final | unit | `uv run invoke tests.tests-unit` | 2026-10-16T16:01:12Z | full development profile | `5884 passed, 4 skipped, 217 deselected, 4 warnings in 137.28s` |
| Lifecycle measurement (T004) | CI e2e | `gh workflow run workflow-nightly-e2e.yml --ref ci/compose-repeat-measure -f sha=8dc921b9... -f compose_repeat=10` | 2026-10-15T20:57:02Z (dispatched) | GitHub runners `ubuntu-22.04` and `ubuntu-24.04-arm`; tested `8dc921b9` | Each iteration ended `37 passed, 216 deselected`, 10 of 10 on each architecture |

## Review findings

The review ran `speckit-review-run` over `bad4e0b3..32472281^` in report-only mode. It found no
critical or high finding: 9 medium and 17 low. The docs reviewer rated finding 1 high, and the
review lead lowered it to medium after checking the code. The fixes are in `32472281`.

| # | Severity | File | Summary | Outcome |
| --- | --- | --- | --- | --- |
| 1 | medium | `infrahub_sync/service/bootstrap.py` | A bad S3 setting printed `object-store-unavailable`, while the docs promise a message that names the setting | Fixed: new family `object-store-setting-invalid`, followed by the builder's text |
| 2 | medium | `infrahub_sync/service/trust_store.py` | A CA directory or file that cannot be read stopped the service with a traceback | Fixed: `CustomCertificateError` names the file; the docs state the UID 10001 requirement |
| 3 | medium | `infrahub_sync/service/trust_store.py` | An indented private key header was not detected | Fixed: lines are stripped, and `PRIVATE KEY-----` anywhere in a file is refused |
| 4 | medium | `infrahub_sync/service/trust_store.py` | The bundle was written to a fixed path that a planted link could redirect | Fixed: `mkstemp` then `os.replace` |
| 5 | medium | `infrahub_sync/service/bootstrap.py` | Every failed Put, Delete or Create call became `object-store-unavailable` | Fixed: 403 gives access-refused; 501 or `NotImplemented` gives unsupported |
| 6 | medium | `docs/docs/operations/compose-troubleshooting.mdx` | No entry for `object-store-unavailable` | Fixed: rows added to the existing table |
| 7 | medium | `tests/compose/test_external_object_store.py` | An assertion in the refused case could not fail | Fixed: a fresh bucket must stay absent |
| 8 | medium | `tests/service/*` | The CA trust variables leaked between tests | Fixed: autouse fixture in `tests/service/conftest.py` |
| 9 | medium | `tests/compose/conftest.py` | The readiness watcher could hang, or hide the test's own failure | Fixed: kill on timeout; the test's exception takes precedence |
| 10 | low | `development/docker-compose.dev.yml` | An exported endpoint broke the development bootstrap | Fixed: the development values are written in the file |
| 25 | low | `docs/docs/compose-deployment.mdx` | The SSH forward assumed port 4200 | Fixed: the command uses `<port>` |
| 11 | low | `bootstrap.py` | The guard decides from DNS alone; MinIO receives the credential before the guard runs | Deferred |
| 12, 13 | low | `trust_store.py` | Variables already set take precedence; matching is case-insensitive now; no log when the directory is empty | Partly fixed (case-insensitive matching and tests); the rest is deferred |
| 14 | low | `workflow-nightly-e2e.yml` | `@actions/artifact` dependencies below the top level are not locked | Deferred |
| 15 | low | `workflow-nightly-e2e.yml` | `compose_repeat` has no upper bound; ten repeats approach the 6-hour cap | Deferred |
| 16 | low | `workflow-publish.yml` | The documented cosign identity regexp is not anchored | Deferred |
| 17 | low | `trigger-release.yml` | `contents: write`: checked, safe | No change |
| 18, 19 | low | `tests/service/*` | Missing negative tests | Fixed together with findings 2 to 5 |
| 20 | low | `tests/compose/test_compose_minimum.py` | No live `up` on Compose 2.24 with the bundled store left out | Deferred |
| 21 | low | `tests/preview/test_preview_configuration.py` | Passes the full environment and has no timeout | Deferred |
| 22 | low | `tests/compose/redaction.py` | Base64 and URL-encoded forms are not searched | Deferred |
| 23, 24 | low | contract tests | Substring checks and weak assertions | Deferred |
| 26 | low | `dev/adr/0023-*.md`, changelog | ADR 0023 is Proposed; the alternatives have no reasons; the fragment wording | Deferred: ADR 0023 stays Proposed until the pull request is approved (T079) |

## Decisions made without the product owner

- I split T004 into dispatch (chunk 2) and classification (chunk 10), so the other chunks did not
  wait for the remote run.
- I split US3 (15 tasks) into three chunks, along the storage code, the Compose files, and the
  compose tests with docs. Phase 9 became two chunks.
- I fixed every medium review finding and two low ones, although the skill requires fixes only
  for high findings. The findings are operator-facing failure messages and test reliability
  problems, the kind of edge case a customer meets first.
- The test of the release workflow contract was rewritten for the repeated suite (`1d26870a`).
  The nightly run no longer stops at the first failure (`-x` removed). The product owner
  confirmed this on 2026-10-17, for the single-iteration nightly run too (decision G below).
- T063 used port 8081 for the preview Infrahub, because port 8080 is taken on this machine.

## Follow-ups outside this specification

- `uv run invoke start` reuses an existing `infrahub-sync:dev` image. After this change an old
  image fails with "No module named infrahub_sync.service.prefect_server". Add `--build` or a
  note to `tasks/dev.py`.
- `uv run invoke docs.generate` removes a paragraph added by hand to
  `docs/docs/reference/cli.mdx` (commit `b5b50af1`).
- `tests/test_workflow_contracts.py::test_only_the_newest_stable_release_is_called_newest` failed
  twice early in the run and then passed in every later run. The cause is not known.
- Quickstart scenarios 1 and 10 need `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` exported in the
  shell. A key added to the CA directory of a running deployment stops only `sync-bootstrap`
  until a restart. `down -v` leaves the earlier `sync-ca-certificates` volume. See
  `implementation-notes.md`, "Quickstart walk-through (T084)".
- The branch `ci/compose-repeat-measure` is still on GitHub. Delete it once T070 has run.

## Suggested next steps

1. Review the branch and decide on the open product points:
   - the deferred low findings.
   (`-x` and the ADR 0023 status were decided on 2026-10-17; see decisions G and H.)
2. Rebase on spec 009 when it merges. `worker.py` holds one `trust_store` call from this branch.
   `test_custom_ca.py` should then assert `tls_certificate_rejected`.
3. Open the pull request into `feature/v3-develop`, with the `+013-compose-images` changelog
   fragment and one `changes/*` label.
4. After merge, run T070: 10 repeats on each architecture against the merged code.
5. After the first pre-release, run T085: quickstart scenario 13.

## Product decisions applied on 2026-10-17

The product owner decided review findings 11, 12, 13, 15 and 16 and two documentation points.
Spec artefacts that stated the earlier behaviour were updated in the commit that adds this
section: `spec.md` (FR-024, FR-025, FR-027, FR-035), `plan.md`, `data-model.md` and
`contracts/object-store.md`. The changelog fragment `+013-compose-images.changed.md` was
extended rather than a new fragment added, because the CA directory and the release notes are
both new in this unreleased change.

### A. Registry name (finding 17 of the follow-up review)

- Commit: `200dbf37`.
- Finding: the repository variable `HARBOR_HOST` (read with `gh variable list`, a read-only call)
  is an OVH managed registry host name, not `registry.opsmill.io`.
  `docs/docs/development/knowledge/image-publishing.md` and ADR 0015 already state that pushes
  must use the `HARBOR_HOST` name, because the login authenticates only that host, and that
  both names reach the same Harbor project.
- Change: the `record_image` job wrote the `HARBOR_HOST` name into the release notes, which
  research R11 had decided would name `registry.opsmill.io/opsmill/infrahub-sync`. The job now
  writes `registry.opsmill.io/${{ github.repository }}`. The push path is unchanged.
- Contract tests in `tests/test_workflow_contracts.py`: every push in `ci-docker-image.yml` and
  `workflow-publish.yml` names `${{ vars.HARBOR_HOST }}/${{ github.repository }}`; no push
  names `registry.opsmill.io`; every `registry.opsmill.io` reference in the user pages,
  `README.md` and `docker-compose.yml` names `opsmill/infrahub-sync` or the bare host; the
  release notes use the pull name; the knowledge page states that both names reach one project.
- Not provable offline, for the owner to check in the repository and registry settings: that
  `HARBOR_HOST` and `registry.opsmill.io` front the same Harbor project, and that a tag pushed to
  `HARBOR_HOST` can be pulled, and verified with cosign, through `registry.opsmill.io`. The first
  pre-release (T085) is the first run that shows it.

### B. Custom CA logging and restart (finding 18)

- Commits: `ef4fcb61` (code, unit tests, compose test, `custom-certificates.mdx`) and `a28322d9`
  (`compose-deployment.mdx`, troubleshooting rows).
- `infrahub_sync/service/trust_store.py` still logs `custom CA bundle installed` with the count
  and the bundle path at every start. It now also logs `custom CA directory holds no certificate
  file` (info, with the directory) when the directory exists but holds no `.pem` or `.crt` file,
  and `trust variable already set; it overrides the custom CA directory` (warning, with the
  variable name only) for each of `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` and
  `INFRAHUB_TLS_CA_FILE` that was already set. An absent directory logs nothing.
- Tests first: three new tests in `tests/service/test_trust_store.py` failed before the change.
  `tests/compose/test_custom_ca.py` now also checks that each of the three services logs the
  empty-directory line when no CA is named; the placeholder volume makes that line appear on
  every start of a deployment without a CA.
- Docs: user ID 10001 must list the directory and read the files (already in
  `custom-certificates.mdx`, now also in the `.env` table of `compose-deployment.mdx`); a change
  to the files takes effect after `docker compose up -d --force-recreate sync-api sync-worker`
  or `docker compose restart sync-api sync-worker`; a change to `INFRAHUB_SYNC_CA_CERTIFICATES`
  itself changes the mount, so `up -d --wait` recreates the three services (the compose test
  relies on that).

### C. Bundled-store guard (finding 11)

- Commit: `a28322d9`. No code change; the DNS-based guard stays for the MVP.
- "Use your own S3-compatible store" now says that `sync-bootstrap` detects the bundled MinIO when
  the name `object-store` resolves, so it stops only after `object-store` started with the
  store's access key and secret key as its root credential on the same host, and tells the
  operator to rotate that key pair or remove the container and its volume if that matters. The
  troubleshooting row for `bundled-object-store-not-left-out` says the same.

### D. Cosign identity (finding 16)

- Commit: `200dbf37`.
- Evidence: Fulcio's identity configuration for GitHub Actions
  (`https://github.com/sigstore/fulcio/blob/main/config/identity/config.yaml`) sets
  `subject-alternative-name-template: "{{ .url }}/{{ .job_workflow_ref }}"`, and
  `docs/oid-info.md` of the same repository describes Build Signer URI as the reusable workflow
  ref. `job_workflow_ref` names the workflow file that contains the job. The `sign` and `sbom`
  jobs are in `ci-docker-image.yml`, called by `workflow-publish.yml`, called by
  `trigger-release.yml` on the `release` event, whose ref is `refs/tags/<version>`; a local
  `./` call runs at the caller's ref. The certificate identity of a release signature is
  therefore `https://github.com/opsmill/infrahub-sync/.github/workflows/ci-docker-image.yml@refs/tags/<version>`.
  This was read from the documentation and the workflow files; no signed release image was
  inspected, because none exists yet.
- Change: `docs/docs/container-image.mdx` and the `record_image` release-notes text use
  `'^https://github\.com/opsmill/infrahub-sync/\.github/workflows/ci-docker-image\.yml@refs/tags/'`
  with `--certificate-oidc-issuer https://token.actions.githubusercontent.com`. The maintainer
  guide `publishing-an-image.md` uses the same file at `@refs/heads/`, because a manual dispatch
  signs from a branch.
- Contract tests: the identity accepts the release form and refuses a branch ref, the caller
  workflow, another workflow, another repository and a prefixed URL; the page holds exactly
  three identity flags, all equal to the constant; `record_image` uses it; the signing jobs are
  in the called workflow reached from the `release` trigger.

### E. `compose_repeat` cap (finding 15)

- Commits: `034d2d7d` (workflow) and `50b5b240` (tests).
- A new step, `compose_repeat_guard`, runs before the image build and refuses an empty,
  non-numeric, zero or above-10 value with `::error::compose_repeat must be a whole number
  between 1 and 10`. The check moved out of the suite step. Tests run the step's script with
  bash for 7 refused and 3 accepted values.

### F. Readiness wait

- Commit: `a28322d9`.
- Checked first: `tests/compose/lifecycle.py` passes 420 s as `--wait-timeout`; Compose 5.1.2
  `up --help` lists `--wait-timeout int` with no default; the shipped health checks run every
  5 s with 30 retries, and `prefect-server` and `sync-api` have a 20 s start period.
- "How long the first start takes" in `compose-deployment.mdx` gives the measured times (about
  20 s on a host that held the images, from the compose run below; 220.5 s for the slowest
  nightly start with a cold test Infrahub, from `lifecycle.py`), says that without
  `--wait-timeout` Compose sets no overall limit, shows `--wait-timeout 420`, and says how to
  continue on a slow host. A troubleshooting row points to it. Judgement call: the page does not
  tell operators to raise health check retries through an override file, because that path was
  not tested.

### G. `-x` in the nightly Compose run

- No code change. The product owner decided that the nightly Compose run keeps running without
  `-x`, in the single-iteration run as well as in a repeated dispatch, so that each run reports
  every failing test instead of stopping at the first.

### H. ADR 0023 accepted

- The product owner accepted ADR 0023. `dev/adr/0023-the-compose-deployment-takes-an-external-object-store-and-custom-ca-certificates-from-env.md`
  now has `**Status**: Accepted`, and the note of T079 in `tasks.md` records the acceptance.
  `dev/adr/README.md` lists no status, so it is unchanged. Finding 26 stays deferred for its
  other two points: the alternatives have no reasons, and the fragment wording.

### Gates

| Gate | Command | Finished (UTC) | Result |
|---|---|---|---|
| Format | `uv run invoke format` | 2026-10-17T02:28:56Z | `382 files left unchanged` |
| Lint | `uv run invoke lint` | 2026-10-17T02:29:18Z | exit 0; `Pylint passed (0 diagnostics, score 10.0)`; ty `Found 17 diagnostics`; yamllint reported no path |
| Pylint | `uv run invoke linter.lint-pylint` | 2026-10-17T02:29:30Z | `Pylint passed (0 diagnostics, score 10.0)` |
| ty | `uv run invoke linter.lint-ty` | 2026-10-17T02:29:30Z | `Found 17 diagnostics` (unchanged) |
| actionlint, yamllint | on `workflow-nightly-e2e.yml` and `workflow-publish.yml` | 2026-10-17T02:29:30Z | exit 0, no output |
| rumdl | changed pages | 2026-10-17T02:29:30Z | `Success: No issues found in 6 files` |
| Unit | `uv run invoke tests.tests-unit` | 2026-10-17T02:32:18Z | `5931 passed, 4 skipped, 217 deselected, 4 warnings in 159.85s` |
| Docs | `uv run invoke docs.docusaurus` | 2026-10-17T02:32:38Z | `[SUCCESS] Generated static files in "build".` |
| Compose CA | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test-013fix uv run pytest -m compose tests/compose/test_custom_ca.py -n 0 --compose-zero-skip`, image built from this worktree | 2026-10-17T02:21:59Z | `1 passed in 146.29s (0:02:26)` |
