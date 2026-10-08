# OpsMill Implementation Report: MVP Acceptance Contract

**Status:** DONE  
**Spec directory:** `/Users/bkohler/automation/opsmill/infrahub-sync/specs/009-mvp-acceptance-contract`  
**Base commit:** `09ff444c94aa57461dca618bf003932b1ea6873f`  
**Head before this report:** `77a4ee828523067b0f018ab4d7f5766d7bf734b1`  
**Wall-clock time:** approximately 1 hour 15 minutes, including one daemon restart.

## Chunk ledger

| Chunk | Tasks | Outcomes | Commits | Decisions or surprises |
|---|---:|---|---|---|
| Phase 1 — Setup | 2 | 2 ✅ / 0 ⚠️ / 0 ❌ | `5f1399c9` | The previously untracked task file entered history with this checkpoint. |
| Phase 2 — Foundation | 3 | 3 ✅ / 0 ⚠️ / 0 ❌ | `57b2bc83` | The normative heading remains parser-compatible with rumdl. |
| Phase 3 — User Story 1 | 10 | 10 ✅ / 0 ⚠️ / 0 ❌ | `bf68ed65` | Validation reports consistency only; spec 014 owns approval and eligibility. |
| Phase 4 — User Story 2 | 4 | 4 ✅ / 0 ⚠️ / 0 ❌ | `39622fc0` | Ten larger-v3 deferral categories are explicit and disjoint from MVP criteria. |
| Phase 5 — User Story 3 | 7 | 7 ✅ / 0 ⚠️ / 0 ❌ | `1f670893` | Evidence reuse is allowed only when every criterion accepts the declared type. |
| Phase 6 — Polish | 9 | 9 ✅ / 0 ⚠️ / 0 ❌ | `a025cafb` | 009-attributable gates pass; aggregate rumdl still sees 17 findings in unrelated untracked Spec Kit setup files. |
| Review fixes | 10 findings | 10 ✅ / 0 ⚠️ / 0 ❌ | `77a4ee82` | Hardened rendered strings, schema, parser grammar, record typing, errors, immutability, and UTC invariants. |

## Tasks not completed

None. T001–T035 are checked.

## Local-pass evidence

Common exact command **C1**:

`rtk proxy uv run pytest -vv tests/release/test_acceptance_contract.py tests/release/test_acceptance_manifest.py tests/release/test_acceptance_command.py tests/release/test_qualification.py::test_acceptance_manifest_support_does_not_change_legacy_qualification_record tests/test_developer_pages_sidebar.py::test_mvp_acceptance_contract_and_schema_are_discoverable`

Common environment **E1**: macOS, Python 3.11.8, pytest 9.0.2, repository virtualenv. The run completed with **96 passed**.

