# Tasks: Plan problem reporting

**Input**: design documents in `dev/specs/019-plan-problem-reporting/`: [spec.md](spec.md),
[plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md).

**Tests**: required. The spec requires tests (FR-022, FR-037, SC-002 to SC-011) and the
constitution's principle V asks for tests per behavior. Write each test first and see it fail.

**Reference code**: branch `009-plan-problem-reporting` holds an older implementation. Read it
with `git -C <worktree> show 009-plan-problem-reporting:<path>` for ideas and tests. Never merge
or cherry-pick it: specs 014, 016, 017 and 018 have changed the code it was written against.

**Every step**: run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .` and
`uv run invoke tests.tests-unit` (through the sandbox; see AGENTS.md). Add a changelog fragment
named `+019-<topic>.<type>.md` under `changelog/` for each user-visible change.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story of spec.md the task belongs to

## Phase 1: Setup

- [ ] T001 Pin today's behavior for the three unverified problem types (research R1): add characterization tests that pass today and show that a duplicate source identity raises `ObjectAlreadyExists`, a `2.5` for a `Number` attribute raises `pydantic.ValidationError` (`int_from_float`), and a Dropdown value outside its choices is accepted by the plan, in `tests/plan/test_problem_baseline.py`; each names the FR whose task will change its expectation (FR-002)
- [ ] T002 [P] Build the shared problem fixture: one record of each of the nine problem types plus valid records and dependents (a device with five interfaces, an IP address with an optional device relationship, a device whose site is missing, an IP address whose VRF is missing), in `tests/data/plan_problems/` with a fixture loader in `tests/plan/conftest.py`
- [ ] T003 [P] Record the plan bytes of the existing valid fixtures before any change, in `tests/plan/test_valid_plan_unchanged.py`, so later phases prove that a plan of valid data keeps its bytes apart from the format version and the empty report (research R3)

## Phase 2: Foundational (blocks every user story)

- [ ] T004 Add the source record reference, plan problem, left-out record and kept record models, with the nine stable problem type names, in `infrahub_sync/plan/models.py` (data-model.md)
- [ ] T005 Add the load report entry model and per-adapter load report in new `infrahub_sync/load_report.py` (research R2; reference `load_report.py` on the old branch)
- [ ] T006 Add `TransformError` (field and transform position, never the value or error text) in `infrahub_sync/__init__.py` and raise it from the transform step (research R7)
- [ ] T007 Add `SideLoadError(role, adapter)` as a subclass of `ValueError` and use it where the plan wraps each side's read failure today, in `infrahub_sync/potenda/__init__.py` (research R12)
- [ ] T008 Add the stage failure record fields and the failure type names to the client models (`failure_type`, `system`, `error_code`, `node_kind`, `attribute`, `infrahub_message`, `record`, `field`, `operations_total`, `failed_operation_detail`) in `infrahub_sync/client/models.py` (data-model.md)

**Checkpoint**: the models exist; no behavior has changed; T003 still passes.

## Phase 3: User Story 1 - Review every bad source record in one plan (Priority: P1)

**Goal**: one plan lists every problem, leaves out records with problems and their dependents,
keeps records through optional relationships, and counts them (FR-001 to FR-003, FR-005 to
FR-009, FR-024, FR-025, FR-027, FR-031, FR-032 for 0020 and 0022).

**Independent test**: plan the T002 fixture; the plan finishes with nine problems, the expected
left-out and kept lists and counts, and no write.

### Tests for User Story 1

- [ ] T009 [P] [US1] Test the base-class read loop and `load_record` for each caught error (wrong type, duplicate identity, excluded required peer, missing required peer, peer not loaded, failed transform), that the read continues with the next record, and that any other error is raised as `RecordLoadError`, in `tests/test_load_record.py`
- [ ] T010 [P] [US1] Test each of the nine problem types, one test per type, against the T002 fixture, in `tests/plan/test_problems.py`
- [ ] T011 [P] [US1] Test the dependency walk at any depth, the two kept cases of FR-005, a record with two problems counted once, and the edge cases of spec.md (both records of a duplicate pair left out; a left-out record that exists in Infrahub gets no update or delete; every record left out), in `tests/plan/test_left_out_walk.py`
- [ ] T012 [P] [US1] Test that the stores and the DiffSync diff are unchanged after the problem pass and the selection step, in `tests/plan/test_stores_unchanged.py`
- [ ] T013 [P] [US1] Test plan format 4: report written in canonical order, digest and counts in the manifest, checksum covers the report, a mismatch is `PlanVerificationError`, an empty report on a plan with no problems, format 3 fields kept (destination IDs, `lineage`, `last_changes.jsonl`), in `tests/plan/test_format_4.py`
- [ ] T014 [P] [US1] Test `peer_not_found_in_source` for each adapter that resolves references while it reads (NetBox, Nautobot, ACI, generic REST): one problem, the plan finishes (SC-010), in `tests/adapters/test_peer_not_found_in_source.py`
- [ ] T015 [P] [US1] Test the three paged list routes: order, filters, `expected_checksum` 409 `plan-changed`, 422 on an unknown type, empty lists on a format 3 plan, in `tests/service/test_plan_report_routes.py`
- [ ] T016 [P] [US1] Test the CLI summary lines, the first 20 problems after `plan`, and `runs plan --problems`, `--left-out`, `--kept` with filters and exclusivity, in `tests/cli/test_plan_problem_output.py`

### Implementation for User Story 1

- [ ] T017 [US1] Implement the default `DiffSyncMixin.model_loader` (mapping lookup, `list_records`, filters with the removed-ID bookkeeping, transforms) and `load_record` (the admission step of research R2) writing to the load report, in `infrahub_sync/__init__.py`; add `ExcludedRequiredPeerError`, `PeerNotLoadedError`, source-side `PeerNotFoundError` and `RecordLoadError`; remove `continue_on_error` from `infrahub_sync/execution.py`, `utils.py`, `potenda/__init__.py`, `adapters/nautobot.py` and `adapters/infrahub.py`
- [ ] T018 [P] [US1] Replace the NetBox `model_loader` and `_records_to_diffsync` with `list_records` and `record_to_data`, moving the filter bookkeeping to the base class, in `infrahub_sync/adapters/netbox.py`
- [ ] T019 [P] [US1] Replace the Nautobot `model_loader` and `_records_to_diffsync` with `list_records` and `record_to_data`, and report the dropped reference as `peer_not_found_in_source`, in `infrahub_sync/adapters/nautobot.py`
- [ ] T020 [P] [US1] Replace the loops of ACI, generic REST and Peering Manager, Prometheus (keep its `lookup` injection in `list_records`), Slurp'it (keep its `load()` override) and IP Fabric (`add` instead of `update_or_add_model_instance`) with `list_records` and `record_to_data`, in `infrahub_sync/adapters/aci.py`, `genericrestapi.py`, `peeringmanager.py`, `prometheus.py`, `slurpitsync.py`, `ipfabricsync.py`
- [ ] T021 [US1] Add the shared peer resolution helper `peer_unique_id` (index by kind and `local_id`; raises `ExcludedRequiredPeerError`, `PeerNotLoadedError` or `PeerNotFoundError` for a required relationship, returns a placeholder and a load report entry for an optional one), in new `infrahub_sync/adapters/references.py`, and use it from the `record_to_data` functions of T018 to T020 (research R5; old 009 research R4)
- [ ] T022 [US1] Call `load_record` from the Infrahub adapter's own `model_loader` with a hook for the SDK store, and detect `destination_identity_ambiguous` on a full read of a kind, in `infrahub_sync/adapters/infrahub.py` (research R2, R6)
- [ ] T023 [US1] Record Dropdown choices in the runtime schema and in the consumed-schema fingerprint, in `infrahub_sync/runtime_schema/` (research R4)
- [ ] T024 [US1] Return collision groups from `infrahub_sync/plan/keying.py` and merge groups from `warn_missing_convergence_key` in `infrahub_sync/plan/derive.py`, instead of raising `DestinationIdentityCollisionError` or only logging (research R6)
- [ ] T025 [US1] Turn `SourcePeerUnresolvedError.absent` into a placeholder reference that the problem pass resolves, in `infrahub_sync/plan/derive.py` and `infrahub_sync/plan/errors.py` (research R5)
- [ ] T026 [US1] Implement the problem pass, the dependency walk and the pure selection step, with reason templates per problem type, and compute no delete for a kind that holds a left-out record without identity or an ambiguous destination identity (FR-008), in new `infrahub_sync/plan/problems.py` (research R3; reference `plan/problems.py` and `plan/left_out_peer.py` on the old branch)
- [ ] T027 [US1] Write and read plan format 4 (`report.jsonl`, manifest `report`, `dropdown_values_checked`), set `PLAN_FORMAT_VERSION = 4` and `SUPPORTED_FORMAT_VERSIONS = {2, 3, 4}`, in `infrahub_sync/plan/models.py`, `writer.py`, `reader.py`, `verify.py` (contracts/plan-artifact-format-4.md)
- [ ] T028 [US1] Run the problem pass and the selection step before the plan is written, in `infrahub_sync/potenda/__init__.py`
- [ ] T029 [US1] Add the summary counts of FR-007 and `deletes_withheld_by_kind` (FR-008, research R21) to the saved plan review and `PlanSummaryResource`, in `infrahub_sync/service/flow.py` (`_review_document`) and `infrahub_sync/client/models.py`
- [ ] T030 [US1] Add `GET /runs/{run_id}/plan/problems`, `/plan/left-out`, `/plan/kept` with paging and filters, in `infrahub_sync/service/app.py` and `infrahub_sync/service/service.py`, and the paged reads in `infrahub_sync/client/client.py` (contracts/sync-api.md)
- [ ] T031 [US1] Print the summary lines, the "deletes not computed" lines and the first 20 problems after `plan`, and add `--problems`, `--left-out`, `--kept` and `--type` to `runs plan`, in `infrahub_sync/cli.py` (contracts/cli.md)
- [ ] T032 [P] [US1] Write `dev/adr/0020-records-with-problems-are-left-out-of-a-plan.md` and `dev/adr/0022-saved-plan-format-4.md` from research R11, R14 and R9, and list them in `dev/adr/README.md`

**Checkpoint**: T009 to T016 pass; T003 passes with only the format version and the empty report changed.

## Phase 4: User Story 2 - Confirm the records left out and apply the rest (Priority: P1)

**Goal**: apply needs the confirmation when records are left out; a `sync` stops after a plan
with left-out records and can be applied once with the confirmation (FR-010 to FR-012, FR-026,
FR-028).

**Independent test**: apply the US1 plan without and with `--confirm-left-out`; start a `sync` on
the fixture and apply the stopped run.

### Tests for User Story 2

- [ ] T033 [US2] Test, before any change, the store's admission rule for a stopped `sync` run (research R11): the stop's writeback releases the run's write admission in the same transaction only when no operation was dispatched; a confirmed apply is then admitted; a second apply, and an apply on a `sync` run that dispatched any operation, are refused with `run-execution-conflict`, in `tests/product_store/test_stopped_sync_admission.py` (reference: the old branch's `tests/service/test_sync_left_out_stop.py`)
- [ ] T034 [P] [US2] Test the apply gate: refusal before any write with the count and audit outcome `refused-left-out-not-confirmed`; confirmation accepted and ignored when nothing is left out; strict boolean (422); a request without the field keeps working; format 3 apply refused with `PlanFormatApplyUnsupportedError` (SC-006), in `tests/service/test_apply_left_out_confirmation.py`
- [ ] T035 [P] [US2] Test the engine's own check for direct callers, in `tests/plan/test_apply_gate.py`
- [ ] T036 [P] [US2] Test the `sync` stop: phase `planned`, outcome `left-out-review-required`, `failure` in the stage result with the run ID, the checksum and `--confirm-left-out` in `next_action`, no destination call, the last log line, in `tests/service/test_sync_left_out_stop.py`
- [ ] T037 [P] [US2] Test the CLI: `--confirm-left-out` sends `confirm_left_out: true`, the refusal line and exit code 1, and the stopped `sync` hint read from `results.failure`, in `tests/cli/test_apply_confirm_left_out.py`
- [ ] T038 [P] [US2] Test the Prefect run artifact lines for left-out and problem counts and the `runs plan --problems` next step, in `tests/service/test_run_artifact.py`
- [ ] T078 [US2] Test that another run that takes the configuration's write admission between a stopped `sync` and its confirmed apply makes that apply wait or be refused, never write alongside it (decision 2 of 2026-11-11), next to T033's test

### Implementation for User Story 2

- [ ] T039 [US2] Add the release of the write admission to the stop's writeback (`release_write_admission`), applied only when no operation was dispatched, in `infrahub_sync/product_store/store.py` and `infrahub_sync/product_store/__init__.py` (research R11; reference the old branch's `service/left_out_stop.py`)
- [ ] T040 [US2] Add `confirm_left_out` to `ApplyRunRequest` (sent only when true) in `infrahub_sync/client/models.py` and `infrahub_sync/client/client.py`
- [ ] T041 [US2] Check `confirm_left_out` after the checksum check and refuse with 409 `left-out-confirmation-required`, in `infrahub_sync/service/service.py` (`apply_run`, around lines 397-420)
- [ ] T042 [US2] Check the confirmation again in the engine's apply and refuse format 2 and 3 plans with `PlanFormatApplyUnsupportedError`, in `infrahub_sync/potenda/__init__.py` and `infrahub_sync/plan/errors.py` (research R9, R10)
- [ ] T043 [US2] Stop the `sync` stage after its plan when `left_out` > 0 and write `result["failure"]`, in `infrahub_sync/service/flow.py` (sync branch, around lines 728-802)
- [ ] T044 [US2] Add `--confirm-left-out` to `apply`, print the refusal, and print the stopped `sync` hint from `results.failure`, in `infrahub_sync/cli.py`
- [ ] T045 [US2] Add left-out and problem counts and the `runs plan` next steps to `run_markdown`, in `infrahub_sync/service/run_artifact.py`

**Checkpoint**: T033 to T038 pass.

## Phase 5: User Story 3 - View why a run failed (Priority: P1)

**Goal**: every recorded failure carries a failure type and the allowlisted cause fields; no
credential or error text enters them (FR-014 to FR-020, FR-029, FR-030, FR-032 for 0021).

**Independent test**: one test per failure type checks the record, the CLI output and the API
response.

This phase does not depend on Phases 3 and 4 and can run in parallel with them after Phase 2.

### Tests for User Story 3

- [ ] T046 [P] [US3] Test `failure_record` for each failure type of data-model.md: a rejected write with and without an Infrahub error code, source and destination unreachable and timeout, source and destination token refused, a 403 permission, a TLS certificate refusal, a lineage refusal, a destination schema read failure, `destination_peer_missing`, `destination_peer_ambiguous`, `record_load_failed` with kind, identity and field, an unknown class as `internal_error`, in `tests/service/test_failure_cause.py`
- [ ] T047 [P] [US3] Test credential absence: each failure type raised with error text that holds a 5-character credential and a credential with `"`, `\` and `,` (and an address with user name and password, a request header), and neither value appears in the failure record, `GET /runs/{run_id}`, `GET /runs/{run_id}/results` or the CLI output (SC-005, SC-007), in `tests/service/test_failure_cause_credentials.py`
- [ ] T048 [P] [US3] Test credential absence in plan files and report entries with the same two credentials referenced by the configuration, in `tests/plan/test_report_credentials_absent.py`
- [ ] T049 [P] [US3] Test the CLI lines between `<stage> failed:` and `hint:` for `plan`, `verify`, `apply`, `sync` and `runs show`, through `_display`, in `tests/cli/test_failure_output.py`
- [ ] T050 [P] [US3] Test that existing keys of `<stage>_failure` keep their values and that a record without the new fields (an older Sync API) still prints as today, in `tests/service/test_failure_record_compat.py`

