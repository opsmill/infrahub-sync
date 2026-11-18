# Tasks: Webex source adapter

## Phase 1: HTTP and configuration foundations

- [X] T001 Validate connection/resource settings and environment references in infrahub_sync/adapters/webex_client.py (FR-001, FR-009–FR-011).
- [X] T002 Implement and test bounded GET transport, pagination, query serialization, failures and safe error output in infrahub_sync/adapters/webex_client.py and tests/adapters/test_webex_client.py (FR-005–FR-010d).
- [X] T003 Implement and test Service App refresh, expiry, 401 recovery and OAuth-only POST in the same client/test files (FR-004, FR-012).
- [X] T004 Keep credential-bearing frames out of displayed failure tracebacks using client-local helpers and tests/adapters/test_webex_client.py; leave the CLI untouched (FR-005).

## Phase 2: P1 inventory project

- [X] T005 [US1] Add invented location/workspace/device/schema fixtures in tests/fixtures/webex/ (FR-013).
- [X] T006 [US1] Implement WebexAdapter/WebexModel and reference-only resource flags in infrahub_sync/adapters/webex.py using existing shared mapping behavior (FR-001, FR-003, FR-012).
- [X] T007 [US1] Ship examples/webex_workspaces_to_infrahub/config.yml and schema.yml for WebexLocation, WebexWorkspace and WebexDevice with stable Webex IDs and optional peers (FR-013).
- [X] T008 [US1] Test generated models, 25 device creates, mapped attributes, peer write order, convergence after reload, one-device delete diffs and organization isolation in tests/adapters/test_webex_examples.py (SC-003, SC-006).

## Phase 3: P2–P4 generic operation

- [X] T009 [US2] Implement/test alternate keys, singleton/list responses, parent path/query expansion, details and shared transforms in infrahub_sync/adapters/webex.py and tests/adapters/test_webex.py (FR-002–FR-003, FR-010).
- [X] T010 [US3] Test pagination counts, token renewal and retries in tests/adapters/test_webex_client.py and tests/adapters/test_webex.py (SC-002–SC-005).
- [X] T011 [US4] Test configurable organization/query defaults and overrides in tests/adapters/test_webex.py (FR-011).

## Phase 4: Documentation and scope validation

- [X] T012 Update docs/docs/adapters/webex.mdx, the single docs/sidebars.ts entry and changelog/+webex-adapter.added.md for one example and no core changes (FR-014).
- [X] T013 Update dev/specs/001-webex-adapter/ artifacts to describe the complete inventory example and the existing persisted-deletion limitation.
- [X] T014 Run tests, Ruff, yamllint, ty, new-file Pylint and docs build.
- [X] T015 Verify restored existing source/CLI docs are byte-identical to 53290877, check the allowed file set and privacy, without changing core code or publishing.

## Dependencies and execution

T001–T004 precede adapter loading; T005–T008 prove the three-kind inventory example; T009–T011 cover configuration flexibility. Documentation follows verified behavior. T014–T015 are the final gates. Persisted Infrahub deletion, cross-side peer preflight and durable OAuth rotation remain follow-ups outside this task list.