| Test id | Type | Run command | Passed at (ISO 8601) | Environment context | Verbatim pass line |
|---|---|---|---|---|---|
| `tests/release/test_acceptance_command.py::test_command_accepts_and_labels_retained_contract_selection` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_accepts_and_labels_retained_contract_selection PASSED` |
| `tests/release/test_acceptance_command.py::test_command_discloses_metadata_only` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_discloses_metadata_only PASSED` |
| `tests/release/test_acceptance_command.py::test_command_labels_checked_out_contract_selection` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_labels_checked_out_contract_selection PASSED` |
| `tests/release/test_acceptance_command.py::test_command_refusal_does_not_echo_credential_canary` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_refusal_does_not_echo_credential_canary PASSED` |
| `tests/release/test_acceptance_command.py::test_command_refuses_invalid_input_with_safe_next_action` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_refuses_invalid_input_with_safe_next_action PASSED` |
| `tests/release/test_acceptance_command.py::test_command_refuses_rendered_control_injection_without_echoing_canary[candidate-version]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_refuses_rendered_control_injection_without_echoing_canary[candidate-version] PASSED` |
| `tests/release/test_acceptance_command.py::test_command_refuses_rendered_control_injection_without_echoing_canary[evaluator-identity]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_refuses_rendered_control_injection_without_echoing_canary[evaluator-identity] PASSED` |
| `tests/release/test_acceptance_command.py::test_command_reports_consistency_without_approval_or_eligibility_claims` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_reports_consistency_without_approval_or_eligibility_claims PASSED` |
| `tests/release/test_acceptance_command.py::test_command_states_spec_014_boundaries_without_approval_or_eligibility_claims` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_command_states_spec_014_boundaries_without_approval_or_eligibility_claims PASSED` |
| `tests/release/test_acceptance_command.py::test_contract_invalid_utf8_is_reported_distinctly` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_contract_invalid_utf8_is_reported_distinctly PASSED` |
| `tests/release/test_acceptance_command.py::test_contract_path_failures_are_distinct[directory-is a directory]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_contract_path_failures_are_distinct[directory-is a directory] PASSED` |
| `tests/release/test_acceptance_command.py::test_contract_path_failures_are_distinct[missing-does not exist]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_contract_path_failures_are_distinct[missing-does not exist] PASSED` |
| `tests/release/test_acceptance_command.py::test_invoke_renders_each_refusal_once` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_invoke_renders_each_refusal_once PASSED` |
| `tests/release/test_acceptance_command.py::test_manifest_io_failures_are_safe_and_actionable[generic-io]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_manifest_io_failures_are_safe_and_actionable[generic-io] PASSED` |
| `tests/release/test_acceptance_command.py::test_manifest_io_failures_are_safe_and_actionable[permission]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_manifest_io_failures_are_safe_and_actionable[permission] PASSED` |
| `tests/release/test_acceptance_command.py::test_manifest_read_failures_are_distinct[invalid-utf8]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_manifest_read_failures_are_distinct[invalid-utf8] PASSED` |
| `tests/release/test_acceptance_command.py::test_manifest_read_failures_are_distinct[malformed-json]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_command.py::test_manifest_read_failures_are_distinct[malformed-json] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_duplicate_identifiers` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_duplicate_identifiers PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[class]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[class] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[id]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[id] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[owner_spec]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[owner_spec] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[requirement]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[requirement] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[validation]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_empty_cells[validation] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_html_in_criterion_cells` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_html_in_criterion_cells PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_invalid_version_header_or_row[changed-header]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_invalid_version_header_or_row[changed-header] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_invalid_version_header_or_row[malformed-row]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_invalid_version_header_or_row[malformed-row] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_invalid_version_header_or_row[non-positive-version]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_invalid_version_header_or_row[non-positive-version] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_unknown_owner_or_class[class-optional]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_unknown_owner_or_class[class-optional] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_refuses_unknown_owner_or_class[owner_spec-015]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_refuses_unknown_owner_or_class[owner_spec-015] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_requires_one_version_declaration_immediately_below_title[intervening-prose]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_requires_one_version_declaration_immediately_below_title[intervening-prose] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_requires_one_version_declaration_immediately_below_title[second-version-like-declaration]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_requires_one_version_declaration_immediately_below_title[second-version-like-declaration] PASSED` |
| `tests/release/test_acceptance_contract.py::test_contract_version_criteria_and_exact_bytes_are_parsed` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_contract_version_criteria_and_exact_bytes_are_parsed PASSED` |
| `tests/release/test_acceptance_contract.py::test_normative_contract_lists_the_complete_larger_v3_deferral_set` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_normative_contract_lists_the_complete_larger_v3_deferral_set PASSED` |
| `tests/release/test_acceptance_contract.py::test_normative_contract_maps_every_mvp_spec_exactly_once` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_normative_contract_maps_every_mvp_spec_exactly_once PASSED` |
| `tests/release/test_acceptance_contract.py::test_normative_criterion_catalog_is_pinned` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_normative_criterion_catalog_is_pinned PASSED` |
| `tests/release/test_acceptance_contract.py::test_required_criteria_and_deferrals_are_disjoint` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_required_criteria_and_deferrals_are_disjoint PASSED` |
| `tests/release/test_acceptance_contract.py::test_standalone_write_requirements_are_explicitly_superseded` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_contract.py::test_standalone_write_requirements_are_explicitly_superseded PASSED` |
| `tests/release/test_acceptance_manifest.py::test_approval_cannot_precede_evidence` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_approval_cannot_precede_evidence PASSED` |
| `tests/release/test_acceptance_manifest.py::test_committed_schema_is_byte_equivalent_to_runtime_generation` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_committed_schema_is_byte_equivalent_to_runtime_generation PASSED` |
| `tests/release/test_acceptance_manifest.py::test_decision_cannot_precede_evaluator_approval` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_decision_cannot_precede_evaluator_approval PASSED` |
| `tests/release/test_acceptance_manifest.py::test_fail_decision_can_be_caused_only_by_evaluator_rejection` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_fail_decision_can_be_caused_only_by_evaluator_rejection PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[dangling-evidence-id]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[dangling-evidence-id] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[duplicate-evidence-id]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[duplicate-evidence-id] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[evaluator-approval]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[evaluator-approval] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[evidence-identity-mismatch]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[evidence-identity-mismatch] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[incompatible-evidence-type]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[incompatible-evidence-type] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[missing-evidence-id]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[missing-evidence-id] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[qualification-record-type]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[qualification-record-type] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[timestamp-order]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[timestamp-order] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[unsafe-locator]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_invalid_manifest_corpus_fails_runtime_validation[unsafe-locator] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_decision_must_match_criterion_results` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_decision_must_match_criterion_results PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_has_no_waiver_result` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_has_no_waiver_result PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_models_are_strict` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_models_are_strict PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_evidence_identity_mismatches[candidate-digest]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_evidence_identity_mismatches[candidate-digest] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_evidence_identity_mismatches[contract-digest]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_evidence_identity_mismatches[contract-digest] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_evidence_identity_mismatches[source-revision]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_evidence_identity_mismatches[source-revision] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_incompatible_cross_criterion_reuse` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_incompatible_cross_criterion_reuse PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[contract-digest]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[contract-digest] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[contract-version]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[contract-version] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[image-digest]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[image-digest] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[source-revision]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_invalid_or_mismatched_identities[source-revision] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[dangling-entry]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[dangling-entry] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[duplicate-reference]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[duplicate-reference] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[missing-criterion-reference]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[missing-criterion-reference] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[missing-qualification-record]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids[missing-qualification-record] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[absolute]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[absolute] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[backslash]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[backslash] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[colon]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[colon] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[empty-segment]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[empty-segment] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[fragment]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[fragment] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[query]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[query] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[scheme]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[scheme] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[traversal]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[traversal] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[userinfo]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_refuses_unsafe_evidence_locators[userinfo] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_declared_evidence_type_for_every_reference` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_declared_evidence_type_for_every_reference PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_exact_criterion_accounting[duplicate]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_exact_criterion_accounting[duplicate] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_exact_criterion_accounting[missing]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_exact_criterion_accounting[missing] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_exact_criterion_accounting[unknown]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_exact_criterion_accounting[unknown] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_zero_offset_utc_timestamps[approval]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_zero_offset_utc_timestamps[approval] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_zero_offset_utc_timestamps[decision]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_zero_offset_utc_timestamps[decision] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_requires_zero_offset_utc_timestamps[evidence]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_requires_zero_offset_utc_timestamps[evidence] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_manifest_resolves_normalized_evidence_catalog` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_manifest_resolves_normalized_evidence_catalog PASSED` |
| `tests/release/test_acceptance_manifest.py::test_passing_manifest_requires_evaluator_approval_invariants[approved]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_passing_manifest_requires_evaluator_approval_invariants[approved] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_passing_manifest_requires_evaluator_approval_invariants[executed_qualified_journey]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_passing_manifest_requires_evaluator_approval_invariants[executed_qualified_journey] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_passing_manifest_requires_evaluator_approval_invariants[independent_from_implementation]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_passing_manifest_requires_evaluator_approval_invariants[independent_from_implementation] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_published_json_schema_encodes_runtime_invariants[failure-without-note]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_published_json_schema_encodes_runtime_invariants[failure-without-note] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_published_json_schema_encodes_runtime_invariants[passing-without-evidence]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_published_json_schema_encodes_runtime_invariants[passing-without-evidence] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_published_json_schema_encodes_runtime_invariants[unsafe-evidence-key]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_published_json_schema_encodes_runtime_invariants[unsafe-evidence-key] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_qualification_record_requires_qualification_record_evidence_fixture` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_qualification_record_requires_qualification_record_evidence_fixture PASSED` |
| `tests/release/test_acceptance_manifest.py::test_schema_expressible_invalid_corpus_fails_published_json_schema[duplicate-evidence-id]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_schema_expressible_invalid_corpus_fails_published_json_schema[duplicate-evidence-id] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_schema_expressible_invalid_corpus_fails_published_json_schema[unsafe-locator]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_schema_expressible_invalid_corpus_fails_published_json_schema[unsafe-locator] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_valid_manifest_corpus_passes_published_json_schema[passing]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_valid_manifest_corpus_passes_published_json_schema[passing] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_valid_manifest_corpus_passes_runtime_validation[passing]` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_valid_manifest_corpus_passes_runtime_validation[passing] PASSED` |
| `tests/release/test_acceptance_manifest.py::test_validated_manifest_is_deeply_immutable` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_acceptance_manifest.py::test_validated_manifest_is_deeply_immutable PASSED` |
| `tests/release/test_qualification.py::test_acceptance_manifest_support_does_not_change_legacy_qualification_record` | unit | C1 | 2026-10-08T20:50:18Z | E1 | `tests/release/test_qualification.py::test_acceptance_manifest_support_does_not_change_legacy_qualification_record PASSED` |
| `tests/test_developer_pages_sidebar.py::test_mvp_acceptance_contract_and_schema_are_discoverable` | integration | C1 | 2026-10-08T20:50:18Z | E1 | `tests/test_developer_pages_sidebar.py::test_mvp_acceptance_contract_and_schema_are_discoverable PASSED` |

