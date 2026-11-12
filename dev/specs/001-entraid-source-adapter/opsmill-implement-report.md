# Implementation report: Microsoft Entra ID source adapter

- **Status**: DONE
- **Spec directory**: `specs/001-entraid-source-adapter` (stored at `dev/specs/001-entraid-source-adapter`)
- **Branch**: `015-entraid-source-adapter` (not pushed)
- **Base commit**: `d3fd9103` (last preparation commit)
- **Head commit before this report**: `34409049`
- **Wall-clock time of the implementation tail**: 2026-11-03T20:46Z to 2026-11-04T00:11Z, about 3 h 25 min. Chunk 7a
  alone took 2 h 41 min.
- **Result**: 26 of 26 tasks done. Full suite: 315 passed, 3 skipped. The `main` baseline was 210 passed,
  3 skipped, so the branch adds 105 tests.

## 1. What was built

- `infrahub_sync/adapters/entraid.py` holds the source-only `entraid` adapter:
    - settings validation;
    - a Microsoft Graph client built on `msgraph-sdk`, with raw JSON pages and `@odata.nextLink` paging;
    - `$select` built from the schema mapping;
    - named collections with OData filters and advanced queries;
    - direct or transitive group membership;
    - `client_secret` or `default` authentication;
    - one retry loop for HTTP 429 and 503 that honours `Retry-After`;
    - specific errors.
- Tests: `tests/adapters/test_entraid_adapter.py`, `test_entraid_graph.py` and `test_entraid_settings.py`, plus the
  helper module `entraid_fakes.py`. They mock Graph with `httpx.MockTransport` and need no tenant.
- Example: `examples/entraid_to_infrahub/`, with `config.yml`, `schema.yml` and code produced by the
  repository generator.
- Documentation:
    - the page `docs/docs/adapters/entraid.mdx` and its sidebar entry;
    - a row in the adapter chooser page;
    - `entraid` added to the adapter lists in `AGENTS.md` and `dev/guides/adding-an-adapter.md`;
    - the changelog fragment `changelog/+entraid-adapter.added.md`.

## 2. Chunk ledger

| # | Chunk (tasks.md phase) | Tasks | ✅ / ⚠️ / ❌ | Commits | Flagged by the subagent |
|---|---|---|---|---|---|
| 1 | Phase 1 Setup and Phase 2 Foundational (T001–T008) | 8 | 8 / 0 / 0 | `806ab157` | Error paths are read after sending, because the base URL is applied only then. Network errors and malformed bodies are mapped to `EntraidGraphError`. |
| 2 | Phase 3, US1: users and groups load (T009–T011) | 3 | 3 / 0 / 0 | `ee7ff494` | Reference resolution and the dropped-reference warning were written early. The plan builder is a module function. |
| 3 | Phase 4, US2: collections (T012–T014) | 3 | 3 / 0 / 0 | `790c85de` | T012 was already met by chunk 2. The per-entry record copy is confirmed necessary: removing it makes the leak test fail. |
| 4 | Phase 5, US3: membership (T015–T016) | 2 | 2 / 0 / 0 | `41f45f79` | Fixed the member request count, which was wrong when reads run in parallel. |
| 5 | Phase 6, US4: authentication tests (T017) | 1 | 1 / 0 / 0 | `8bc80b2d` | No defect found. Tests patch the credential classes with recorders. |
| 6 | Phase 7, US5: failure tests (T018–T019) | 2 | 2 / 0 / 0 | `1a083132` | No defect found. Added a test that a 403 during `load()` raises `EntraidPermissionError`. |
| 7a | Phase 8: example (T020–T021) | 2 | 2 / 0 / 0 | `6c9d2bb3` | Code generated with `render_adapter` from `schema.yml`, with no running Infrahub. The example loads against the mocked Graph. |
| 7b | Phase 8: docs, lists, changelog (T022–T024) | 3 | 3 / 0 / 0 | `08124d7b` | Added `Entra` and `OData` to the vale spelling exceptions. vale and the Docusaurus build were not run. |
| 7c | Phase 8: lint and validation (T025–T026) | 2 | 2 / 0 / 0 | `b04b0c5f` | Fixed rumdl findings in the spec documents. `invoke lint` gives the same pylint output as the baseline. |
| R | Review fixes (Phase 6 of this run) | n/a | n/a | `34409049` | See section 5. |

