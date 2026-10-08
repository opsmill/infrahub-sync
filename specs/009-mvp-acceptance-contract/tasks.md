# Tasks: MVP Acceptance Contract

**Input**: Design documents from `specs/009-mvp-acceptance-contract/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, and
`quickstart.md`

**Tests**: Required by the feature's independent tests, the constitution, and the verification
critique. Write each story's tests first and confirm the intended failure before implementation.

**Organization**: Tasks are grouped by user story so scope decisions, release decisions, and
evidence traceability can be validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files and has no incomplete dependency.
- **[Story]**: Maps the task to a user story in `spec.md`.
- Every task names its concrete file path.

## Phase 1: Setup

**Purpose**: Establish the change record and shared fixture layout without changing behavior.

- [X] T001 [P] Add the user-visible acceptance-contract news fragment in `changelog/+mvp-acceptance-contract.added.md`
- [X] T002 [P] Document the valid/invalid fixture corpus conventions in `tests/release/fixtures/acceptance/README.md`

---

## Phase 2: Foundational Contract Infrastructure

**Purpose**: Create the shared contract, error, and fixture scaffolding that all user stories use.

**⚠️ CRITICAL**: Complete this phase before implementing any user story.

- [X] T003 Create the version declaration, exact criterion-table header, scope headings, and knowledge-index link in `docs/docs/develop/knowledge/mvp-acceptance-contract.md` and `docs/docs/develop/knowledge/index.md`
- [X] T004 Define acceptance paths, safe public error types, and validation-result carriers in `tasks/acceptance.py`
- [X] T005 [P] Create deterministic contract, candidate, evaluator, criterion, and evidence fixture builders in `tests/release/acceptance_fixtures.py`

**Checkpoint**: The normative document has one parseable skeleton, tests can build deterministic
inputs, and later failures have a safe typed boundary.

---

## Phase 3: User Story 1 — Determine Whether the MVP Is Complete (Priority: P1) 🎯

**Goal**: A release owner can validate one finite contract and obtain a deterministic pass/fail
decision for one candidate.

**Independent Test**: Give the same contract and manifest to two reviewers. Both receive the same
criterion accounting, selected-contract consistency result, and named refusals for missing or
contradictory input. Neither result claims contract approval or release eligibility.

### Tests for User Story 1

- [X] T006 [P] [US1] Add failing tests for version parsing, exact criterion-table parsing, content digesting, duplicate identifiers, malformed rows, unknown owners/classes, and empty cells in `tests/release/test_acceptance_contract.py`
- [X] T007 [P] [US1] Add failing tests for strict manifest structure, candidate and contract identities, exact criterion accounting, no waiver state, decision consistency, and generated-schema stability in `tests/release/test_acceptance_manifest.py`
- [X] T008 [P] [US1] Add failing tests for `release.validate-acceptance` selected-contract consistency, absence of approval or eligibility claims, non-zero refusal, bounded output, and next-action messages in `tests/release/test_acceptance_command.py`

### Implementation for User Story 1

- [X] T009 [US1] Populate stable 009–014 criterion IDs, owner specs, classes, requirements, validation methods, and evidence types in `docs/docs/develop/knowledge/mvp-acceptance-contract.md`
- [X] T010 [US1] Implement fail-closed contract-version and criterion-table parsing plus byte-exact SHA-256 identity in `tasks/acceptance.py`
- [X] T011 [US1] Implement strict Pydantic contract, candidate, evaluator, criterion-result, locator, evidence, and qualification-manifest models in `tasks/acceptance.py`
- [X] T012 [US1] Implement exact criterion accounting, pass/fail derivation, selected-contract identity matching, timestamp ordering, and safe typed refusals in `tasks/acceptance.py`
- [X] T013 [US1] Generate and commit the runtime-model schema to `docs/static/schemas/infrahub-sync-mvp-qualification-manifest-v1.schema.json`
- [X] T014 [US1] Add the read-only `release.validate-acceptance --manifest` Invoke wrapper and bounded result rendering in `tasks/release.py`
- [X] T015 [US1] Run the User Story 1 tests in `tests/release/test_acceptance_contract.py`, `tests/release/test_acceptance_manifest.py`, and `tests/release/test_acceptance_command.py`

**Checkpoint**: A complete selected-contract manifest produces one deterministic consistency result;
malformed, mismatched, incomplete, or contradictory input fails closed without exposing untrusted
values or claiming approval or release eligibility.

---

## Phase 4: User Story 2 — Keep Post-MVP Work Out of the Release Gate (Priority: P1)

**Goal**: A product owner can classify every known gap as a required MVP criterion or an explicit
larger-v3 deferral without accidentally weakening safety.

**Independent Test**: Parse the normative contract and verify each approved gap category maps to
exactly one owner specification or one deferral, while no deferral appears in the required criterion
set.

### Tests for User Story 2

- [X] T016 [US2] Add failing scope tests for complete 009–014 ownership, the explicit larger-v3 deferral set, no criterion/deferral overlap, and superseded standalone-write requirements in `tests/release/test_acceptance_contract.py`

### Implementation for User Story 2

- [X] T017 [US2] Complete the MVP scope mapping, service-first boundary, downstream contract-increment rule, and explicit post-MVP deferrals in `docs/docs/develop/knowledge/mvp-acceptance-contract.md`
- [X] T018 [US2] Add product-owner guidance for classifying new requirements and changing the contract version in `docs/docs/develop/knowledge/mvp-acceptance-contract.md`
- [X] T019 [US2] Run the User Story 2 scope tests in `tests/release/test_acceptance_contract.py`

**Checkpoint**: Required and deferred work are exhaustive, disjoint, readable without Jira, and
testable from the normative document alone.

---

## Phase 5: User Story 3 — Trace Requirements to Evidence (Priority: P2)

**Goal**: An engineer or reviewer can follow any criterion to immutable candidate-bound evidence and
an independent evaluator's approval without exposing evidence content or secrets.

**Independent Test**: Select any criterion in a passing manifest and resolve every referenced
evidence ID to one normalized catalog entry with matching contract, source, and candidate identities;
then prove unsafe locators and non-independent approvals are refused.

### Tests for User Story 3

- [X] T020 [P] [US3] Add failing tests for normalized evidence references, missing/dangling/duplicate IDs, declared evidence types, incompatible cross-criterion reuse, restricted locator grammar, identity mismatches, timestamp ordering, and evaluator approval in `tests/release/test_acceptance_manifest.py`
- [X] T021 [P] [US3] Add failing selected-versus-retained contract mode, no-approval-or-eligibility claim, metadata-only disclosure, safe-output, and credential-canary tests in `tests/release/test_acceptance_command.py`
- [X] T022 [P] [US3] Add one stable passing manifest and one focused file per refusal family under `tests/release/fixtures/acceptance/valid/` and `tests/release/fixtures/acceptance/invalid/`

### Implementation for User Story 3

- [X] T023 [US3] Implement normalized evidence-catalog resolution, no-dangling-entry checks, evidence-type acceptance for every criterion reference, locator validation, identity binding, and evaluator invariants in `tasks/acceptance.py`
- [X] T024 [US3] Add `--contract` retained-contract validation, explicit selection-mode labeling, no approval or eligibility claim, metadata-only disclosure, and spec-014 boundary notices in `tasks/release.py`
- [X] T025 [US3] Document selected- and retained-contract consistency, evidence-type reuse, safe evidence locators, and the spec-014 approval, eligibility, and byte-verification boundaries in `docs/docs/develop/knowledge/mvp-acceptance-contract.md`
- [X] T026 [US3] Run the User Story 3 manifest and command tests in `tests/release/test_acceptance_manifest.py` and `tests/release/test_acceptance_command.py`

**Checkpoint**: Every criterion is traceable through compatible, safe immutable metadata, and
neither selected- nor retained-contract validation overclaims artifact availability, contract
approval, evaluator authority, or release eligibility.

---

## Phase 6: Polish and Cross-Cutting Verification

**Purpose**: Close schema, compatibility, documentation, security, and repository quality gates.

- [ ] T027 [P] Assert the committed JSON Schema is byte-equivalent to Pydantic generation and run the shared valid/invalid corpus through runtime validation in `tests/release/test_acceptance_manifest.py`
- [ ] T028 [P] Add backward-compatibility coverage proving `.release/qualification.json` generation and parsing remain unchanged in `tests/release/test_qualification.py`
- [ ] T029 [P] Add published-page and downloadable-schema discoverability coverage in `tests/test_developer_pages_sidebar.py`
- [ ] T030 Execute every passing and fail-closed scenario in `specs/009-mvp-acceptance-contract/quickstart.md` and correct that file if observed output differs
- [ ] T031 Run `uv run invoke format` and apply only formatter changes attributable to the 009 implementation
- [ ] T032 Run `uv run invoke lint` and resolve all rumdl, Ruff, Pylint, yamllint, and ty findings attributable to the 009 implementation
- [ ] T033 Run the offline unit tier with `uv run invoke tests.tests-unit` and record any unrelated pre-existing failure separately
- [ ] T034 Run `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help`, and `uv run infrahub-sync runs --help` as CLI sanity checks
- [ ] T035 Run `uv run invoke docs.generate` and `uv run invoke docs.docusaurus` for the published contract and schema

---

## Dependencies and Execution Order

### Phase dependencies

- **Phase 1 — Setup**: No dependencies; T001 and T002 can run in parallel.
- **Phase 2 — Foundation**: Depends on Phase 1 and blocks story implementation.
- **Phase 3 — US1**: Depends on Phase 2 and supplies the parser, models, validator, and command.
- **Phase 4 — US2**: Depends on the Phase 2 contract skeleton. Its tests may be authored alongside
  US1, but edits to `mvp-acceptance-contract.md` must be serialized with T009.
- **Phase 5 — US3**: Depends on US1's models and validation entry point.
- **Phase 6 — Polish**: Depends on all selected user stories.

### User story dependencies

```text
Foundation
├── US1 Determine MVP completion ──┐
└── US2 Bound post-MVP scope ──────┼──▶ US3 Trace criteria to evidence
                                   └──▶ Polish and full verification
