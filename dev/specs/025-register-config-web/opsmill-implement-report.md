# Implementation report: register a configuration package from the web interface

- Spec directory: `dev/specs/025-register-config-web`.
- Branch: `025-register-config-web`, in the worktree `.worktrees/025-register-config-web`.
- Base commit: `f35a59c8`. Head commit: the commit that adds this report. The report was
  committed first as `21a34ee6`; review fixes followed, listed in
  [Code review fixes on 2026-11-19](#code-review-fixes-on-2026-11-19).
- The first pull request (User Story 1) no longer ends cleanly at `869395e6`, because some review
  fixes belong to it. The local branch `025-register-config-web-us1` holds `869395e6` plus those
  fixes. The second pull request is the rest of `025-register-config-web`.
- Result: all 39 tasks are done. Every new Python test passed locally, including the PostgreSQL
  cases. The web interface has no test runner, and the five quickstart scenarios were not run by
  hand.
- Total time: not recorded.

## Baseline before any change

Recorded at commit `f35a59c8` on branch `025-register-config-web`, before any code change for this
feature. Python profile: `uv sync --extra dev --extra prefect --extra service`.

- `uv sync --extra dev --extra prefect --extra service`: pass.
- `pnpm --dir frontend install --frozen-lockfile`: pass.
- `uv run invoke lint`: fail, at the first gate (rumdl), so the later gates did not run from this command.
  The 40 rumdl findings are all in two untracked working files at the worktree root that are not part of
  the repository: `morning-report.md` (13 findings) and `overnight-log.md` (27 findings, all MD007 list
  indentation). No tracked file has a rumdl finding. The remaining gates were run one at a time:
    - `uv run invoke linter.lint-ruff`: pass (543 files already formatted, all checks passed).
    - `uv run invoke linter.lint-pylint`: pass (0 diagnostics, score 10.0).
    - `uv run invoke linter.lint-yaml`: pass.
    - `uv run invoke linter.lint-ty`: pass (exit 0). ty reports 26 warnings, all
      `unused-ignore-comment`, in `infrahub_sync/adapters/nautobot.py`, `infrahub_sync/adapters/netbox.py`
      and files under `tests/`. These exist on the base branch and are not caused by this feature.
- `uv run ruff check .`: pass.
- `uv run ruff format --check .`: pass.
- `uv run invoke tests.tests-unit`: pass (8929 passed, 4 skipped, 383 deselected, 2 xfailed).

A later lint failure on `morning-report.md` or `overnight-log.md` is not caused by this feature.

## Final workflow (T038)

Recorded on branch `025-register-config-web` after the Phase 7 documentation changes. Python profile:
`uv sync --extra dev --extra prefect --extra service`.

- `uv run invoke format`: pass, no file changed.
- `uv run invoke lint`: pass, every gate ran:
    - rumdl: "No issues found in 172 files".
    - ruff: "544 files already formatted", "All checks passed!".
    - pylint: "Pylint passed (0 diagnostics, score 10.0)".
    - yamllint: pass.
    - ty: exit 0, 28 warnings, all `unused-ignore-comment` (26 at the baseline).
- `uv run ruff check .`: "All checks passed!".
- `uv run invoke docs.lint`: "No issues found in 172 files".
- `uv run invoke tests.tests-unit`: 9043 passed, 4 skipped, 389 deselected, 2 xfailed.
- `uv run infrahub-sync --help`, `configs --help`, `runs --help`: exit 0.
- `pnpm --dir frontend check`: "Checked 78 files", no fixes applied.
- `pnpm --dir frontend typecheck`: exit 0.
- `pnpm --dir frontend build`: built.
- `uv run invoke docs.generate`: no change to `docs/docs/reference/cli.mdx`, because the second pull
  request adds no CLI option.

## Quickstart scenarios (T039)

No scenario of [quickstart.md](quickstart.md) was run by hand. Every scenario needs a running Sync API
with the development stack and Infrahub, and scenarios 2 to 5 also need a person to drop a file in a
browser. Other sessions use the development stacks on this machine, so this chunk did not start, stop
or use one: registering a package on another session's stack would write to its database, and
scenario 4 stops Infrahub.

The automated checks of the quickstart ran and passed; see "Final workflow (T038)" above. The unit
tests listed below cover the server and CLI steps of each scenario. They do not replace running the
scenario.

| Scenario | Status | Reason | Unit tests that cover its steps |
| --- | --- | --- | --- |
| 1. Validate without registering | Not run | Needs a running Sync API and Infrahub. | `tests/service/test_config_routes.py`: `test_an_administrator_checks_a_package_and_registers_nothing`, `test_a_check_with_a_branch_reads_that_branch`, `test_a_check_finds_what_validating_the_registered_version_finds`. `tests/cli/test_client_commands.py`: `test_configs_check_sends_the_file_as_package_and_prints_the_report`, `test_configs_check_exits_1_when_the_page_has_a_finding`. |
| 2. Add a version from the web interface | Not run | Needs a browser, a running Sync API and Infrahub. | `tests/service/test_config_routes.py`: `test_an_administrator_checks_a_document_in_yaml_or_json`, `test_duplicate_configuration_version_checksum_returns_existing_version_without_a_second_write`. The web interface has no test runner (research R12); `pnpm check` and `pnpm typecheck` passed. |
| 3. Register a new configuration | Not run | Needs a browser and a running Sync API. | `tests/service/test_config_routes.py`: `test_the_register_response_shows_the_checksum_and_no_declared_content`. The same-name warning is in the web interface only and has no automated test. |
| 4. Destination unreachable | Not run | Needs a browser, and stopping Infrahub on a development stack. | `tests/service/test_config_routes.py`: `test_registration_accepts_the_mark_and_every_version_resource_returns_it`, `test_the_mark_defaults_to_false`, `test_an_identical_version_request_with_the_mark_returns_the_existing_version_unchanged`, `test_a_key_reused_with_a_different_mark_is_refused`, `test_an_unmarked_request_keeps_the_fingerprint_of_receipts_stored_before_the_mark`. `tests/product_store/test_configuration_unvalidated_mark.py`, including databases created before the mark. Its SQLite cases ran in this chunk (6 passed); its PostgreSQL cases skip without `PRODUCT_STORE_TEST_POSTGRESQL_DSN` and were not run in this chunk. `tests/cli/test_client_commands.py`: `test_configs_version_prints_the_unvalidated_mark_and_never_sets_it`. |
| 5. Refusals and access | Not run | Needs a browser and a running Sync API. | `tests/service/test_config_routes.py`: `test_a_v2_configuration_is_refused_with_the_conversion_command`, `test_a_document_that_is_not_a_mapping_is_an_invalid_package`, `test_a_check_by_a_non_administrator_is_refused_before_any_read`. The "Drop one package file." message and the disabled drop areas are in the web interface only and have no automated test. |

## Chunk ledger

Each chunk ran in its own subagent. Chunks ran one at a time.

| Chunk | Tasks | Done / partial / blocked | Commits | Points the subagent raised |
|---|---|---|---|---|
| 1. Setup | T001 | 1 / 0 / 0 | `4fb6a404` | Before any change, lint failed only on two untracked notes at the worktree root. The notes were moved out of the worktree. |
| 2. Shared validation | T002 to T004 | 3 / 0 / 0 | `19bd9cea` | `_check_parsed_package` is the one function both validation paths call. A destination that cannot read a schema reports `read`. |
| 3. User Story 1, store and service | T005, T006, T009 to T011 | 5 / 0 / 0 | `f167fd92` | The request model checks "exactly one field" in the route, so the answer is 400 and not 422. `declared_content` is not redacted: it holds credential references, not values, and redacting it would change the checksum. |
| 4. User Story 1, client and CLI | T007, T008, T012 to T014 | 5 / 0 / 0 | `15469349` | `configs check` exits 1 on any finding on the page, as the contract says. `configs validate` exits 1 only on an error finding with `--branch`. |
| 5. User Story 1, documentation | part of T035 to T038 | done, not ticked until chunk 10 | `a52a79ef` | ADR 0051 without the mark; `configs check` documentation; changelog fragment `+validate-config-package.added.md`. |
| Review fix for User Story 1 | (review) | n/a | `869395e6` | Documentation now lists the `rejected` reason, uses the finding name `destination-schema-read-failed`, and says the Sync API sets no body size limit. |
| 6. User Story 2 | T015 to T022 | 8 / 0 / 0 | `6c8f375a`, `e2f08a5b` | API types generated from the app in Python, without Docker. The `frontend/vendor/infrahub` submodule had to be initialized in this worktree. The generated types also picked up plan step types that were out of date on the base branch. Each review step runs two checks: without a branch, then with the package's branch. |
| 7. User Story 3 | T023 to T025 | 3 / 0 / 0 | `2cd7b4db` | Name lookup uses `GET /configs?name=...&limit=1`, which the Sync API already supports. |
| 8. User Story 4, server | T026 to T031, T034 | 7 / 0 / 0 | `388e99de`, `da15b99e` | The subagent had no PostgreSQL database, so T026 was partial. The orchestrator ran the PostgreSQL cases in a throwaway container and ticked T026 in `da15b99e`. The mark is read with an `EXISTS` column. |
| 9. User Story 4, web interface | T032, T033 | 2 / 0 / 0 | `c62e7f14` | The mark shows on the version bar and in the version list, because the version route's response has no typed fields in the OpenAPI document. |
| 10. Documentation and final checks | T035 to T039 | 5 / 0 / 0 | `23244edc` | `configs versions` does not print the mark; `configs show --version` does. The five quickstart scenarios were not run. |

## Tasks not completed

None. Every task in `tasks.md` is ticked.

## Local-pass evidence

All runs were on macOS with Python 3.11.8, from the worktree, offline. Every row passed. Rows list
one test function each, with its parameter IDs. The full verbose output was kept in the session's
temporary directory and is not committed.

Commands:
- A: `uv run pytest tests/configuration/test_schema_validation.py -v -k "returns_its_reason or returns_no_read_failure_reason"`
- B: `uv run pytest -v -p no:randomly tests/product_store/test_configs_service.py tests/service/test_config_routes.py -k "check_package or checked_content or error_boundary or check or document or v2_configuration or non_administrator or failed_destination_read or discloses_no_collected or not_taken_for"`
- C: `uv run pytest -v -p no:randomly tests/cli/test_client_commands.py tests/client/test_client.py tests/client/test_models.py tests/cli/test_parity_and_closure.py tests/client/test_public_surface.py -k "check_package or package_check or configs_check or parity or public or json_native"`
- D: `uv run pytest -v -p no:cacheprovider tests/configuration/test_schema_validation.py::test_a_failed_schema_read_is_an_error_finding_not_a_raise tests/configuration/test_schema_validation.py::test_a_failed_schema_read_returns_its_reason tests/service/test_config_routes.py::test_a_failed_destination_read_is_returned_by_its_reason tests/product_store/test_configs_service.py::test_check_package_reports_a_failed_destination_read_by_its_reason`
- E: `uv run pytest -v -rs -p no:cacheprovider tests/product_store/test_configuration_unvalidated_mark.py tests/client/test_models.py::test_a_configuration_request_carries_the_unvalidated_mark_and_defaults_it_to_false tests/cli/test_client_commands.py::test_configs_version_prints_the_unvalidated_mark_and_never_sets_it tests/service/test_config_routes.py -k "unvalidated or mark or fingerprint or carries" -m "not slow or slow" --override-ini=addopts=`
- F: `PRODUCT_STORE_TEST_POSTGRESQL_DSN=postgresql://postgres:<password>@127.0.0.1:55525/store uv run pytest -v -rs -p no:cacheprovider tests/product_store/test_configuration_unvalidated_mark.py -m "not slow or slow" --override-ini=addopts=`

| Test | Type | Command | Passed at | Environment | Pass line |
|---|---|---|---|---|---|
| `tests/configuration/test_schema_validation.py::test_a_failed_schema_read_returns_its_reason` [timeout, unreachable, unauthorized, forbidden, nobranch, unconfigured, credentials; rejected added later] | unit | A; D for `[rejected]` | 2026-11-19T14:22:06Z; 15:38:10Z | n/a | `PASSED` |
| `tests/configuration/test_schema_validation.py::test_a_failed_access_read_returns_its_reason` | unit | A | 2026-11-19T14:22:06Z | n/a | `PASSED` |
| `tests/configuration/test_schema_validation.py::test_a_successful_read_returns_no_read_failure_reason` | unit | A | 2026-11-19T14:22:06Z | n/a | `PASSED` |
| `tests/configuration/test_schema_validation.py::test_a_failed_schema_read_is_an_error_finding_not_a_raise[rejected]` (new case) | unit | D | 2026-11-19T15:38:10Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_every_public_service_operation_carries_the_error_boundary` (modified) | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_check_package_reports_the_findings_validate_reports_after_registration` [12 cases: worker or no worker, declared only or destination schema, clean, warning or destination mismatch package] | unit | B, C | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_check_package_reports_a_failed_destination_read_by_its_reason` [unreachable, rejected] | unit | B; D | 2026-11-19T14:58:52Z; 15:38:10Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_check_package_registers_nothing` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_check_package_refuses_what_registration_refuses` [store-key, credential-error, structural-error] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_check_package_returns_the_checksum_and_content_the_registration_stores` [clean, warning] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_registering_the_checked_content_gives_the_original_checksum` [clean, warning] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/product_store/test_configs_service.py::test_check_package_refuses_a_wrong_typed_destination_option` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_an_administrator_checks_a_package_and_registers_nothing` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_an_administrator_checks_a_document_in_yaml_or_json` [yaml, json] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_the_registered_example_package_document_is_checked` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_needs_exactly_one_of_package_and_document` [neither, both, both-null] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_document_that_is_not_a_mapping_is_an_invalid_package` [empty, blank, comment, list, scalar, number, integer-key, empty-mapping, invalid-yaml] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_v2_configuration_is_refused_with_the_conversion_command` [document, package] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_by_a_non_administrator_is_refused_before_any_read` [valid-package, invalid-document, both-fields, neither-field, schema-invalid] | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_without_a_token_is_refused_before_any_read` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_with_a_branch_reads_that_branch` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_finds_what_validating_the_registered_version_finds` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_failed_destination_read_is_returned_by_its_reason` [timeout, unreachable, unauthorized, forbidden, nobranch, unconfigured, credentials; rejected] | unit | B; D for `[rejected]` | 2026-11-19T14:58:52Z; 15:38:10Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_pages_findings_like_the_validation_of_a_version` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_check_discloses_no_collected_secret` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_the_check_route_is_not_taken_for_a_configuration_id` | unit | B | 2026-11-19T14:58:52Z | n/a | `PASSED` |
| `tests/cli/test_client_commands.py::test_configs_check_sends_the_file_as_package_and_prints_the_report` | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/cli/test_client_commands.py::test_configs_check_exits_1_when_the_page_has_a_finding` [no-findings, branch-no-findings, a-warning, an-error, branch-with-findings] | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/cli/test_client_commands.py::test_configs_check_refuses_an_unreadable_file_like_configs_register` [missing-file, invalid-yaml, not-a-mapping, not-utf-8] | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_client.py::test_check_package_posts_exactly_one_field_without_an_idempotency_key` [package, document] | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_client.py::test_check_package_without_a_branch_sends_no_branch` | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_client.py::test_check_package_refuses_bad_page_or_branch_arguments_before_sending` [negative-offset, zero-limit, limit-over-256, untrimmed-branch, empty-branch] | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_client.py::test_check_package_v2_refusal_is_a_configs_api_error` | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_models.py::test_a_package_check_request_needs_exactly_one_of_package_and_document` [neither, both, both-null] | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_models.py::test_a_package_check_request_keeps_the_json_native_package_rule` [nan, tuple, integer-key] | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_models.py::test_a_package_check_request_serializes_only_the_field_it_carries` | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/client/test_models.py::test_a_package_check_request_refuses_unknown_fields` | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/cli/test_parity_and_closure.py::test_parity_matrix_is_exactly_the_accepted_cli_surface` (modified) | unit | C | 2026-11-19T15:14:00Z | n/a | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_marked_first_version_reads_back_marked_everywhere` [sqlite] | unit | E | 2026-11-19T16:11:17Z | SQLite | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_marked_first_version_reads_back_marked_everywhere` [postgresql] | integration | F | 2026-11-19T16:14:00Z | PostgreSQL 16 in a throwaway container on 127.0.0.1:55525, removed afterwards | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_version_registered_without_the_mark_reads_false` [sqlite] | unit | E | 2026-11-19T16:11:17Z | SQLite | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_version_registered_without_the_mark_reads_false` [postgresql] | integration | F | 2026-11-19T16:14:00Z | PostgreSQL 16, as above | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_marked_added_version_is_marked_and_its_predecessor_is_not` [sqlite] | unit | E | 2026-11-19T16:11:17Z | SQLite | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_marked_added_version_is_marked_and_its_predecessor_is_not` [postgresql] | integration | F | 2026-11-19T16:14:00Z | PostgreSQL 16, as above | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_an_identical_package_does_not_change_the_existing_mark` [sqlite-unmarked-existing, sqlite-marked-existing] | unit | E | 2026-11-19T16:11:17Z | SQLite | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_an_identical_package_does_not_change_the_existing_mark` [postgresql-unmarked-existing, postgresql-marked-existing] | integration | F | 2026-11-19T16:14:00Z | PostgreSQL 16, as above | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_sqlite_store_created_before_the_mark_gains_the_table_at_start_up` | unit | E | 2026-11-19T16:11:17Z | SQLite | `PASSED` |
| `tests/product_store/test_configuration_unvalidated_mark.py::test_a_postgresql_store_created_before_the_mark_gains_the_table_at_start_up` | integration | F | 2026-11-19T16:14:00Z | PostgreSQL 16, as above | `PASSED` |
| `tests/client/test_models.py::test_a_configuration_request_carries_the_unvalidated_mark_and_defaults_it_to_false` | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |
| `tests/cli/test_client_commands.py::test_configs_version_prints_the_unvalidated_mark_and_never_sets_it` | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_registration_accepts_the_mark_and_every_version_resource_returns_it` | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_the_mark_defaults_to_false` | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_an_identical_version_request_with_the_mark_returns_the_existing_version_unchanged` | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_a_key_reused_with_a_different_mark_is_refused` [true-then-absent, absent-then-true, false-then-true] | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |
| `tests/service/test_config_routes.py::test_an_unmarked_request_keeps_the_fingerprint_of_receipts_stored_before_the_mark` [absent, explicit-false] | unit | E | 2026-11-19T16:11:17Z | n/a | `PASSED` |

The web interface has no test runner (research R12), so its changes have no tests. Its checks
(`pnpm check`, `pnpm typecheck`, `pnpm build`) passed after every web interface chunk and after the
final review. The full unit tier passed after the last change: 9043 passed, 4 skipped, 2 xfailed
(2026-11-19T16:33:54Z). All 435 store tests that take a PostgreSQL parameter also passed against
the throwaway database.

## Review findings

Two reviews ran: one on the first pull request (`f35a59c8..a52a79ef`) before the web interface
work, and one on the second (`869395e6..HEAD`). Neither found a problem rated high or above.

| Severity | Where | Finding | Status |
|---|---|---|---|
| Medium | ADR 0051, `contracts/sync-api.md`, `configuration-package.mdx` | The `rejected` reason was missing from the lists of read failure reasons. | Fixed in `869395e6` |
| Medium | same files | The finding name was spelled `destination_schema_read_failed`; the code emits `destination-schema-read-failed`. | Fixed in `869395e6` |
| Low | ADR 0051, contract, spec FR-016 | They said the Sync API answers 413 for a large body. It sets no limit; only a proxy can answer 413. | Fixed in `869395e6` |
| Low | ADR 0051 | It did not say that a body that is not valid JSON gets 422 before the token check. | Fixed in `869395e6` |
| Medium | `infrahub_sync/product_store/configs.py` | `destination_read` is `read` when no read ran, for a destination that cannot read a schema. Only an invalid destination does this, and it already produces an error finding. | Deferred |
| Medium | `infrahub_sync/service/config_routes.py` | Parsing `document` with YAML aliases can use a lot of CPU and memory. Only administrators reach the parser (accepted in critique E2). | Deferred |
| Medium | `configuration-package.mdx:228`, `sync-http-api.mdx:472`, ADR 0051 | They say `created: false` happens when the package matches the newest version. The store matches any stored version of the configuration. The web interface message is correct. | Deferred |
| Medium | `tests/product_store/test_configuration_unvalidated_mark.py` | No test checks that a failed insert of the mark rolls back the version. | Deferred |
| Low | `PackageReview.tsx:105,199` | When reading the configuration fails, the review step shows no error and keeps Register unavailable. | Deferred |
| Low | `PackageReview.tsx:128` | The check request has no time limit in the browser; a request that never returns keeps "Validating…". | Deferred |
| Low | `PackageReview.tsx:388` | After a refused check on a changed branch, the hint says to drop the package again, but changing the branch is enough. | Deferred |
| Low | `frontend/src/api/types.ts` | `RegisteredVersion.version` leaves out `registered_without_validation`. Nothing reads it. | Deferred |
| Existing | registration routes | When registration fails with a server error that may have happened after the write, the Sync API keeps the request key claimed, so Retry is refused with 409 `idempotency-in-progress`. This existed before this feature. | Not changed |
| Low | `infrahub_sync/cli.py` | `configs versions` does not print the mark; `configs show --version` does. | Deferred |

## Decisions made without asking

- The first pull request's documentation, ADR and changelog fragment were written right after
  User Story 1, before the web interface, and a review ran on those commits first. That keeps the
  first pull request a clean prefix of the branch (`..869395e6`). `tasks.md` says Phase 7 runs last.
- The two notes from the overnight run, `morning-report.md` and `overnight-log.md`, were moved
  out of the worktree to `.worktrees/025-register-config-web-notes/`, because the Markdown linter
  failed on them.
- API types were generated from the app in Python instead of a running development stack, because
  other sessions run development stacks on this machine.
- The PostgreSQL cases ran in a throwaway container started and removed by this run, not in a
  database another session uses.
- Review findings rated medium that are documentation errors in the first pull request were fixed;
  the other medium and low findings are listed above and left for you.
- No end-to-end test was added. The quickstart scenarios need a person in a browser and a running
  Sync API with Infrahub, so they were recorded as not run.

## Suggested next steps

1. Run quickstart scenarios 1 to 5 by hand against a development stack.
2. Decide on the findings left for a decision, listed under
   [Code review fixes on 2026-11-19](#code-review-fixes-on-2026-11-19). The `created: false`
   wording, the `read` value without a read, the mark rollback test, the three web interface
   findings and the type of `RegisteredVersion` in the table above are fixed there.
3. Open the first pull request from `025-register-config-web-us1` into `feature/v3-develop`, then
   the second.

## Code review fixes on 2026-11-19

A code review of the branch (`/code-review xhigh --fix`, 21 commits since `feature/v3-develop`)
reported 14 findings. This section lists what changed after the report commit `21a34ee6`, and
what is left for a decision.

### Commits and the pull request each belongs to

| Commit on `025-register-config-web` | Change | Pull request |
|---|---|---|
| `5c30db1e` | Locks the branch and reason fields while Register reads the configuration again; detects a 403 by its status code; the two validation routes share `_findings_page` and `_destination_options`. | Both: the `config_routes.py` part is the first, the web interface part the second. |
| `4f12b59f` | The review step blocks registration only on an `error` finding and lists warnings (spec decision D-10, research R4 corrected). | Second; its spec, research and alignment-check changes are also on the first. |
| `d38e7a0d` | `configs check` exits 1 only on an `error` finding. ADR 0051, the contract, the docs and the CLI reference follow. | First |
| `e8fd98ea` | `destination_read` is `not_read`, not `read`, when the destination adapter is unknown or declares no schema read. | First |
| `be569f84` | The generated API type comment for `destination_read` names `not_read`. | Second |
| `fb0c3055` | The docs, ADR 0051 and FR-012 say `created: false` matches any stored version of the configuration, not only the newest. | Second |
| `74715d03` | The review step shows why it cannot compare when the configuration cannot be read, says to change the branch after a refused branch check, and keeps warnings out of the "cannot be reached" notice. `RegisteredVersion` types the mark. | Second |
| `f0bae654` | A test proves that a failed insert of the mark stores no version (SQLite). | Second |
| `95b31331`, `62f16e66` | `configs versions` prints the mark. | Second |
| `7702d117` | The review step lists only the credential variables that the configuration's settings use, with the `env` provider, as the Sync API's worker check does. | Second |
| `0d882e01` | `PackageCheckRequest`'s serializer has no return annotation, so the model keeps its serialization schema. | First |

The branch `025-register-config-web-us1` starts at `869395e6` and holds, in order: the
`config_routes.py` part of `5c30db1e` (`7abfd283`), `d38e7a0d` (`e11b6a08`), `e8fd98ea`
(`140fbad4`), `0d882e01` (`bad2d5c7`), and the spec part of `4f12b59f` (`e64510cf`). The commits
applied without conflicts. To open the first pull request from it, rebase
`025-register-config-web` onto it afterwards or merge it first; the second branch already
contains the same changes.

### Decision made without asking: only an error finding blocks registration

The spec required "no findings" for "Valid", because research R4 read only `validation.py` and
concluded that every finding is an error. `ValidationFinding.severity` also accepts `warning`, and
two warnings exist:

- `worker-environment-unknown`, returned by every check with a branch while no worker has
  published its credential variables. With the old rule, nothing could be registered from the web
  interface in that state, not even with "Register without validation".
- `intentional-omission`, returned for a package that declares `omissions`. Such a package could
  never be registered from the web interface, though `configs register` accepts it.

The grilling brief says a package is valid "only when no finding has `error` severity", and that
lower-severity findings are listed. The change follows that sentence. It also changes
`configs check`: it now exits 1 only on an `error` finding, because ADR 0051 gave no reason for the
stricter rule other than the same assumption. When the report has more findings than one page, the
review step still blocks Register, because an error can be on a later page.

### Findings left for a decision

- **The registered checksum can differ from the checked one.** The browser reads
  `declared_content` from the check as JSON and sends it back to register. A value such as `30.0`
  comes back as `30`, so the stored checksum differs from the checksum of the same file registered
  with the CLI, and `created: false` does not detect it. Integers above 2^53 also lose precision.
  Options:
  - register from the file text (`document`), as the check does, which needs a new field on the
    registration routes;
  - send `package_checksum` from the check and have the Sync API refuse a different checksum.
- **A literal credential value gets a refusal with no location.** The check returns 422
  `configs-validation` with "the configuration service refused the request", because `_call` drops
  the findings that `_validation_refusal` collected. ADR 0051 accepts this. A fix returns those
  findings in the refusal.
- **YAML alias expansion.** A 408-byte file took 2.6 seconds to refuse, and each added level
  multiplies the time by about nine. Only administrators reach the parser; ADR 0051 accepts it. A
  fix needs a size or alias limit shared by the CLI and the Sync API.
- **JSON indented with tabs is refused.** YAML does not allow tab indentation. The CLI has the same
  gap. A fix tries `json.loads` before `yaml.safe_load` in both the CLI and the Sync API; ADR 0051
  names `yaml.safe_load` as the one parser, so it needs an ADR change. A fix tried during the review
  was reverted because it changed only the Sync API.
- **Register after a definitive refusal.** After a 403 or 422 on Register, the request stays locked
  and Register sends it again. FR-017 keeps one key per review step; deciding which refusals allow a
  new key is a design decision.
- **Two checks per dropped file.** The first check, without a branch, only reads the declared
  branch; the second validates against it. Removing the first needs an API option that validates
  against the declared branch.
- **The check has no time limit in the browser.** The destination read uses the time limits from
  the package's own settings, so a fixed browser limit could stop a check that would complete.
  Cancel stays available.
- **Retry after a server error gets 409.** This existed before this feature (see above).

### Verification after the last commit

On `025-register-config-web` at `0d882e01`:

- `uv run invoke format`: no change. `uv run invoke lint`: exit 0 (28 `unused-ignore-comment`
  warnings from ty, which do not fail the gate). `uv run ruff check .`: pass.
- `uv run invoke tests.tests-unit`: 9046 passed, 4 skipped, 389 deselected, 2 xfailed.
- `uv run invoke docs.generate`: no change. CLI `--help` for `infrahub-sync`, `configs` and `runs`:
  pass.
- Web interface: `tsc --noEmit`, `biome check src` and `vite build`: pass. There is no test runner;
  `credentialVariables` was checked by hand in Node on a sample package.
- PostgreSQL: not run again. No store query changed after the earlier PostgreSQL run; the new
  rollback test runs on SQLite only.

On `025-register-config-web-us1` at `e64510cf`:

- `uv run invoke format`: no change. `uv run invoke lint`: exit 0. `uv run ruff check .`: pass.
- `uv run invoke docs.generate`: no change, so the CLI reference on that branch is current.
  `infrahub-sync configs --help`: pass.
- `uv run invoke tests.tests-unit`: 9029 passed, 4 skipped, 383 deselected, 2 xfailed.