### Implementation for User Story 3

- [ ] T051 [US3] Read Infrahub's `extensions.code`, node kind, field name and message from a GraphQL error of a rejected write, capped and without control characters, in `infrahub_sync/infrahub_errors.py`
- [ ] T052 [US3] Write the per-adapter classification table (exception type and HTTP status code only, never text) for source and destination connection, timeout and token failures, in `infrahub_sync/service/failure_cause.py` (plan.md open risks)
- [ ] T053 [US3] Implement `failure_record(stage, exc, ...)` with the failure type table of research R12 and the allowlist of FR-019, in `infrahub_sync/service/failure_cause.py`
- [ ] T054 [US3] Call `failure_record` from `_failure_evidence`, add `operations_total` and `failed_operation_detail` from the plan, and rewrite the comment at `flow.py:834-836` to the rule of ADR 0021, in `infrahub_sync/service/flow.py`
- [ ] T055 [US3] Print the new fields in `_echo_failure_record`, in `infrahub_sync/cli.py` (contracts/cli.md)
- [ ] T056 [P] [US3] Write `dev/adr/0021-a-failure-record-holds-a-structured-cause.md` from research R14, and list it in `dev/adr/README.md`
- [ ] T057 [P] [US3] Update `docs/docs/development/guidelines/secret-redaction.md` to the allowlist rule of ADR 0021, stating that the masking pass is unchanged and that the worker and Prefect logs keep masked error text until 3.0

