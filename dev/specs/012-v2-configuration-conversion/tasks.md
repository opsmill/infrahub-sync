# Tasks: Convert a v1 or v2 configuration and prove the first V3 plan

**Input**: design documents in `dev/specs/012-v2-configuration-conversion/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/convert-command.md](contracts/convert-command.md),
[quickstart.md](quickstart.md)

**Tests**: included. Constitution V requires tests with each change, and SC-001 to SC-003 are
verified by tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 (convert and register), US2 (view why a configuration is refused), US3 (gate)

## Path Conventions

Source in `infrahub_sync/`, tests in `tests/`, nightly scripts in `.github/scripts/`, docs in
`docs/docs/`. Run commands from the repository root.

---

## Phase 1: Setup

- [ ] T001 [P] Copy each v2 example `config.yml` from the `2.0.2` tag into `tests/data/v2_examples/<example directory>/config.yml` (14 files, `git show 2.0.2:examples/<dir>/config.yml`), and the v1 NetBox example from `1.6.1` into `tests/data/v2_examples/v1_netbox_to_infrahub/config.yml`; add `tests/data/v2_examples/README.md` naming the tags and saying the files must not be edited
- [ ] T002 [P] Create `tests/data/v2_gate/expected_deletes.yml` as an empty list, with a header comment giving the entry format (`kind`, `identity`, `reason`) and the rule that an entry is added only with a reason accepted in review (data-model.md)
- [ ] T003 Create `infrahub_sync/configuration/conversion.py` with the module docstring and the typed, frozen result types from data-model.md "Conversion result": `ConversionResult`, `CredentialEntry`, `SettingOriginEntry`, `VisibleVariablesEntry`, `DroppedEntry`, `NoteEntry`, `RefusalEntry`, and the exception `ConversionInputError`; no logic yet

---

## Phase 2: Foundational (blocks every user story)

- [ ] T004 Add the constant `V2_ENVIRONMENT_FIRST` to `infrahub_sync/configuration/conversion.py`: for each adapter, each setting, its v2 variable names in v2's order and its kind (`address`, `credential`, `other`), exactly as the table in research.md R2
- [ ] T005 [P] Add parametrized tests, one per row of research.md R2, in `tests/configuration/test_conversion.py` asserting `V2_ENVIRONMENT_FIRST` content, and a test that every `credential` row matches `credential_setting_paths` in `BUILTIN_ADAPTER_CAPABILITIES`
- [ ] T006 Add the constant `COMPOSE_WORKER_CREDENTIAL_VARIABLES` to `infrahub_sync/configuration/conversion.py` and `tests/configuration/test_conversion_worker_variables.py`, which parses `docker-compose.yml` and asserts the constant equals the `INFRAHUB_SYNC_CREDENTIAL_*` keys of the `sync-worker` service environment (research.md R5)
- [ ] T007 Add `credential_reference(adapter, setting)` to `infrahub_sync/configuration/conversion.py` returning the reference name and the identifier (spec FR-006: `<adapter>-<setting>` with `_` to `-`, `INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_<SETTING>`, Infrahub `infrahub-token` and `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`), with a parametrized test over every `credential_setting_paths` entry of the 9 bundled adapters in `tests/configuration/test_conversion.py`
- [ ] T008 Add `visible_v2_variables(environ)` to `infrahub_sync/configuration/conversion.py`, returning only the `address` and `other` names of `V2_ENVIRONMENT_FIRST` that are set (spec FR-030), with a test in `tests/configuration/test_conversion.py` that credential names and unrelated names set in `environ` are never returned

**Checkpoint**: tables and naming are pinned; user story work can start.

---

## Phase 3: User Story 1 - Convert the v2 NetBox configuration and register it without edits (Priority: P1) 🎯 MVP

**Goal**: one command turns the v2 NetBox `config.yml` into a package that registers unchanged,
after showing every change and asking once.

**Independent Test**: convert `tests/data/v2_examples/netbox_to_infrahub/config.yml`, answer
`y`, and check the package passes `parse_configuration_package` and `collect_findings` with no
error; then register it on a preview stack (quickstart.md step 1).

### Tests for User Story 1

- [ ] T009 [P] [US1] In `tests/configuration/test_conversion.py`, test credential conversion for a literal value, a `${VARIABLE}` placeholder and no value: the package holds the reference from T007 and the `credentials` entry; the report's `CredentialEntry` names the v2 origin; `worker_receives` follows `COMPOSE_WORKER_CREDENTIAL_VARIABLES`; a literal adds the "config.yml still holds a literal value" warning (FR-006 to FR-009)
- [ ] T010 [P] [US1] In `tests/configuration/test_conversion.py`, parametrize the five address cases of the grilling table (file only, variable only, both equal, both different with the variable winning and both values reported, neither so a refusal naming the flag and variables), the flag overriding both, the `localhost` and `127.0.0.1` warnings, the warning for a value from `genericrestapi`'s `URL` or `ADDRESS`, ACI `verify` from `CISCO_APIC_VERIFY` (FR-029), and an address with a user name or password refused without printing it (FR-010 to FR-014, FR-029, FR-030)
- [ ] T011 [P] [US1] In `tests/configuration/test_conversion.py`, test that `name`, `schema_mapping`, `order`, `diffsync_flags` and the destination `branch`, `source`, `owner`, `verify_ssl` are copied unchanged; `incremental` is copied for `infrahub`, `nautobot`, `netbox` sources and dropped with a warning otherwise; `store`, `adapters_path` and `genericrestapi` `*_env_vars` are dropped with their reasons; the "no generation step" note is present (FR-017 to FR-019)
- [ ] T012 [P] [US1] In `tests/configuration/test_conversion.py`, test that the NetBox example's package parses with `parse_configuration_package` and has no error finding from `collect_findings`, and that a forced error finding turns into a refusal naming the finding code (FR-020)
- [ ] T013 [P] [US1] Create `tests/cli/test_convert_command.py` with `CliRunner` tests: report printed before any write; `--yes` writes; input `n` writes nothing and exits 0; standard input not a terminal without `--yes` writes nothing and exits 1 with the "run again with --yes" line; existing output without `--force` exits 1 before the report; `--force` replaces; `--output` sets the path; unreadable file, invalid YAML (message holds only line and column), already-V3 input, and a non-v2 mapping each exit 2; no Sync API variable is needed (FR-001 to FR-005, FR-021, contract)

### Implementation for User Story 1

- [ ] T014 [US1] Implement `convert_v2_configuration(data, visible, *, source_url, destination_url)` in `infrahub_sync/configuration/conversion.py`: credential replacement and report entries, using T004 to T008 (FR-006 to FR-009); no I/O, no printing
- [ ] T015 [US1] Implement the environment-first resolution in `infrahub_sync/configuration/conversion.py` for `address` and `other` settings: placeholders read only when they name the setting's own v2 variables, variable over file, flags over both, origins, both values when they differ, visible-variables entry, `localhost` and generic-name warnings, user-information refusal (FR-010 to FR-014, FR-029, FR-030)
- [ ] T016 [US1] Implement copying and dropping in `infrahub_sync/configuration/conversion.py` (FR-017 to FR-019) and the bundled-module `adapter:` note (FR-016, research.md R7)
- [ ] T017 [US1] Implement the pre-write check in `infrahub_sync/configuration/conversion.py`: build the package mapping, run `parse_configuration_package` and `collect_findings`, turn error findings into refusals and warning findings into report entries (FR-020, research.md R6)
- [ ] T018 [US1] Add `configs convert` to `infrahub_sync/cli.py` per contracts/convert-command.md: arguments and options; read the input with line-and-column-only YAML errors; refuse a `format_version` input; check the output path; filter the environment with `visible_v2_variables`; call `convert_v2_configuration`; print the report in the contract's section order; confirm only when standard input is a terminal; write through a temporary file and rename; print the next steps; exit statuses 0, 1 and 2 as the contract states
- [ ] T019 [US1] Add a test in `tests/configuration/test_conversion.py` that `tests/data/v2_examples/v1_netbox_to_infrahub/config.yml` converts the same way as the v2 NetBox example except for `incremental` (spec Assumptions, v1)

**Checkpoint**: User Story 1 works on its own: quickstart.md step 1 passes.

---

## Phase 4: User Story 2 - View why a configuration cannot be converted (Priority: P1)

**Goal**: a configuration V3 cannot run is refused with every reason, before anything is written.

**Independent Test**: convert the `custom_adapter` and `infrahub_to_peering-manager` examples and
a fixture holding every refused case; check exit 1, no file, and each reason (quickstart.md
step 2).

### Tests for User Story 2

- [ ] T020 [P] [US2] In `tests/configuration/test_conversion.py`, parametrize one fixture per refusal: a name that is not bundled (reason lists the bundled names), `adapter:` as a filesystem path, a dotted path and an entry point (reason cites INFP-654 and 3.0), destination `peeringmanager` ("V3 does not write to `peeringmanager` yet; this release writes only to Infrahub"), an unaccepted adapter setting, a placeholder in a setting v2 did not read from the environment; plus one fixture holding all of them that yields every reason; plus a check that a refusal never carries a credential setting's value (FR-015, FR-016)
- [ ] T021 [P] [US2] In `tests/cli/test_convert_command.py`, test that a refusal prints to standard error in the contract's layout, writes no file and exits 1

### Implementation for User Story 2

- [ ] T022 [US2] Implement refusal collection in `infrahub_sync/configuration/conversion.py` so every refusal in the file is gathered before returning, and `package` is empty whenever a refusal exists (FR-015)
- [ ] T023 [US2] Print refusals in `infrahub_sync/cli.py` per the contract's refusal layout, with an empty value column for credential settings

**Checkpoint**: User Stories 1 and 2 work independently; quickstart.md step 2 passes.

---

## Phase 5: Cross-story verification of the examples and of secrets (US1, US2)

- [ ] T024 [P] [US2] Create `tests/configuration/test_conversion_examples.py`: the 12 examples that read from a bundled adapter and write to Infrahub convert and their packages have no error finding; `custom_adapter` and `infrahub_to_peering-manager` are refused with their reasons. Set `CISCO_APIC_URL` and `CISCO_APIC_VERIFY` in the visible variables for the ACI example, because its file holds placeholders for both (SC-002, research.md R3)
- [ ] T025 [P] [US1] Create `tests/configuration/test_conversion_secrets.py` with a canary value, following `tests/test_credential_canary.py`: a literal token in the file, every v2 credential variable of `V2_ENVIRONMENT_FIRST` set to the canary, `url: ${NETBOX_TOKEN}`, and an address with a password; assert the canary is absent from the package, standard output, standard error and captured logs (SC-003, FR-007, FR-030)

---

## Phase 6: User Story 3 - Plan from V3 against the data v2 wrote and view no changes (Priority: P1)

**Goal**: a repeatable gate proves that the first V3 plan on data v2 wrote holds nothing to
create or update, leaves nothing out and holds only expected deletes.

**Independent Test**: `uv run python .github/scripts/nightly_e2e.py suite v2-gate` writes
`gate-result.json` with `passed: true` (quickstart.md step 5).

### Tests for User Story 3

- [ ] T026 [P] [US3] Add unit tests for the gate's comparison function (counts, unexpected entries with v2 and V3 values, expected-delete matching, `passed`) in `tests/test_nightly_v2_gate.py`, using a recorded plan detail fixture in `tests/data/v2_gate/plan_detail_sample.txt`

### Implementation for User Story 3

- [ ] T027 [US3] Add the `v2-gate` suite to `.github/scripts/nightly_e2e.py` and to `SUITES`: seed the `demo` dataset, start a fresh preview stack, load `tests/data/nightly_schema`, create the gate branch, reusing the helpers the `from-netbox` suite uses (research.md R10 steps 1)
- [ ] T028 [US3] In `.github/scripts/nightly_e2e.py`, run v2 `2.0.2` once in its own environment (`uvx --from infrahub-sync==2.0.2 infrahub-sync sync --name from-netbox --directory <tmp>`, installed from PyPI as customers installed it), with `tests/data/v2_examples/netbox_to_infrahub/config.yml` copied into `<tmp>` and its destination `branch` set to the gate branch; pass `NETBOX_ADDRESS`, `NETBOX_TOKEN`, `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN` for the local NetBox and the preview Infrahub (research.md R10 step 2)
- [ ] T029 [US3] In `.github/scripts/nightly_e2e.py`, run `infrahub-sync configs convert <tmp>/config.yml --yes` with `NETBOX_ADDRESS` and `INFRAHUB_ADDRESS` visible, register the package, run `diff` on the gate branch, and read `runs plan --detail` (research.md R10 steps 3 and 4)
- [ ] T030 [US3] In `.github/scripts/nightly_e2e.py`, implement the comparison tested by T026: count creates, updates and deletes; match deletes against `tests/data/v2_gate/expected_deletes.yml`; record for each create, update and unlisted delete the kind, the identity, and for an update each field with the v2 and V3 values; write `gate-result.json` (data-model.md); fail the suite when `passed` is false (FR-023 to FR-025)
- [ ] T031 [US3] **Blocked on spec 009 being merged** (`009-plan-problem-reporting`): add records left out and problems to the counts and the pass condition in `.github/scripts/nightly_e2e.py`. Until the plan summary carries them, the suite stops with "records left out and problems cannot be counted on this build" and does not report a pass (FR-023, critique E4)
- [ ] T032 [US3] Add a step that runs `suite v2-gate` to `.github/workflows/workflow-nightly-e2e.yml`, with container logs saved on failure and the result included in the run summary, as the `from-netbox` step does (FR-026)
- [ ] T033 [US3] Run the gate once on a local machine, attach `gate-result.json` to the pull request, and open a defect for each create, update, unlisted delete, record left out or problem it reports (FR-024)

**Checkpoint**: the gate runs nightly; it passes once spec 009 is merged and the defects from
T033 are fixed.

---

## Phase 7: Polish and cross-cutting concerns

- [ ] T034 [P] Replace the manual steps of "Move a V2 configuration to V3" in `docs/docs/v2-to-v3.mdx` with the command, the report, the confirmation and the next steps; list each v2 key and how the command handles it (data-model.md input table); state the gate result (FR-027)
- [ ] T035 [P] Add a short pointer to `configs convert` in `docs/docs/configuration-package.mdx` (FR-027)
- [ ] T036 Regenerate `docs/docs/reference/cli.mdx` with `uv run invoke docs.generate` and check it documents the options, warnings and refusals (FR-028)
- [ ] T037 [P] Add the `v2-gate` suite to `docs/docs/development/guidelines/testing-tiers.md` next to "The `from-netbox` example check"
- [ ] T038 [P] Add the news fragment `changelog/+v2-configuration-conversion.added.md` with `uv run --extra dev towncrier create -c "..." +v2-configuration-conversion.added.md`
- [ ] T039 Run `uv run invoke format`, `uv run invoke lint`, `uv run invoke tests.tests-unit`, and the CLI sanity commands from AGENTS.md (`--help`, `configs --help`, `runs --help`, plus `configs convert --help`)
- [ ] T040 Run quickstart.md steps 1 to 4 and record the results in the pull request

---

## Dependencies and execution order

### Phase dependencies

- Phase 1 (Setup) has no dependency.
- Phase 2 (Foundational) depends on T003 and blocks every user story.
- Phase 3 (US1) and Phase 4 (US2) depend on Phase 2. US2's refusals live in the same module as
  US1, so T022 follows T014; the US2 tests can be written in parallel with US1.
- Phase 5 depends on T014 to T018 and T022, T023.
- Phase 6 (US3) depends on T018 (the command it runs). T031 also depends on spec 009 merging.
- Phase 7 depends on the stories it documents.

### User story dependencies

- US1: after Phase 2. MVP.
- US2: after Phase 2; shares `conversion.py` with US1, so implement after T014.
- US3: after US1. Its pass also depends on spec 009 and on the defects found in T033.

### Within each user story

Tests first and failing, then implementation, then the checkpoint.

### Parallel opportunities

- T001, T002 in parallel; T005 alongside T006 to T008.
- US1 tests T009 to T013 in parallel (different test focus; T009 to T012 share one file, so
  split them into separate test classes or run them in sequence when editing one file).
- US2 tests T020, T021 in parallel with US1 implementation.
- T024, T025 in parallel.
- T026 in parallel with T027 to T029.
- Docs tasks T034, T035, T037, T038 in parallel.

## Parallel example: User Story 1

```text
T010 address-resolution tests      (tests/configuration/test_conversion.py, class TestAddresses)
T011 copy-and-drop tests           (tests/configuration/test_conversion.py, class TestCopyDrop)
T013 CLI tests                     (tests/cli/test_convert_command.py)
```

## Implementation strategy

### MVP first (User Story 1 only)

1. Phases 1 and 2.
2. Phase 3: the NetBox example converts and registers unchanged.
3. Stop and validate with quickstart.md step 1.

### Incremental delivery

1. US1, then US2 (refusals), then Phase 5 (every example, secrets).
2. US3 (gate) last, because it needs a running stack and spec 009.
3. Docs and changelog with the pull request that ships the command.

## Notes

- Commit with explicit paths; this checkout is shared.
- The gate's pass condition is incomplete until spec 009 merges (T031). Do not mark US3 done
  before then.
