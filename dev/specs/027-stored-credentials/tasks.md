# Tasks: Stored credentials managed through the Sync API

**Input**: Design documents from `dev/specs/027-stored-credentials/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md),
[critique](critiques/critique-20261120.md)

**Tests**: requested. Each requirement of the spec has a Verification line, and constitution principle
V requires tests with the change. Write each test task before the implementation it covers, and see it
fail first. A test task covers only behavior that exists at the end of its phase.

**Order rule (plan, "Delivery order")**: the unit tier (`uv run invoke tests.tests-unit`) stays green
at the end of every phase. Phases 2 and 3 add the new path beside the old one. Phase 4 moves the tests,
the examples, the development tasks and the conversion to the new path. Phase 5 removes the old path,
changes the deployment files, and records the change in the documentation, the decision record and
the constitution.

**Verification after each phase**: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`,
`uv run invoke tests.tests-unit`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 create and use (P1), US2 change once (P2), US3 converted v2 file (P3), US4 delete (P3)

---

## Phase 1: Setup

- [X] T001 Add `cryptography` with a range pin (for example `>=44,<47`, matching the version in `uv.lock`) to the `service` extra in `pyproject.toml`, run `uv lock`, and check in a base-only environment that the base install does not pull it (research R3, critique E13)
- [X] T002 [P] Add `INFRAHUB_SYNC_ENCRYPTION_KEY`, `INFRAHUB_SYNC_WORKER_TOKEN` and the worker's `INFRAHUB_SYNC_API_URL` to `infrahub_sync/service/settings.py`: loaders that refuse a missing key, a key that is not 32 bytes after base64 decoding, a worker token shorter than the `admin` token minimum, with an `ifs_pat_` or `ifs_ses_` prefix, or equal to the `admin` token; messages name the variable and never the value; `repr=False` on values (contracts/environment.md, critique E8)
- [X] T003 [P] Write `tests/service/test_credential_settings.py`: parametrized refusals of T002 for each rule, and a check that no message contains the value

---

## Phase 2: Foundational (blocks every user story)

### Encryption

- [X] T004 Write `tests/service/test_credential_crypto.py`: round trip; a ciphertext copied to another `credential_id` or revision fails; a wrong key is reported as `CredentialDecryptionError` from the key identifier before decryption; the key identifier is stable for one key
- [X] T005 Implement `infrahub_sync/service/credential_crypto.py`: `CredentialCipher` with `encrypt(credential_id, revision, fields) -> (key_id, nonce, ciphertext)` and `decrypt(...)`, AES-256-GCM through `cryptography.hazmat.primitives.ciphers.aead.AESGCM`, associated data `infrahub-sync/credential/v1|<credential_id>|<revision>`, random 12-byte nonce, `key_id` = first 8 bytes of SHA-256 of the key in hex; `CredentialDecryptionError` (research R3)

### Store

- [X] T006 Write `tests/product_store/test_stored_credentials.py` on SQLite and PostgreSQL as the existing store tests do: create, read by name, list sorted by name with offset and limit, new revision on value change, description change keeps the revision, unique name, the field sets of data-model.md (all three fields together refused), `configuration_version_credentials` rows written with a version, `run_credential_uses` replaced on a second release for the same execution and block
- [X] T007 Add the four tables of data-model.md (`stored_credentials`, `stored_credential_revisions`, `configuration_version_credentials`, `run_credential_uses`) and the index on `configuration_version_credentials.credential_name` to `_SCHEMA` and the index tuples in `infrahub_sync/product_store/store.py`; add no column to an existing table
- [X] T008 Implement `infrahub_sync/product_store/stored_credentials.py` with the statements for T006, called with `(cursor, self._sql, ...)` as `product_store/accounts.py` is; the name pattern `^[a-z][a-z0-9-]{0,62}$`; a description of at most 500 characters
- [X] T009 Add `StoredCredential`, `StoredCredentialRevision` and `CredentialUse` to `infrahub_sync/product_store/models.py`, and facade methods on `ProductProjection` and `_RunStore` in `infrahub_sync/product_store/store.py`