Additional gate evidence:

| Gate | Exact command | Result |
|---|---|---|
| Full release tests | `uv run pytest tests/release -q` | 365 passed |
| Offline unit tier | `uv run invoke tests.tests-unit` | 6549 passed, 4 skipped, 207 deselected |
| Format | `uv run invoke format` | Passed |
| Focused Ruff/Pylint/ty/rumdl | Repository Invoke tasks scoped to touched files | Passed; Pylint 10.00/10 |
| CLI sanity | `uv run infrahub-sync --help`, `configs --help`, `runs --help` | Passed |
| Documentation | `uv run invoke docs.generate`; `uv run invoke docs.docusaurus` | Passed |

## Review findings

| Severity | File | Summary | Disposition |
|---|---|---|---|
| High | `tasks/acceptance.py`, `tasks/release.py` | Rendered untrusted metadata allowed terminal-control injection. | Fixed in `77a4ee82`; canary tests added. |
| High | Generated JSON Schema | Published schema was weaker than runtime validation. | Fixed in `77a4ee82`; schema corpus tests added. |
| High | `tasks/acceptance.py` | Contract grammar accepted ambiguous version placement and HTML cells. | Fixed in `77a4ee82`. |
| High | `tasks/acceptance.py` | `qualification_record` did not require the producer-record evidence type. | Fixed in `77a4ee82`. |
| High | `tasks/acceptance.py`, `tasks/release.py` | Read failures were over-general and Invoke printed refusals twice. | Fixed in `77a4ee82`. |
| High | `tasks/acceptance.py` | Validated models were shallowly mutable and timestamps allowed non-UTC offsets. | Fixed in `77a4ee82`. |
| Medium | Tests | Normative catalog and evaluator-only failure branch were not pinned. | Fixed in `77a4ee82`. |
| Low | `tasks/acceptance.py` | Unused path/result scaffolding and duplicate parsing could be simplified. | Deferred; behavior is correct and the change is non-blocking. |

## Autonomous decisions

- Accepted the dirty preflight because every outstanding path belonged to the earlier, user-requested Spec Kit preparation.
- Re-dispatched User Story 3 after a daemon restart only after confirming it had produced no edits or commit.
- Treated the 17 aggregate rumdl findings in untouched, untracked Spec Kit setup artifacts as outside feature 009; all feature-attributable checks pass.
- Kept MVP-014 linkage semantic through a typed `qualification-record` reference; release-promotion provenance remains owned by spec 014.
- Added no runtime dependency; `jsonschema` is used only by the development test profile.

## Suggested next steps

1. Review and commit the remaining prepared Spec Kit artifacts separately.
2. Open a pull request for feature 009 when the preparation commits are organized.
3. Consider the two deferred low-severity simplifications in a follow-up.