**Checkpoint**: T046 to T050 pass.

## Phase 6: User Story 4 - Recover a stopped apply by planning again (Priority: P1)

**Goal**: a stopped apply shows what it wrote and which operation failed; a new plan holds only
the remaining operations (FR-021 to FR-023).

**Independent test**: stop an apply at a known operation and plan again.

- [ ] T058 [P] [US4] Test that planning again after an apply stopped at a known operation holds only the remaining operations, minus any the destination now matches (SC-004), in `tests/plan/test_replan_after_stopped_apply.py`
- [ ] T059 [P] [US4] Test that `runs show` prints written of total and the failed operation's action, kind and identity, and that a second apply prints the `run-execution-conflict` refusal with "create and review a new plan", in `tests/cli/test_stopped_apply_output.py`
- [ ] T060 [US4] Print the `run-execution-conflict` refusal with its next step when `apply` meets it, in `infrahub_sync/cli.py`

**Checkpoint**: T058 and T059 pass (the record fields come from T054).

## Phase 7: User Story 5 - Check a configuration before a run (Priority: P2)

**Goal**: `configs validate --branch` reports schema, Dropdown literal, worker credential,
permission and lineage findings; validation and the plan agree on kinds without a writable HFID;
the register response holds no redacted content (FR-034 to FR-038).

