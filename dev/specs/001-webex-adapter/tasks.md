# Tasks: Webex source adapter

## Phase 1: HTTP and configuration foundations

- [X] T001 Validate connection/resource settings and environment references in infrahub_sync/adapters/webex_client.py (FR-001, FR-009–FR-011).
- [X] T002 Implement and test bounded GET transport, pagination, query serialization, failures and safe error output in infrahub_sync/adapters/webex_client.py and tests/adapters/test_webex_client.py (FR-005–FR-010d).
- [X] T003 Implement and test Service App refresh, expiry, 401 recovery and OAuth-only POST in the same client/test files (FR-004, FR-012).
- [X] T004 Keep credential-bearing frames out of displayed failure tracebacks using client-local helpers and tests/adapters/test_webex_client.py; leave the CLI untouched (FR-005).

## Phase 2: P1 meeting rooms

- [X] T005 [US1] Add invented location/workspace/schema fixtures in tests/fixtures/webex/ (FR-013).
- [X] T006 [US1] Implement WebexAdapter/WebexModel and reference-only resource flags in infrahub_sync/adapters/webex.py using existing shared mapping behavior (FR-001, FR-003, FR-012).
- [X] T007 [US1] Keep only examples/webex_workspaces_to_infrahub/config.yml with the meetingRoom filter, name transform, site references and destination preservation (FR-013).
- [X] T008 [US1] Test generated models, filtering, correct parent node IDs, zero site writes, retained old names and convergence in tests/adapters/test_webex_examples.py using populated destination site IDs (SC-003, SC-006).

## Phase 3: P2–P4 generic operation

- [X] T009 [US2] Implement/test alternate keys, singleton/list responses, parent path/query expansion, details and shared transforms in infrahub_sync/adapters/webex.py and tests/adapters/test_webex.py (FR-002–FR-003, FR-010).
- [X] T010 [US3] Test pagination counts, token renewal and retries in tests/adapters/test_webex_client.py and tests/adapters/test_webex.py (SC-002–SC-005).
- [X] T011 [US4] Test configurable organization/query defaults and overrides in tests/adapters/test_webex.py (FR-011).

## Phase 4: Documentation and scope validation

- [X] T012 Update docs/docs/adapters/webex.mdx, the single docs/sidebars.ts entry and changelog/+webex-adapter.added.md for one example and no core changes (FR-014).
- [X] T013 Update dev/specs/001-webex-adapter/ artifacts to document empty destination site identifiers and other follow-ups, with no claim that core loading is fixed.
- [X] T014 Run tests, Ruff, yamllint, ty, new-file Pylint and docs build; record current results in dev/specs/001-webex-adapter/validation.md.
- [X] T015 Verify restored existing source/CLI docs are byte-identical to 53290877, check the allowed file set and privacy, and squash onto 53290877 as one local commit without a push or PR.

## Dependencies and execution

T001–T004 precede adapter loading; T005–T008 prove the meeting-room example; T009–T011 cover configuration flexibility. Documentation follows verified behavior. T014–T015 are the final gates. Empty destination site IDs, site enrichment, cross-side parent preflight and rename reconciliation remain follow-ups outside this task list.
