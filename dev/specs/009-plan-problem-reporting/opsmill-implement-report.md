# Implementation report: spec 009, plan problem reporting

- Spec directory: `dev/specs/009-plan-problem-reporting`
- Branch: `009-plan-problem-reporting`
- Base commit: `1f620b93` (tip of `013-compose-images`)
- Head commit before this report: `2be8e98b`
- Wall-clock time: 2026-10-16T18:47:30Z to 2026-10-17T01:55:32Z (about 7 hours 8 minutes)
- Result: DONE. 109 of 110 tasks are complete. T110 is the merge gate. It waits for product sign-off of decision records 0020, 0021 and 0022 and of the AD-12 amendment, all of which have the status Proposed.
- Final unit tier: 6371 passed, 4 skipped, 0 failed (`uv run invoke tests.tests-unit`, 2026-10-17T01:44:59Z).
- Final compose tier: all 12 compose files passed against an image built from this branch in chunk 15b. `test_managed_run.py` and `test_credential_leaks.py` passed again after the last fix (F3).

## Chunk ledger

| # | Chunk (tasks.md phase) | Tasks | Done / partial / blocked | Commit | Decisions or surprises |
|---|---|---|---|---|---|
| 1 | Phase 1 Setup, T001-T007 | 7 | 7 / 0 / 0 | `7f5b9524` | The parity baseline from T002 did not go through any real adapter loader. The decision records were written with the status Proposed. |
| 2 | Phase 2 loader step tests and core, T008-T015 | 8 | 5 / 3 / 0 | `1c4f5b4b` | T011, T012 and T013 were partial until the adapters changed. They were ticked in chunks 3 and 4. The chunk set the `peer_unique_id` signature and the placeholder string for a peer that is not in the source. |
| 2b | Inserted step: parity through real loaders (strengthens T002) | 0 | n/a | `d328c25b` | Requested by the team lead. Three cases plan through the real NetBox and Infrahub loaders, using fake clients. The baselines were captured before the adapter change. A mutation check showed that the cases catch a change. |
| 3 | Phase 2 adapters, T016-T023 | 8 | 8 / 0 / 0 | `5c6b3aa4` | Added the shared module `adapters/references.py`. Slurp'it now reports duplicate records. |
| 4 | Phase 2 engine, T024-T030 | 7 | 7 / 0 / 0 | `88a8eafc` | T029 parity passed byte for byte. Two modules reached Pylint's 1000-line limit. |
| 5 | US1 Dropdown and identity groups, T036, T037, T043-T045 | 5 | 5 / 0 / 0 | `0311ec17` | Added `plan/left_out_peer.py`. |
| 6 | US1 problem pass and plan format 4, T031-T035, T038, T039, T046-T050 | 12 | 12 / 0 / 0 | `1159cb10` | The fixture gives 8 problem entries of 7 types. `checkpoints.py` now includes `report.jsonl`. |
| 7 | US1 routes, client and CLI, T040-T042, T051-T056 | 9 | 9 / 0 / 0 | `44292722` | The worker publishes a public `plan-report` artifact. |
| 8 | US2 left-out confirmation, T057-T066 | 10 | 10 / 0 / 0 | `09d973c0` | A stopped `sync` gives back its write admission. `PlanResource` gained `format_version`. |
| 9 | US3 failure cause core, T067, T068, T070, T072-T074 | 6 | 5 / 1 / 0 | `ed28bdf2` | T068 was partial until chunk 10. |
| 10 | US3 wiring, T069, T071, T075-T079 | 7 | 7 / 0 / 0 | `3e890914` | The cause is stored once under `results["failure"]`. Spec 013's `test_custom_ca.py` now checks for `tls_certificate_rejected` and passes. |
| 10b | Inserted correction: shape of Infrahub error extensions | 0 | n/a | `2faad948` | Requested by the team lead. Kind and attribute are now read from `extensions.data`. |
| 11 | US4 stopped apply, T080-T084 | 5 | 5 / 0 / 0 | `ca9a742e` | `new-plan-required` replaces `run-execution-conflict` only after a write was admitted. |
| 12 | Phase 7 worker environment, T086, T090-T092 | 4 | 4 / 0 / 0 | `41f337c5` | Spec 013's trust store call stays first in `main()`. |
| 13 | Phase 7 `configs validate`, T085, T087-T089, T093-T097 | 9 | 9 / 0 / 0 | `5a1ba4e1` | Validation is stricter about writable human-friendly ID components. |
| 14 | Phase 8 NetBox example, T098-T102 | 5 | 5 / 0 / 0 | `4b1a40f3` | The live `from-netbox` check ran. The problem cases were added to a local NetBox because the pinned dataset does not contain them. |
| 15a | Phase 9 documentation, T103-T107 | 5 | 5 / 0 / 0 | `ae9e7549` | The `INFRAHUB_SYNC_API_TOKEN` paragraph in `cli.mdx` now survives `docs.generate`. The Infrahub extensions shape in research.md R13 is corrected. |
| 15b | Phase 9 gates, T108-T110 | 3 | 2 / 0 / 0 (T110 waiting) | `b43b63b6` | Fixed the next action of `apply_refused` and of six apply-stage failure types. Fixed the destination system name when the schema read fails. Fixed PostgreSQL test isolation and five outdated preview tests. |
| F1 | Review fixes H1, H2, H5, M3 | 0 | n/a | `f749d6a3` | research.md R4 is aligned with FR-005. |
| F2 | Review fixes H4, M1, M2, M4 | 0 | n/a | `5bf1c505` | See the M4 decision below. |
| F3 | Review fixes H3, M7, M8 | 0 | n/a | `2be8e98b` | EXTRA not fixed: it needs a product decision. |

## Tasks not completed

- T110, the SIGN-OFF merge gate, waits for product sign-off. Decision records 0020, 0021 and 0022 and the AD-12 amendment (`ad-12-amendment.md`) have the status Proposed. The note under T110 in tasks.md says this.

## Local-pass evidence

All runs were on the local macOS host with Python 3.11 and uv, unless the environment column says otherwise. Rows that list a file cover every test node in that file, as the runner reported.

