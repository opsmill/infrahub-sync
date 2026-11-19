# Tasks: Register a configuration package from the web interface

**Input**: Design documents from `dev/specs/025-register-config-web/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md),
[critiques/critique-20261118.md](critiques/critique-20261118.md)

**Tests**: included. The constitution's principle V requires tests for new behavior, and the plan
lists them. The web interface has no test runner (research R12); its tasks end with `pnpm check`
and `pnpm typecheck`, and the quickstart scenarios check it by hand.

**Format**: `- [ ] [ID] [P?] [Story] Description`. `[P]` marks a task that can run in parallel
with the other `[P]` tasks of its phase (different files, no dependency on an unfinished task).

## Phase 1: Setup

- [X] T001 Install the full development profile in the worktree with `uv sync --extra dev --extra prefect --extra service` and `cd frontend && pnpm install --frozen-lockfile`; confirm `uv run invoke lint` and `uv run invoke tests.tests-unit` pass before any change, and record any failure that exists on `feature/v3-develop` in `dev/specs/025-register-config-web/opsmill-implement-report.md` so it is not attributed to this feature

---

## Phase 2: Foundational (blocks every story)

**Purpose**: one validation function that both the registered and the unregistered paths use, so
FR-002 (same findings) holds by construction (research R1, R3).

- [X] T002 In `infrahub_sync/configuration/schema_validation.py`, make `collect_destination_schema_findings` also return the reason of a `DestinationSchemaReadError` (`timeout`, `unreachable`, `unauthorized`, `forbidden`, `nobranch`, `unconfigured`, `credentials`), or `None` when every read succeeded, as a new typed field on the result it already returns; keep the existing `destination_schema_read_failed` findings and their messages unchanged
- [X] T003 In `infrahub_sync/product_store/configs.py`, extract the body of `validate` that runs after the version lookup (`collect_findings`, `collect_destination_schema_findings`, `_worker_credential_findings`, `sort_findings`) into one private function that takes a parsed `ConfigurationPackage`, `destination_schema`, `secrets`, `projection` and `worker_liveness_seconds`, and returns the sorted findings, the schema fingerprint and the destination read outcome (`not_requested`, `read` or the reason from T002); make `validate` call it, with no change to its result
- [X] T004 Run `uv run pytest tests/product_store/test_configs_service.py tests/configuration -q` and the service validate tests in `tests/service/test_config_routes.py` to confirm T002 and T003 changed no existing result

**Checkpoint**: existing validation behaves exactly as before.

---

## Phase 3: User Story 1 - Validate a package before registering it (Priority: P1)

**Goal**: an administrator validates a package against the destination without storing it, from
the Sync API and the CLI.

**Independent test**: quickstart Scenario 1.

### Tests for User Story 1

- [X] T005 [P] [US1] In `tests/product_store/test_configs_service.py`, add parametrized tests for a new `check_package` service function: (a) for packages with and without findings, `check_package(package, ...)` and `validate(config_id, 1, ...)` after `register(package, ...)` return equal findings in equal order, with and without `destination_schema` (use the destination schema test doubles the existing validate tests use); (b) `check_package` leaves `list_configuration_page` unchanged; (c) a package the registration refuses (store key set, credential error, structural error) raises the same exception type and reason from `check_package` as from `register`; (d) `package_checksum` and `declared_content` equal those of the version `register` stores; (e) registering the `declared_content` that `check_package` returns is accepted and gives the same `package_checksum` as registering the original package (the web interface registers that object, research R2)
- [X] T006 [P] [US1] In `tests/service/test_config_routes.py`, add tests for `POST /configs/validate` per [contracts/sync-api.md](contracts/sync-api.md): administrator with `package`; administrator with `document` in YAML and in JSON; both or neither field → 400 `request`; empty or non-mapping `document` → 400 `request` with reason `invalid_package`; a v2 `config.yml` taken from `examples/` → 422 `validation` with reason `v2_configuration` and a message naming `infrahub-sync configs convert`; a token that is not an administrator → 403, no destination read (assert the schema reader double was not called), and one audit refusal with operation `check-config-package`; `branch` given and the destination double raising each `DestinationSchemaReadError` reason → `destination_read` equals that reason; no `branch` → `destination_read` is `not_requested`; no secret value from the service's collected secrets appears in the response
- [ ] T007 [P] [US1] In `tests/cli/test_client_commands.py`, add tests for `infrahub-sync configs check PACKAGE [--branch]`: it sends the file as `package`, prints the fields of `configs validate` plus `destination_read`, exits 0 with no findings and 1 when the page has a finding, and refuses an unreadable file with the same error as `configs register`
- [ ] T008 [P] [US1] In `tests/client/test_client.py` and `tests/client/test_models.py`, add tests for the client method `check_package` and its request model (exactly one of `package` and `document`; the existing JSON-native package rule applies to `package`); update `tests/client/test_public_surface.py` and `tests/cli/test_parity_and_closure.py` if they enumerate client methods or CLI commands

### Implementation for User Story 1

- [X] T009 [US1] In `infrahub_sync/product_store/configs.py`, add `check_package(*, package, projection, destination_schema=None, secrets=(), worker_liveness_seconds=...)` decorated like `validate` with `@_service_boundary`: parse with `_parse(package)` (the registration's refusals), call `validate_package_credentials(parsed)` from `infrahub_sync.configuration` (the check `ProductProjection.create_configuration` runs before inserting, `infrahub_sync/product_store/store.py` line 3824) and turn its `CredentialConfigurationError` into the same refusal `register` raises (`_validation_refusal(exc, parsed)`), then call the function from T003; return a new frozen result type holding `package_checksum`, `name`, `declared_content`, `findings`, `destination_schema_fingerprint` and `destination_read`; export it where `validate` is exported
- [X] T010 [US1] In `infrahub_sync/service/config_routes.py`, add the request model (`package: dict | None`, `document: str | None`, exactly one) and the response model per [data-model.md](data-model.md); add a `check_package` method on `ConfigurationRoutes` that parses `document` with `yaml.safe_load` (refusing a non-mapping or empty result as 400 `request` with reason `invalid_package`), refuses a mapping with no `configuration` key and with top-level `source` and `destination` keys as 422 `validation` with reason `v2_configuration`, calls the service through `_call`, pages findings with the same offset and limit rules as `validate`, and applies the same secret redaction
- [X] T011 [US1] In `configuration_router` in `infrahub_sync/service/config_routes.py`, register `POST /configs/validate` before the `/configs/{config_id}` routes, with the same `branch`, `offset` and `limit` query parsing as the existing validate route; refuse a principal that is not an administrator with `routes.audit_refusal(principal.actor, "check-config-package", None)` and 403 `forbidden`, before any parsing or destination read
- [ ] T012 [US1] In `infrahub_sync/client/models.py` and `infrahub_sync/client/client.py`, add the request and response models and `SyncClient.check_package(request, *, offset, limit, branch)`, following `validate_config`
- [ ] T013 [US1] In `infrahub_sync/cli.py`, add `@configs_app.command("check")` taking `PACKAGE` (read with `_package`), `--branch`, `--offset` and `--limit`; print with a variant of `_echo_validation` that adds `destination_read`; exit 1 when a finding is on the returned page, like `configs validate`
- [ ] T014 [US1] Run T005 to T008 and `uv run infrahub-sync configs --help`; all pass

**Checkpoint**: User Story 1 ships on its own.

---

## Phase 4: User Story 2 - Add a version by dropping a file (Priority: P1)

**Goal**: on a configuration's page, an administrator drops a package, reviews it and registers the
next version.

**Independent test**: quickstart Scenario 2.

- [ ] T015 [US2] Start the development API (`uv run invoke dev.start`, then `uv run invoke dev.api` or the preset that serves the Sync API) and run `cd frontend && pnpm codegen:openapi` to regenerate `frontend/src/api/types.generated.ts` with the new route; if the stack cannot start, generate the OpenAPI document from the FastAPI app in Python (`create_app(...).openapi()`, see `infrahub_sync/service/app.py`) into a temporary file and run `openapi-typescript` on that file
- [ ] T016 [P] [US2] In `frontend/src/api/actions.ts`, add `useCheckPackage` (`POST /configs/validate`) and `useCreateConfigVersion` (`POST /configs/{config_id}/versions` with an `Idempotency-Key` header passed by the caller), following the existing mutations; invalidate the configuration queries on success of the registration only
- [ ] T017 [P] [US2] Create `frontend/src/features/configurations/package-review.ts` with pure functions: the default branch (`declared_content.configuration.destination.settings.branch` or `main`); the result kind from a check response (`valid`, `findings`, `unreachable` for `timeout` and `unreachable`); the credential variable names from `declared_content.credentials`; the rename text when the name differs; the messages of [contracts/web-interface.md](contracts/web-interface.md) keyed by API error code and reason (`invalid_package`, `v2_configuration`, HTTP 413); the "N more findings" text when `total_findings` exceeds the page
- [ ] T018 [P] [US2] Create `frontend/src/features/configurations/PackageDropArea.tsx`: a region that accepts one dropped file through the browser's drag events, plus a "choose a file" button backed by a hidden file input (keyboard accessible); refuses more than one file with "Drop one package file."; reads the file as text; shows a disabled state with "Registering needs an administrator token" when a `disabled` prop is set; calls `onFile(text, fileName)`
- [ ] T019 [US2] Create `frontend/src/features/configurations/PackageReview.tsx`: a modal from `@infrahub/ui` that, on open, generates one idempotency key, calls `useCheckPackage` with `document` and the default branch, and shows the content listed in [contracts/web-interface.md](contracts/web-interface.md) section "Review step content" for a new version: title, name, source and destination adapters, rename text, the mapping comparison with the newest version (reuse `diffMappings`, `mappingEntries` and `removedEntries` from `frontend/src/domain/mapping.ts` and the existing comparison view in `frontend/src/features/configurations/detail-version-bar.tsx` or `detail-mapping.tsx`, passing the newest version's and the check's `declared_content.configuration.schema_mapping`), credential names, the branch field (changing it re-runs the check), the validating state with Cancel available, the result, the reason field (at most 512 characters), Register enabled only for `valid`, and "Register without validation" with the warning "Could not validate: the destination cannot be reached" for `unreachable` (FR-020; without User Story 4 it sends no mark); when the check request fails without a refusal of the package, show "Validation did not complete." with "Validate again" and keep Register disabled (FR-017a)
- [ ] T020 [US2] In `PackageReview.tsx`, on Register: read the configuration again (`configQuery` or the versions list); if its newest version is not the one compared with, show "Version M was registered meanwhile." and re-run the comparison without registering; otherwise call `useCreateConfigVersion` with the check's `declared_content` as `package`, the reason and the review step's idempotency key; on `created: false` show "This package is the same as version N. No new version was registered."; on success navigate to the new version's page; from the first attempt on, lock the reason field, the branch field and the file (the Sync API refuses a key reused with a different request, `ConfigurationRoutes.mutate`); on a network failure show "The request did not complete. Retry." and send the identical request with the same key, or let the operator cancel
- [ ] T021 [US2] In `frontend/src/features/configurations/ConfigurationPage.tsx`, add the "Add a version" drop area, disabled unless the `whoami` result used in `frontend/src/routes/__root.tsx` says `administrator`; open `PackageReview` for the dropped file; show refusals from the check (400, 413, 422) as messages before the review content
- [ ] T022 [US2] Check that no text added in T017 to T021 names a user or an actor (`AGENTS.md`, "Sync frontend"); run `cd frontend && pnpm check && pnpm typecheck`

**Checkpoint**: User Stories 1 and 2 work; quickstart Scenario 2 passes by hand.

---

## Phase 5: User Story 3 - Register a new configuration by dropping a file (Priority: P2)

**Goal**: on the Configurations page, an administrator registers a new configuration from a file.

**Independent test**: quickstart Scenario 3.

- [ ] T023 [P] [US3] In `frontend/src/api/actions.ts`, add `useRegisterConfig` (`POST /configs` with an `Idempotency-Key` header passed by the caller)
- [ ] T024 [US3] In `PackageReview.tsx`, support the target "new configuration": no comparison; title "Register a new configuration"; look up `GET /configs?name=<name>` and, when one exists, show "A configuration named A already exists." with a link to that configuration's page labelled "Add a version to it instead"; Register calls `useRegisterConfig` and navigates to the new configuration's page
- [ ] T025 [US3] In `frontend/src/features/configurations/ConfigurationsPage.tsx`, add the "Register a configuration" drop area with the same administrator rule as T021; run `cd frontend && pnpm check && pnpm typecheck`

**Checkpoint**: quickstart Scenario 3 passes by hand.

---

## Phase 6: User Story 4 - Show that a version was registered without validation (Priority: P3)

**Goal**: a version registered while the destination could not be reached shows that it was never
validated. Registering in that case already works after User Story 2 (T019); leaving this phase out
only means no mark is recorded (FR-022).

**Independent test**: quickstart Scenario 4.

### Tests for User Story 4

- [ ] T026 [P] [US4] Create `tests/product_store/test_configuration_unvalidated_mark.py`, run against SQLite and against PostgreSQL where the existing store tests do (see `tests/product_store/postgresql_isolation.py`): a version created with the mark reads back `registered_without_validation: true` from lookup, version list and version page; a version created without it reads `false`; an identical package registered again with the mark (`created: false`) does not change the existing version's value; a store initialized before this change (table missing) gets the table at start-up and reads `false` for existing versions
- [ ] T027 [P] [US4] In `tests/service/test_config_routes.py`, add tests: `POST /configs` and `POST /configs/{config_id}/versions` accept `registered_without_validation: true` and the version resources return it; the field defaults to `false`; the client request model in `tests/client/test_models.py` accepts and serializes it

### Implementation for User Story 4

- [ ] T028 [US4] In `infrahub_sync/product_store/store.py`, add `CREATE TABLE IF NOT EXISTS configuration_versions_unvalidated (config_id TEXT NOT NULL, registry_version INTEGER NOT NULL, PRIMARY KEY (config_id, registry_version), FOREIGN KEY (config_id, registry_version) REFERENCES configuration_versions(config_id, registry_version))` to `_SCHEMA`; add a `registered_without_validation` keyword to `create_configuration` and `add_configuration_version` (protocol and both implementations) that inserts the row in the same transaction when a version is created; read the mark in every query that builds a `ConfigurationVersion` (left join or a second lookup) and add `registered_without_validation: bool = False` to `ConfigurationVersion`
- [ ] T029 [US4] In `infrahub_sync/product_store/configs.py`, pass `registered_without_validation` through `register` and `create_version`
- [ ] T030 [US4] In `infrahub_sync/service/config_routes.py` and `infrahub_sync/client/models.py`, add `registered_without_validation: bool = False` to `ConfigMutationRequest`, pass it to `routes.mutate` and through to the service, and add the field to every configuration version resource model; add the field to the request fingerprint in `ConfigurationRoutes.mutate` beside `package` and `reason`, so a key reused with a different value is refused as for a different reason; keep the fingerprint of a request without the field unchanged (omit the key when the value is `false`), so receipts stored before this change still match their retries
- [ ] T031 [US4] In `infrahub_sync/cli.py`, print `registered_without_validation` in `_echo_config_version_reference`; add no CLI flag to set it (the CLI user can run `configs check` and decide)
- [ ] T032 [US4] Regenerate `frontend/src/api/types.generated.ts` as in T015
- [ ] T033 [US4] In `PackageReview.tsx`, make "Register without validation" (added in T019) send `registered_without_validation: true`; in the version view (`frontend/src/features/configurations/detail-version-bar.tsx`), show "Registered without validation" for a marked version; run `cd frontend && pnpm check && pnpm typecheck`
- [ ] T034 [US4] Run T026 and T027

**Checkpoint**: quickstart Scenario 4 passes by hand.

---

## Phase 7: Polish and cross-cutting

The work ships in two pull requests (spec decision D-9, plan "Implementation order").
- The first pull request holds Phases 1 to 3 and the User Story 1 part of T035 to T038:
  - the ADR without the mark;
  - the `configs check` documentation and the CLI reference;
  - the fragment `changelog/+validate-config-package.added.md`.
- The second pull request holds Phases 4 to 6 and the rest of Phase 7:
  - the ADR section on the mark's table;
  - the web interface documentation;
  - the fragment `changelog/+register-config-web.added.md`;
  - T039.

- [ ] T035 [P] Write the ADR `dev/adr/0051-a-package-can-be-validated-before-it-is-registered.md` (take the next free number if 0051 is used when this merges) recording: validation of an unregistered package, the administrator rule and its reason (critique E1), server-side parsing of `document` (research R2), the mark stored in its own table (research R8); add it to `dev/adr/README.md`; cite no internal chat or tracker links
- [ ] T036 [P] Update the user documentation: the configuration package page `docs/docs/configuration-package.mdx` (validating before registering, `configs check`, registering from the web interface, the mark); regenerate the CLI reference with `uv run invoke docs.generate`
- [ ] T037 [P] Add the news fragment `changelog/+register-config-web.added.md` with `uv run --extra dev towncrier create -c "<one sentence>" +register-config-web.added.md`
- [ ] T038 Run the full required workflow: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, `uv run infrahub-sync --help`, `uv run infrahub-sync configs --help`, `uv run infrahub-sync runs --help`, `cd frontend && pnpm check && pnpm typecheck && pnpm build`
- [ ] T039 Run the quickstart scenarios that the local environment allows ([quickstart.md](quickstart.md)) and record in `dev/specs/025-register-config-web/opsmill-implement-report.md` which ran and which could not

---

## Dependencies and execution order

- Phase 1 → Phase 2 → Phase 3 (User Story 1).
- User Story 2 needs User Story 1 (the route and regenerated types).
- User Story 3 needs the review step from User Story 2 (T019, T020).
- User Story 4 needs User Story 1 for the server side (T026 to T031) and User Story 2 for the web
  interface (T033). Its server tasks can run in parallel with User Story 2.
- Phase 7 runs last.

## Parallel examples

- User Story 1 tests: T005, T006, T007 and T008 touch different files.
- User Story 2: T016, T017 and T018 touch different files; T019 needs all three.
- User Story 4 tests T026 and T027 can be written while User Story 2 is in progress.
- Polish: T035, T036 and T037.

## Implementation strategy

1. First pull request: User Story 1. It gives CLI and script users a check before registration,
   and every other story depends on it.
2. Second pull request: User Story 2, the most frequent web interface journey, then User Story 3,
   then User Story 4. User Story 4 is the only story that changes the store; the maintainer
   confirmed it is kept (spec decision D-8).