**Independent test**: SC-009 and SC-011 on one configuration.

### Tests for User Story 5

- [ ] T061 [P] [US5] Test `configs validate --branch` and the route's `branch` parameter: missing kind, attribute and relationship; Dropdown literal outside choices; unchanged result without a branch, in `tests/service/test_config_routes.py` and `tests/configuration/test_schema_validation.py`
- [ ] T062 [P] [US5] Test the permission and lineage findings (`destination-permission-missing`, `destination-lineage-node-not-found`, `-ambiguous`) on the current lineage code, in `tests/configuration/test_validate_permissions.py` (reference commit `a154b907` on the old branch)
- [ ] T063 [P] [US5] Test the worker environment: names published, never values; an empty value counts as unset; `credential-not-set-in-worker` per variable; `worker-environment-unknown` when no live worker published, in `tests/service/test_worker_environment.py`
- [ ] T064 [P] [US5] Test that validation and the plan give the same answer for each kind without a writable human-friendly ID (SYNC-147), parametrized over the same kinds, in `tests/test_writable_convergence_identity.py`
- [ ] T065 [P] [US5] Test that the register and new-version responses hold `package_checksum` and no `declared_content`, and that a replayed request returns the same body (SYNC-107), in `tests/service/test_config_routes.py`

### Implementation for User Story 5

