# Tasks: A second plan shows only real changes, and the plan step is measured by part

**Input**: Design documents from `dev/specs/019-second-plan-real-changes/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: The spec requires tests (FR-022, FR-023, FR-011) and the constitution requires tests
with every change, so each story holds its test tasks, written before the implementation.

**Format**: `- [ ] [ID] [P?] [Story] Description`. `[P]` means the task touches different files
from the other open tasks of its phase and has no dependency on them.

## Phase 1: Setup (probes that can change the design)

**Purpose**: Settle the two facts that research R2 and R8 left open. If either result contradicts
the design, stop and amend `plan.md` and `research.md` before Phase 2.

- [X] T001 Write an opt-in probe that starts the development stack's Infrahub 1.11.9, creates a new branch, and lists every node of every kind that is not `Core*` or `Internal*`, with its node metadata (creator, creation time) and, for `IpamNamespace`, the `default` attribute, in `tests/integration/test_infrahub_builtin_nodes_integration.py` (marker `integration`)
- [X] T002 Run T001 against the development stack (`uv run invoke dev.start`), commit its output as `tests/data/infrahub_builtin_nodes/infrahub-1.11.9.json`, and record in `dev/specs/019-second-plan-real-changes/research.md` R2 which mark was chosen: the node-level mark, or the `default` attribute of `BuiltinIPNamespace`. If built-in nodes of other mappable kinds exist and no node-level mark covers them, stop and amend `spec.md`
- [X] T003 [P] Write an opt-in live test that creates a node with an optional one-peer relationship set, applies an Upsert that renders `<field>: null` through the SDK as research R8 describes, and reads the node back with no peer, in `tests/integration/test_infrahub_one_peer_clear_integration.py` (marker `integration`, modelled on `tests/integration/test_infrahub_replace_set_shrink_integration.py`). If the edge is not removed, stop and amend research R8

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Shared helpers that User Stories 1, 2 and 4 use.

- [X] T004 [P] Write tests for `operations_digest`: equal for two operation lists in different orders, different when one payload value differs, unchanged when only `destination_id` differs, in `tests/plan/test_digest.py`
- [X] T005 [P] Implement `operations_digest(operations) -> str` (SHA-256 of the sorted `canonical_json_bytes` lines of `{action, kind, identity, payload, relationships}`, research R16) in `infrahub_sync/plan/digest.py`
- [X] T006 Create `ApplyingDestination`, a stand-in destination that implements `new_peer_resolver` and `apply_planned_operation`, keeps model data per kind, applies payloads and references (an empty one-peer reference gives `None`, a many reference replaces the set), and returns the destination data for the next `plan_fixture` call, in `tests/plan/second_plan.py` (research R9; reuse `FixtureSource` and `plan_fixture` of `tests/plan/problem_plan.py`)
- [X] T007 Add second-plan fixture data in `tests/data/second_plan/`: devices, `InterfaceLag` and `InterfacePhysical` with the one-peer `lag` relationship, prefixes and IP addresses without a VRF (pointing at the destination-only `default` namespace, as `tests/plan/test_destination_only_peer.py:485-584` does), interfaces with tagged VLANs (a many relationship), and an `IpamNamespace` named `default` present in the destination only

**Checkpoint**: Foundation ready.

---

## Phase 3: User Story 1 - A second plan on unchanged data is empty (Priority: P1) 🎯 MVP

**Goal**: A plan holds no delete for a node that Infrahub creates itself, and a second plan on
unchanged data holds zero operations.

**Independent Test**: `uv run pytest tests/plan/test_second_plan.py -k "unchanged or builtin" -q`.

### Tests for User Story 1

- [X] T008 [P] [US1] Test that `derive_deletes` skips an unmatched destination node whose id is in the destination's built-in set, keeps a delete for an unmatched node created by hand, and treats a built-in node that a source record matches like any other node (FR-001, FR-003; edge cases of the spec), in `tests/plan/test_builtin_deletes.py`
- [X] T009 [P] [US1] Test that the Infrahub destination adapter fills its built-in set from the mark chosen in T002, using the fixture of T002 and the stand-in client of `tests/adapters/test_infrahub_peer_identifier.py`, and that the mark does not enter the model data, in `tests/adapters/test_infrahub_builtin_nodes.py`
- [X] T010 [US1] Write the unchanged-data second-plan tests: plan, apply with `ApplyingDestination`, plan again; zero operations for LAG member interfaces, prefixes and IP addresses without a VRF, and tagged VLANs; three plans in a row give the same `operations_digest` and the same plan report (FR-022, FR-026), in `tests/plan/test_second_plan.py`
- [X] T011 [US1] Test that a first plan whose report holds records left out and kept records (`Variant.ALL` of `tests/data/plan_problems/`) gives a second plan with zero operations and the same plan report (FR-025, FR-026), in `tests/plan/test_second_plan.py`
- [X] T012 [P] [US1] Test the real Infrahub converter on shallow `InterfaceLag` peers that miss `device` across two plans (#167), with the fakes of `tests/adapters/test_infrahub_peer_identifier.py`, in `tests/adapters/test_infrahub_lag_second_plan.py`. If it fails, fix the defect in `infrahub_sync/adapters/infrahub.py` as part of this task
- [X] T013 [P] [US1] For NetBox, Nautobot and generic REST, test that reversed peer lists through each adapter's `record_to_data` give the same operations (FR-023), in `tests/adapters/test_netbox_peer_order.py`, `tests/adapters/test_nautobot_peer_order.py` and `tests/adapters/test_genericrestapi_peer_order.py`

### Implementation for User Story 1

- [X] T014 [US1] Add the optional protocol for the set of destination node ids that Infrahub creates itself, next to `MetadataReadingDestination`, in `infrahub_sync/plan/write_surface.py`
- [X] T015 [US1] Fill the built-in set in `InfrahubAdapter.model_loader`, next to `_record_last_changes`, reading the mark chosen in T002 only for the kinds that can carry it, in `infrahub_sync/adapters/infrahub.py`
- [X] T016 [US1] Skip a node of the built-in set in the unmatched branch of `derive_deletes` (`infrahub_sync/plan/derive.py:748-750`), log one `info` line with the count, and pass the destination set from `infrahub_sync/potenda/__init__.py:969-978`
- [X] T017 [US1] Remove the `builtin_deletes` field and its name-based exclusion from `tasks/bench.py:480-494` and `development/bench/records.py:245`, and update `tests/test_benchmark_runner.py:173, 418, 483, 614` (FR-004)
- [X] T018 [US1] Remove the `IpamNamespace name=default` entry from `tests/data/v2_gate/expected_deletes.yml`, keep the file and its loader in `.github/scripts/nightly_e2e.py`, and update `tests/test_nightly_v2_gate.py` so the gate fails on that delete (FR-024)
- [X] T019 [P] [US1] Remove the `default` namespace delete from "Current limitations" in `examples/netbox_to_infrahub/README.md` and from `docs/docs/development/guides/netbox-benchmark-runner.md:153-170`
- [X] T020 [P] [US1] Write ADR 0043 "A plan leaves out the deletes of nodes that Infrahub creates itself" in `dev/adr/0043-a-plan-leaves-out-the-deletes-of-nodes-that-infrahub-creates-itself.md` with the probe result of T002, add a one-line note pointing to it in `dev/adr/0041-an-unmapped-ip-namespace-converts-to-the-default-namespace.md` and `dev/adr/0042-the-v2-gate-passes-on-no-creates-and-reviewed-lists.md`, and list it in `dev/adr/README.md`

**Checkpoint**: A second plan on unchanged data is empty.

---

## Phase 4: User Story 2 - A second plan holds every real change, and only those (Priority: P1)

**Goal**: An emptied one-peer relationship is planned in format 5, applied and verified.

**Independent Test**: `uv run pytest tests/plan/test_second_plan.py -k "change or emptied or replaced" tests/plan/test_format_5.py -q`.

### Tests for User Story 2

- [ ] T021 [P] [US2] Test format 5 models: a one-peer reference with no peer is accepted on an update, refused on a create and on an identity field, and refused in a format 4 plan (FR-007, FR-010; `contracts/plan-format-5.md`), in `tests/plan/test_format_5.py`
- [ ] T022 [P] [US2] Test readers: format 4 plans are still read, verified and applied; a format 6 plan is refused by `runs plan`, `verify`, apply and resume with a message that names the version and the supported versions (FR-011, FR-012), in `tests/plan/test_format_5.py`
- [ ] T023 [P] [US2] Test derivation: an update gains an empty one-peer reference when the destination holds a peer and the source holds none; no reference when both are empty; when the field is required, no update for the record and a problem of the new type `required_relationship_empty_in_source` that names the record and the field (FR-013a); no reference when the peer was left out (kept record), or when a configured filter removed the peer (`tests/adapters/test_netbox_filtered_references.py:297-341` stays true); no warning is logged (FR-005, FR-008; research R6, R7), in `tests/plan/test_emptied_relationship.py`
- [ ] T024 [P] [US2] Test apply against rendered SDK input: an emptied relationship renders `<field>: null` in the one Upsert; an update without an emptied relationship renders the same input as before this feature; the write is refused when another relationship renders (FR-006; research R8), in `tests/plan/test_apply_conformance.py`
- [ ] T025 [P] [US2] Test verify: a planned empty one-peer set matches an empty destination relationship and is reported as a difference against a destination that holds a peer (FR-013), in `tests/plan/test_destination_check.py`
- [ ] T026 [US2] Write the change second-plan tests: one changed attribute gives one update; an interface that leaves its LAG gives one update, and after apply the next plan is empty; a peer `B` replaced by `C` in a many relationship gives the right peers after apply and an empty next plan (FR-022; SC-002), in `tests/plan/test_second_plan.py`
- [ ] T027 [P] [US2] Test that `runs plan --detail` prints `<field>: becomes empty` for an emptied one-peer relationship and that the summary counts the update once (FR-009; `contracts/cli.md`), in `tests/cli/test_client_commands.py`

### Implementation for User Story 2

- [ ] T028 [US2] Set `PLAN_FORMAT_VERSION = 5` and `SUPPORTED_FORMAT_VERSIONS = {2, 3, 4, 5}`, allow 0 or 1 peer for a one-peer reference from format 5 on, and add the operation rules for creates and identity fields, in `infrahub_sync/plan/models.py`
- [ ] T029 [US2] Update the tests that assert format 4: `tests/plan/test_models.py:100`, `tests/plan/test_lineage_manifest.py:106`, `tests/plan/test_plan_format_3.py:94`, `tests/plan/test_problem_pass.py:331`, `tests/plan/test_problems.py:80`, `tests/plan/test_format_4.py:112, 310`
- [ ] T030 [US2] Record `(owner kind, owner source id, field)` when a configured filter removes an optional peer, in the shared reference helper `infrahub_sync/adapters/references.py:95-107`, and expose it on the source adapter's read state
- [ ] T031 [US2] Add the emptied one-peer reference for an update after `_resolve_references`, with the conditions of research R6, and remove `_warn_dropped_cardinality_one_clear`, in `infrahub_sync/plan/derive.py`
- [ ] T031a [US2] Add the problem type `required_relationship_empty_in_source` to `ProblemType` and `_REASON_TEMPLATES`, raise it when the source holds no peer for a required one-peer relationship, and leave the record out of the plan with its destination node kept, as `required_peer_excluded_by_filter` does (FR-013a; research R6, last paragraph). First test what the current code does for that case, in `infrahub_sync/plan/models.py`, `infrahub_sync/plan/problems.py` and the source-read path that raises `required_peer_excluded_by_filter`
- [ ] T032 [US2] Write an emptied one-peer relationship in `apply_planned_operation`: no entry in the create data for it, the destination id in the create data for that operation only, the field set to `None`, and the rendered-input guard extended, in `infrahub_sync/adapters/infrahub.py:1852-1937`
- [ ] T033 [US2] Print `<field>: becomes empty` in `_operation_detail`, in `infrahub_sync/cli.py:1075-1094`
- [ ] T034 [P] [US2] Describe format 5 and the emptied one-peer reference in `docs/docs/development/knowledge/plan-artifact.md`
- [ ] T035 [P] [US2] Write ADR 0044 "Saved plan format 5 records an emptied one-peer relationship" in `dev/adr/0044-saved-plan-format-5-records-an-emptied-one-peer-relationship.md` and list it in `dev/adr/README.md`
- [ ] T036 [P] [US2] Add the news fragment `changelog/387.fixed.md`: a plan now records and apply now writes an emptied one-peer relationship; plans are saved in format 5; upgrade the API and the worker together

**Checkpoint**: A second plan holds every real change.

---

## Phase 5: User Story 3 - View how long each part of a plan took (Priority: P2)

**Goal**: The run response and `runs show` show the seven parts of the plan step.

**Independent Test**: `uv run pytest tests/service/test_plan_step.py tests/product_store/test_plan_step_column.py tests/cli/test_client_commands.py -k plan_step -q`.

### Tests for User Story 3

- [ ] T037 [P] [US3] Test the `plan_step` column: added at start-up to a store created before this feature, `NULL` for old rows, a document with an unknown key or value refused, a write after `terminal_at` refused (FR-019, FR-021), in `tests/product_store/test_plan_step_column.py` (modelled on `tests/product_store/test_side_read_columns.py:76`)
- [ ] T038 [P] [US3] Test derivation of `PlanStepTimings`: `waiting` from `submitted_at` and `claimed_at` only when the document exists and the times are in order; `preparing` from `claimed_at` to the earlier read start; a running part; a stopped part with `stopped_at` equal to `terminal_at`; a stopped part with no verdict; a completed execution with a part still running gives no entry; no zero duration for a part that did not run (FR-014 to FR-017; research R13), in `tests/service/test_plan_step.py`
- [ ] T039 [P] [US3] Test that the engine calls the plan-step observer around comparing, checking and saving, that an observer error is logged by class and does not change the plan, and that the worker closes saving after `_publish_plan` and the running part before a failure commit, in `tests/service/test_plan_step.py`
- [ ] T040 [P] [US3] Test the run response: `plan_step` present for a new `plan` and `sync` execution, `null` for an old one, and an older client keeps the unknown field (`tests/service/test_http_api.py:2031` pattern), in `tests/service/test_http_api.py`
- [ ] T041 [P] [US3] Test `runs show` output: one `plan_<part>` line per part after the read lines, `running`, `stopped after <s>s`, `stopped`, and no plan line for an old run (`contracts/cli.md`), in `tests/cli/test_client_commands.py`

### Implementation for User Story 3

- [ ] T042 [US3] Add the `plan_step` column to `CREATE TABLE`, the start-up migration, the SELECT and INSERT SQL and the row parse, add `_check_plan_step_document` and `record_plan_step` to the store protocol, the relational store and the projection, in `infrahub_sync/product_store/store.py`, and the field on `PrefectExecutionLink` in `infrahub_sync/product_store/models.py`
- [ ] T043 [US3] Add the `PlanStepObserver` protocol (`part_started`, `part_ended`) in `infrahub_sync/potenda/plan_step.py`, call it around comparing, `run_problem_pass` and `write_artifact` in `infrahub_sync/potenda/__init__.py`, and pass it through a private `_plan_step_observer` keyword of `execute_run` in `infrahub_sync/execution.py`
- [ ] T044 [US3] Add `StorePlanStepObserver` and `stage_plan_step` (derivation of research R11 and R13) in `infrahub_sync/service/plan_step.py`; wire the observer in `infrahub_sync/service/flow.py` for `plan` and `sync`, close saving after `_publish_plan` (`flow.py:840`, `:999`) and close the running part before the failure commit (`flow.py:1546-1559`)
- [ ] T045 [US3] Add `PlanStepTimings` and `PlanStepPart` to `infrahub_sync/client/models.py`, `OrchestrationSummary.plan_step`, the exports in `infrahub_sync/client/__init__.py`, and fill it in `_resource_with_observations` in `infrahub_sync/service/service.py`
- [ ] T046 [US3] Print the `plan_<part>` lines after the read lines in `runs_show`, and add the "subject to change" sentence to its docstring, in `infrahub_sync/cli.py`
- [ ] T047 [P] [US3] Document `plan_step` in `docs/docs/reference/sync-http-api.mdx`, each part marked as subject to change; mention the timings in `docs/docs/running-a-sync.mdx`
- [ ] T048 [P] [US3] Add the news fragment `changelog/+plan-step-timings.added.md`

**Checkpoint**: An operator views the duration of each part of a plan.

---

## Phase 6: User Story 4 - Measure the plan step on the benchmark tiers (Priority: P3)

**Goal**: Benchmark records hold the new fields, and a results page holds the measurements of
tiers S and M.

**Independent Test**: `uv run pytest tests/test_benchmark_runner.py -q`; then the manual run of
`quickstart.md` section 4.

### Tests for User Story 4

- [ ] T049 [P] [US4] Test the new record fields (`plan_parts`, read modes, `plan_bytes`, `operations_digest`, `left_out`, `kept`, `problems`), their clearing for a result that is not `ok`, and that a part without times is left out (`contracts/benchmark-record.md`), in `tests/test_benchmark_runner.py`
- [ ] T050 [P] [US4] Test the checks: a `warm` run fails on any operation, deletes included; a `changed` run compares deletes; a run whose plan leaves records out records the report counts and compares the apply summary only when an apply ran (FR-029, FR-030), in `tests/test_benchmark_runner.py`

### Implementation for User Story 4

- [ ] T051 [US4] Add the new fields to `ResultRecord` in `development/bench/records.py`
- [ ] T052 [US4] Fill the new fields from the run response, the plan review artifact and `operations_digest`, replace the `warm` check with `summary.total == 0`, and handle a plan with records left out, in `tasks/bench.py`
- [ ] T053 [P] [US4] Update the record description in `docs/docs/development/guides/netbox-benchmark-runner.md`
- [ ] T054 [US4] On the benchmark machine, run tiers S and M, scenarios `cold`, `warm` and `changed`, three repetitions each, on the code of this feature before any speed change (FR-031, FR-033; `quickstart.md` section 4). If the machine is not available, leave this task open and say so in the final report
- [ ] T055 [US4] Write `docs/docs/development/guides/netbox-benchmark-results.md` from the 18 records (FR-032; `contracts/benchmark-record.md`), link it from `netbox-benchmark-runner.md`, `netbox-benchmark-tiers.md` and `docs/docs/development/guides/index.md`, and add it to the docs sidebar and to the page list of `tests/test_developer_pages_sidebar.py:68-73`

**Checkpoint**: The measurements are committed.

---

## Phase 7: User Story 5 - A second plan at tier M takes no longer than the first plan (Priority: P3)

**Goal**: FR-034 is met, or the reason is recorded.

**Independent Test**: The verdict section of the results page.

- [ ] T056 [US5] Compare the median plan time of the `warm` and `changed` scenarios with the `cold` scenario at tier M, and write the verdict in `docs/docs/development/guides/netbox-benchmark-results.md` (FR-034)
- [ ] T057 [US5] If FR-034 is not met and one part accounts for the difference, change that part, measure again, and confirm the operations digests are unchanged (FR-035). If no single part accounts for it, or the change needs work beyond this feature, open a GitHub issue that links the results page and record it on the page (FR-036)

---

## Phase 8: Polish & cross-cutting concerns

- [ ] T058 [P] Add the FR-027 paragraph to `docs/docs/running-a-sync.mdx` ("Review the plan") and the help sentence of `runs plan` in `infrahub_sync/cli.py`
- [ ] T059 Regenerate `docs/docs/reference/cli.mdx` with `uv run invoke docs.generate`
- [ ] T060 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .` and `uv run invoke tests.tests-unit`, and fix every finding
- [ ] T061 Run the CLI sanity commands: `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help`, `uv run infrahub-sync runs --help`
- [ ] T062 Run the `from-netbox` example check twice (`quickstart.md` section 3) and confirm the second plan holds 0 operations (SC-001)