### The worker account

- [X] T010 Write `tests/service/test_worker_account.py`: `sync-api` start applies a `worker` account (environment-managed, no password, not an administrator) and its token; a second start with a new token replaces it; an existing non-environment-managed account named `worker` stops the start with a named conflict; sign-in as `worker` is refused; every account action on `worker` answers `409 environment-managed-account`
- [X] T011 Implement `apply_worker_account` in `infrahub_sync/service/auth.py` beside `apply_admin_account` (`auth.py:166`), call it from `infrahub_sync/service/serve.py:136-155`, and add the worker token to `resolver.secret_values` (research R4)
- [X] T012 Write `tests/service/test_worker_route_boundary.py`: parametrized over every route of `create_app`, the worker token gets `403 forbidden` on every route outside `/api/worker/` (the refusal of accounts on `/api/worker/` is tested in T022, once the route exists)
- [X] T013 Add the worker-only rule to `_authenticator` in `infrahub_sync/service/app.py:334` and a `require_worker` dependency for the worker routes; `Principal` (`auth.py:36`) gains a `worker` flag set from "named `worker` and environment-managed"

### Package format beside the old one

- [X] T014 Write parametrized tests in `tests/configuration/test_block_credential.py`: `credential: <name>` accepted on the source and destination blocks; the name pattern; a block that names a credential refuses its credential settings in `settings`; the checksum changes when the name changes; for now a package with the old `credentials` block still parses
- [X] T015 Add the optional `credential` key to the adapter block model in `infrahub_sync/configuration/models.py`, and the refusal of credential settings in `settings` when `credential` is set
- [X] T016 Write `tests/configuration/test_credential_field_map.py`: parametrized over every adapter and `auth_method` of research R2, the fields needed and the setting each field fills; `credential-not-used` for a method that needs none
- [X] T017 Implement the field map in `infrahub_sync/configuration/capabilities.py` next to `credential_setting_paths` (`:736-815`), reusing the method table of `configuration/conversion_authentication.py:30-65`

### Redaction from the moment of use (critique E6)

- [X] T018 Write `tests/service/test_released_value_redaction.py`: an error that holds a decrypted value, raised inside a function that runs within the request's redaction registry, is redacted in the log and the answer; an error that holds a value added to a run's registry is redacted in the worker's log bridge and in the failure cause
- [X] T019 Implement a per-run and per-request redaction registry used by `infrahub_sync/execution.py` `redact`, so that `credential_crypto.decrypt` and, later, the worker client add each value before they return it; wire it into `service/service.py:373`, `service/apply_guard.py:303`, `service/schedules.py:322` and the worker's log bridge in `service/flow.py`

### Shared test helper

- [X] T020 Create `tests/helpers/stored_credentials.py`: a fixture that creates stored credentials in a store (unit tier) and a function that creates them through the API (Compose, development stack, testcontainers), with a fixed test key; document its use in its module docstring

**Checkpoint**: unit tier green; nothing user-visible yet.

---

## Phase 3: User Story 1 — create a credential once and use it in several packages (P1) 🎯

**Goal**: an administrator creates a credential with the CLI, registers a package that names it, and any
worker runs it with no `INFRAHUB_SYNC_CREDENTIAL_*` variable.

**Independent test**: quickstart scenario 3; FR-001, FR-002, FR-004 to FR-008, FR-012, FR-013,
FR-016 to FR-019.

### Tests for User Story 1

