---

description: "Task list for the lineage of the data an import writes"
---

# Tasks: Lineage of the data an import writes

**Input**: Design documents from `specs/014-import-lineage/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Included. Every functional requirement in the spec names its test, and constitution
principle V requires tests with the change. Write each story's tests first and check that they fail
before the implementation tasks of that story.

**Organization**: Tasks are grouped by user story so that each story can be built and tested on
its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1 to US4)
- `SDK/` means `.venv/lib/python3.11/site-packages/infrahub_sdk/` (infrahub-sdk 1.23.2)
- "Import account" is the account behind the token the worker uses (spec, Key Entities).

## Phase 1: Setup (checks that settle the plan's open items)

**Purpose**: Answer the open items in [plan.md](./plan.md) before code depends on them. Each task
records its result in [research.md](./research.md) and updates "Items left open" in plan.md.

- [ ] T001 [P] Find a read that returns `relationship_metadata` (`updated_by`, `updated_at`) for cardinality-many relationship edges within the requests the destination read already makes: check whether `RelationshipManager.fetch()` in `SDK/node/relationship.py` accepts or forwards `include_metadata`, and whether `client.all(..., include_metadata=True)` with the relationship in `include` returns it (`infrahub_sync/adapters/infrahub.py:93-100`, `:815-817`); record the read in research.md R-2. This outcome is binding: if no such read exists, stop work on User Story 3 and bring an amendment of FR-007 to the product owner; do not show "not recorded" for data Infrahub holds
- [ ] T002 [P] Read what apply loads from the destination before it writes (`infrahub_sync/adapters/infrahub.py` `PeerResolver` around :552 and the write path :1290-1364, `infrahub_sync/plan/derive.py:590-682`, `infrahub_sync/execution.py` apply path) and decide how an update tells which relationship edges it adds, so the owner is set only on those (FR-002); record the decision and its evidence in research.md R-6
- [ ] T003 [P] Check whether the checkpoint artifact (`infrahub_sync/product_store/bundle.py:64`, `infrahub_sync/service/checkpoints.py:90`, `:132`) copies the whole plan directory or a fixed list of files; record which, and the file to change if it is a list, in research.md R-4
- [ ] T004 Run the NetBox benchmark runner (`tasks/bench.py`, guide `docs/docs/development/guides/netbox-benchmark-runner.md`) on the pinned Infrahub 1.10.6 stack and record the destination read time for about 10,000 nodes on the default branch and on another branch, without metadata, as the baseline in research.md R-2
- [ ] T005 Find the import account's smallest permission set on the pinned Infrahub 1.10.6, for the default branch and for another branch: start from an account with no permission, run plan and sync with it, and add one permission at a time; include the `InfrahubInfo` query (`SDK/client.py:526-529`), the `AccountProfile` query (`SDK/client.py:540-542`) and the read of each allowed named kind; record the result as a table in research.md R-7

**Checkpoint**: open items answered except the cost with metadata, which T056 measures.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Typed settings and the run-start step that every story uses.

- [ ] T006 Add typed models `NamedLineageNode` (`kind` in `CoreAccount`, `CoreAccountGroup`, `CoreRepository`, `CoreReadOnlyRepository`; non-empty `name`; no other key) and `InfrahubLineageSettings` (`source`, `owner`, `protect: bool = False`), plus one parser that returns typed findings, in `infrahub_sync/configuration/capabilities.py`
- [ ] T007 [P] Unit tests for the parser in `tests/configuration/test_infrahub_lineage_settings.py`: each allowed kind accepted; bare string, unknown kind, empty name, extra key and non-boolean `protect` refused; absent settings give no source, no owner, `protect` false
- [ ] T008 Add a `ResolvedLineage` type (import account, source node with id, owner node with id or none, `protect`) and a lineage-resolution method on the Infrahub adapter in `infrahub_sync/adapters/infrahub.py`; call it from Potenda in `infrahub_sync/potenda/__init__.py` before the two parallel loads are submitted (:532-536), in plan and in apply; remove the account-by-source-name lookup and both fallbacks (`infrahub_sync/adapters/infrahub.py:713-742`)

**Checkpoint**: settings parse to typed models; lineage is resolved once per run before any source read.

---

## Phase 3: User Story 1 - Import with an account used only by this import (Priority: P1) — MVP

**Goal**: Every value the import writes has the import account as source, no owner and no
protection flag; an HTTP 403 is reported as a missing permission; apply refuses when the import
account differs from the plan's.

**Independent Test**: Quickstart scenarios 1 and 5, with the permission set from T005.

### Tests for User Story 1

- [ ] T009 [P] [US1] Unit tests in `tests/adapters/test_infrahub_lineage.py`: with no `source` setting the resolved source is the id returned by `client.get_user()`; no `CoreAccount` lookup by the source system's name is made; `get_user()` is called once per run (FR-001, FR-005)
- [ ] T010 [P] [US1] Unit tests in `tests/adapters/test_infrahub_planned_write.py`: a create sends `source` on every attribute and edge, sends no `owner`, and sends no `is_protected` key when no owner and no `protect` are set (FR-001, FR-006)
- [ ] T011 [P] [US1] Unit tests in `tests/adapters/test_infrahub_error_classification.py` for each case in `contracts/run-errors.md`: 401, 403 with one permission, 403 with several, 403 with another message, `GraphQLError` starting "You do not have", `AuthenticationError` with no HTTP cause; the recorded cause never contains the token (FR-008, SC-006)
- [ ] T012 [P] [US1] Unit tests in `tests/plan/test_lineage_manifest.py`: the manifest carries `lineage.import_account` and `lineage.source` as in `contracts/plan-last-changes.md`; the field is absent on a plan written without it; the checksum changes when `lineage` changes; the format version stays 3 (FR-012)
- [ ] T013 [P] [US1] Unit tests in `tests/adapters/test_infrahub_lineage_apply.py`: apply with a different import account refuses before any write call and names both accounts, whether or not a source is named; apply of a saved plan with no `lineage.import_account` refuses before any write call and asks for a new plan (FR-012, SC-007)

### Implementation for User Story 1

- [ ] T014 [US1] Resolve the import account from `client.get_user()` (`SDK/client.py:540-542`) in the lineage-resolution method from T008 and use its id as the default source, in `infrahub_sync/adapters/infrahub.py`
- [ ] T015 [US1] Change the write at `infrahub_sync/adapters/infrahub.py:1333-1345` to pass the resolved source id, no owner unless named, and `is_protected=None` so the key is not sent (`SDK/schema/__init__.py:222-255`)
- [ ] T016 [US1] Add one classification function for Infrahub errors that reads the status from `AuthenticationError.__cause__.response.status_code` (`SDK/client.py:1439-1446`) and quotes the permission text, and use it in `infrahub_sync/potenda/__init__.py:220-238` and in the schema read at `infrahub_sync/configuration/capabilities.py:258-264`
- [ ] T017 [US1] Add the `lineage` manifest field with `import_account`, `source` and `protect` (data-model.md) to `PlanManifest` in `infrahub_sync/plan/models.py:377-407` and write it in `infrahub_sync/plan/writer.py`
- [ ] T018 [US1] In apply, before any write, refuse a saved plan with no `lineage.import_account` with a cause that asks for a new plan, and compare the import account with the plan's `lineage.import_account.id`, refusing with a cause that names both accounts, in the lineage-resolution method of `infrahub_sync/adapters/infrahub.py`; log the refusal with `structlog`
- [ ] T019 [US1] Opt-in integration test for quickstart scenarios 1 and 5 in `tests/integration/test_infrahub_lineage_integration.py`, using an account that holds exactly the permission set from T005: created values have that account as source, no owner, no protection; created nodes have `created_by` set to it; removing one permission gives a missing-permission cause; applying with another account's token is refused (SC-001, SC-002, SC-006, SC-007)

**Checkpoint**: User Story 1 works on its own; this is the MVP.

---

## Phase 4: User Story 2 - Make a team the owner (Priority: P1)

**Goal**: A named owner is set on values the run creates only; a bare name or an unknown kind is
refused at registration; a missing named node stops the plan before the source is read and stops
apply before any write.

**Independent Test**: Quickstart scenario 2.

### Tests for User Story 2

- [ ] T020 [P] [US2] Registration tests in `tests/configuration/test_infrahub_lineage_validation.py` for each refused input in `contracts/infrahub-destination-settings.md`, checking the finding pointer `/configuration/destination/settings/<setting>` (FR-003)
- [ ] T021 [P] [US2] Unit tests in `tests/adapters/test_infrahub_lineage.py`: a named `source` or `owner` that does not exist stops the plan before the source adapter's load is called; the cause names the setting, the kind and the name; no fallback node is used; a named `CoreAccount` is looked up, a named group needs no account read (FR-004, FR-005, SC-003)
- [ ] T022 [P] [US2] Unit tests in `tests/adapters/test_infrahub_lineage_apply.py`: a named node deleted after the plan stops apply before any write call, with the same cause as the plan (FR-011, SC-007)
- [ ] T023 [P] [US2] Unit tests in `tests/adapters/test_infrahub_planned_write.py`: a create sends the named owner on every attribute and edge; an update sends no owner on attributes; an update sends the owner only on relationship edges it adds, as decided in T002; an update that changes nothing makes no write call (FR-002, SC-004, spec edge case)
- [ ] T024 [P] [US2] Unit tests in `tests/plan/test_lineage_manifest.py`: `lineage.owner` is the named owner or `null`; the summary notice is present exactly when `lineage.owner` is not `null` (FR-009)

### Implementation for User Story 2

- [ ] T025 [US2] Register a `validator` on the `infrahub` entry of `infrahub_sync/configuration/capabilities.py:522-531` that runs the parser from T006 and returns one finding per refusal
- [ ] T026 [US2] Look up each named node with one `client.get(kind=..., name__value=...)` in the lineage-resolution method, in plan and in apply, and raise a specific `NamedLineageNodeNotFoundError` whose message names the setting, the kind and the name; log the stop with `structlog`, with the setting, kind and name as context, in `infrahub_sync/adapters/infrahub.py`
- [ ] T027 [US2] Set the owner on creates and on relationship edges an update adds (decision from T002), and never on attributes of an existing node, in the payload code of `infrahub_sync/adapters/infrahub.py:1290-1364`
- [ ] T028 [US2] Add `owner` to the manifest's `lineage` field in `infrahub_sync/plan/models.py` and `infrahub_sync/plan/writer.py`
- [ ] T029 [US2] Show the notice "The owner setting applies only to values this run creates." in the summary of `infrahub-sync runs plan` (`infrahub_sync/cli.py`) when `lineage.owner` is not `null`, and add the optional `lineage` field to `PlanResource` (`infrahub_sync/client/models.py:341`), filled in `infrahub_sync/service/flow.py:178-187`
- [ ] T030 [US2] Opt-in integration test for quickstart scenario 2 in `tests/integration/test_infrahub_lineage_integration.py`: owner on create, unchanged after updates, set on an added edge, stop on a missing group in plan and in apply, refusal of `owner: network-team`

**Checkpoint**: User Stories 1 and 2 both work on their own.

---

## Phase 5: User Story 3 - View who changed a value before overwriting it (Priority: P2)

**Goal**: The plan's detail shows `updated_by` and `updated_at`, or "not recorded" when Infrahub
holds no value, for each proposed update to an attribute or a relationship of an existing node,
without a second read. Creates have no last-change record.

**Independent Test**: Quickstart scenario 3.

**Depends on**: T001 found a read for cardinality-many edges. If it did not, this phase waits for
the product owner's decision on FR-007.

### Tests for User Story 3

- [ ] T031 [P] [US3] Unit tests in `tests/adapters/test_infrahub_metadata_read.py`: on a server version of 1.7.0 or later the existing reads, including the cardinality-many read chosen in T001, pass `include_metadata=True` and no other read is added; before 1.7.0 or with an unparsable version they do not; a value Infrahub returns with `updated_by` is never shown as "not recorded"; metadata goes into the adapter's map and not into the DiffSync models, so planning twice over a value a person changed gives the same operations (FR-007, constitution principle II)
- [ ] T032 [P] [US3] Unit tests in `tests/plan/test_last_changes.py`: `last_changes.jsonl` has one record per changed field of each update and none for a create; records are sorted and canonical; `null` values are kept; the manifest's `last_changes` digest and count match; review refuses a sidecar whose digest does not match; a plan without the sidecar is reviewed without a last-change column (FR-007, SC-005)
- [ ] T033 [P] [US3] Unit tests in `tests/service/test_plan_last_changes.py`: `PlanResource` carries `last_changes` for a new plan and omits it for an older plan

### Implementation for User Story 3

- [ ] T034 [US3] Read the server version once with `client.get_version()` (`SDK/client.py:526-529`) in the lineage-resolution method and keep a `metadata_read` flag (true from 1.7.0) in `infrahub_sync/adapters/infrahub.py`
- [ ] T035 [US3] Pass `include_metadata=metadata_read` to `client.all` at `infrahub_sync/adapters/infrahub.py:815-817`, to `client.filters` at `:771-776`, and to the cardinality-many read chosen in T001
- [ ] T036 [US3] Fill a map from (destination node id, field name) to (`updated_by` id and display label, `updated_at`) from `node.<attribute>.updated_by` / `.updated_at` (`SDK/node/attribute.py:121-135`) and `get_relationship_metadata()` (`SDK/node/related_node.py:243-253`) in `infrahub_sync/adapters/infrahub.py`, outside the DiffSync models
- [ ] T037 [US3] Add the `LastChangeRecord` model and the `last_changes` manifest field (data-model.md) to `infrahub_sync/plan/models.py`
- [ ] T038 [US3] Produce one record per changed field of each update in `infrahub_sync/plan/derive.py:590-682`, using the adapter's map; write `last_changes.jsonl` before the manifest in `infrahub_sync/plan/writer.py`; read it and check its digest in `infrahub_sync/plan/reader.py` and `infrahub_sync/plan/review.py`
- [ ] T039 [US3] Include `last_changes.jsonl` in the checkpoint artifact in `infrahub_sync/product_store/bundle.py` if T003 found a fixed file list; otherwise add a test that the checkpoint carries it
- [ ] T040 [US3] Add the optional `last_changes` field to `PlanResource` in `infrahub_sync/client/models.py:341` and fill it in `infrahub_sync/service/flow.py:178-187`
- [ ] T041 [US3] Check whether the optional `lineage` and `last_changes` fields of `PlanResource` need a change to the client and service compatibility check in `infrahub_sync/service/compatibility.py`; make the change, or record in research.md R-4 why none is needed, with a test in `tests/service/test_compatibility.py`
- [ ] T042 [US3] Show, for each changed field of each update in `infrahub-sync runs plan`, the display label of `updated_by` and `updated_at`, or "not recorded", in `infrahub_sync/cli.py`
- [ ] T043 [US3] Opt-in integration test for quickstart scenario 3 in `tests/integration/test_infrahub_lineage_integration.py`: a value changed by a second account shows that account and the time; a field with no recorded change shows "not recorded"; a create shows no last-change record

**Checkpoint**: User Stories 1 to 3 work on their own.

---

## Phase 6: User Story 4 - Opt in to protection (Priority: P2)

**Goal**: The import sends the protection flag only when `protect` is on; values protected earlier
stay protected when it is off.

**Independent Test**: Quickstart scenario 4.

### Tests for User Story 4

- [ ] T044 [P] [US4] Unit tests in `tests/adapters/test_infrahub_planned_write.py`: with `protect` on and no owner, creates and updates send `is_protected: true` on attributes and edges they write; with `protect` off, no `is_protected` key is sent (FR-006)
- [ ] T045 [P] [US4] Registration test in `tests/configuration/test_infrahub_lineage_validation.py`: `protect` accepted as `true` or `false`, refused otherwise

### Implementation for User Story 4

- [ ] T046 [US4] Add `protect` to the allowed settings of the `infrahub` entry in `infrahub_sync/configuration/capabilities.py:525`
- [ ] T047 [US4] Pass `is_protected=True` when `protect` is on, and `None` otherwise, in the payload code of `infrahub_sync/adapters/infrahub.py:1333-1345`; record `protect` in the manifest's `lineage` field
- [ ] T048 [US4] Opt-in integration test for quickstart scenario 4 in `tests/integration/test_infrahub_lineage_integration.py`: protected with `protect` on; with `protect` off, no flag sent and values protected earlier keep their flag; a value written before this feature keeps its owner and protection and gets the import account as source when the import next writes it (spec edge case)

**Checkpoint**: all four user stories work on their own.

---

## Phase 7: Polish and cross-cutting concerns

- [ ] T049 [P] Add a guard for future resource-pool allocation, with a unit test, in `tests/adapters/test_infrahub_planned_write.py` and `infrahub_sync/adapters/infrahub.py`: a relationship edge whose input carries `from_pool` gets no `source`, `owner` or `is_protected`, so the pool stays its source. No V3 write path allocates from a pool today (research R-6); the guard keeps the rule when one is added (spec Clarifications Q2)
- [ ] T050 [P] Add a unit test in `tests/adapters/test_infrahub_write_payload_guard.py` that no write payload carries `updated_at` or `_relation__updated_at`, so nodes read with `include_metadata=True` are never sent back as written (research R-6)
- [ ] T051 [P] Rewrite the "Lineage tracking" section of `docs/docs/adapters/infrahub.mdx` (lines 185-230 and the settings table near line 107) for FR-010: the permission set from research.md R-7 for each branch; read permission for each allowed source or owner kind; `protect` with and without an owner; values written before this release keep their protection; the resource-pool exception; how to set the owner of existing data; one import account per import; imports that share a token cannot be told apart by `updated_by`; apply refuses a different import account; "not recorded" before Infrahub 1.7.0
- [ ] T052 [P] Document `last_changes.jsonl` and the `lineage` and `last_changes` manifest fields in `docs/docs/development/knowledge/plan-artifact.md` (field table near line 164)
- [ ] T053 [P] Replace every bare-name `source:` or `owner:` in Infrahub destination settings under `examples/` and `docs/docs/` with the `{kind, name}` form, and regenerate generated examples from their YAML source
- [ ] T054 [P] Add the news fragment with `uv run --extra dev towncrier create -c "..." +import-lineage.changed.md` in `changelog/`, describing the import account as source, the `{kind, name}` form, the stop on a missing named node, the refusal of a different import account at apply, `protect` off by default, and who last changed a value in the plan; the pull request carries the label `changes/minor`
- [ ] T055 Run `uv run invoke format`, `uv run invoke lint`, `uv run invoke tests.tests-unit`, `uv run invoke docs.generate`, and the CLI checks `uv run infrahub-sync --help`, `configs --help`, `runs --help`
- [ ] T056 Run the benchmark from T004 again with metadata reads on, on the default branch and on another branch; record the read time with and without metadata in research.md R-2 and close the item in plan.md. The result is information only and does not block the merge
- [ ] T057 Run every scenario in `quickstart.md` against the development stack and record the result in the pull request description

---

## Dependencies and execution order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies. T001 gates Phase 5; T002 feeds T023 and T027; T003 feeds T039; T004 feeds T056; T005 feeds T019 and T051.
- **Foundational (Phase 2)**: blocks all user stories.
- **User Story 1 (Phase 3)**: depends on Phase 2 and T005.
- **User Story 2 (Phase 4)**: depends on Phase 2 and T002. It shares the payload code with US1 (T015 then T027) and the manifest field (T017 then T028).
- **User Story 3 (Phase 5)**: depends on Phase 2, T001 (binding) and T003. It does not depend on US1 or US2.
- **User Story 4 (Phase 6)**: depends on Phase 2. It shares the payload code with US1 (T015 then T047).
- **Polish (Phase 7)**: after the stories it documents; T056 after US3.

### Within each user story

- Tests first, and they fail before the implementation.
- Models before the code that writes them (T037 before T038).
- Story checkpoint before the next story when working alone.

## Parallel opportunities

- Setup: T001, T002 and T003 run in parallel; T004 and T005 need the stack and can run alongside.
- Foundational: T007 in parallel with T008 once T006 is done.
- US1 tests: T009 to T013 in parallel.
- US2 tests: T020 to T024 in parallel.
- US3 tests: T031 to T033 in parallel.
- US4 tests: T044, T045 in parallel.
- Polish: T049 to T054 in parallel.
- After Phase 2, US3 can be built in parallel with US1 and US2 because it touches the read path and the plan's last-change file, not the write payload.

### Parallel example: User Story 3

```text
T031 tests/adapters/test_infrahub_metadata_read.py
T032 tests/plan/test_last_changes.py
T033 tests/service/test_plan_last_changes.py
```

## Implementation strategy

### MVP first (User Story 1)

1. Phase 1 (T001-T005) and Phase 2 (T006-T008).
2. Phase 3 (T009-T019).
3. Stop and check quickstart scenarios 1 and 5.

### Incremental delivery

1. US1: the import account as source, no default owner or protection, 403 reported as a missing permission, apply refuses a different import account.
2. US2: named owner and source in `{kind, name}` form, stop on a missing named node in plan and apply, owner notice.
3. US3: who last changed each value in the plan, `last_changes.jsonl`, "not recorded" only when Infrahub holds no value.
4. US4: `protect` opt-in.
5. Polish: pool guard, docs, examples, news fragment (`changed`, label `changes/minor`), benchmark with metadata.

## Notes

- [P] tasks touch different files and do not depend on unfinished tasks.
- Commit after each task or logical group, with explicit paths.
- Start from `feature/v3-develop`.