---

## Dependencies & execution order

### Phase dependencies

- **Phase 1** (probes) has no dependency. Its results can stop the work.
- **Phase 2** depends on Phase 1. It blocks User Stories 1, 2 and 4.
- **User Story 1** (Phase 3) depends on Phase 2. T015 depends on T002.
- **User Story 2** (Phase 4) depends on Phase 2. T032 depends on T003. The LAG case of T026
  depends on T031.
- **User Story 3** (Phase 5) depends on nothing in Phases 3 and 4 and can run beside them.
- **User Story 4** (Phase 6) depends on User Stories 1 and 3 (T052 reads `plan_step` and drops
  the name-based exclusion). T054 depends on the benchmark machine.
- **User Story 5** (Phase 7) depends on T054.
- **Phase 8** depends on every story that is done.

### Within each story

Tests first, and they fail before the implementation. Models before derivation, derivation
before apply, service before CLI.

### Parallel opportunities

- Phase 1: T003 beside T001 and T002.
- Phase 2: T004 and T005 beside T006 and T007.
- User Story 1: T008, T009, T012 and T013 together; T019 and T020 together.
- User Story 2: T021 to T025 and T027 together; T034, T035 and T036 together.
- User Story 3: T037 to T041 together; the whole story beside User Stories 1 and 2.

### Parallel example: User Story 3

```text
T037 tests/product_store/test_plan_step_column.py
T038 tests/service/test_plan_step.py (derivation)
T040 tests/service/test_http_api.py
T041 tests/cli/test_client_commands.py
```

## Implementation strategy

### MVP first

1. Phases 1 and 2.
2. User Story 1: the empty second plan. Stop and validate with `quickstart.md` section 1.

### Incremental delivery

1. User Story 1, then User Story 2: correct plans. One pull request.
2. User Story 3: timings. Can be a second pull request.
3. User Stories 4 and 5: measurement, when the benchmark machine is available.