- [X] T021 [P] [US1] Write `tests/service/test_credential_routes.py`: create, list, view, change value, change description, for an administrator; list and view for a non-administrator; create, change and describe refused for a non-administrator before body validation; `409 credential-exists`; `422 credential-fields-invalid`; a 422 does not repeat the body; no response contains the value; audit events hold names and revisions only (contracts/http-api.md, FR-001, FR-002, FR-012, FR-013, FR-014)
- [X] T022 [P] [US1] Write `tests/service/test_worker_credentials_route.py`: release for a claimed, non-terminal execution; refused for a finished run, for another run's `flow_run_id`, for a terminal execution (`409 execution-not-in-progress`); plan and sync get both blocks, apply, resume and verify get the destination only; no `mutation_receipts` row; `run_credential_uses` rows with `credential_id`; `credential-not-found`, `credential-field-missing`, `credential-undecryptable`; every account token and session, administrator included, gets `403 forbidden` (FR-004, FR-005, FR-016, research R5)
- [X] T023 [P] [US1] Write `tests/service/test_credential_client.py`: a 10-second timeout; three retries after 1, 2 and 4 seconds on connection errors, timeouts, `502`, `503`, `504`; no retry on `403`, `409`, `422`; the failure reasons of research R6; released values are in the run's redaction registry before the client returns (critique E3, E6)
- [X] T024 [P] [US1] Write `tests/product_store/test_credential_admission.py`: registration refuses `credential-not-found` and `credential-field-missing` (a credential with only `token` for a block that needs `username` and `password`); run start refuses the same after the credential is deleted or its fields change; a non-administrator cannot register a package that names a credential (FR-008, spec assumption "not bound to an address")
- [X] T025 [P] [US1] Write `tests/runtime_schema/test_stored_credential_execution.py`: two workers with no `INFRAHUB_SYNC_CREDENTIAL_*` variable complete plans of a package that names stored credentials; an error raised after the release and before `flow.py:1166` is redacted (FR-006, SC-003 at unit level, critique E6)

### Implementation for User Story 1

- [X] T026 [US1] Implement `infrahub_sync/service/credential_actions.py`: rights through one `manage-credentials` permission held by administrators (FR-015), the checks, refusals, audit events (`credential.created`, `credential.value_changed`, `credential.updated`), and the field-set rules
- [X] T027 [US1] Implement `infrahub_sync/service/credential_routes.py` with `GET /credentials`, `GET /credentials/{name}`, `POST /credentials`, `PUT /credentials/{name}/value`, `PATCH /credentials/{name}`, the mutating ones with `Depends(idempotency_key)`, following `service/account_routes.py`; mount it in `_include_routers` (`service/app.py:309`)
- [X] T028 [US1] Implement `POST /worker/runs/{run_id}/credentials` in `infrahub_sync/service/credential_routes.py` and its action in `credential_actions.py` (research R5): claimed non-terminal `prefect_executions` row check, blocks by stage, decrypt, `run_credential_uses` rows and the audit event in one transaction, a `structlog` event `credential.released` with names and revisions (critique E12), no idempotency key and no receipt
- [X] T029 [US1] Add the credential resources and requests to `infrahub_sync/client/models.py` and the methods to `infrahub_sync/client/client.py` (`SyncClient`)
- [X] T030 [US1] Implement `infrahub_sync/service/credential_client.py` (research R6): calls the worker route with `INFRAHUB_SYNC_WORKER_TOKEN`, the timeout and retry rules, `INFRAHUB_SYNC_CA_DIRECTORY` trust, adds released values to the run's redaction registry before it returns, and raises `CredentialUnavailableError` with a reason
- [X] T031 [US1] Change `resolve_runtime_instance` in `infrahub_sync/configuration/runtime.py` to take released values per block and fill the block settings through the field map of T017; keep the `$credential` path working until Phase 5
- [X] T032 [US1] Call `credential_client` in `_worker_execution_context` (`service/flow.py:897`) after the claim (`flow.py:1941`) for packages that use `credential:`, and in `_destination_settings` / `_earlier_proposed_changes_reader` (`flow.py:859-872`); read the destination token in `service/branch_review.py:171-177` from the released values
    - Done in chunk 8: the stage asks the worker route once for its execution and fills the block settings; the branch review client of a run uses the released token.
    - Done in chunk 8b (overnight log A2): for a destination that names a stored credential, `_earlier_proposed_changes_reader` calls the second worker route, `POST /worker/schedules/{schedule_id}/proposed-change-states`; the Sync API reads Infrahub itself and returns the states only.
