---

description: "Task list for spec 009: plan problem reporting"
---

# Tasks: Plan problem reporting

**Input**: design documents in `dev/specs/009-plan-problem-reporting/`: [spec.md](spec.md),
[plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md).

**Tests**: the spec asks for tests (problem fixtures with all seven types, a store-unchanged
test, a `diff_from`-only test, no-credential-in-cause tests, the left-out confirmation 409). In
each story phase the test tasks come first and must fail before the implementation tasks start.
Follow [docs/docs/development/guidelines/testing.md](../../../docs/docs/development/guidelines/testing.md):
for each contract-bearing test, name the mutation, apply it, confirm a test fails, revert it.
Call shared helpers (canonical encoding, checksum, fingerprint) instead of recomputing them in
a test.

**Organization**: one phase per user story of spec.md (all four are P1, in spec order), plus
two phases for the requirements that no user story covers (`configs validate`, FR-032 to
FR-035; the NetBox example, FR-031).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel with the other [P] tasks of the same phase (different files, no
  dependency on an unfinished task).
- **[Story]**: US1 to US4, the user stories of spec.md.
- **SIGN-OFF**: the task's output cannot be merged before the product owner signs off the
  decision named in the task (FR-029, FR-030).
- **DEP-013**: the task touches a file that spec 013 also changes; the dependency is stated in
  the task.

Paths are relative to the repository root. Line numbers come from research.md (commit
`4646b716`) and may have moved; find the named function when they differ.

---

## Phase 1: Setup (shared fixtures, parity baseline, decision records)

**Purpose**: test fixtures that several stories use, a baseline that proves the loader change
leaves plans of valid data unchanged, and the decision records that need product sign-off
early.