- [ ] T066 [US5] Add `--branch` to `configs validate` in `infrahub_sync/cli.py` and the `branch` query parameter in `infrahub_sync/service/config_routes.py`, passing `DestinationSchemaOptions` to `validate` in `infrahub_sync/product_store/configs.py`
- [ ] T067 [US5] Check Dropdown literal values against the choices of T023, in `infrahub_sync/configuration/schema_validation.py`
- [ ] T068 [US5] Read the account's permissions and resolve the lineage names on the named branch, and report the findings, in `infrahub_sync/configuration/schema_validation.py`, `infrahub_sync/configuration/capabilities.py` and `infrahub_sync/adapters/infrahub_lineage.py`
- [ ] T069 [US5] Add the `worker_environments` table with `CREATE TABLE IF NOT EXISTS`, in `infrahub_sync/product_store/store.py`; publish names from the worker on each poll, in `infrahub_sync/service/worker.py`; collect referenced names, in new `infrahub_sync/configuration/worker_credentials.py`
- [ ] T070 [US5] Return a version reference without `declared_content` from `POST /configs` and `POST /configs/{config_id}/versions`, in `infrahub_sync/product_store/configs.py`, `infrahub_sync/service/config_routes.py` and `infrahub_sync/client/models.py`

**Checkpoint**: T061 to T065 pass.