- [X] T033 [US1] Map `CredentialUnavailableError` reasons to failure causes in `infrahub_sync/service/failure_cause.py` (`:261`, `:427-466`), with next-action texts that name `infrahub-sync credentials create <name>` and the administrator (FR-019, critique P5)
- [X] T034 [US1] Add the `credential-not-found`, `credential-field-missing` and `credential-not-used` findings to registration in `infrahub_sync/product_store/configs.py`, write `configuration_version_credentials` rows with each version, and lock the credential rows the version names in the same transaction (critique E4)
- [X] T035 [US1] Add the run-start check to the run-starting routes in `infrahub_sync/service/service.py` (near `:537`): plan, sync, apply, verify, approve, resume refuse with `422` and the codes of T034 (FR-008)
- [X] T036 [US1] Change the destination schema read in `infrahub_sync/configuration/capabilities.py:195-232` to take a decrypted token from the Sync API, which decrypts it inside the request's redaction registry and never returns it (FR-004)
- [X] T037 [P] [US1] Write `tests/cli/test_credentials_command.py`: `list`, `show`, `create` (prompt, `--token-stdin`, `--password-stdin`, `--username`), `set`, `describe`; no output contains the value; exit codes 0, 1, 2 (contracts/cli.md)
- [X] T038 [US1] Implement `infrahub_sync/cli_credentials.py` (`list`, `show`, `create`, `set`, `describe`) following `cli_accounts.py`, and register it in `infrahub_sync/cli.py`

**Checkpoint**: unit tier green; packages that use `credential:` run end to end; the old format still works.

---

## Phase 4: Move the tests, the examples, the tasks and the conversion

- [X] T039 [US1] Change `examples/netbox_to_infrahub/package.yml`, `examples/aci_to_infrahub/package.yml` and `examples/slurpit_to_infrahub/package.yml` to `credential:` in each block and no `credentials` block; update their comments to name the CLI commands that create the credentials (FR-023)
- [X] T040 [US1] Move `tests/configuration/` to the helper of T020 and `credential:` packages (`test_runtime.py`, `test_validation_core.py`, `test_validation_compatibility.py`, `test_contracts.py`, `test_parse_diagnostics.py`, `test_registered_adapter_authority.py`, `test_adapter_setting_conformance.py`, `test_schema_validation.py`, `test_validate_permissions.py`, `test_infrahub_lineage_validation.py`, `validation_packages.py`); one commit
- [X] T041 [US1] Move `tests/product_store/` (`test_validate_destination_schema.py`, `test_configs_service.py`, `test_contract.py`); one commit
- [X] T042 [US1] Move `tests/service/` (`failure_cases.py`, `schedule_fixtures.py`, `test_branch_review_operations.py`, `test_validation_secret_containment.py`, `test_http_api.py`, `test_registered_plan_apply.py`, `test_registered_schema_guard.py`, `test_verify_destination.py`, `test_worker_configuration_directory.py`, `test_managed_write_guard.py`, `test_approval*.py`, `test_apply_checkpoint_order.py`, `test_sync_checkpoint_order.py`, `test_sync_left_out_stop.py`, `test_sync_store_policy.py`, `test_cancel_planned_run.py`, `test_events_route.py`, `test_artifact_confidentiality.py`, `test_schedule_branch_review.py`); one commit
- [X] T043 [US1] Move `tests/runtime_schema/`, `tests/plan/test_report_credentials_absent.py`, `tests/adapters/test_aci_verify_precedence.py`, `tests/cli/test_parity_and_closure.py` and `tests/test_credential_canary.py`; one commit
- [X] T044 [US1] Change `tasks/dev.py` (`:120-123`, `:334`, `:480`, `:770-779`) to create the development credentials through the API after the stack starts, and change `tasks/bench.py` and `.github/scripts/nightly_e2e.py` in the same way; the variables stay in the Compose files until T054
- [X] T045 [P] [US3] Change `tests/configuration/test_conversion.py`, `test_conversion_examples.py` and `test_conversion_secrets.py` and `tests/cli/test_convert_command.py`: every v2 example in `examples/` converts to a package with `credential:` in each authenticated block, no `credentials` block, no `env` reference and no value (FR-009, SC-005)
- [X] T046 [US3] Change `infrahub_sync/configuration/conversion.py` (`:144-161`, `:481`, `:661-680`, `:747-780`): write `credential: <adapter>-<setting>` on the block and no `credentials` block; `CredentialEntry` holds the fields needed instead of an environment variable; stop importing `ENV_CREDENTIAL_PREFIX`; keep `COMPOSE_WORKER_CREDENTIAL_VARIABLES` until T053