| Test id | Type | Run command | Passed at (ISO 8601) | Environment context | Verbatim pass line |
|---|---|---|---|---|---|
| tests/plan/test_loader_plan_parity.py (4 baseline cases plus 3 cases through real loaders) | unit | `uv run pytest tests/plan/test_loader_plan_parity.py -q -p no:randomly` | 2026-10-17T01:35:09Z (last run, inside F2's batch) | n/a | `7 passed in 0.18s` |
| tests/test_diffsync_mixin_contract.py | unit | `uv run pytest tests/test_diffsync_mixin_contract.py -q -p no:randomly` | 2026-10-16T20:02:47Z | n/a | `8 passed in 0.02s` |
| tests/adapters/test_record_admission.py | unit | `uv run pytest tests/adapters/test_record_admission.py -q -p no:randomly` | 2026-10-16T19:16:36Z | n/a | `13 passed in 0.02s` |
| tests/adapters/test_peer_unique_id.py | unit | `uv run pytest tests/adapters/test_peer_unique_id.py ... -q -p no:randomly` (F2 batch) | 2026-10-17T01:35:09Z | n/a | `45 passed in 0.36s` (batch); F1: `13 passed in 0.02s` |
| tests/adapters/test_list_records_only_adapter.py | unit | `uv run pytest tests/adapters/test_list_records_only_adapter.py -q` | 2026-10-16T19:48:15Z | n/a | `3 passed` |
| tests/adapters/test_netbox_filtered_references.py | unit | `uv run pytest tests/adapters/test_netbox_filtered_references.py -q -p no:cacheprovider` | 2026-10-17T01:22:38Z | n/a | `18 passed in 0.11s` |
| tests/adapters/test_infrahub_loader_fields.py | unit | `uv run pytest tests/adapters/test_infrahub_loader_fields.py -q -p no:cacheprovider` | 2026-10-17T01:22:38Z | n/a | `18 passed in 0.11s` |
| tests/adapters/test_infrahub_peer_identifier.py | unit | `uv run pytest tests/adapters/test_infrahub_peer_identifier.py -q -p no:randomly` | 2026-10-16T19:16:37Z | n/a | `29 passed in 0.11s` |
| tests/adapters/test_infrahub_incremental.py | unit | `uv run pytest tests/adapters/test_infrahub_incremental.py -q -p no:cacheprovider` | 2026-10-17T01:22:38Z | n/a | `8 passed in 0.08s` |
| tests/test_execution_surface.py (continue_on_error tests) | unit | `uv run pytest "tests/test_execution_surface.py::test_branch_reaches_the_factory_from_run_remote_request" tests/test_execution_surface.py::test_no_module_of_the_package_names_continue_on_error -v -p no:randomly` | 2026-10-16T20:02:42Z | n/a | `3 passed in 0.11s` |
| tests/adapters adapter conversion files (test_netbox_namespace_identity, test_reference_conversion, test_reference_conversion_optional_sdk, test_aci_reference_conversion, test_static_values, test_slurpit_model_loading, test_netbox_benchmark_changes, test_netbox_incremental) | unit | `uv run pytest <file> -q` | 2026-10-16T19:48:15Z | n/a | `14 passed`, `68 passed`, `10 passed`, `6 passed`, `29 passed`, `33 passed`, `1 passed`, `8 passed` |
| tests/cache/test_incremental_admission.py | unit | `uv run pytest tests/cache/test_incremental_admission.py -q -p no:randomly` | 2026-10-16T20:02:43Z | n/a | `5 passed in 0.07s` |
| tests/cache/ (test_destination_local_id_snapshot, test_detached_store_delta, test_incremental_engine, test_safe_watermarks) | unit | `uv run pytest <file> -q -p no:randomly` | 2026-10-16T20:02:44Z to 20:02:46Z | n/a | `6 passed`, `1 passed`, `4 passed`, `44 passed` |
| tests/runtime_schema/test_dropdown_choices.py | unit | `uv run pytest tests/runtime_schema/test_dropdown_choices.py -q -n 0` | 2026-10-16T20:18:23Z | n/a | `14 passed in 0.08s` |
| tests/plan/test_identity_groups.py | unit | `uv run pytest tests/plan/test_identity_groups.py -q -n 0` | 2026-10-16T20:18:23Z | n/a | `17 passed in 0.07s` |
| tests/plan/test_left_out_peer_placeholder.py | unit | `uv run pytest tests/plan/test_left_out_peer_placeholder.py -q -n 0` | 2026-10-16T20:18:24Z | n/a | `5 passed in 0.10s` |
| tests/plan/test_source_identity_collision.py | unit | `uv run pytest tests/plan/test_source_identity_collision.py -q` | 2026-10-16T20:50:31Z | n/a | `34 passed in 0.15s` |
| tests/runtime_schema/test_projection.py and test_accessor_snapshot.py | unit | `uv run pytest <file> -q -n 0` | 2026-10-16T20:18:25Z | n/a | `40 passed in 0.09s`, `21 passed in 0.08s` |
| tests/plan/test_problem_pass.py | unit | `uv run pytest tests/plan/test_problem_pass.py -q` | 2026-10-16T20:50:27Z | n/a | `14 passed in 0.30s` |
| tests/plan/test_dependency_walk.py | unit | F2 batch command | 2026-10-17T01:35:09Z | n/a | `45 passed in 0.36s` (batch) |
| tests/plan/test_select_operations.py | unit | F2 batch command | 2026-10-17T01:35:09Z | n/a | `45 passed in 0.36s` (batch) |
| tests/plan/test_records_without_source_id.py | unit | F2 batch command | 2026-10-17T01:35:09Z | n/a | `45 passed in 0.36s` (batch) |
| tests/plan/test_stores_unchanged.py | unit | `uv run pytest tests/plan/test_stores_unchanged.py -q` | 2026-10-16T20:50:28Z | n/a | `3 passed in 0.10s` |
| tests/plan/test_plan_format_4.py | unit | `uv run pytest tests/plan/test_plan_format_4.py -q` | 2026-10-16T20:50:29Z | n/a | `14 passed in 0.14s` |
| tests/adapters/test_peer_not_found_in_source.py | unit | `uv run pytest tests/adapters/test_peer_not_found_in_source.py -q -p no:cacheprovider` | 2026-10-17T01:22:38Z | Slurp'it SDK stubbed, because the extra is not installed | `7 passed in 0.13s` |
| tests/plan/test_report_credentials_absent.py | unit | `uv run pytest tests/plan/test_report_credentials_absent.py -q` | 2026-10-16T20:50:30Z | n/a | `1 passed in 0.10s` |
| tests/plan format-4 updates (test_models, test_plan_format_3, test_schema_binding, test_writer), tests/service/test_plan_checkpoint.py, tests/test_potenda_plan_artifact.py | unit | `uv run pytest <file> -q` | 2026-10-16T20:50:31Z to 20:50:34Z | n/a | `107 passed`, `35 passed`, `24 passed`, `38 passed`, `14 passed`, `60 passed` |
| tests/service/test_plan_report_routes.py | unit | `uv run pytest tests/service/test_plan_report_routes.py -q` | 2026-10-16T21:11:18Z | n/a | `55 passed, 1 warning in 2.22s` |
| tests/client/test_models.py and tests/client/test_client.py | unit | `uv run pytest <file> -q` | 2026-10-16T21:11:21Z | n/a | `19 passed in 0.15s`, `39 passed in 0.06s` |
| tests/cli/test_client_commands.py | unit | `uv run pytest tests/cli/test_client_commands.py -q -p no:randomly` | 2026-10-16T22:10:00Z | n/a | `91 passed in 0.58s` |
| tests/service/test_http_api.py | unit | `uv run pytest tests/service/test_http_api.py -q -n 0` | 2026-10-16T22:35:48Z | n/a | `74 passed, 1 warning in 2.94s` |
| tests/service/test_flow_and_prefect.py | unit | `uv run pytest tests/service/test_flow_and_prefect.py -q` (M7 node: `-k verify_failure_merges`) | 2026-10-16T21:32:16Z; M7 node around 2026-10-17T01:40Z | n/a | `42 passed`; M7: `1 passed, 41 deselected` |
| tests/service/test_apply_left_out_confirmation.py | unit | `uv run pytest tests/service/test_apply_left_out_confirmation.py -q -n 0` | 2026-10-16T22:35:45Z | n/a | `11 passed, 1 warning in 0.98s` |
| tests/plan/test_apply_left_out_count.py | unit | `uv run pytest tests/plan/test_apply_left_out_count.py -q -p no:randomly` | 2026-10-16T21:32:12Z | n/a | `9 passed in 0.17s` |
| tests/service/test_sync_left_out_stop.py | unit | `uv run pytest tests/service/test_sync_left_out_stop.py -q -p no:randomly` | 2026-10-16T22:09:58Z | n/a | `7 passed, 1 warning in 0.86s` |
| tests/service (test_destination_binding, test_registered_schema_guard, test_apply_versus_verify_race) | unit | `uv run pytest <file> -q -p no:randomly` | 2026-10-16T21:32:16Z to 21:32:23Z | SQLite (the PostgreSQL variant is covered in the PostgreSQL row) | `27 passed`, `34 passed`, `1 passed, 1 skipped` |
| tests/service/test_failure_cause.py | unit | `uv run pytest tests/service/test_failure_cause.py -q` | 2026-10-16T23:50:51Z | n/a | `101 passed` |
| tests/service/test_failure_cause_credentials.py | unit | `uv run pytest tests/service/test_failure_cause_credentials.py -q` | 2026-10-16T22:19:22Z | n/a | `36 passed, 1 warning in 1.14s` |
| tests/test_transform_error.py | unit | `uv run pytest tests/test_transform_error.py tests/service/test_failure_cause.py tests/service/test_failure_cause_credentials.py -q` | 2026-10-16T21:44:55Z | n/a | `91 passed in 0.50s` |
| tests/service/test_run_failure_resource.py | unit | `uv run pytest tests/service/test_run_failure_resource.py -q` | 2026-10-16T22:19:22Z | n/a | `4 passed, 1 warning in 0.71s` |
| tests/service/test_stopped_apply.py | unit | `uv run pytest tests/service/test_stopped_apply.py -q -n 0` | 2026-10-16T22:35:42Z | SQLite store | `5 passed, 1 warning in 0.73s` |
| tests/plan/test_replan_after_stopped_apply.py | unit | `uv run pytest tests/plan/test_replan_after_stopped_apply.py -q -n 0` | 2026-10-16T22:35:44Z | n/a | `3 passed in 0.20s` |
| tests/product_store/test_write_admission.py | unit | `uv run pytest tests/product_store/test_write_admission.py -q -n 0` | 2026-10-16T22:35:47Z | SQLite (PostgreSQL cases are in the PostgreSQL row) | `28 passed, 28 skipped in 0.37s` |
| tests/service/test_worker_environment.py | unit | `uv run pytest tests/service/test_worker_environment.py -q` | around 2026-10-17T01:38Z | SQLite (PostgreSQL cases are in the PostgreSQL row) | `13 passed, 5 skipped` |
| tests/product_store/test_contract.py and tests/configuration/test_contracts.py (one node each) | unit | `uv run pytest <node ids>` | around 2026-10-16T22:47Z | n/a | `2 passed in 0.26s` |
| tests/service/test_config_routes.py (branch tests) | unit | `uv run pytest -p no:cacheprovider -n 0 -q tests/service/test_config_routes.py -k branch` | 2026-10-16T23:18:17Z | n/a | `6 passed` |
| tests/configuration/test_schema_validation.py | unit | `uv run pytest tests/configuration/test_schema_validation.py -q` | after 2026-10-16T23:18:17Z | n/a | `59 passed` |
| tests/test_writable_convergence_identity.py (agreement test) | unit | `... -k agree` | 2026-10-16T23:18:17Z | n/a | `7 passed` |
| tests/product_store/test_configs_service.py (declared_content tests) | unit | `... -k 'declared_content or round_trips or own_records'` | 2026-10-16T23:18:17Z | n/a | `4 passed` |
| tests/configuration/test_warnings.py and tests/service/test_validation_secret_containment.py | unit | `uv run pytest <file> -q ...` | 2026-10-16T23:18:17Z | n/a | `3 passed`, `16 passed` |
| tests/test_netbox_example_device_status.py | unit | `uv run pytest tests/test_netbox_example_device_status.py -q` | 2026-10-16T23:33:44Z | n/a | `16 passed in 0.22s` |
| tests/test_custom_adapter_example.py | unit | `uv run pytest tests/test_custom_adapter_example.py -q` | around 2026-10-17T01:42Z | n/a | `3 passed` |
| Product store PostgreSQL tier, including test_worker_environment[postgresql] and test_write_admission | integration | `uv run --with 'psycopg[binary]' invoke tests.tests-product-store-postgresql` | 2026-10-16T23:56:12Z | Throwaway container `infrahub-sync-009-pgtest` (postgres:16-alpine) on 127.0.0.1:55432, removed after the run | `63 passed, 683 deselected` |
| tests/preview (including test_run_review, test_config_lifecycle, test_netbox_package, test_schema_drift) | integration | `uv run invoke preview.smoke` | 2026-10-17T00:05:30Z | Preview stack with Infrahub on port 8081 (temporary `preview.local.env`, deleted afterwards); torn down with `preview.down --volumes` | `23 passed, 69 deselected` |
| Live `from-netbox` example check (T101) | e2e | testing-tiers.md "The from-netbox example check" | 2026-10-16, chunk 14 | Local NetBox with the pinned demo dataset on port 8082, plus the preview stack on 8081; both torn down | Plan of 1,688 operations with 0 problems; sync wrote 1,687 operations. The plan with the added cases listed 5 problems and left out 7 records (recorded under T101). |
| Quickstart scenarios 1 to 5b (T109) | e2e | dev/specs/009-plan-problem-reporting/quickstart.md | 2026-10-17 around 00:11Z | Preview stack plus local NetBox demo | All scenarios passed. Some rows are covered offline; see T109 in tasks.md. |
| tests/compose/test_credential_leaks.py | e2e | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose/test_credential_leaks.py -n 0 --compose-zero-skip` | 2026-10-17T01:49:50Z | Image `infrahub-sync:compose-test` built from the branch | `1 passed` |
| tests/compose/test_managed_run.py | e2e | same pattern | 2026-10-17T01:47:51Z | same image | `4 passed` |
| tests/compose/test_custom_ca.py | e2e | same pattern | 2026-10-17 around 00:32Z | same image | `1 passed` |
| tests/compose/test_lifecycle.py | e2e | same pattern | 2026-10-17 around 00:29Z | same image | `12 passed` |
| tests/compose: test_bootstrap_idempotence, test_compose_minimum, test_configuration_free_startup, test_container_cli, test_external_object_store, test_object_store_upgrade, test_prefect_server_upgrade, test_source_credentials | e2e | same pattern | 2026-10-17 from 00:36Z to 00:50Z | same image | `6 passed`, `10 passed`, `6 passed`, `2 passed, 2 deselected`, `3 passed`, `1 passed`, `1 passed`, `1 passed, 19 deselected` |
| Full unit tier | unit | `uv run invoke tests.tests-unit` | 2026-10-17T01:44:59Z | n/a | `6371 passed, 4 skipped, 222 deselected` |

## Review findings

The review was run with `speckit-review-run` over `1f620b93..b43b63b6`, with six review agents.

| Severity | File | Summary | Status |
|---|---|---|---|
| High | infrahub_sync/__init__.py | An optional link to a peer that was not loaded and has no identity left the referring record out. | Fixed in F1 `f749d6a3` |
| High | infrahub_sync/cache/incremental.py, netbox.py, nautobot.py | A changed row that could not be built during an incremental load failed the whole plan. | Fixed in F1 |
| High | infrahub_sync/service/worker_environment.py | A database driver error during publication could stop the worker. | Fixed in F3 `2be8e98b` |
| High | infrahub_sync/plan/problems.py | A kept record whose identity holds a reference to a left-out peer failed the plan. | Fixed in F2 `5bf1c505` |
| High | infrahub_sync/adapters/references.py | A required many relationship with no resolved peer was kept with no peers, and Infrahub rejected it at apply. | Fixed in F1 |
| Medium | plan/problems.py:365 | Problems of records without a source ID were dropped. | Fixed in F2 |
| Medium | plan/problems.py:237-240 | Two duplicates without a source ID shared one key. | Fixed in F2 |
| Medium | adapters/infrahub.py:843 | Two destination nodes with the same identity failed the plan. | Fixed in F1 (update_or_add restored) |
| Medium | plan/derive.py:747-768 | A left-out record with unknown identity got a planned delete. | Fixed in F2 |
| Medium | service/failure_record.py | A failed verify set `run.failure` on a run that stays planned. | Fixed in F3 |
| Medium | compose-deployment.mdx, adding-an-adapter.md, examples/custom_adapter | The pages still described plan format 3 and the old `model_loader` override. | Fixed in F3 |
| Medium | plan/problems.py:467-473 | The `identities_merge_in_destination` grouping is coarser than the real key and can leave out valid records. | Deferred |
| Medium | product_store/worker_environment.py:37 | `configs validate --branch` compares with the last worker that published, not with every live worker. | Deferred |
| Medium | failure_cause.py:442 | For `internal_error`, `error_class` is the wrapper class. | Deferred |
| Medium | plan/models.py:438-476 | Per-type model validators are missing, and single-member identity groups are emitted. | Deferred |
| Medium | tests/plan/problem_plan.py:145 | The fixture sets `schema_record` by hand; no test covers the worker snapshot path. | Deferred |
| Medium | infrahub_sync/** | About 50 docstrings cite spec FR and research numbers. | Deferred |
| Medium | __init__.py:419 and plan/models.py:404 | The value-length helper is duplicated, and the two `record_source_id` functions disagree. | Deferred |
| Low | plan/models.py:532-541 | Ties in the report sort order can change the plan checksum. | Deferred |
| Low | __init__.py:664-682 | `_settle_pending` order can ignore the entry that carries the identity. | Deferred |
| Low | potenda/__init__.py:425-472 | Direct incremental runs persist the cursor when the load report is not empty. | Deferred |
| Low | failure_cause.py:84-106 | The `sync` stage tells the user to run again. | Deferred |
| Low | changelog/+009-configs-validate-branch.added.md | The removal of `declared_content` is in an "added" fragment. | Deferred |
| Low | service/failure_record.py:96-106 | A store error while reading the plan loses the whole cause. | Deferred |
| Open (chunk 15b) | failure_cause.py | An apply whose planned peer was deleted from Infrahub is reported as `internal_error`. | Not fixed. Calling it `destination_rejected_write` would be wrong, because Infrahub did not answer. A new failure type changes the stable contract and needs a product decision. |

Other fixes found by the chunks:

- 15b: the next action of `apply_refused` and of six apply-stage failure types now says to create a new plan.
- 15b: the destination system is named when the schema read fails.
- 15b: PostgreSQL tests are isolated, and `test_worker_environment.py` is added to the PostgreSQL task.
- 15a: the `cli.mdx` token paragraph survives regeneration.

## Autonomous decisions and decisions made without the product owner

- Two steps were inserted at the team lead's request:
  - 2b: parity cases that go through the real loaders;
  - 10b: the shape of Infrahub error extensions.
- The final phase was split into 15a (documentation) and 15b (gates). The review fixes ran as three sequential chunks: F1, F2 and F3.
- The fixture has 8 problem entries of 7 types. It has two `peer_not_found_in_source` entries: one on a required relationship, which is left out, and one on an optional relationship, which is kept. This follows data-model.md and FR-005. The spec's wording "seven problems" counts types.
- A direct run, which has no schema snapshot, skips the Dropdown check.
- A stopped `sync` gives back its write admission, so the plan can be applied with `--confirm-left-out`.
- After a write was admitted, every later stage of the run answers `new-plan-required`. A verify is not a write, so it still answers `run-execution-conflict`; the spec 013 page did not need a change.
- Validation is now stricter. When every human-friendly ID component of a kind can be written, the identifiers must name all of them, which matches the plan's rule. A configuration that passed validation before can now fail.
- Example status mapping in the NetBox example:
  - planned and staged become provisioning;
  - offline and failed become maintenance;
  - inventory becomes reserved;
  - decommissioning becomes deprecated.
  The fifth problem planted for #369 is a Dropdown value outside the choices instead of `UndefinedError`, because an invalid transform stays a failure of the whole plan.
- M4: when a left-out record with unknown identity exists, no delete of that kind is planned until the record is fixed. This withholds more deletes than strictly needed.
- When a filter removes the peer named in an identifier field, the record is left out with `required_peer_excluded_by_filter`. Before, the whole plan failed.
- After a failed verify, the cause is stored as `verify_failure.failure`. This is a new nested shape.
- The Infrahub destination merges nodes with the same mapped identity (update_or_add), as before spec 009. The spec defines no typed problem for this case.
- research.md R4 and R13 were corrected to match FR-005 and the real shape of Infrahub's extensions.
- Pylint's module limit: `infrahub_sync/__init__.py` is exactly 1000 lines. Further code there needs a split.

## Suggested next steps

1. Request product sign-off of ADR 0020 with the AD-12 amendment, ADR 0021 and ADR 0022, then tick T110.
2. Decide how to classify an apply whose planned peer was deleted from Infrahub: keep `internal_error`, or add a new failure type.
3. Address the deferred medium findings, first the `identities_merge_in_destination` grouping and the comparison with every live worker.
4. Open a pull request when the sign-off is done. Use the label `changes/minor` and confirm the `+009-` changelog fragments.

## Product decisions applied on 2026-10-17

Benoit Kohler decided three points on 2026-10-17. They replace the decision "Infrahub destination merges nodes with the same mapped identity" above, and answer suggested next step 2.

### A yes or no flag confirms the records left out (commit `cae7f220`)

- The CLI option `apply --confirm-left-out` takes no value. The apply request field `confirm_left_out: bool` (default false, strict JSON boolean) replaces `confirmed_left_out_count`. The client leaves the field out of the body when it is false.
- When the plan leaves out records, an apply without `confirm_left_out: true` is refused before any write with 409 `left-out-confirmation-required`. The message states the count ("this plan leaves out N records; confirm with confirm_left_out=true"), the body keeps `expected_left_out_count`, and the audit outcome is `refused-left-out-not-confirmed`. The CLI prints "apply refused: this plan leaves out N records; confirm with --confirm-left-out".
- The refusal of a count that differs from the plan's is removed, because the plan checksum already binds the apply to the reviewed plan. When the plan leaves out no records, the flag is accepted and ignored. The order of the checks is unchanged. `sync` still stops after a plan that leaves out records.
- Updated: spec 009 (FR-010, FR-011, FR-012, FR-026, FR-029, user story 2, SC-002, SC-006), spec 011 FR-026 and user story 2 (checkbox "I have reviewed the N records left out"), data-model.md, contracts, quickstart.md, research.md, the drafts of ADR 0020 and 0022, the docs, the example README and the changelog fragment. `tests/plan/test_apply_left_out_count.py` is renamed `tests/plan/test_apply_gate.py`. tasks.md keeps the history of the count as it was implemented.

### Infrahub objects that share one identity are the problem `destination_identity_ambiguous` (commit `bdfb41e7`)

- New module `infrahub_sync/plan/destination_ambiguity.py`. The Infrahub adapter stores each destination record through `DestinationIdentityAmbiguities.store`, which keeps the store as `update_or_add_model_instance` did and records the node IDs when a second node has an identity already stored.
- The problem pass lists each such identity with the kind, the identity and the node IDs (`destination_ids`, a new optional field of `PlanProblem` and `PlanProblemResource`, left out of every other problem line, so the bytes of other report lines and the loader parity baselines are unchanged). The source record with that identity is left out with its dependents. The selection step removes every operation of that identity, a delete included. Without a source record the problem is still listed, nothing is left out, and no delete is planned; when the identity cannot be formed from the destination store, no delete of that kind is planned.
- `infrahub-sync runs problems` prints the node IDs. The shared fixture gains a source site `twin` and two Infrahub `twin` nodes, so the fixture plan lists nine problems of eight types.
- Judgement call: the check covers the full destination load of the Infrahub adapter (`model_loader`). An incremental destination read through the cache does not check it.

### A peer missing in Infrahub at apply time is `destination_peer_missing` (commit `e4f13fce`)

- `PeerNotFoundError` and `PeerAmbiguousError` (`infrahub_sync/plan/errors.py`) carry the typed attributes `peer_kind` and `referring_operation_id`, set by `PeerResolver._query`. `failure_cause.py` classifies `PeerNotFoundError` with a peer kind as `destination_peer_missing`: system destination (`infrahub`), `node_kind` the peer kind, a summary that names the operation, and the next action "Create and review a new plan, then apply that plan: a run accepts one apply." No error text enters the cause. Verify resolves no peer, so only apply and sync reach this type.
- Judgement call: `PeerAmbiguousError` was also classified `internal_error`. It is now the new type `destination_peer_ambiguous`, with the same fields. A `PeerNotFoundError` raised because no query could be formed from the peer identity has no peer kind and stays `internal_error`, because nothing showed that the peer is missing.

### Evidence

| Gate | Command | Time (UTC) | Result |
| --- | --- | --- | --- |
| Unit tests | `uv run invoke tests.tests-unit` | 2026-10-17T02:30:42Z | `6389 passed, 4 skipped, 222 deselected, 4 warnings in 156.77s` |
| Loader parity | `uv run pytest -q -p no:randomly tests/plan/test_loader_plan_parity.py` | 2026-10-17T02:33:25Z | `7 passed in 0.20s`, baselines unchanged |
| Compose lifecycle | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose/test_lifecycle.py -n 0 --compose-zero-skip` | 2026-10-17T02:33:47Z | `12 passed in 321.89s` |
| Compose managed run | same, `tests/compose/test_managed_run.py` | 2026-10-17T02:39:13Z | `4 passed in 111.72s` |
| Compose credential leaks | same, `tests/compose/test_credential_leaks.py` | 2026-10-17T02:41:08Z | `1 passed in 229.28s` |
| Docs build | `uv run invoke docs.docusaurus` | 2026-10-17T02:45:02Z | `[SUCCESS] Generated static files in "build".` |
| Lint | `uv run invoke lint` | 2026-10-17 | rumdl, ruff and Pylint (0 diagnostics) pass; yamllint fails only on paths under `./.worktrees/` |
| Types | `uv run ty check .` | 2026-10-17 | `Found 17 diagnostics`, the pre-existing count |

The compose image was rebuilt with `docker build -t infrahub-sync:compose-test .` before the compose runs.

### A transform that fails for one record is the problem `transform_failed` (commit `a34db3cd`)

Decided later on 2026-10-17, with the review fixes below.

- New module `infrahub_sync/load_report.py` holds the load report types, moved out of `infrahub_sync/__init__.py` (still re-exported from it), and `transform_each`, which applies a mapping entry's transforms record by record. A `TransformError` names the entry's kind and the transform's position. The loader of the base class and the source loaders of the NetBox, Nautobot and Infrahub adapters pass `record_transform_failure`, which records the record in the load report as `transform_failed` with the field and `config_entry` (`transforms[0]`), and does not load it. The other records are transformed and loaded.
- The problem pass lists the ninth problem type, `transform_failed`. The problem names the kind, the record (source ID; the identity is not known), the field and `config_entry`. It holds no source value and no error text. The existing rules leave out the record and its dependents; a record that refers to it through an optional relationship is kept without that relationship. `infrahub-sync runs problems` prints the transform and the field.
- The shared fixture maps VRF names through a transform without an entry for `q-in-q`, so the fixture plan lists ten problems of nine types. Updated: spec.md (decision, FR-002, user story 1, SC-002, assumptions), data-model.md, research.md (R1 table, revision note, R13), the contracts, quickstart.md, ADR 0020 (list and text), `running-a-sync.mdx`, `sync-http-api.mdx`, `plan-artifact.md`, `adapter-anatomy.md` and the changelog fragment.
- Judgement call: every `TransformError` of a record becomes the problem, a refused sandbox access included. A transform that fails for every record leaves out every record of its kind. The failure type `transform_failed` of the run failure cause stays for a model that overrides `transform_records` without the `on_failure` parameter: such a model keeps its old behaviour, and a failed transform fails the load.
- Review #13 is in this commit: the same `config_entry` field names the filter that removed the peer of a `required_peer_excluded_by_filter` problem (`filters[0]` of the peer's mapping entry), and the reason says "which the filter filters[0] of the DcimDevice mapping entry removed". The filter value is not named. The loader records the position of the first filter that removed each record; a model that filters its own way records no position, and the reason then says "a configured filter".
- Process: the tests of this item were written with the code, not before it. The tests of items 2 and 3 were run and seen to fail before the code was written.

### Plan format 4 reserves `cleared_relationships` (commit `b01f81e5`)

- `PlannedOperation` gains the optional field `cleared_relationships` (a list of relationship names). It is accepted on an update and refused on a create or a delete. The model omits it when it is absent, so an operation without it keeps its bytes: operation IDs, checksums and the loader parity baselines are unchanged. This release never writes it.
- `require_applyable_plan` refuses a plan whose update carries a non-empty list with `PlanFeatureNotSupportedError` (`wrote = False`, an `apply_refused` cause), after the format check and before the left-out confirmation, so before any write. The Sync API refuses it with 409 `plan-feature-not-supported`, audited `refused-plan-feature`, after the format check. An empty list applies.
- Updated: contracts/plan-artifact-format-4.md, contracts/sync-api.md (check 6), data-model.md, ADR 0022 (decision and consequences), `plan-artifact.md`, `sync-http-api.mdx` and a new changelog fragment `+009-cleared-relationships.added.md`.
- Not done: `dev/specs/010-second-sync-plan-speed/plan.md` exists only on the branch `v3-mvp`, not on `009-plan-problem-reporting`, and this work did not switch branches. The note to add to its "Coordination with spec 009" row "Plan format": "Format 4 reserves the optional `cleared_relationships` list on updates (spec 009, decided on 2026-10-17). Spec 009 never writes it and refuses to apply a plan that carries a non-empty list (409 `plan-feature-not-supported`). Spec 010 implements the clearing in format 4; no plan format 5 is needed."

### Review fixes (commit `b24e497a`)

- #4: `identities_merge_in_destination` groups records only when the closest destination key is a strict subset of the configured identity, and only groups that hold a create. Two updates of racks that Infrahub already holds now plan normally (`tests/plan/test_identity_merge_grouping.py`).
- #3: `PlanReport.records_without_identity_by_kind` counts, per kind, the left-out records the load could not build without a known identity, and the `destination_identity_ambiguous` identities that cannot be formed. `select_operations` uses it to withhold deletes, and the plan summary carries it as `deletes_withheld_by_kind`. The CLI prints "deletes not computed for <kind>: N records without identity". In the fixture plan, `InterfacePhysical` (2) and `IpamVRF` (1).
- #8: a run without a destination schema snapshot logs a warning, and the manifest records `dropdown_values_checked: false` (absent otherwise). The summary carries `dropdown_values_checked`, and the CLI prints "Dropdown values were not checked: no destination schema snapshot in this run". Judgement call: a manifest field rather than a report entry, because the check is a fact of the run, not of a record; the fixtures of `tests/test_potenda_plan_artifact.py`, which plan without a snapshot, now carry the field.
- #6: for `internal_error`, `error_class` is the class of the innermost error of the cause chain that is not a `SideLoadError`. The summary names it too. ADR 0021 says so.
- #9: `persist_cursors_for_run` saves no cursor for a kind that has an entry in the side's load report, so the next run reads that kind in full. Finding: no service or Prefect path calls `persist_cursors_for_run`; they extract in full (`incremental-and-cache.md`), so only direct Python callers are affected.
- #10: the next action of a `sync` stopped after its plan names this run: "Review the plan with `infrahub-sync runs plan <run_id>` and the records it leaves out with `infrahub-sync runs left-out <run_id>`, then apply this run with `infrahub-sync apply <run_id> --expected-checksum <checksum> --confirm-left-out`."
- Stable order: report entries that tie on the sort keys are ordered by their canonical bytes, and the two records of a `duplicate_source_identity` problem are named in reference order. Two load orders give identical report bytes and report digest (`tests/plan/test_report_order.py`). Which of two duplicates the store keeps still depends on the load order, and the report says so in `loaded`; the source snapshot files keep rows in load order, so the whole plan checksum can still differ between load orders.
- #16: plan.md describes a record without identity as the code handles it: a referrer through an optional relationship is kept without it, and no delete of its kind is planned.

### Evidence of the second set

| Gate | Command | Time (UTC) | Result |
| --- | --- | --- | --- |
| Unit tests | `uv run invoke tests.tests-unit` | 2026-10-17T03:25:55Z | `6470 passed, 4 skipped, 222 deselected, 4 warnings in 153.97s` |
| Loader parity | `uv run pytest -q -p no:randomly tests/plan/test_loader_plan_parity.py` | 2026-10-17, before commit `b24e497a` (time not recorded) | `7 passed in 0.18s`, baselines unchanged |
| Lint | `uv run invoke lint` | 2026-10-17T03:26:12Z | rumdl, ruff and Pylint pass; yamllint fails only on paths under `./.worktrees/` |
| Pylint | `uv run invoke linter.lint-pylint` | 2026-10-17T03:26:55Z | `Pylint passed (0 diagnostics, score 10.0)` |
| Types | `uv run invoke linter.lint-ty` | 2026-10-17T03:26:56Z | `Found 17 diagnostics`, the pre-existing count |
| Image | `docker build -t infrahub-sync:compose-test .` | 2026-10-17T03:27:50Z | built |
| Compose lifecycle | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose/test_lifecycle.py -n 0 --compose-zero-skip` | 2026-10-17T03:28:09Z | `12 passed in 332.92s` |
| Compose managed run | same, `tests/compose/test_managed_run.py` | 2026-10-17T03:33:47Z | `4 passed in 113.36s` |
| Compose credential leaks | same, `tests/compose/test_credential_leaks.py` | 2026-10-17T03:35:44Z | `1 passed in 233.75s` |
| Docs build | `uv run invoke docs.docusaurus` | 2026-10-17T03:39:43Z | `[SUCCESS] Generated static files in "build".` |

### Sign-off of ADRs 0020, 0021 and 0022 (commit `94119cda`)

- Benoit Kohler signed off on 2026-10-17. ADRs 0020, 0021 and 0022 and the AD-12 amendment have the status "Accepted (2026-10-17)", and `dev/adr/README.md` and `plan-artifact.md` say so. The sign-off line of `ad-12-amendment.md` names Benoit Kohler and the date, and notes that the AD-12 row was written to `product/decisions.md` in `infrahub-sync-process` on the branch `product/ad-12-plan-stage-policy` (commit `f0486ff`), which waits for a pull request there. T110 is ticked.
- Wording fixed to match the code, with no new decision: the README entry of ADR 0020 said "seven named problems" and now says nine; ADR 0022 said apply refuses format 3 and pointed to `potenda/__init__.py:148-163`, and now says formats 2 and 3 and points to `infrahub_sync/plan/apply_gate.py`; ADR 0021 no longer cites line numbers of `service/flow.py` and `potenda/__init__.py` that have moved. The nine problem types, the yes or no confirmation, the failure types `destination_peer_missing` and `destination_peer_ambiguous`, and the refusal of `cleared_relationships` already matched the code.

### Upgrade from the V3 alpha (commit `008b3a60`)

- New page `docs/docs/operations/upgrading-from-the-v3-alpha.mdx`, in the Operations sidebar and linked from `v2-to-v3.mdx`. It covers: plans of format 2 or 3 must be planned again (`plan-format-not-applicable`, "plan again before applying"); the schema fingerprint now includes Dropdown choices, so validated packages that map a Dropdown get a new fingerprint; the stricter identifier rule of `configs validate --branch`, with a real finding and fix (`LocationRack`, `identifiers: ["name"]`, missing `site__name__value`); `declared_content` is read with `configs show --version` or `GET /configs/{config_id}/versions/{registry_version}`; `run.failure`, `verify_failure.failure`, the report counts and the new failure types.
- `changelog/+009-declared-content.removed.md` holds the `declared_content` removal, taken out of `+009-configs-validate-branch.added.md`. `changelog/+009-stricter-validation.changed.md` records the stricter identifier rule and the fingerprint change. `durable-product-records.mdx` lists the Dropdown choices in the fingerprint.
- Offline check of the examples: the destination-schema checks of `configs validate` (`collect_destination_schema_findings`) ran on `examples/netbox_to_infrahub/package.yml` with a schema built from `tests/data/nightly_schema` (generic inheritance applied, minimal Infrahub core kinds added). Result: 0 findings. With `LocationRack` set to `identifiers: ["name"]`, the check reported the finding quoted in the page, so the rule ran. The Nautobot examples (`nautobot-v1_to_infrahub`, `nautobot-v2_to_infrahub`) parse as package configurations, but their destination kinds (`RoleGeneric`, `StatusGeneric`, `ChoiceLocationType`, `InfraRack` and others) have no schema in this repository or in the schema library, so their identifiers could not be checked offline. It is not known whether they pass. The script is not committed.

### NetBox example status mapping (commit `f4848eaa`)

- Both `device_status` transforms of `examples/netbox_to_infrahub/package.yml` explain each pair and say that teams adjust the pairs to their own status meanings. The example README says the same in one sentence.

### Limits and spec 011 (commit `6b5b1a81`)

- `supported-platforms-and-limits.mdx` states that the testing release supports one worker per deployment (`configs validate --branch` compares with the most recent live worker, live for 90 seconds, `LIVE_WORKER_MAX_AGE`), and that tests need Infrahub 1.11 or later, because an older Infrahub returns no error code and the cause loses the node kind and attribute. `configuration-package.mdx` and both tutorials link these limits.
- Finding: the measured versions in the docs and the development stack pin (`development/docker-compose.infrahub.yml`) are Infrahub 1.10.6, below the 1.11 floor. The benchmark runner uses 1.11.3. The pin was not changed.
- Spec 011 FR-029 now says the UI reads a failed or stopped run's cause from `run.failure` and a failed verify's cause from `verify_failure.failure` of `GET /runs/{run_id}/results`, and shows a failure type it does not know by its name. Spec 011 does not list the problem types or the failure types, so no list was added.

### Evidence of the final set

| Gate | Command | Time (UTC) | Result |
| --- | --- | --- | --- |
| Markdown | `uv run rumdl check <changed docs and ADRs>` | 2026-10-17 | `Success: No issues found` |
| Docs build | `uv run invoke docs.docusaurus` | 2026-10-17T03:48:34Z | `[SUCCESS] Generated static files in "build".`, no broken link or anchor |
| Format | `uv run invoke format` | 2026-10-17T03:48:43Z | `All formatters have been executed!`, no file changed |
| Pylint | `uv run invoke linter.lint-pylint` | 2026-10-17T03:48:44Z | `Pylint passed (0 diagnostics, score 10.0)` |
| Unit tests | `uv run invoke tests.tests-unit` | 2026-10-17T03:48:56Z | `6472 passed, 4 skipped, 222 deselected, 4 warnings in 153.68s`; the two added tests are page checks on the new docs page |

### Infrahub 1.11.7 in the development and preview stacks (commit `7ca2eb26`)

- This answers the finding above. `.github/scripts/update_infrahub.py 1.11.7` moved the pin in its seven files from 1.10.6 (`sha256:03162de5140789ea1b12eb275d2af6eca379beec02d7d3d84069e4e46e8e657c`) to 1.11.7 (`sha256:411dbe42552e257da149e2a9411ca725c69abedcca903fc010ef13981063e0aa`). The script read the digest with `docker buildx imagetools inspect` and checked that the index includes `linux/amd64` and `linux/arm64`. 1.11.7 is the latest release on the registry and the latest GitHub release of `opsmill/infrahub`, published on 2026-10-14.
- `development/docker-compose.infrahub.yml` takes the changes of the upstream `docker-compose.yml` from tag `infrahub-v1.10.6` to tag `infrahub-v1.11.7`: 35 new configuration variables, and Neo4j `2026.05.0-community` pinned by its index digest `sha256:6c162e2432f861f2c4e3da77a6ba478e7f10e2160b870541f85294532bc6ff5f`. The file still differs from upstream only by its digest pins and its loopback port bindings.
- `supported-platforms-and-limits.mdx` states that the development and preview stacks run 1.11.7, and which checks passed against it. The measured versions in that page and in both tutorials stay at 1.10.6, because the tutorials were not worked through again on 1.11.7.
- Not changed: the benchmark runner pins 1.11.3 (`tasks/bench.py`), which its recorded baselines were measured on. ADRs name the version their measurements used.

### Known gap: the Nautobot examples are not checked against the stricter `configs validate` rule

- The Nautobot v1 and v2 examples are not checked against the stricter `configs validate` rule, because the repository holds no Nautobot schema to check them against. Benoit Kohler deferred this check to after the MVP on 2026-10-17.

### Evidence of the pin change

| Gate | Command | Time (UTC) | Result |
| --- | --- | --- | --- |
| Unit tests | `uv run invoke tests.tests-unit` | 2026-10-17T06:11:48Z | `6472 passed, 4 skipped, 222 deselected, 4 warnings in 154.50s` |
| Pin contract tests | `uv run pytest tests/compose/test_compose_contract.py tests/preview/test_preview_configuration.py tests/test_update_infrahub.py -q` | 2026-10-17T06:14:31Z | `214 passed in 5.38s` |
| Preview smoke | `uv run invoke preview.smoke`, on a preview started with `uv run invoke preview.up` (Infrahub `/api/info` reports 1.11.7) | 2026-10-17T06:32:45Z | `23 passed, 69 deselected in 194.78s` |
| `from-netbox` example check | The steps in `testing-tiers.md`, on a fresh preview and the local NetBox `demo` dataset | 2026-10-17T06:37:31Z | Plan `operations: 1688`, `by_action: create=1687, delete=1`, `problems: 0`; sync `outcome: applied`, `operations_written: 1687`; the object count of each of the 20 kinds on `netbox-import` equals its planned operations |
| Format | `uv run invoke format` | 2026-10-17T06:40:58Z | `All formatters have been executed!`, no file changed |
| Lint | `uv run invoke lint` | 2026-10-17T06:40:58Z | Ruff and Pylint pass (`Pylint passed (0 diagnostics, score 10.0)`); yamllint fails only on files under `./.worktrees/` |
| ty | `uv run invoke linter.lint-ty` | 2026-10-17T06:41:39Z | `Found 17 diagnostics`, all `unused-ignore-comment` warnings; the same 17 at commit `7f1b99d8` |
| Markdown | `uv run rumdl check docs/docs/operations/supported-platforms-and-limits.mdx` | 2026-10-17T06:41:29Z | `Success: No issues found in 1 file` |
| Docs build | `uv run invoke docs.docusaurus` | 2026-10-17T06:42:04Z | `[SUCCESS] Generated static files in "build".` |