Phase 1 (one task with no file change) was merged into chunk 1; see section 6.

## 3. Tasks not completed

None. All 26 tasks in `tasks.md` are ticked `[X]`.

## 4. Local-pass evidence

All tests below are unit tests that mock Graph. The rows come from one verbose run of every Entra ID test after the
review fixes. Each chunk subagent had already reported a passing run of its own tests.

| Test id | Type | Run command | Passed at (ISO 8601) | Environment context | Verbatim pass line |
|---------|------|-------------|----------------------|---------------------|--------------------|
| `tests/adapters/test_entraid_adapter.py::test_load_users_and_groups_from_bare_mappings` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_load_users_and_groups_from_bare_mappings PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_load_reads_each_collection_once` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_load_reads_each_collection_once PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_select_built_from_fields_filters_id_and_settings_without_transform_keys` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_select_built_from_fields_filters_id_and_settings_without_transform_keys PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_empty_tenant_loads_zero_objects` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_empty_tenant_loads_zero_objects PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_destination_target_is_refused` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_destination_target_is_refused PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_load_list_property_mapped_to_single_attribute_asks_for_transform` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_load_list_property_mapped_to_single_attribute_asks_for_transform PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_model_is_read_only[create]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_model_is_read_only[create] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_model_is_read_only[update]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_model_is_read_only[update] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_model_is_read_only[delete]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_model_is_read_only[delete] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_cursor_tier_is_none` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_cursor_tier_is_none PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_shared_collection_splits_records_between_kinds_with_one_read` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_shared_collection_splits_records_between_kinds_with_one_read PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_shared_collection_transform_does_not_leak_into_other_kind` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_shared_collection_transform_does_not_leak_into_other_kind PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_unknown_collection_is_refused_at_construction_listing_valid_names` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_unknown_collection_is_refused_at_construction_listing_valid_names PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_collection_with_resource_name_is_refused[users]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_collection_with_resource_name_is_refused[users] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_collection_with_resource_name_is_refused[groups]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_collection_with_resource_name_is_refused[groups] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_collection_not_named_by_any_entry_is_not_read` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_collection_not_named_by_any_entry_is_not_read PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_membership_mode_reads_its_endpoint_and_keeps_only_users[direct]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_membership_mode_reads_its_endpoint_and_keeps_only_users[direct] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_membership_mode_reads_its_endpoint_and_keeps_only_users[transitive]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_membership_mode_reads_its_endpoint_and_keeps_only_users[transitive] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_user_reached_twice_appears_once` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_user_reached_twice_appears_once PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_members_not_loaded_are_dropped_with_one_warning` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_members_not_loaded_are_dropped_with_one_warning PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_no_member_requests_when_no_entry_maps_members` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_no_member_requests_when_no_entry_maps_members PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_summary_log_counts_member_requests` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_summary_log_counts_member_requests PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_members_mapping_without_reference_is_refused_naming_the_field` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_members_mapping_without_reference_is_refused_naming_the_field PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_permission_refusal_during_load_raises_permission_error[users-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_permission_refusal_during_load_raises_permission_error[users-read] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_permission_refusal_during_load_raises_permission_error[member-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_permission_refusal_during_load_raises_permission_error[member-read] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_failing_member_read_cancels_the_other_reads_before_the_client_closes` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_failing_member_read_cancels_the_other_reads_before_the_client_closes PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_single_peer_relationship_takes_one_peer_and_refuses_several[owner_numbers0-00000000-0000-0000-0000-000000000001]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_single_peer_relationship_takes_one_peer_and_refuses_several[owner_numbers0-00000000-0000-0000-0000-000000000001] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_single_peer_relationship_takes_one_peer_and_refuses_several[owner_numbers1-None]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_single_peer_relationship_takes_one_peer_and_refuses_several[owner_numbers1-None] PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_load_from_inside_a_running_event_loop_loads_the_objects` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_load_from_inside_a_running_event_loop_loads_the_objects PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_token_failure_during_load_raises_authentication_error_without_graph_requests` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_token_failure_during_load_raises_authentication_error_without_graph_requests PASSED` |
| `tests/adapters/test_entraid_adapter.py::test_entries_sharing_a_collection_send_one_request_selecting_both_properties` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_adapter.py::test_entries_sharing_a_collection_send_one_request_selecting_both_properties PASSED` |
| `tests/adapters/test_entraid_graph.py::test_paging_three_pages_returns_every_record_in_order[users]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_paging_three_pages_returns_every_record_in_order[users] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_paging_three_pages_returns_every_record_in_order[groups]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_paging_three_pages_returns_every_record_in_order[groups] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_paging_follows_next_link_of_empty_page` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_paging_follows_next_link_of_empty_page PASSED` |
| `tests/adapters/test_entraid_graph.py::test_empty_result_returns_empty_list` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_empty_result_returns_empty_list PASSED` |
| `tests/adapters/test_entraid_graph.py::test_first_request_carries_select_and_top_page_size` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_first_request_carries_select_and_top_page_size PASSED` |
| `tests/adapters/test_entraid_graph.py::test_filter_sent_unchanged_without_consistency_level_or_count[users-accountEnabled` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_filter_sent_unchanged_without_consistency_level_or_count[users-accountEnabled eq true] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_filter_sent_unchanged_without_consistency_level_or_count[groups-securityEnabled` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_filter_sent_unchanged_without_consistency_level_or_count[groups-securityEnabled eq true and NOT groupTypes/any(c:c eq 'Unified')] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_advanced_query_sends_count_and_consistency_level_on_every_page` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_advanced_query_sends_count_and_consistency_level_on_every_page PASSED` |
| `tests/adapters/test_entraid_graph.py::test_retry_after_429_waits_the_header_value_then_succeeds` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_retry_after_429_waits_the_header_value_then_succeeds PASSED` |
| `tests/adapters/test_entraid_graph.py::test_retry_after_503_without_header_waits_the_fallback` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_retry_after_503_without_header_waits_the_fallback PASSED` |
| `tests/adapters/test_entraid_graph.py::test_retry_after_above_the_cap_waits_the_cap` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_retry_after_above_the_cap_waits_the_cap PASSED` |
| `tests/adapters/test_entraid_graph.py::test_retry_429_on_every_attempt_raises_after_max_retries[0]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_retry_429_on_every_attempt_raises_after_max_retries[0] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_retry_429_on_every_attempt_raises_after_max_retries[2]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_retry_429_on_every_attempt_raises_after_max_retries[2] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_permission_status_raises_permission_error_naming_the_permissions[401]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_permission_status_raises_permission_error_naming_the_permissions[401] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_permission_status_raises_permission_error_naming_the_permissions[403]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_permission_status_raises_permission_error_naming_the_permissions[403] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_timeout_raises_graph_error_naming_the_path` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_timeout_raises_graph_error_naming_the_path PASSED` |
| `tests/adapters/test_entraid_graph.py::test_credential_failure_raises_authentication_error_naming_the_auth_mode[client_secret]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_credential_failure_raises_authentication_error_naming_the_auth_mode[client_secret] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_credential_failure_raises_authentication_error_naming_the_auth_mode[default]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_credential_failure_raises_authentication_error_naming_the_auth_mode[default] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[client_secret-ServiceRequestError]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[client_secret-ServiceRequestError] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[client_secret-ServiceResponseError]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[client_secret-ServiceResponseError] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[default-ServiceRequestError]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[default-ServiceRequestError] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[default-ServiceResponseError]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_unreachable_token_endpoint_raises_authentication_error[default-ServiceResponseError] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_request_error_after_the_response_raises_graph_error_naming_the_path[DecodingError]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_request_error_after_the_response_raises_graph_error_naming_the_path[DecodingError] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_request_error_after_the_response_raises_graph_error_naming_the_path[TooManyRedirects]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_request_error_after_the_response_raises_graph_error_naming_the_path[TooManyRedirects] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[object-without-value-collection-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[object-without-value-collection-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[object-without-value-member-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[object-without-value-member-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[value-null-collection-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[value-null-collection-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[value-null-member-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[value-null-member-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[empty-body-collection-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[empty-body-collection-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[empty-body-member-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_without_value_list_raises_instead_of_reading_zero_records[empty-body-member-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_with_empty_value_list_reads_zero_records[collection-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_with_empty_value_list_reads_zero_records[collection-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_page_with_empty_value_list_reads_zero_records[member-read]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_page_with_empty_value_list_reads_zero_records[member-read] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_graph_url_of_another_cloud_sets_the_token_scope_and_the_request_host` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_graph_url_of_another_cloud_sets_the_token_scope_and_the_request_host PASSED` |
| `tests/adapters/test_entraid_graph.py::test_next_link_to_another_host_is_sent_without_the_token` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_next_link_to_another_host_is_sent_without_the_token PASSED` |
| `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_throttling]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_throttling] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_permission_error]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_permission_error] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_server_error]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_server_error] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_timeout]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[queue_timeout] PASSED` |
| `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[refuse_token]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_graph.py::test_error_messages_and_logs_never_contain_the_token[refuse_token] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_client_secret_auth_builds_client_secret_credential_from_variables` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_client_secret_auth_builds_client_secret_credential_from_variables PASSED` |
| `tests/adapters/test_entraid_settings.py::test_client_secret_is_the_default_auth` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_client_secret_is_the_default_auth PASSED` |
| `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[settings-only]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[settings-only] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[variables-win]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[variables-win] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[variables-only]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[variables-only] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[tenant-variable-client-setting]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[tenant-variable-client-setting] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[empty-variables-fall-back]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_tenant_and_client_ids_come_from_variables_then_settings[empty-variables-fall-back] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_client_secret_setting_is_refused_naming_the_variable[client_secret]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_client_secret_setting_is_refused_naming_the_variable[client_secret] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_client_secret_setting_is_refused_naming_the_variable[default]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_client_secret_setting_is_refused_naming_the_variable[default] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_missing_secret_raises_naming_only_the_secret_variable[ids-from-variables]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_missing_secret_raises_naming_only_the_secret_variable[ids-from-variables] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_missing_secret_raises_naming_only_the_secret_variable[ids-from-settings]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_missing_secret_raises_naming_only_the_secret_variable[ids-from-settings] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_missing_secret_raises_naming_only_the_secret_variable[empty-secret-variable]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_missing_secret_raises_naming_only_the_secret_variable[empty-secret-variable] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_missing_everything_raises_naming_the_three_variables` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_missing_everything_raises_naming_the_three_variables PASSED` |
| `tests/adapters/test_entraid_settings.py::test_default_auth_without_variables_needs_no_tenant_client_or_secret` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_default_auth_without_variables_needs_no_tenant_client_or_secret PASSED` |
| `tests/adapters/test_entraid_settings.py::test_default_auth_ignores_client_secret_variables` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_default_auth_ignores_client_secret_variables PASSED` |
| `tests/adapters/test_entraid_settings.py::test_build_credential_returns_the_real_azure_credential_without_contacting_azure[client_secret-ClientSecretCredential]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_build_credential_returns_the_real_azure_credential_without_contacting_azure[client_secret-ClientSecretCredential] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_build_credential_returns_the_real_azure_credential_without_contacting_azure[default-DefaultAzureCredential]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_build_credential_returns_the_real_azure_credential_without_contacting_azure[default-DefaultAzureCredential] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_repr_of_settings_does_not_contain_the_secret` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_repr_of_settings_does_not_contain_the_secret PASSED` |
| `tests/adapters/test_entraid_settings.py::test_built_credential_is_closed_when_graph_client_exits[client_secret]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_built_credential_is_closed_when_graph_client_exits[client_secret] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_built_credential_is_closed_when_graph_client_exits[default]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_built_credential_is_closed_when_graph_client_exits[default] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[page-size-zero]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[page-size-zero] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[page-size-above-maximum]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[page-size-above-maximum] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[page-size-not-integer]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[page-size-not-integer] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[membership-nested]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[membership-nested] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[auth-token]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[auth-token] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[graph-url-http]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[graph-url-http] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[timeout-zero]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[timeout-zero] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[max-retries-above-maximum]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[max-retries-above-maximum] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[unknown-top-level-key]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[unknown-top-level-key] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[collection-without-resource]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[collection-without-resource] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[collection-resource-devices]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[collection-resource-devices] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[collection-unknown-key]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[collection-unknown-key] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[advanced-query-not-boolean]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[advanced-query-not-boolean] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[select-key-devices]` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_invalid_setting_raises_config_error_naming_the_setting[select-key-devices] PASSED` |
| `tests/adapters/test_entraid_settings.py::test_given_credential_is_closed_when_graph_client_exits_on_error` | unit | `uv run pytest -v -p no:warnings -p no:cacheprovider tests/adapters -k entraid` | 2026-11-04T00:11:36Z | n/a | `tests/adapters/test_entraid_settings.py::test_given_credential_is_closed_when_graph_client_exits_on_error PASSED` |

Run summary at 2026-11-04T00:11:36Z: `================ 105 passed, 2 skipped, 49 deselected in 0.31s =================`

## 5. Review findings

Six read-only review agents ran in parallel: code, tests, errors, comments, types and simplify. Findings that were
high severity, or reported by two agents, were fixed in commit `34409049`. That commit also added tests for each fix.

| Severity | File | Finding | Status |
|---|---|---|---|
| High | `entraid.py` (page parsing) | An HTTP 200 page with no `value` key, or an empty body, was read as zero records. A diff could then plan to delete every synced user. | Fixed: `EntraidGraphError` |
| High | `entraid.py` (`_send`) | Network errors while getting a token (`ServiceRequestError`, `ServiceResponseError`) were not mapped (FR-030). | Fixed: `EntraidAuthenticationError` |
| High | tests | The FR-030 test did not check the guidance text or that the secret is absent. | Fixed |
| High | tests | Nothing tested the token scope for a non-default `graph_url`, or that the token is sent only to the Graph host. | Fixed |
| Medium | `entraid.py` (member reads) | One failed member read left the other reads running while the client closed. | Fixed: the other reads are cancelled and awaited |
| Medium | `entraid.py` (references) | A relationship to one node that resolved to several peers kept the first one silently. | Fixed: `EntraidConfigError` |
| Medium | `entraid.py`, docs, example | The permission wording differed between code, docs and example. HTTP 401 was described as a missing permission. | Fixed: 401 means the token was rejected, 403 means a permission is missing; the wording depends on `auth` |
| Medium | tests | Untested: the thread path for `load()` inside a running event loop, a token failure through `load()`, and the union `$select`. | Fixed |
| Medium | docs | The log examples did not use the CLI format. | Fixed |
| Low | `entraid.py` | `httpx.RequestError` was not fully caught; `record_to_diffsync` had dead fallbacks; `client_secret` took part in equality. | Fixed |
| Low | docs | The start-up error prefix, the scope of `$select`, `tenant_id` and `client_id` with `auth: default`, other Microsoft clouds being untested, and the meaning of the tested versions were missing or unclear. | Fixed |
| Low | tests | An assertion on `requests=5` also matched `member_requests=5`; `make_adapter` had a dead guard. | Fixed |
| Medium | `entraid.py` (types) | `EntraidSettings` and `Collection` are checked only in `from_settings`. Building them directly accepts invalid values. | Deferred |
| Low | `entraid.py` (types) | Possible type changes: `Literal` types for `resource`, `auth` and `membership`; mappings that cannot be changed in frozen dataclasses; a private `sdk` attribute; a shared `EntraidError` base class. | Deferred |
| Low | tests | No tests for a record without `id`, a `Retry-After` value that is not a number, or the message of an HTTP 500. | Deferred |
| Low | `entraid_fakes.py` | The two user test models repeat the same fields. | Deferred |

## 6. Autonomous decisions

- Chunking:
    - Phase 1 (T001, a check with no file change) was merged into the Phase 2 chunk.
    - Phase 8 was split into three chunks: example, docs, lint.
- Review agents ran in parallel rather than one after another. They were read-only, so they could not write over
  each other's work.
- One fix subagent applied all review fixes, because they spanned code, tests, docs and spec documents.
- `ty` warnings: the brief requires `# ty: ignore[unresolved-import]` on the SDK imports. With the SDK installed,
  `ty` reports 11 `unused-ignore-comment` warnings on those lines; it still exits 0. Without the SDK, as in CI after
  `uv sync`, the comments are needed to avoid errors. Probes found no comment form that is clean in both
  environments. The brief's form was kept, and the conflict is recorded in `alignment-check.md`.
- Settings the brief does not name: `timeout` (default 60), `max_retries` (default 5) and `select`. `auth` defaults
  to `client_secret`.
- The collection names `users` and `groups` are reserved for unfiltered reads.
- The dropped-reference warning is logged once per groups kind, at the end of that kind's load, so it also works on
  the engine's incremental path.
- The example's code was produced by the repository generator from a committed `schema.yml`, because
  `infrahub-sync generate` needs a running Infrahub.
- Not run in this environment: vale, the Docusaurus build (no `docs/node_modules`), `infrahubctl schema load` for the
  example schema, and any run against a real Entra ID tenant.

## 7. Suggested next steps

1. Delete `_ty_probe.py` at the repository root. It is an empty file left from planning, untracked and never
   committed; the run could not delete it because `rm` was denied.
2. Decide whether to keep the brief's `# ty: ignore[unresolved-import]` form (see section 6).
3. Run `diff` against a test tenant with the example configuration. The points mocks cannot confirm are:
   - the `@odata.type` values Graph returns on member pages;
   - Graph's answer to the advanced-query filter used in the example.
4. Run vale and the docs build in CI. If the example schema is loaded, check it with `infrahubctl schema load`.
5. Review the deferred findings in section 5.
6. Run `/speckit.opsmill.extract` to capture knowledge, guidelines and ADRs from this spec, then open a pull request
   with the `changes/minor` label.

## 8. Resumed run on 2026-11-04

A second run was started because the first run was thought to have stopped before it finished. It had not: the
report above was already committed with status DONE.

- **Base commit**: `456a4353`. **Status**: DONE.
- State found:
    - All 26 tasks in `tasks.md` were ticked; no chunk was dispatched.
    - The only uncommitted file was the empty, untracked `_ty_probe.py`.
- `_ty_probe.py` was committed in `456a4353`, as the resume instructions required, so the working tree was clean. Its
  removal with `git rm` was denied by the permission mode, so the empty file is still on the branch.
- Review: the diff of this run is the empty `_ty_probe.py` only, so `speckit-review-run` was not run again. The
  review in section 5 covers the full change.
- Checks run at 2026-11-04T00:12:50Z:
    - `uv run pytest -q`: `315 passed, 3 skipped`. This is the same result as section 4.
    - `uv run ruff check .`: `All checks passed!`
    - `uv run ruff format --check .`: `79 files already formatted`.
    - `uv run ty check .`: 14 warnings and no errors. 11 are the `unused-ignore-comment` warnings in `entraid.py`
      described in section 6. The other 3 are in `tests/adapters/test_nautobot_incremental.py`, which this branch does
      not change.
    - pylint and rumdl were not run again. No file they check changed after the first run's lint pass.
- No test was added or changed in this run.
- Change to next step 1: delete `_ty_probe.py` with a new commit, because it is now tracked.

## 9. Second resumed run on 2026-11-04

A third run was started with the same resume instructions. The work was already finished.

- **Base commit**: `50f0bd3f`. **Status**: DONE.
- State found:
    - The working tree was clean, so there was nothing from an interrupted run to commit.
    - `tasks.md` exists and all 26 tasks are ticked, so `speckit-opsmill-prep` was not run and no chunk was dispatched.
- `git rm _ty_probe.py` was denied by the permission mode again. The empty file is still on the branch.
- Review: this run changed no code, so `speckit-review-run` was not run again. The review in section 5 covers the full
  change.
- Checks run at 2026-11-04T00:14:00Z, with the same results as section 8:
    - `uv run pytest -q`: `315 passed, 3 skipped`.
    - `uv run ruff check .`: `All checks passed!`
    - `uv run ruff format --check .`: `79 files already formatted`.
    - `uv run ty check .`: 14 warnings and no errors.
- No test was added or changed in this run.
- Next step 1 is unchanged: a person with permission to delete files removes `_ty_probe.py` in a new commit.