**Checkpoint**: unit tier green; outside `infrahub_sync/configuration/credentials.py`, `models.py`,
`runtime.py`, `validation.py`, `worker_credentials.py`, the worker publishing, the Compose files and their
tests, nothing uses `INFRAHUB_SYNC_CREDENTIAL_*` or `$credential`.

---

## Phase 5: Remove the old path and record the change (end of US1)

### Removal

- [ ] T047 [US1] Write `tests/configuration/test_removed_credential_format.py`: a package with a `credentials` block or a `$credential` reference is refused at parse time with a message that names `credential: <name>`; a stored version in the old format is refused at run start and by the worker route with `credential-format-removed` (FR-021)
- [ ] T048 [US1] Remove `CredentialReference`, `CredentialReferenceNode`, `ConfigurationPackage.credentials` and the `/credentials` diagnostics from `infrahub_sync/configuration/models.py`; add the parse refusal of T047
- [ ] T049 [US1] Remove `EnvironmentCredentialProvider`, `CredentialProvider`, `provider_for`, `resolve_reference`, `ENV_CREDENTIAL_PREFIX` and `select_runtime_credential`'s environment branch from `infrahub_sync/configuration/credentials.py`, and their exports from `infrahub_sync/configuration/__init__.py`; remove the `$credential` path from `configuration/runtime.py`
- [ ] T050 [US1] Remove the reference findings that no longer apply (`malformed-credential-reference`, `unknown-credential-provider`, `unknown-credential-reference`) from `infrahub_sync/configuration/validation.py`; keep `inline-credential-value` and `setting-contains-credential-material`
- [X] T051 [US1] Remove `infrahub_sync/configuration/worker_credentials.py`, the publishing in `infrahub_sync/service/worker.py:382-437`, `publish_worker_environment`, `worker_environments`, `live_worker_environments` and the `worker_environments` statement in `infrahub_sync/product_store/store.py`, `WorkerEnvironment` in `product_store/models.py`, and `credential-not-set-in-worker` and `worker-environment-unknown` in `product_store/configs.py:827-910`; delete `tests/service/test_worker_environment.py` and `tests/product_store/test_worker_environments.py` (FR-022)
- [ ] T052 [US1] Add `credential-format-removed` to the run-start check of T035 and the worker route of T028
- [ ] T053 [US1] Remove `COMPOSE_WORKER_CREDENTIAL_SUFFIXES` from `configuration/credentials.py` and `COMPOSE_WORKER_CREDENTIAL_VARIABLES` from `configuration/conversion.py`, and delete `tests/configuration/test_conversion_worker_variables.py`, which checks that `docker-compose.yml` matches them
- [ ] T054 [US1] Change `docker-compose.yml`, `development/docker-compose.yml`, `infrahub_sync/testcontainers/docker-compose.yml` and `infrahub_sync/testcontainers/helpers.py`: add `INFRAHUB_SYNC_ENCRYPTION_KEY` (API only), `INFRAHUB_SYNC_WORKER_TOKEN` (API and worker), `INFRAHUB_SYNC_API_URL` (worker); remove every `INFRAHUB_SYNC_CREDENTIAL_*` (contracts/environment.md, FR-024)
- [ ] T055 [US1] Move `tests/compose/` (`test_source_credentials.py`, `test_credential_leaks.py`, `test_compose_contract.py`, `test_configuration_free_startup.py`, `test_container_cli.py`, `test_external_store.py`, `test_testcontainers_fixture.py`, `conftest.py`, `lifecycle.py`), `tests/dev_stack/`, `tests/integration/` and `tests/testcontainers/test_image_selection.py` to the API helper of T020; one commit
- [ ] T056 [US1] Write `tests/test_stored_credential_marker.py`: one test that creates a credential with a marker value, runs a plan and an apply on the unit tier, and searches every API answer except the worker route, the CLI output, the captured logs, the run records and a dump of the SQLite store for the marker (SC-004, FR-002, FR-007, critique P6)