```

- **US1 (P1)**: Independently delivers deterministic selected-contract consistency results.
- **US2 (P1)**: Independently delivers the authoritative required/deferred scope boundary.
- **US3 (P2)**: Uses US1's manifest boundary and the criteria finalized by US1/US2 to deliver
  end-to-end evidence traceability.

### Within each user story

- Write the story's tests and confirm the intended failure before implementation.
- Establish the normative contract shape before parsing or validation code.
- Implement Pydantic structure before cross-entity semantic validation.
- Implement pure validation before the Invoke wrapper and rendering.
- Complete the story checkpoint before advancing.

## Parallel Opportunities

- T001 and T002 can run together.
- T005 can run while T003 and T004 establish production scaffolding.
- T006, T007, and T008 can run together because they target separate test files.
- US2 scope tests can be authored while US1 code is underway, but the shared contract-page edits must
  be serialized.
- T020, T021, and T022 can run together after US1.
- T027, T028, and T029 can run together after all stories.

## Parallel Example: User Story 1

```text
Task T006: Contract grammar and digest tests in tests/release/test_acceptance_contract.py
Task T007: Manifest and schema tests in tests/release/test_acceptance_manifest.py
Task T008: Invoke boundary and safe-output tests in tests/release/test_acceptance_command.py
```

## Parallel Example: User Story 3

```text
Task T020: Evidence and evaluator semantics in tests/release/test_acceptance_manifest.py
Task T021: Historical mode and canary output in tests/release/test_acceptance_command.py
Task T022: Shared valid/invalid JSON corpus under tests/release/fixtures/acceptance/
```

## Implementation Strategy

### Smallest useful delivery

Both P1 stories define the useful acceptance boundary:

1. Complete Setup and Foundation.
2. Complete US1 to make release decisions deterministic.
3. Complete US2 to ensure the decision covers MVP—and only MVP.
4. Stop and validate both checkpoints before adding evidence ergonomics.

### Incremental delivery

1. **Foundation**: One parseable contract skeleton and safe error boundary.
2. **US1**: Selected-contract consistency and strict manifest structure.
3. **US2**: Exhaustive required/deferred scope mapping.
4. **US3**: Normalized evidence, safe locators, evaluator approval, and historical review.
5. **Polish**: Schema equivalence, compatibility, docs, and full repository gates.

## Notes

- `[P]` means the task touches a distinct file or fixture subtree and has no incomplete dependency.
- `[US1]`, `[US2]`, and `[US3]` map directly to the three user stories in `spec.md`.
- Do not modify the existing producer qualification-record schema; the final acceptance manifest
  references it by evidence ID.
- Do not fetch evidence or add release-promotion wiring; spec 014 owns those behaviors.
- Commit only after a logical group passes its focused tests and repository checks.