- [X] T001 Create the shared problem fixture in `tests/plan/problem_fixtures.py`: an in-memory source adapter (implementing `list_records` and `record_to_data` only) and a destination schema record that together hold one record of each of the seven problem types of data-model.md (`dropdown_value_not_in_choices`, `duplicate_source_identity`, `destination_identity_collision`, `wrong_value_type`, `required_peer_excluded_by_filter`, `identities_merge_in_destination`, `peer_not_found_in_source` through a required relationship), a set of valid records, one `DcimDevice` with a problem and five `InterfacePhysical` records that refer to it through the required relationship `device`, one `IpamIPAddress` that refers to that device only through an optional relationship, and one `IpamIPAddress` that refers to a VRF source ID that the source does not return through the optional relationship `vrf`. Export builder functions so each test can take a variant (no problems, every record with a problem, a dependency chain of three levels). Fixture credentials: one of five characters and one that holds `"`, `\` and `,`, exposed as constants for the absence tests.
- [X] T002 Capture the plan baseline before any loader change: add `tests/plan/test_loader_plan_parity.py` that plans every existing valid fixture used by `tests/test_potenda_plan_artifact.py` and `tests/test_potenda_tiers.py` with the current code, and writes the canonical operations (through `infrahub_sync/plan/canonical.py`) to `tests/data/loader_parity/<fixture>.jsonl`. Commit the baseline files with this task; the same test compares against them after Phase 2 (T029).
- [X] T003 [P] SIGN-OFF (FR-029) Write `dev/adr/0020-records-with-problems-are-left-out-of-a-plan.md` from the draft in research.md R11 (MADR format, Status "Proposed, needs product sign-off (spec 009 FR-029)", Date 2026-10-15). Cite no Slack, Jira or chat sources; cite only repository files and GitHub issues.
- [X] T004 [P] SIGN-OFF (FR-030) Write `dev/adr/0021-a-run-failure-record-holds-a-structured-cause.md` from the draft in research.md R12 (Status "Proposed, needs product sign-off (spec 009 FR-030)"). State that it replaces the rule in the comment at `infrahub_sync/service/flow.py:722-725` and keeps ADR 0005.
- [X] T005 [P] SIGN-OFF (depends on ADR 0020) Write `dev/adr/0022-saved-plan-format-4-records-left-out-records.md` from the draft in research.md R10 (Status "Proposed").
- [X] T006 Add the three records to the "Current records" list in `dev/adr/README.md`, each with a one-line summary, marked "proposed".
- [X] T007 SIGN-OFF (FR-029) Write the AD-12 amendment note in `dev/specs/009-plan-problem-reporting/ad-12-amendment.md`: the draft AD-12 row of research.md R11, the target file (`infrahub-sync-process/product/decisions.md`, outside this repository), and a "Sign-off" line with the product owner's name and date left empty. The product owner applies the row to `decisions.md`; this repository only holds the note.

**Checkpoint**: fixtures and baseline exist; sign-off requests for ADR 0020, 0021, 0022 and the AD-12 amendment are open.

---

## Phase 2: Foundational (shared loader step, removal of `continue_on_error`)

**Purpose**: the adapter base class owns the per-record load loop and the one admission step
(research R3, R4, R5). Every story depends on it.

**Critical**: no story phase starts before this phase is complete. Between this phase and
Phase 3 a record that cannot be built is absent from the plan without being listed, so Phase 2
must not be released without Phase 3.

### Tests for the loader step (write first, confirm they fail)

- [ ] T008 [P] Extend `tests/test_diffsync_mixin_contract.py`: the default `DiffSyncMixin.model_loader` finds the mapping entry, calls `list_records(model_name, element)`, on the source side applies `model.filter_records` and records the IDs a filter removed, applies `model.transform_records`, and calls `load_record(model, element, record)` once per record, in order.
- [ ] T009 [P] Create `tests/adapters/test_record_admission.py`: on the source side `load_record` records `wrong_value_type` (field, pydantic error type, value limited to 256 characters) for a `ValidationError` whose errors are all type errors; `duplicate_source_identity` naming `existing_object` for `ObjectAlreadyExists`, and the store keeps the first record unchanged; `required_peer_excluded_by_filter` for `ExcludedRequiredPeerError`; `peer_not_loaded` for `PeerNotLoadedError`; `peer_not_found_in_source` for `PeerNotFoundError`. A `ValidationError` holding a missing required value is raised again as `RecordLoadError` with kind, record ID and field. On the destination side nothing is caught. No load report entry holds error text (assert the exception message string is absent).
- [ ] T010 [P] Create `tests/adapters/test_peer_unique_id.py`: one parametrized case per row of the research.md R4 table (peer in the store; in the load report with identity; in the load report without identity raises `PeerNotLoadedError` for required and optional; removed by a filter with required raises `ExcludedRequiredPeerError`; removed by a filter with optional returns no value; not in the source with required raises `PeerNotFoundError`; not in the source with optional records `peer_not_found_in_source` with `loaded: true` and returns a placeholder marked "not in the source").
- [ ] T011 [P] Create `tests/adapters/test_list_records_only_adapter.py`: the adapter of `tests/plan/problem_fixtures.py` (T001), which implements only `list_records` and `record_to_data`, loads the fixture and produces the same load report as the NetBox adapter on the same records (quickstart scenario 5b, last item).
- [ ] T012 [P] Update the tests that set `continue_on_error` to the admission behaviour: `tests/adapters/test_infrahub_peer_identifier.py` (the peer-identifier case keeps raising `PeerIdentifierError`), `tests/adapters/test_infrahub_loader_fields.py`, `tests/adapters/test_infrahub_incremental.py`, and the `continue_on_error` assertions in `tests/test_execution_surface.py` (around line 1479). Add a static test in `tests/test_execution_surface.py` that no module under `infrahub_sync/` contains the name `continue_on_error`.
- [ ] T013 [P] Update `tests/adapters/test_netbox_filtered_references.py`: an excluded required peer no longer raises `ValueError("Configured filter excluded all peers …")`; the record is in the load report as `required_peer_excluded_by_filter`, and the rest of the kind loads.

### Implementation of the loader step

- [ ] T014 Add to `infrahub_sync/__init__.py`: the exceptions `ExcludedRequiredPeerError`, `PeerNotLoadedError`, `PeerNotFoundError` and `RecordLoadError` (each with typed attributes `kind`, `source_id`, `field`, and `peer_kind`/`peer_source_id` where relevant, no free text built from data other than these attributes), and the typed load report models `SourceRecordReference` and `LoadReportEntry` with the fields of data-model.md "Load report" and "Source record reference".
- [ ] T015 Implement in `DiffSyncMixin` (`infrahub_sync/__init__.py`): a default `model_loader` (mapping lookup, `list_records`, filter with the bookkeeping of removed IDs moved from `infrahub_sync/adapters/netbox.py:29-59, 226-227`, transform, `load_record` per record); `load_record(model, element, record, *, after_add=None)` as the admission step of research.md R3 (catches only on the source side, records into `self.load_report`); and `peer_unique_id(peer_kind, peer_source_id, *, owner, field, required)` with an index by kind and `local_id`, following research.md R4. Declare `list_records` and `record_to_data` as the methods an adapter implements. Depends on T014.
- [ ] T016 [P] Change `infrahub_sync/adapters/netbox.py`: replace `model_loader` (`:182-227`) with `list_records` and rename `netbox_obj_to_diffsync` (`:229`) to `record_to_data`; replace the scan of `self.store.get_all(...)` in reference resolution (`:250-312`) with `self.peer_unique_id(...)`; remove the filter bookkeeping now in the base class.
- [ ] T017 [P] Change `infrahub_sync/adapters/nautobot.py`: `list_records` from `model_loader` (`:208-254`), `record_to_data` from `nautobot_obj_to_diffsync` (`:256`), reference resolution (`:276-300`) through `self.peer_unique_id(...)` so the "Unable to locate the node" warning-and-drop path is gone; delete the `continue_on_error` record-skipping branch (`:236-253`).
- [ ] T018 [P] Change `infrahub_sync/adapters/aci.py`: `list_records` from `model_loader` (`:240-287`), `record_to_data` from `obj_to_diffsync` (`:289`), reference resolution (`:312-367`) through `self.peer_unique_id(...)`.
- [ ] T019 [P] Change `infrahub_sync/adapters/genericrestapi.py`: `list_records` from `model_loader` (`:100-145`), `record_to_data` from `obj_to_diffsync` (`:179`), reference resolution (`:209-234`) through `self.peer_unique_id(...)`. Check that `infrahub_sync/adapters/peeringmanager.py` inherits both methods and needs no loop of its own; remove any override that duplicates them.
- [ ] T020 [P] Change `infrahub_sync/adapters/prometheus.py`: `list_records` from `model_loader` (`:466-511`), keeping the `lookup` injection inside `list_records`; `record_to_data` from `obj_to_diffsync` (`:513`).
- [ ] T021 [P] Change `infrahub_sync/adapters/slurpitsync.py`: `list_records` from `model_loader` (`:231-281`), `record_to_data` from `slurpit_obj_to_diffsync` (`:288`), reference resolution (`:308`) through `self.peer_unique_id(...)`; keep the `load()` override that closes the client (`:104-109`).
- [ ] T022 [P] Change `infrahub_sync/adapters/ipfabricsync.py`: `list_records` from `model_loader` (`:76-105`), `record_to_data` from `ipfabric_dict_to_diffsync` (`:107`). Records now go through `load_record`, which calls `add`, so a duplicate becomes `duplicate_source_identity` instead of overwriting the first record through `update_or_add_model_instance`.
- [ ] T023 Change `infrahub_sync/adapters/infrahub.py`: keep its own `model_loader` (`:825-854`) and call `self.load_record(model, element, record, after_add=...)` for each record, with `after_add` filling the SDK store; keep peer hydration (`:956-957`) on `update_or_add_model_instance`; delete the `continue_on_error` record-skipping branch (`:839-850`); keep `PeerIdentifierError` raising (`:930-951`) without the `continue_on_error` skip.
- [ ] T024 Route the engine's other model builds through `load_record` with an identity conversion: the snapshot rows in `infrahub_sync/potenda/__init__.py` (`:442-444`) and the changed rows in `infrahub_sync/cache/incremental.py` (`:219-220`). Depends on T015.
- [ ] T025 Remove `continue_on_error` from `infrahub_sync/potenda/__init__.py` (`:256-262`, `:309-312`), `infrahub_sync/execution.py` (`:108`, `:1133`, `:1232`) and `infrahub_sync/utils.py` (`:208`, `:313`). Depends on T017 and T023.
- [ ] T026 Change `_load_side` in `infrahub_sync/potenda/__init__.py` (`:488-501`) so that a load that records problems in the load report does not raise; keep both adapters' load reports on the `Potenda` instance (`self.source_load_report`, `self.destination_load_report`) for the problem pass. A `RecordLoadError` still fails the load.
- [ ] T027 [P] Update `docs/docs/development/knowledge/adapter-anatomy.md`: an adapter implements `list_records` and `record_to_data`; the base class owns the loop, the filter bookkeeping and the admission step; `peer_unique_id` resolves references; an adapter that overrides `model_loader` opts out of admission (the Infrahub adapter calls `load_record` itself).
- [ ] T028 [P] Update `docs/docs/development/guidelines/writing-an-adapter.md` with the same contract as rules: implement `list_records` and `record_to_data`, resolve references with `peer_unique_id`, do not call `self.add(model(**data))` in an adapter; and remove `continue_on_error` from `docs/docs/development/knowledge/execution-surface.md`.
- [ ] T029 Run `tests/plan/test_loader_plan_parity.py` (T002) against the changed code and confirm every valid fixture plans to the same canonical operations as the baseline. Any difference is a defect of T015 to T026, not a reason to update the baseline.
- [ ] T030 Add the changelog fragments `changelog/+009-record-admission.changed.md` (the base class owns the per-record load loop; four adapters list a reference to a peer that is not in the source instead of failing or dropping it; IP Fabric reports duplicates) and `changelog/+009-continue-on-error.removed.md` (the `continue_on_error` parameter is removed from the engine and adapters), each created with `uv run --extra dev towncrier create -c "<text>" <name>`.

**Checkpoint**: every bundled adapter loads through `load_record`; `continue_on_error` is gone; plans of valid data are unchanged.

---

## Phase 3: User Story 1 - Review every bad source record in one plan (Priority: P1), MVP

**Goal**: one plan lists every problem of the seven types, leaves out records with problems
and their dependents through required relationships, keeps records that refer to them through
optional relationships, saves all of it in plan format 4, and returns it through the Sync API
and the CLI. Ships only after sign-off of ADR 0020 and the AD-12 amendment (FR-029).

**Independent Test**: plan the fixture of T001. The plan finishes, lists seven problems, leaves
out those records and their dependents, keeps the IP addresses without their relationship, and
writes nothing to Infrahub (spec User Story 1, quickstart scenarios 1, 1b and 5b).

### Tests for User Story 1 (write first, confirm they fail)

- [ ] T031 [P] [US1] Create `tests/plan/test_problem_pass.py`: planning the T001 fixture gives exactly one problem per type with `problem_type`, `record` (kind, identity, `source_id`), `field`, `value`, `allowed` (Dropdown only), `peer` (peer types), `other_records` (at least one for the identity types) and a `reason` of at most 512 characters rendered from the fixed template; a record with two problems has two entries and counts once in `left_out`; a fixture with no problems gives zero `left_out` and an empty list (scenario 6); after the fixture data is fixed, the next plan contains the formerly left-out records as creates or updates (FR-009, scenario 5); the recording destination receives no write during the plan (FR-004).
- [ ] T032 [P] [US1] Create `tests/plan/test_dependency_walk.py`: the device with a problem and its five interfaces are left out, each interface has `cause: depends_on_left_out`, `depends_on` the device and `relationship: device`, and the summary counts six (scenario 2); a three-level chain leaves out every record, each naming its direct dependency; a reference that is a component of the destination human-friendly ID leaves the record out even when optional; the IP address with an optional reference to the left-out device is kept with `cause: peer_left_out` (scenario 7); a record whose only problem is `peer_not_found_in_source` through an optional relationship is listed as a problem and as kept with `cause: peer_not_found_in_source`, is not counted in `left_out`, and the walk does not start from it (scenario 8); a record whose referenced peer was not loaded and has no identity is left out even through an optional relationship.
- [ ] T033 [P] [US1] Create `tests/plan/test_select_operations.py` for `select_operations(operations, report)`: removes every create and update of a left-out record; removes every delete whose identity is a load-report record or a left-out record (a left-out record that exists in Infrahub stays unchanged); on a create of a kept record the relationship is absent and, for cardinality many, the other peers stay; on an update the relationship is removed from the operation, for cardinality many the whole relationship; an update whose only change was that relationship is removed; no placeholder remains in the result; the function does not mutate its inputs.
- [ ] T034 [P] [US1] Create `tests/plan/test_stores_unchanged.py`: a canonical dump of the source and destination stores taken before `diff_from` equals the dump taken after `select_operations`; the IP address that refers to the left-out device holds that reference in the source store while its plan operation does not; no runtime model built by `infrahub_sync/runtime_schema/models.py` declares `_children`; a static check over `infrahub_sync/potenda/__init__.py` finds `diff_from` and no call to `sync_from`, `sync_to` or `diff_to`.
- [ ] T035 [P] [US1] Create `tests/plan/test_plan_format_4.py`: the writer writes `operations.jsonl`, then `report.jsonl`, then `manifest.json`, each through a temporary name; `manifest.format_version` is 4 and `manifest.report` holds `digest`, `size`, `problems`, `left_out`, `left_out_with_problem`, `left_out_dependent`, `kept`; an empty report is a zero-byte file with zero counts; report lines follow the stable order of data-model.md; the plan checksum (computed with `infrahub_sync/plan/checksum.py`) changes when one report byte changes; verification refuses a missing `report.jsonl`, a digest or size mismatch and a line-count mismatch with `PlanVerificationError`; the reader accepts formats 2, 3 and 4 (3 with an empty report); the writer refuses an operation that still holds a left-out placeholder.
- [ ] T036 [P] [US1] Create `tests/runtime_schema/test_dropdown_choices.py`: `_attribute_shape` in `infrahub_sync/configuration/capabilities.py` adds `choices` for a Dropdown attribute; `NormalizedAttribute.choices` is set for Dropdown and `None` otherwise; the consumed-schema fingerprint from `infrahub_sync/runtime_schema/projection.py` changes when a mapped Dropdown's choices change and does not change for an unmapped one.
- [ ] T037 [P] [US1] Create `tests/plan/test_identity_groups.py`: the group-returning halves of `refuse_destination_identity_collisions` and `refuse_source_identity_collisions` (`infrahub_sync/plan/keying.py`) return every group; the raising halves raise on the same input; `_warn_identity_finer_than_destination_key` (`infrahub_sync/plan/derive.py`) returns its merge groups; every record of a group is left out; `warn_missing_convergence_key` on the selected operations finds nothing. Update `tests/plan/test_source_identity_collision.py` and `tests/test_uniqueness_refusal_summary.py` where they assert that a plan is refused.
- [ ] T038 [P] [US1] Create `tests/adapters/test_peer_not_found_in_source.py` (SC-010): parametrized over NetBox, Nautobot, ACI, generic REST and Slurp'it with recorded API responses, a device that refers to a site the source does not return through a required relationship and an IP address that refers to a VRF the source does not return through `vrf`: the plan finishes, lists two `peer_not_found_in_source` problems with kind, identity, relationship and peer source ID, leaves out the device, keeps the IP address, and its operation holds no `vrf` change.
- [ ] T039 [P] [US1] Create `tests/plan/test_report_credentials_absent.py` (SC-005, SC-007 for plan files): with the two fixture credentials of T001 configured as referenced credentials and also placed in the text of every exception the fixture adapter raises, neither value and no exception text appears in any problem reason, left-out or kept entry, `report.jsonl`, `operations.jsonl` or `manifest.json`. The test asserts absence on raw bytes and does not depend on value replacement.
- [ ] T040 [P] [US1] Create `tests/service/test_plan_report_routes.py`: `GET /runs/{run_id}/plan/problems`, `/plan/left-out` and `/plan/kept` return `run_id`, `plan_checksum`, `entries`, `offset`, `limit`, `total`, `next_offset`; `limit` outside 1 to 500 is 422; pages hold each entry once in the stable order; `problem_type` and `kind` filters; an unknown `problem_type` is 422; `expected_checksum` that differs is 409 `plan-changed`; a format 2 or 3 plan returns empty lists; 404 `run-not-found`, 410 `artifact-expired`, 503 `plan-unavailable`; the routes require the bearer token of the run routes. `GET /runs/{run_id}/plan` returns the summary counts of data-model.md "Plan summary additions" with `left_out_with_problem + left_out_dependent = left_out` and `left_out_by_kind` summing to `left_out`.
- [ ] T041 [P] [US1] Extend `tests/client/test_models.py` and `tests/client/test_client.py`: the report entry resources and summary fields parse the contract examples of `contracts/sync-api.md`; the client reads every page of a report route.
- [ ] T042 [P] [US1] Extend `tests/cli/test_client_commands.py`: `diff` and `runs plan` print the three lines of `contracts/cli.md` "Plan summary"; `diff` prints the first 20 problems and the command that lists the rest; `runs problems [--type] [--kind]`, `runs left-out [--kind]` and `runs kept [--kind]` read every page and print one line per entry in the format of `contracts/cli.md`.

### Implementation for User Story 1

- [ ] T043 [P] [US1] Add `choices: tuple[str, ...] | None` to `NormalizedAttribute` in `infrahub_sync/runtime_schema/domain.py`, add `"choices": [names]` for a Dropdown attribute in `_attribute_shape` in `infrahub_sync/configuration/capabilities.py` (`:344`), and add `choices` for a mapped Dropdown attribute in `_member_semantics` in `infrahub_sync/runtime_schema/projection.py` (`:38`).
- [ ] T044 [P] [US1] Split `refuse_destination_identity_collisions` (`infrahub_sync/plan/keying.py:523-557`) and `refuse_source_identity_collisions` (`:67-122`) into a function that returns the groups and a function that raises when the groups are not empty; keep the raising names as the public callers use them.
- [ ] T045 [US1] Change `infrahub_sync/plan/derive.py`: `_warn_identity_finer_than_destination_key` (`:791-837`) returns its merge groups; a reference to a record of the load report or to a peer marked "not in the source" resolves to a placeholder pair marked left out instead of raising `SourcePeerUnresolvedError` (`:272-338`, next to the existing `allow_missing`); `warn_missing_convergence_key` (`:888`) runs on the operations it is given and stays unchanged otherwise. Depends on T044.
- [ ] T046 [US1] Add the plan report models to `infrahub_sync/plan/models.py`: `PlanProblem`, `LeftOutRecord`, `KeptRecord` and `PlanReport` with the fields and rules of data-model.md (reason at most 512 characters, values limited to 256 characters), the seven problem type names as a `StrEnum` or `Literal` constant, `PlanReportCounts` for the manifest `report` object, and `format_version` 4 on the manifest model.
- [ ] T047 [US1] Create `infrahub_sync/plan/problems.py`: `find_problems(source_load_report, destination_load_report, source_store, schema_record, operations) -> PlanReport` (load-report problems, the Dropdown check of research.md R6 over every mapped Dropdown value in the source store with `None` on an optional attribute not checked, the identity group checks of T044 and T045, reasons rendered from one fixed template per problem type over typed fields only); the dependency walk of research.md R7 point 2; and the pure `select_operations(operations, report)` of research.md R7 point 3. No function in this module reads a connection setting or an exception's text. Depends on T045 and T046.
- [ ] T048 [US1] Add the errors the plan stage needs to `infrahub_sync/plan/errors.py`: a writer refusal for an operation that still holds a left-out placeholder, and the verification failures for the report (missing file, digest, size and count mismatch) as `PlanVerificationError` subclasses.
- [ ] T049 [US1] Change `infrahub_sync/plan/writer.py` to write `report.jsonl` in the canonical encoding of ADR 0001 between `operations.jsonl` and `manifest.json`, with the manifest `report` object and `format_version` 4, and to refuse a placeholder (T048). Change `infrahub_sync/plan/reader.py` to read formats 2, 3 and 4 (an empty report for 2 and 3) and to return report pages by offset, limit, `problem_type` and `kind`. Change `infrahub_sync/plan/verify.py` to check the report's presence, digest, size and line counts. Depends on T046 and T048.
- [ ] T050 [US1] Integrate the problem pass in `_write_plan_artifact` in `infrahub_sync/potenda/__init__.py`: after `operations_from_diff` and `derive_deletes` (`:655-680`), call `find_problems`, then `select_operations`, then `warn_missing_convergence_key` on the selected operations, then the writer with the report. Do not change the stores, the diff returned by `diff_from` (`:553`) or the arguments passed to it. Depends on T026, T047 and T049.
- [ ] T051 [US1] Add the summary counts of data-model.md "Plan summary additions" to `PlanSummaryResource` in `infrahub_sync/client/models.py` (`:315`), add the response model for the three report routes and its entry types, and fill the counts from the manifest `report` object where the plan summary is built (`infrahub_sync/plan/review.py` and `get_plan` in `infrahub_sync/service/service.py`); export the new names from `infrahub_sync/service/models.py` and `infrahub_sync/client/__init__.py`.
- [ ] T052 [US1] Add `GET /runs/{run_id}/plan/problems`, `/plan/left-out` and `/plan/kept` to `infrahub_sync/service/app.py` with the authentication of the existing run routes, and their handlers in `infrahub_sync/service/service.py` reading pages through `infrahub_sync/plan/reader.py`, with the errors of `contracts/sync-api.md`. Depends on T049 and T051.
- [ ] T053 [US1] Add paged report reads (`plan_problems`, `plan_left_out`, `plan_kept`, each reading every page) to `infrahub_sync/client/client.py`. Depends on T051.
- [ ] T054 [US1] Add `runs problems`, `runs left-out` and `runs kept` to `infrahub_sync/cli.py`, and the summary lines and first-20 problem list to `diff` and `runs plan`, in the formats of `contracts/cli.md`. Depends on T053.
- [ ] T055 [US1] Update `docs/docs/development/knowledge/plan-artifact.md` for plan format 4 (`report.jsonl`, the manifest `report` object, read and apply rules) and the plan steps of research.md R2, linking ADR 0022.
- [ ] T056 [US1] Add the changelog fragment `changelog/+009-plan-problems.added.md`: one plan lists every problem of seven named types, leaves out records with problems and their dependents, keeps records that refer to them only through an optional relationship, and saves the list in plan format 4; plans saved by earlier releases must be planned again before apply.

**Checkpoint**: User Story 1 works on its own: `infrahub-sync diff` on the fixture lists seven problems and the left-out and kept records, and Infrahub receives no write.

---

## Phase 4: User Story 2 - Confirm the left-out count and apply the rest (Priority: P1)

**Goal**: apply requires the confirmed left-out count in the CLI and the Sync API; a `sync`
run whose plan leaves out records stops after the plan.

**Independent Test**: apply the User Story 1 plan once without the count (refused before any
write) and once with it (only the plan's operations are written) (quickstart scenario 2).

### Tests for User Story 2 (write first, confirm they fail)

- [ ] T057 [P] [US2] Create `tests/service/test_apply_left_out_confirmation.py`: with `left_out` 12, `POST /runs/{run_id}/apply` without `confirmed_left_out_count`, and with 11, returns 409 `left-out-confirmation-required` with `{"expected_left_out_count": 12}` and is audited as `refused-left-out-count`; the checks run in the order of `contracts/sync-api.md` (confirmation, checksum, count, new plan required, format); with 12 the apply writes only the plan's operations and none for left-out records; with `left_out` 0 the field may be absent or 0; a format 3 plan returns 409 `plan-format-not-applicable`; the recording destination receives no write before a refusal.
- [ ] T058 [P] [US2] Create `tests/plan/test_apply_left_out_count.py`: `Potenda` apply compares the confirmed count with `manifest.report.left_out` before the first ownership proof, and refuses a format 3 plan with `PlanFormatApplyUnsupportedError` and the instruction to plan again (`infrahub_sync/potenda/__init__.py:148-163`).
- [ ] T059 [P] [US2] Create `tests/service/test_sync_left_out_stop.py`: `POST /runs` with `operation: "sync"` on the T001 fixture ends with phase `planned`, outcome `left-out-review-required`, `failure.failure_type` `left_out_review_required`, `failure.stage` `sync`, and no write admitted; applying it with the right count writes the operations; no request field skips the stop; a `sync` with `left_out` 0 applies as today.
- [ ] T060 [P] [US2] Extend `tests/cli/test_client_commands.py`: `apply` without `--confirm-left-out`, or with a wrong count, exits 1 before any write and prints `apply refused: this plan leaves out 12 records; confirm with --confirm-left-out 12`; with the right count it succeeds; a plan with no records left out applies without the option; `runs show` on a completed apply prints the operations written and the left-out count (scenario 5).

### Implementation for User Story 2

- [ ] T061 [US2] Add `confirmed_left_out_count: int | None = Field(default=None, ge=0)` to `ApplyRunRequest` in `infrahub_sync/client/models.py` (`:77`), and the counts of operations written and records left out to the run resource that `runs show` reads.
- [ ] T062 [US2] Add the left-out count check and the format check to the apply path in `infrahub_sync/service/service.py` (after the checks at `:315` and `:317-330`), with the 409 codes, the error body and the audit outcome of `contracts/sync-api.md`. Depends on T061.
- [ ] T063 [US2] Add the count comparison before the first ownership proof and the format 4 only rule to the apply path in `infrahub_sync/potenda/__init__.py`, passing the confirmed count from the service through the execution request in `infrahub_sync/execution.py`. Depends on T061.
- [ ] T064 [US2] Stop a `sync` run after its plan when `left_out` is greater than zero in `infrahub_sync/service/flow.py`, recording phase `planned` and outcome `left-out-review-required` in the run record through `infrahub_sync/product_store/store.py`, so that the run can be applied with `POST /runs/{run_id}/apply`. Depends on T050.
- [ ] T065 [US2] Add `--confirm-left-out <count>` to `apply` in `infrahub_sync/cli.py` (`:683`), pass it as `confirmed_left_out_count`, and print the refusal message of `contracts/cli.md`; print the written and left-out counts in `runs show`. Depends on T061.
- [ ] T066 [US2] Add the changelog fragment `changelog/+009-left-out-confirmation.added.md`: apply of a plan that leaves out records needs `--confirm-left-out` (CLI) or `confirmed_left_out_count` (Sync API); a `sync` run whose plan leaves out records stops after the plan.

**Checkpoint**: User Stories 1 and 2 work together: the fixture plan is refused without the count and applies with it.

---

## Phase 5: User Story 3 - View why a run failed or stopped (Priority: P1)

**Goal**: every failed or stopped run records a structured cause built by one module from
allowlisted fields only, and the CLI and the Sync API return it. Ships only after sign-off of
ADR 0021 (FR-030).

**Independent Test**: one test per failure type checks that the CLI output and the Sync API run
record contain the cause fields of FR-019 and no other error text (quickstart scenarios 3 and 5).

### Tests for User Story 3 (write first, confirm they fail)

- [ ] T067 [P] [US3] Create `tests/service/test_failure_cause.py`: `build_failure_cause` classifies by exception type in the cause chain, one parametrized case per row of research.md R13: `GraphQLError` during apply gives `destination_rejected_write` with `error_code`, `node_kind`, `attribute` from the first error's `extensions` when they are strings (at most 128 characters) and `infrahub_message` (control characters removed, at most 1,024 characters), and without a code when Infrahub returns none (scenario 5); `requests.ConnectionError`, `httpx.ConnectError` and `ServerNotReachableError` give `system_unreachable`; `requests.Timeout`, `httpx.TimeoutException` and `ServerNotResponsiveError` give `system_timeout`; HTTP 401 and 403 from `requests`, `httpx` and `pynetbox.RequestError`, and Infrahub `AuthenticationError`, give `token_rejected`; `ssl.SSLCertVerificationError` gives `tls_certificate_rejected`; `RecordLoadError` gives `record_load_failed` and `TransformError` gives `transform_failed`, both with `node_kind`, `record` and `field`; the existing typed errors give `verification_failed`, `apply_refused`, `credential_missing`; anything else gives `internal_error` with the class name. `system` comes from `SideLoadError.side` and `.adapter`. `summary` and `next_action` come from fixed templates. Each case runs with the mutation "use `str(exc)` for `summary`" applied once to confirm the test fails.
- [ ] T068 [P] [US3] Create `tests/service/test_failure_cause_credentials.py` (SC-005, SC-007, scenario 6): for every failure type of T067, raise the exception with text that holds the two fixture credentials of T001, an address with a user name and password, a request header and a traceback; neither value and no part of that text appears in the stored cause, in `GET /runs/{run_id}` or in the CLI output of `runs show`. Assert absence on raw bytes; do not depend on value replacement.
- [ ] T069 [P] [US3] Create `tests/service/test_run_failure_resource.py`: `GET /runs/{run_id}` returns `run.failure` with the fields of data-model.md "Run failure cause" for a failed run and `null` for a successful one; the existing `results.{stage}_failure` keys (`stage`, `outcome`, `error_type`, `cause_type`, `applied_operations`, `failed_operation`, `failed_operation_wrote`, `may_have_partially_written`) are unchanged.
- [ ] T070 [P] [US3] Create `tests/test_transform_error.py`: `apply_transform` raises `TransformError` (a `ValueError` subclass) with the field and the record's `local_id`; `_load_side` raises `SideLoadError` (a `ValueError` subclass) with `side` and `adapter`; the handlers of ADR 0005 still match both.
- [ ] T071 [P] [US3] Extend `tests/cli/test_client_commands.py`: `diff`, `sync`, `apply` and `runs show` print the cause in the formats of `contracts/cli.md` "Cause of a failed or stopped run" instead of `<stage> failed: <ClassName>`; the CLI prints only fields of `failure`; the exit code stays 1.

### Implementation for User Story 3

- [ ] T072 [P] [US3] Add `TransformError(ValueError)` to `infrahub_sync/__init__.py` with `field` and `source_id` attributes, raised by `apply_transform` (`:436-441`) instead of a bare `ValueError`; its message text is not used by any cause.
- [ ] T073 [P] [US3] Add `SideLoadError(ValueError)` with `side` and `adapter` attributes, raised by `_load_side` in `infrahub_sync/potenda/__init__.py` (`:488-501`) instead of a bare `ValueError`, keeping the original exception as `__cause__`.
- [ ] T074 [US3] Create `infrahub_sync/failure_cause.py`: the typed `RunFailureCause` and `ApplyProgress` models of data-model.md and `build_failure_cause(stage, exc, record=None, apply_record=None, plan=None) -> RunFailureCause`, which walks the cause chain by type only, applies the length limits, removes control characters from `infrahub_message`, and never reads `str(exc)`, `exc.args`, an address, a header or a traceback. Depends on T072 and T073.
- [ ] T075 [US3] Change `_failure_evidence` in `infrahub_sync/service/flow.py` (`:697-736`) to call `build_failure_cause` and store the result next to the existing `{stage}_failure` keys, and rewrite the comment at `:722-725` to state the rule of ADR 0021. No other code turns an exception into text that leaves the worker. Depends on T074.
- [ ] T076 [US3] Add `failure: RunFailureCause | None` to `PublicRunResource` in `infrahub_sync/client/models.py` (`:173`) and return the stored cause from `get_run` in `infrahub_sync/service/service.py`. Depends on T074.
- [ ] T077 [US3] Replace the `{stage} failed: {error_type}` output in `infrahub_sync/cli.py` (`:730-745`) and add cause output to `runs show` (`:759`), printing only the fields of `failure` in the formats of `contracts/cli.md`. Depends on T076.
- [ ] T078 [US3] Rewrite `docs/docs/development/guidelines/secret-redaction.md`: the prevention rule (credential values are resolved only into adapter and SDK settings and never formatted into messages; a cause, a problem reason and a plan file are built from typed fields only); `build_failure_cause` as the one layer, called only from `_failure_evidence`; the existing replacement (`collect_secret_values`, `redact`, `sanitize_exception_chain`, the passes of `publish_artifact`) stays unchanged and no longer protects the run record; the remaining gaps (worker and orchestration logs can hold a credential shorter than six characters; SYNC-262) as follow-up work after the MVP. Link ADR 0021.
- [ ] T079 [US3] Add the changelog fragment `changelog/+009-failure-cause.added.md`: a failed or stopped run records a structured cause (failure type, system, Infrahub's error code, node kind, attribute and message for a rejected write) that `GET /runs/{run_id}` returns as `failure` and the CLI prints.

**Checkpoint**: User Story 3 works on its own: each failure type shows its cause in `runs show` and `GET /runs/{run_id}`, with no error text and no credential.

---

## Phase 6: User Story 4 - Recover a stopped apply by planning again (Priority: P1)

**Goal**: a stopped apply shows what it wrote and which operation failed; its plan cannot be
applied again; a new plan contains only the remaining operations.

**Independent Test**: stop an apply of N operations at operation K, then plan again with no
source change: the new plan has N - (K - 1) operations, less any the destination now matches
(quickstart scenario 4).

### Tests for User Story 4 (write first, confirm they fail)

- [ ] T080 [P] [US4] Create `tests/service/test_stopped_apply.py`: a destination fake that rejects operation K gives `failure.apply_progress` with `operations_total` N, `operations_written` K - 1, `failed_operation` with `operation_id`, `action`, `kind` and `identity` read from the plan by its ID, and `failed_operation_may_have_written` from `ApplyRecord.failed_operation_wrote`; `GET /runs/{run_id}/results` lists the written IDs under `apply_failure`; a second apply of the same run returns 409 `new-plan-required` with the message of `contracts/sync-api.md`; the CLI prints the same message.
- [ ] T081 [P] [US4] Create `tests/plan/test_replan_after_stopped_apply.py`: with the same fake and an in-memory destination that keeps the K - 1 writes, a new plan with no source change has N - (K - 1) operations, holds the failed operation and the operations not attempted, and holds none for the nodes already written (SC-004).

### Implementation for User Story 4

- [ ] T082 [US4] Fill `apply_progress` in `build_failure_cause` (`infrahub_sync/failure_cause.py`) from `ApplyRecord` (`infrahub_sync/plan/models.py:497-583`) and the failing operation's kind and identity read from the saved plan by operation ID. Depends on T074.
- [ ] T083 [US4] Give the refusal of `_reservation_refused` (`infrahub_sync/product_store/store.py:1827`) the error code `new-plan-required`, map it to 409 with the contract message in `infrahub_sync/service/service.py`, and print it in `apply` in `infrahub_sync/cli.py`.
- [ ] T084 [US4] Add the changelog fragment `changelog/+009-new-plan-required.changed.md`: a stopped apply shows the operations written and the operation that failed; applying its plan again is refused with `new-plan-required`.

**Checkpoint**: all four user stories work on their own.

---

## Phase 7: Problems found before a plan with `configs validate` (FR-032 to FR-035)

**Goal**: `configs validate --branch` runs the destination schema check (Dropdown literals
included) and reports credentials the worker does not hold; validation and the plan agree on
keyability; registration responses do not show redacted content.

**Independent Test**: quickstart scenario 7 (SC-009).

### Tests (write first, confirm they fail)

- [ ] T085 [P] Extend `tests/service/test_config_routes.py`: `POST /configs/{config_id}/versions/{registry_version}/validate` without `branch` is unchanged; with `branch` it adds `destination-schema-mismatch`, `destination-dropdown-value-not-in-choices`, `credential-not-set-in-worker` naming the variable and never a value, and the warning `worker-environment-unknown` when no live worker has a record.
- [ ] T086 [P] Create `tests/service/test_worker_environment.py`: the worker publishes the names of set and non-empty `INFRAHUB_SYNC_CREDENTIAL_*` variables (an empty value counts as unset) at start and on each poll; the record holds no value (assert the value bytes are absent from the stored row); the most recent record of a live worker is the one compared.
- [ ] T087 [P] Extend `tests/configuration/test_schema_validation.py`: a literal `static` value of a mapped Dropdown attribute outside the choices gives `destination-dropdown-value-not-in-choices`.
- [ ] T088 [P] Extend `tests/test_writable_convergence_identity.py` with a parametrized test that runs the plan's create check (`refuse_unkeyed_create`) and validation's check (`writable_convergence_reason`) over the same kinds and asserts they agree (FR-034).
- [ ] T089 [P] Extend `tests/product_store/test_configs_service.py`: the responses of `configs register` and `configs version` return `package_checksum` and no `declared_content` (FR-035).

### Implementation

- [ ] T090 Add the `worker_environment` record (`worker_id`, `credential_names`, `observed_at`) to `infrahub_sync/product_store/store.py` with a write method and a read method for the most recent record of a live worker.
- [ ] T091 Create `infrahub_sync/service/worker_environment.py`: collect the names of set, non-empty `INFRAHUB_SYNC_CREDENTIAL_*` variables using the prefix of `infrahub_sync/configuration/credentials.py:29`, and publish them through T090. Depends on T090.
- [ ] T092 DEP-013 Call the publication of T091 in `main()` and on each poll in `infrahub_sync/service/worker.py`. 009 owns this file; spec 013 task T010 adds one `trust_store` import and one call at the top of `main()`. Whichever branch merges second rebases; keep 013's call first in `main()`. Depends on T091.
- [ ] T093 [P] Add the Dropdown literal check over `static` values to `infrahub_sync/configuration/schema_validation.py` using the choices of T043, and move the schema-only part of `refuse_unkeyed_create` (`infrahub_sync/plan/keying.py:397`) and `writable_convergence_reason` (`:181`) into one shared function in `infrahub_sync/plan/keying.py` that both call.
- [ ] T094 Accept an optional `branch` query parameter on the validate route in `infrahub_sync/service/config_routes.py` (`:368-369`), pass `DestinationSchemaOptions(branch=...)` to `configs.validate` in `infrahub_sync/product_store/configs.py` (`:863-891`), and compare the package's `$credential` references with the worker environment record. Depends on T090 and T093.
- [ ] T095 [P] Remove `declared_content` from the `configs register` and `configs version` responses in `infrahub_sync/product_store/configs.py` and the matching resource in `infrahub_sync/client/models.py`.
- [ ] T096 Add `--branch <name>` to `configs validate` in `infrahub_sync/cli.py` and pass it through `infrahub_sync/client/client.py`. Depends on T094.
- [ ] T097 Add the changelog fragment `changelog/+009-configs-validate-branch.added.md`: `configs validate --branch` checks the destination schema, Dropdown literals and the credentials the worker holds; registration responses no longer show `declared_content`.

---

## Phase 8: The NetBox example on the pinned `demo` dataset (FR-031)

**Goal**: the example plans the `demo` dataset with no run failure; the three #374 cases are
written or listed as problems (research.md R16).

- [ ] T098 [P] Create `tests/test_netbox_example_device_status.py`: an offline test that loads `examples/netbox_to_infrahub/package.yml` and asserts that both `DcimDevice` entries carry the `device_status` transform mapping `planned` and `staged` to `provisioning`, `offline` and `failed` to `maintenance`, `inventory` to `reserved`, `decommissioning` to `deprecated`, and that every result is one of the five destination choices.
- [ ] T099 Add the `device_status` transform to both `DcimDevice` entries of `examples/netbox_to_infrahub/package.yml`.
- [ ] T100 Update `examples/netbox_to_infrahub/README.md`: the status translation table; a rack position such as `2.5` is listed as `wrong_value_type`; two devices with the same name at two sites are listed as `destination_identity_collision`.
- [ ] T101 Run the `from-netbox` example check of `docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check` (live tier) and quickstart scenario 6: no run failure, statuses translated, the two problems listed; then plant the five problems of #369 and confirm one plan lists all five (SC-001, SC-008).
- [ ] T102 Add the changelog fragment `changelog/+009-netbox-example-device-status.fixed.md`: the NetBox example translates device statuses outside the five Infrahub choices, and the pinned `demo` dataset plans without a run failure.

---

## Phase 9: Polish and cross-cutting concerns

- [ ] T103 [P] Update `docs/docs/running-a-sync.mdx` (owned by 009; spec 013 task T075 changes only the Prefect UI port sentence near line 235): reviewing problems, left-out and kept records with `runs problems`, `runs left-out`, `runs kept`; confirming the count with `--confirm-left-out`; a `sync` run that stops after its plan; reading a run's cause; planning again after a stopped apply; `configs validate --branch`.
- [ ] T104 DEP-013 Add the section "A run failed: read its cause" at the end of `docs/docs/operations/compose-troubleshooting.mdx` (owned by spec 013; 013 tasks T031, T045 and T054 change existing rows and add table rows). Rebase onto 013's changes to that file before merging; change nothing above the new section.
- [ ] T105 [P] Update `docs/docs/reference/sync-http-api.mdx` with the three report routes, the summary counts, `run.failure`, `confirmed_left_out_count`, the 409 codes `left-out-confirmation-required`, `new-plan-required` and `plan-format-not-applicable`, the `sync` stop, the validate `branch` parameter and its finding codes, and the registration response change, following `contracts/sync-api.md`.
- [ ] T106 [P] Update `docs/docs/development/knowledge/repository-tour.md` with the new modules `infrahub_sync/failure_cause.py`, `infrahub_sync/plan/problems.py` and `infrahub_sync/service/worker_environment.py`.
- [ ] T107 Regenerate `docs/docs/reference/cli.mdx` with `uv run invoke docs.generate`, then build the site with `uv run invoke docs.docusaurus` and lint Markdown with `uv run rumdl check .`.
- [ ] T108 Run the required workflow of `AGENTS.md`: `uv run invoke format`, `uv run invoke lint` (rumdl, ruff, pylint, yamllint, ty; no new `[[tool.ty.overrides]]`), `uv run invoke tests.tests-unit`, and the CLI checks `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help`, `uv run infrahub-sync runs --help`.
- [ ] T109 Run quickstart scenarios 1 to 5b of `dev/specs/009-plan-problem-reporting/quickstart.md` against the development stack, and record in the pull request which scenarios ran on the live tier.
- [ ] T110 SIGN-OFF gate: before merge, confirm in the pull request that the product owner signed off ADR 0020 with the AD-12 amendment (T003, T007) and ADR 0021 (T004); then set the Status of `dev/adr/0020-records-with-problems-are-left-out-of-a-plan.md`, `dev/adr/0021-a-run-failure-record-holds-a-structured-cause.md` and `dev/adr/0022-saved-plan-format-4-records-left-out-records.md` to "Accepted" with the date, and fill the sign-off line of `dev/specs/009-plan-problem-reporting/ad-12-amendment.md`. Without sign-off, Phases 3 and 5 do not merge.

---

## Dependencies and execution order

### Phase dependencies

- Phase 1 (Setup): no dependency. T002 must run before any change of Phase 2.
- Phase 2 (Foundational): depends on T001 and T002. Blocks every story phase.
- Phase 3 (US1): depends on Phase 2. Merge needs sign-off of ADR 0020 and the AD-12 amendment.
- Phase 4 (US2): depends on Phase 3 (the left-out count is in the format 4 manifest, T049, T050).
- Phase 5 (US3): depends on Phase 2 only (T073 changes `_load_side` of T026). It can run in parallel with Phases 3 and 4. Merge needs sign-off of ADR 0021.
- Phase 6 (US4): depends on Phase 5 (T074) for `apply_progress`; T083 is independent.
- Phase 7 (`configs validate`): depends on T043 (Dropdown choices, Phase 3) for T093; the other tasks depend on Phase 2 only.
- Phase 8 (NetBox example): T098 to T100 can start any time; T101 needs Phases 3 and 5.
- Phase 9 (Polish): depends on every phase it documents.

### Dependencies on spec 013 (agreed with `tasks-013`, 2026-10-15)

- `infrahub_sync/service/worker.py` (T092): owned by 009. 013 task T010 adds one `trust_store` import and one call at the top of `main()`. The branch that merges second rebases.
- `docs/docs/operations/compose-troubleshooting.mdx` (T104): owned by 013 (tasks T031, T045, T054 change existing rows and add table rows). 009 adds one section at the end only.
- `docs/docs/running-a-sync.mdx` (T103): owned by 009. 013 task T075 changes only the Prefect UI port sentence near line 235.
- `tls_certificate_rejected` (T067, T074): defined by 009. 013 task T049 (`test_custom_ca.py`) asserts it only after 009 merges.
- 009 changes none of these 013 files: the compose files, `Dockerfile`, `infrahub_sync/service/serve.py`, `storage.py`, `bootstrap.py`, `compose-deployment.mdx`, `custom-certificates.mdx`, `operations/day-2-operations.mdx`, `dev/specs/v3-release-scope.md`. No 013 task changes `infrahub_sync/failure_cause.py`, the redaction helpers in `infrahub_sync/execution.py`, `infrahub_sync/configuration/credentials.py` or `docs/docs/development/guidelines/secret-redaction.md`.
- Decision record numbers: 009 uses 0020, 0021, 0022; 013 uses the 0019 amendment and 0023. Fragments: 009 uses `+009-…`, 013 uses `+013-…`.

### Rules passed to spec 010 (not implemented here)

Spec 010 owns incremental reads (research.md R17): a plan that follows a stopped apply of the
same configuration reads the destination in full, and a plan that follows one that left out
records reads the source in full. The new plan steps run inside 010's part "checking the plan
before it is saved", which 010 times.

### Within each story

Tests first, and they fail; models before the problem pass; the problem pass before the writer
integration; service before client before CLI; the changelog fragment last.

## Parallel opportunities

- Phase 1: T003, T004, T005 (three decision records).
- Phase 2: tests T008 to T013 together; after T015, the seven adapter tasks T016 to T022 together (one file each), with T027 and T028.
- Phase 3: tests T031 to T042 together; T043 and T044 together.
- Phase 4: tests T057 to T060 together.
- Phase 5: tests T067 to T071 together; T072 and T073 together. Phase 5 can run alongside Phases 3 and 4 by a second developer.
- Phase 7: tests T085 to T089 together; T093 and T095 together.
- Phase 8: T098 alongside any other phase.

### Parallel example: User Story 1

```text
Task: "T031 problem pass test in tests/plan/test_problem_pass.py"
Task: "T032 dependency walk test in tests/plan/test_dependency_walk.py"
Task: "T033 selection test in tests/plan/test_select_operations.py"
Task: "T034 stores unchanged test in tests/plan/test_stores_unchanged.py"
Task: "T035 plan format 4 test in tests/plan/test_plan_format_4.py"
Task: "T040 report route test in tests/service/test_plan_report_routes.py"
```

### Parallel example: Phase 2 adapters

```text
Task: "T016 netbox list_records/record_to_data in infrahub_sync/adapters/netbox.py"
Task: "T017 nautobot in infrahub_sync/adapters/nautobot.py"
Task: "T018 aci in infrahub_sync/adapters/aci.py"
Task: "T019 generic REST in infrahub_sync/adapters/genericrestapi.py"
Task: "T020 prometheus in infrahub_sync/adapters/prometheus.py"
Task: "T021 slurpit in infrahub_sync/adapters/slurpitsync.py"
Task: "T022 ipfabric in infrahub_sync/adapters/ipfabricsync.py"
```

## Implementation strategy

### First increment

Phases 1 and 2, then Phase 3 (User Story 1). Stop and check quickstart scenarios 1, 1b and 5b:
one plan of the fixture lists seven problems, leaves out the dependents, keeps the optional
referrers, and the stores are unchanged. This increment merges only after sign-off of ADR 0020
and the AD-12 amendment, and together with Phase 2, because Phase 2 alone leaves out records
without listing them.

### Next increments

1. Phase 4 (User Story 2): apply with the confirmed count; the `sync` stop.
2. Phase 5 (User Story 3), after sign-off of ADR 0021; it can be developed in parallel with
   Phases 3 and 4.
3. Phase 6 (User Story 4).
4. Phases 7 and 8.
5. Phase 9, including the sign-off gate T110.

Each increment adds its changelog fragment and passes `uv run invoke format`,
`uv run invoke lint` and `uv run invoke tests.tests-unit` before it merges.