### Records and documentation of the first increment

- [ ] T057 [P] [US1] Write `dev/adr/0054-the-sync-api-stores-credentials-and-releases-them-to-the-worker-account.md`: the decision, the worker account, encryption, rollback (critique E5), a later external store (critique P3), the validate route (critique E9), the receipt check that start-up replaces (overnight log A1) with its two limits: an earlier build that starts later adds its old check back, which on PostgreSQL probably fails once a credential receipt exists; and old and new builds running on one database at the same time can leave both checks in place, so credential changes are refused until a new build starts again; the second worker route, `POST /worker/schedules/{schedule_id}/proposed-change-states`, through which the Sync API reads the states of a schedule's earlier proposed changes itself and returns the states only, because that read comes before the run exists (overnight log A2), and that this route makes the worker boundary wider; and the limit of the execution check: workers write `prefect_executions` with the same database password, so a compromised worker can create an execution row for any run and get its values; mark ADR 0039 superseded and ADR 0026 and ADR 0040 changed by 0054; update `dev/adr/README.md` (research R12)
- [ ] T058 [P] [US1] Amend `.specify/memory/constitution.md` principle VI and the security requirements to allow values encrypted in the Sync database and released only through the worker route, version 1.3.0, last amended date; change the matching line of `AGENTS.md` "Configuration and Examples" and add `credentials` to "CLI Commands" (FR-026)
- [ ] T059 [P] [US1] Write `docs/docs/guides/credentials.mdx` (create, change, the worker account, the key, losing the key) and add it to the sidebar
- [ ] T060 [P] [US1] Update `docs/docs/configuration-package.mdx`, `compose-deployment.mdx`, `kubernetes-deployment.mdx`, `v2-to-v3.mdx`, `reference/durable-product-records.mdx`, `reference/sync-http-api.mdx`, `guides/accounts.mdx`, `development-stack.mdx`, `running-a-sync.mdx`, each `docs/docs/adapters/*.mdx` and `README.md`; the deployment pages say that changing the key is not supported, that losing it means re-entering every credential, and that the worker's call is plain HTTP inside Compose (FR-025, critique E7)
- [ ] T061 [US1] Run `uv run invoke docs.generate` for the CLI reference and `uv run invoke docs.docusaurus`
- [ ] T062 [P] [US1] Add `changelog/+stored-credentials.added.md` and `changelog/+credential-env-provider.removed.md`

**Checkpoint**: `grep -rn 'INFRAHUB_SYNC_CREDENTIAL_\|\$credential\|worker_environment' infrahub_sync tests tasks examples docs *.yml development` returns nothing except refusal messages, their tests and the superseded decision record. The first increment is releasable.

---

## Phase 6: User Story 2 — change a value in one place (P2)

**Goal**: changing a value creates a revision, no new version, and each run records the revision it used.

**Independent test**: quickstart scenario 4; FR-003, SC-002.