## Phase 8: User Story 6 - Import the NetBox example from real NetBox data (Priority: P2)

**Goal**: the NetBox example plans the pinned `demo` dataset and handles the three #374 cases
(FR-033). Depends on Phase 3.

- [ ] T071 [US6] Add the device status translation transform, reading the choices from the pinned schema, in `examples/netbox_to_infrahub/package.yml` (research R18)
- [ ] T072 [US6] Run the `from-netbox` check on the pinned `demo` dataset; record whether the two same-named devices are written or listed, and the plan time before and after this feature (research R18, R19), in `dev/specs/019-plan-problem-reporting/opsmill-implement-report.md`
- [ ] T073 [US6] State in `examples/netbox_to_infrahub/README.md` which #374 cases are written by the mapping and which are listed as problems (SC-008)

## Phase 9: Polish and cross-cutting

- [ ] T074 [P] Update the user guide for reviewing problems, confirming left-out records, the stopped `sync`, and reading a failure, in `docs/docs/` (the plan-and-apply guide), and regenerate the CLI and HTTP API references with `uv run invoke docs.generate`
- [ ] T075 [P] Add to the upgrade documentation and a changelog fragment that plans saved in format 3 before the upgrade must be planned again before apply, and that the new `worker_environments` table stays unused after a rollback (critique P3, E6)
- [ ] T076 [P] Update `docs/docs/development/knowledge/orchestration-prefect.md` and `docs/docs/development/knowledge/repository-tour.md` for the new modules, the stopped `sync` and the artifact lines
- [ ] T079 [P] Find the tests on `feature/v3-develop` that prove old FR-004 (a plan writes nothing to the destination) and old FR-013 (a rejected write stops the apply); if a case has no test, add one
- [ ] T080 SIGN-OFF gate (merge blocker, not implementation work): the product owner accepts ADR 0020, ADR 0021 and ADR 0022, written with the status Proposed (decision 3 of 2026-11-11). Leave unticked until that sign-off is recorded
- [ ] T077 Run the full quickstart (offline and live), the required workflow of AGENTS.md, and the CLI sanity commands; record results in `dev/specs/019-plan-problem-reporting/opsmill-implement-report.md`

## Dependencies and execution order

- Phase 1 then Phase 2 block everything.
- US1 (Phase 3) blocks US2 (Phase 4), US6 (Phase 8) and T067 of US5 (needs T023).
- US3 (Phase 5) depends only on Phase 2 and can run in parallel with US1.
- US4 (Phase 6) needs T054 of US3.
- US5 (Phase 7) needs T023 for T067; the rest depends only on Phase 2.
- T033 runs before T039; T039 before T043.
- External: Phases 3 and 4 ship only after the AD-12 amendment is merged in
  `infrahub-sync-process` (spec, "Dependencies"). Spec 012 task T031 waits for Phase 3.

## Parallel examples

- After Phase 2: Phase 3 tests T009 to T016 together; Phase 5 tests T046 to T050 together.
- Adapter changes T018, T019, T020 in parallel after T017 and T021's helper signature.
- Phase 7 tests T061 to T065 together.

## Implementation strategy

1. Phase 1, Phase 2, then US3 first if a quick, independent delivery is wanted: it changes no
   plan output.
2. US1, then US2: the core of the testing-release journey. Stop and check SC-001, SC-002, SC-006.
3. US4, then US5 and US6.
4. Polish.