- [ ] T063 [P] [US2] Write `tests/service/test_credential_revisions_on_runs.py`: 5 packages name `netbox-lab`; one value change registers no version; the next run of each records revision 2; a plan with revision 3, a change, and the apply records revision 4 for the apply and 3 for the plan (FR-003, SC-002, spec edge case)
- [ ] T064 [US2] Add `credentials_used: [{stage, block, name, revision}]` to the run detail answer in `infrahub_sync/service/service.py` and `infrahub_sync/client/models.py`, read from `run_credential_uses`
- [ ] T065 [US2] Show a "Credentials used" section in `infrahub-sync runs show` in `infrahub_sync/cli.py`, add a CLI test to `tests/cli/test_credentials_command.py`, and describe it in `docs/docs/guides/credentials.mdx`

---

## Phase 7: User Story 4 — delete a credential that nothing current uses (P3)

**Goal**: deletion is refused while a latest version or a schedule that is not deleted names the
credential, and the refusal lists them.

**Independent test**: spec User Story 4; FR-010, FR-011.

- [ ] T066 [P] [US4] Write `tests/service/test_credential_deletion.py`: refused by a latest version (lists the configuration), refused by a schedule that is not deleted whose pinned version names it (lists the schedule), allowed when only an older version names it, then a run of that older version refused before it starts; no route renames a credential (FR-010, FR-011)
- [ ] T067 [US4] Implement the in-use query in `infrahub_sync/product_store/stored_credentials.py` over `configuration_version_credentials`, the latest `registry_version` of each configuration and `schedules` with `state <> 'deleted'` (research R8), and the deletion with the row lock of T034
- [ ] T068 [US4] Implement `DELETE /credentials/{name}` with `409 credential-in-use` and `details` in `infrahub_sync/service/credential_routes.py` and `credential_actions.py`, with the audit event `credential.deleted`
- [ ] T069 [US4] Add `credentials delete NAME [--yes]` to `infrahub_sync/cli_credentials.py` with confirmation and the in-use listing, its tests in `tests/cli/test_credentials_command.py`, and a "Delete a credential" section in `docs/docs/guides/credentials.mdx`

---

## Phase 8: User Story 3 — register a converted v2 file (P3)

**Goal**: the conversion lists the credentials to create, and registration names the missing ones.

**Independent test**: quickstart scenario 7; FR-009, SC-005.

- [ ] T070 [P] [US3] Add to `tests/cli/test_convert_command.py`: the report lists each credential, its fields and the `credentials create` command; `configs register` of a converted package before its credentials exist names each missing credential and its fields
- [ ] T071 [US3] Change the report printed by `configs convert` in `infrahub_sync/cli.py:916-994` to list the credentials to create with the command for each
- [ ] T072 [US3] Update `docs/docs/v2-to-v3.mdx` with the report and the commands

---

## Phase 9: Web interface

Plan step 5. `pnpm` commands run in `frontend/`. Regenerate `frontend/src/api/types.generated.ts` with
`pnpm codegen:openapi` against a running API first.

**Design method (maintainer instruction, 2026-11-20)**: every screen of this phase is designed with the
`emil-prototype` skill first. It builds several different versions behind a live picker, with the real
API data. The maintainer picks one version per screen. Only then do T075 to T078 build the chosen
version. Remove the prototype route and the picker before the phase ends.

- [ ] T073 [US1] Regenerate `frontend/src/api/types.generated.ts`, and add `credentialsQuery`, `credentialQuery`, the create, change-value and describe actions, and the error descriptions to `frontend/src/api/queries.ts` and `frontend/src/api/actions.ts`
- [ ] T074 [US1] Run the `emil-prototype` skill for the Credentials page (list, the create and change-value forms with the three field sets, the administrator and non-administrator views, the delete confirmation with the in-use listing), the "Credentials used" section of the run details and, if spec 025 is merged, the drop-area offer; use the queries of T073 and the contracts of contracts/web-interface.md; stop until the maintainer picks one version per screen, and record the choices in `dev/specs/027-stored-credentials/notes.md`
- [ ] T075 [US1] Build the chosen version: create `frontend/src/routes/credentials.tsx` and `frontend/src/features/credentials/` (list, "New credential", "Change value", "Edit description"), password inputs cleared after each request, administrator actions hidden for other accounts with the note of contracts/web-interface.md; add the navigation entry in `frontend/src/routes/__root.tsx`
- [ ] T076 [US4] Add "Delete" with confirmation and the in-use listing with links to `frontend/src/features/credentials/`
- [ ] T077 [US2] Add the "Credentials used" section to the run details in `frontend/src/features/runs/`
- [ ] T078 [US3] If spec 025 is merged on the base, add the offer "Create credential `<name>`" for each `credential-not-found` finding in the drop area, and "Ask an administrator to create the credential `<name>`" for other accounts; otherwise record it as a follow-up of spec 025 in `dev/specs/027-stored-credentials/notes.md`
- [ ] T079 Run `pnpm check`, `pnpm typecheck` and `pnpm build` in `frontend/`, and the web scenarios of `quickstart.md`

---

## Phase 10: Polish and cross-cutting concerns

- [ ] T080 Open the Helm chart pull request in `opsmill/infrahub-helm` (`charts/infrahub-sync/values.yaml`: add `credentials.encryptionKey` (API only) and `credentials.workerToken` (API and worker), remove `credentials.infrahubApiToken`, `worker.credentials` and `worker.credentialsExistingSecrets`, and the matching templates and tests); only when the user asks for a pull request (FR-024)
- [ ] T081 Run the full verification (`uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, the CLI sanity commands of `AGENTS.md`, `uv run ty check .`) and the development-stack scenarios 3 to 7 of `quickstart.md`

---

## Dependencies and execution order

### Phase dependencies

- Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5: strictly in order (plan, "Delivery order").
- Phases 6, 7 and 8 depend on Phase 5 and are independent of one another.
- Phase 9 depends on Phases 6 to 8 for the parts it shows.
- Phase 10 depends on every earlier phase.

### User story dependencies

- **US1 (P1)**: Phases 2 to 5. Every other story depends on it.
- **US2 (P2)**: needs `run_credential_uses` from T028.
- **US4 (P3)**: needs `configuration_version_credentials` from T034.
- **US3 (P3)**: the conversion itself changes in Phase 4 (T045, T046), because Phase 5 removes what the
  old conversion writes; Phase 8 adds the report and the registration check.

### Within each phase

- Tests before implementation; models before routes; routes before the CLI and the web page.

### Parallel opportunities

- T002 and T003; T004 with T006, T010, T014 and T016 (different files).
- T021 to T025 (tests in different files).
- T057 to T060 and T062.
- T063, T066 and T070 once Phase 5 is done.

## Parallel example: User Story 1

```text
T021 tests/service/test_credential_routes.py
T022 tests/service/test_worker_credentials_route.py
T023 tests/service/test_credential_client.py
T024 tests/product_store/test_credential_admission.py
T025 tests/runtime_schema/test_stored_credential_execution.py
```

## Implementation strategy

### First releasable increment (User Story 1)

Phases 1 to 5. An administrator creates credentials with the CLI, registers a package and runs it on
any worker; the documentation, the decision record and the constitution describe it. Stop and run
quickstart scenarios 1 to 3, 5 and 6.

### Incremental delivery

1. US1 (Phases 1 to 5): stored credentials replace `env`.
2. US2 (Phase 6): revisions on runs.
3. US4 (Phase 7): deletion.
4. US3 (Phase 8): the conversion report.
5. Web interface (Phase 9).
6. The Helm pull request in `opsmill/infrahub-helm` (T080), on request.

## Notes

- Commit with explicit paths (`git commit -- <paths>`); the checkout may be shared.
- Never print a value in a test failure message; compare digests instead.
- The direct Prefect flow (`infrahub_sync/orchestration/`) does not change.
