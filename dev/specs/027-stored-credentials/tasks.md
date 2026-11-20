# Tasks: Stored credentials managed through the Sync API

**Input**: Design documents from `dev/specs/027-stored-credentials/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md),
[critique](critiques/critique-20261120.md)

**Tests**: requested. Each requirement of the spec has a Verification line, and constitution principle
V requires tests with the change. Write each test task before the implementation it covers, and see it
fail first.

**Order rule (plan, "Delivery order")**: the unit tier (`uv run invoke tests.tests-unit`) stays green
at the end of every phase. Phases 2 and 3 add the new path beside the old one; Phase 4 moves the tests
and the examples; Phase 5 removes the old path.

**Verification after each phase**: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`,
`uv run invoke tests.tests-unit`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 create and use (P1), US2 change once (P2), US3 converted v2 file (P3), US4 delete (P3)

---

## Phase 1: Setup

- [ ] T001 Add `cryptography` with a range pin (for example `>=44,<47`, matching the version in `uv.lock`) to the `service` extra in `pyproject.toml`, run `uv lock`, and check with `uv pip show` in a base-only environment that the base install does not pull it (research R3, critique E13)
- [ ] T002 [P] Add `INFRAHUB_SYNC_ENCRYPTION_KEY`, `INFRAHUB_SYNC_WORKER_TOKEN` and the worker's `INFRAHUB_SYNC_API_URL` to `infrahub_sync/service/settings.py`: loaders that refuse a missing key, a key that is not 32 bytes after base64 decoding, a worker token shorter than the `admin` token minimum, with an `ifs_pat_` or `ifs_ses_` prefix, or equal to the `admin` token; messages name the variable and never the value; `repr=False` on values (contracts/environment.md, critique E8)
- [ ] T003 [P] Write `tests/service/test_credential_settings.py`: parametrized refusals of T002 for each rule, and a check that no message contains the value

---

## Phase 2: Foundational (blocks every user story)

### Encryption

- [ ] T004 Write `tests/service/test_credential_crypto.py`: round trip; a ciphertext copied to another `credential_id` or revision fails; a wrong key is reported as `CredentialDecryptionError` with the key identifier mismatch before decryption; the key identifier is stable for one key
- [ ] T005 Implement `infrahub_sync/service/credential_crypto.py`: `CredentialCipher` with `encrypt(credential_id, revision, fields) -> (key_id, nonce, ciphertext)` and `decrypt(...)`, AES-256-GCM through `cryptography.hazmat.primitives.ciphers.aead.AESGCM`, associated data `infrahub-sync/credential/v1|<credential_id>|<revision>`, random 12-byte nonce, `key_id` = first 8 bytes of SHA-256 of the key in hex; `CredentialDecryptionError` (research R3)

### Store

- [ ] T006 Write `tests/product_store/test_stored_credentials.py` on SQLite and PostgreSQL as the existing store tests do: create, read by name, list sorted by name with offset and limit, new revision on value change, description change keeps the revision, unique name, field-set rules of data-model.md, `configuration_version_credentials` rows written with a version, `run_credential_uses` replace on a second release for the same execution and block
- [ ] T007 Add the four tables of data-model.md (`stored_credentials`, `stored_credential_revisions`, `configuration_version_credentials`, `run_credential_uses`) and the index on `configuration_version_credentials.credential_name` to `_SCHEMA` and the index tuples in `infrahub_sync/product_store/store.py`; add no column to an existing table
- [ ] T008 Implement `infrahub_sync/product_store/stored_credentials.py` with the statements for T006, called with `(cursor, self._sql, ...)` as `product_store/accounts.py` is; the name pattern `^[a-z][a-z0-9-]{0,62}$`; a description of at most 500 characters
- [ ] T009 Add `StoredCredential`, `StoredCredentialRevision` and `CredentialUse` to `infrahub_sync/product_store/models.py`, and facade methods on `ProductProjection` and `_RunStore` in `infrahub_sync/product_store/store.py`

### The worker account

- [ ] T010 Write `tests/service/test_worker_account.py`: `sync-api` start applies a `worker` account (environment-managed, no password, not an administrator) and its token; a second start with a new token replaces it; an existing non-environment-managed account named `worker` stops the start with a named conflict; sign-in as `worker` is refused; every account action on `worker` answers `409 environment-managed-account`
- [ ] T011 Implement `apply_worker_account` in `infrahub_sync/service/auth.py` beside `apply_admin_account` (`auth.py:166`), call it from `infrahub_sync/service/serve.py:136-155`, and add the worker token to `resolver.secret_values` (research R4)
- [ ] T012 Write `tests/service/test_worker_route_boundary.py`: parametrized over every route of `create_app`, the worker token gets `403 forbidden` outside `/api/worker/`; every account token and session, administrator included, gets `403 forbidden` on `/api/worker/` routes (FR-005)
- [ ] T013 Add the worker-only rule to `_authenticator` in `infrahub_sync/service/app.py:334` and a `require_worker` dependency for the worker routes; `Principal` (`auth.py:36`) gains a `worker` flag set from "named `worker` and environment-managed"

### Package format beside the old one

- [ ] T014 Write parametrized tests in `tests/configuration/test_block_credential.py`: `credential: <name>` accepted on source, destination and store blocks; the name pattern; a block that names a credential refuses its credential settings in `settings`; the checksum changes when the name changes; for now a package with the old `credentials` block still parses
- [ ] T015 Add the optional `credential` key to the adapter block and store block models in `infrahub_sync/configuration/models.py`, and the refusal of credential settings in `settings` when `credential` is set
- [ ] T016 Write `tests/configuration/test_credential_field_map.py`: parametrized over every adapter and `auth_method` of research R2, the fields needed and the setting each field fills; `credential-not-used` for a method that needs none; the Redis store needs `username` and `password`
- [ ] T017 Implement the field map in `infrahub_sync/configuration/capabilities.py` next to `credential_setting_paths` (`:736-815`) and `_STORE_CAPABILITIES` (`configuration/credentials.py:132`), reusing the method table of `configuration/conversion_authentication.py:30-65`

### Redaction from the moment of use (critique E6)

- [ ] T018 Write `tests/service/test_released_value_redaction.py`: an error that holds a released value, raised inside the worker between release and `collect_secret_values` (`flow.py:1166`), is redacted in logs and in the failure cause; an error that holds a decrypted value raised inside the Sync API's destination schema read is redacted in the log and the answer
- [ ] T019 Implement a per-run and per-request redaction registry used by `infrahub_sync/execution.py` `redact`, so that `credential_crypto.decrypt` and the worker client add each value before they return it; wire it into `service/service.py:373`, `service/apply_guard.py:303`, `service/schedules.py:322` and the worker's log bridge in `service/flow.py`

### Shared test helper

- [ ] T020 Create `tests/helpers/stored_credentials.py`: a fixture that creates stored credentials in a store (unit tier) and a function that creates them through the API (Compose, development stack, testcontainers), with a fixed test key; document its use in its module docstring

**Checkpoint**: unit tier green; nothing user-visible yet.

---

## Phase 3: User Story 1 — create a credential once and use it in several packages (P1) 🎯

**Goal**: an administrator creates a credential with the CLI, registers a package that names it, and any
worker runs it with no `INFRAHUB_SYNC_CREDENTIAL_*` variable.

**Independent test**: quickstart scenario 3; FR-001, FR-002, FR-004 to FR-008, FR-012, FR-013,
FR-016 to FR-019.

### Tests for User Story 1

- [ ] T021 [P] [US1] Write `tests/service/test_credential_routes.py`: create, list, view, change value, change description, for an administrator; list and view for a non-administrator; create, change and describe refused for a non-administrator before body validation; `409 credential-exists`; `422 credential-fields-invalid`; a 422 does not repeat the body; no response contains the value; audit events hold names and revisions only (contracts/http-api.md, FR-001, FR-002, FR-012, FR-013, FR-014)
- [ ] T022 [P] [US1] Write `tests/service/test_worker_credentials_route.py`: release for a claimed, non-terminal execution; refused for a finished run, for another run's `flow_run_id`, for a terminal execution (`409 execution-not-in-progress`); only the blocks the stage needs (plan and sync get all, apply, resume and verify get destination and store when the stage builds it); no `mutation_receipts` row; `run_credential_uses` rows with `credential_id`; `credential-not-found`, `credential-field-missing`, `credential-undecryptable`, `credential-format-removed` (FR-004, FR-016, research R5)
- [ ] T023 [P] [US1] Write `tests/service/test_credential_client.py`: a 10-second timeout; three retries after 1, 2 and 4 seconds on connection errors, timeouts, `502`, `503`, `504`; no retry on `403`, `409`, `422`; the failure reasons of research R6 (critique E3)
- [ ] T024 [P] [US1] Write `tests/product_store/test_credential_admission.py`: registration refuses `credential-not-found` and `credential-field-missing` (a credential with only `token` for a block that needs `username` and `password`); run start refuses the same after the credential is deleted or its fields change; a non-administrator cannot register a package that names a credential (FR-008, spec assumption "not bound to an address")
- [ ] T025 [P] [US1] Write `tests/runtime_schema/test_stored_credential_execution.py`: two workers with no `INFRAHUB_SYNC_CREDENTIAL_*` variable complete plans of a package that names stored credentials (FR-006, SC-003 at unit level)

### Implementation for User Story 1

- [ ] T026 [US1] Implement `infrahub_sync/service/credential_actions.py`: rights through one `manage-credentials` permission held by administrators (FR-015), the checks, refusals, audit events (`credential.created`, `credential.value_changed`, `credential.updated`), and the field-set rules
- [ ] T027 [US1] Implement `infrahub_sync/service/credential_routes.py` with `GET /credentials`, `GET /credentials/{name}`, `POST /credentials`, `PUT /credentials/{name}/value`, `PATCH /credentials/{name}`, the mutating ones with `Depends(idempotency_key)`, following `service/account_routes.py`; mount it in `_include_routers` (`service/app.py:309`)
- [ ] T028 [US1] Implement `POST /worker/runs/{run_id}/credentials` in `infrahub_sync/service/credential_routes.py` and its action in `credential_actions.py` (research R5): claimed non-terminal `prefect_executions` row check, blocks by stage, decrypt, `run_credential_uses` rows and the audit event in one transaction, a `structlog` event `credential.released` with names and revisions (critique E12), no idempotency key and no receipt
- [ ] T029 [US1] Add the credential resources and requests to `infrahub_sync/client/models.py` and the methods to `infrahub_sync/client/client.py` (`SyncClient`)
- [ ] T030 [US1] Implement `infrahub_sync/service/credential_client.py` (research R6): calls the worker route with `INFRAHUB_SYNC_WORKER_TOKEN`, the timeout and retry rules, `INFRAHUB_SYNC_CA_DIRECTORY` trust, adds released values to the redaction registry before it returns, and raises `CredentialUnavailableError` with a reason
- [ ] T031 [US1] Change `resolve_runtime_instance` in `infrahub_sync/configuration/runtime.py` to take released values per block and fill the block settings through the field map of T017; keep the `$credential` path working until Phase 5
- [ ] T032 [US1] Call `credential_client` in `_worker_execution_context` (`service/flow.py:897`) after the claim (`flow.py:1941`) for packages that use `credential:`, and in `_destination_settings` / `_earlier_proposed_changes_reader` (`flow.py:859-872`); read the destination token in `service/branch_review.py:171-177` from the released values
- [ ] T033 [US1] Map `CredentialUnavailableError` reasons to failure causes in `infrahub_sync/service/failure_cause.py` (`:261`, `:427-466`), with next-action texts that name `infrahub-sync credentials create <name>` and the administrator (FR-019, critique P5)
- [ ] T034 [US1] Add the `credential-not-found`, `credential-field-missing` and `credential-not-used` findings to registration in `infrahub_sync/product_store/configs.py`, write `configuration_version_credentials` rows with each version, and lock the credential rows the version names in the same transaction (critique E4)
- [ ] T035 [US1] Add the run-start check to the run-starting routes in `infrahub_sync/service/service.py` (near `:537`): plan, sync, apply, verify, approve, resume refuse with `422` and the codes of T034 (FR-008)
- [ ] T036 [US1] Change the destination schema read in `infrahub_sync/configuration/capabilities.py:195-232` to take a decrypted token from the Sync API, which decrypts it inside the request's redaction registry and never returns it (FR-004)
- [ ] T037 [P] [US1] Write `tests/cli/test_credentials_command.py`: `list`, `show`, `create` (prompt, `--token-stdin`, `--password-stdin`, `--username`), `set`, `describe`; no output contains the value; exit codes 0, 1, 2 (contracts/cli.md)
- [ ] T038 [US1] Implement `infrahub_sync/cli_credentials.py` (`list`, `show`, `create`, `set`, `describe`) following `cli_accounts.py`, register it in `infrahub_sync/cli.py`; the `create` help names the Redis username `default` (critique P4)

**Checkpoint**: unit tier green; packages that use `credential:` run end to end; the old format still works.

---

## Phase 4: Move the tests, the tasks and the examples (part of US1)

- [ ] T039 [US1] Change `examples/netbox_to_infrahub/package.yml`, `examples/aci_to_infrahub/package.yml` and `examples/slurpit_to_infrahub/package.yml` to `credential:` in each block and no `credentials` block; update their comments to name the CLI commands that create the credentials (FR-023)
- [ ] T040 [US1] Move `tests/configuration/` to the helper of T020 and `credential:` packages (`test_runtime.py`, `test_validation_core.py`, `test_validation_compatibility.py`, `test_contracts.py`, `test_parse_diagnostics.py`, `test_registered_adapter_authority.py`, `test_adapter_setting_conformance.py`, `test_schema_validation.py`, `test_validate_permissions.py`, `test_infrahub_lineage_validation.py`, `validation_packages.py`); one commit
- [ ] T041 [US1] Move `tests/product_store/` (`test_validate_destination_schema.py`, `test_configs_service.py`, `test_contract.py`); one commit
- [ ] T042 [US1] Move `tests/service/` (`failure_cases.py`, `schedule_fixtures.py`, `test_branch_review_operations.py`, `test_validation_secret_containment.py`, `test_http_api.py`, `test_registered_plan_apply.py`, `test_registered_schema_guard.py`, `test_verify_destination.py`, `test_worker_configuration_directory.py`, `test_managed_write_guard.py`, `test_approval*.py`, `test_apply_checkpoint_order.py`, `test_sync_checkpoint_order.py`, `test_sync_left_out_stop.py`, `test_sync_store_policy.py`, `test_cancel_planned_run.py`, `test_events_route.py`, `test_artifact_confidentiality.py`, `test_schedule_branch_review.py`); one commit
- [ ] T043 [US1] Move `tests/runtime_schema/`, `tests/plan/test_report_credentials_absent.py`, `tests/adapters/test_aci_verify_precedence.py`, `tests/cli/test_parity_and_closure.py` and `tests/test_credential_canary.py`; one commit
- [ ] T044 [US1] Change `tasks/dev.py` (`:120-123`, `:334`, `:480`, `:770-779`) to create the development credentials through the API after the stack starts instead of setting `INFRAHUB_SYNC_CREDENTIAL_*`, and change `tasks/bench.py` and `.github/scripts/nightly_e2e.py` in the same way
- [ ] T045 [US1] Change `docker-compose.yml`, `development/docker-compose.yml`, `infrahub_sync/testcontainers/docker-compose.yml` and `infrahub_sync/testcontainers/helpers.py`: add `INFRAHUB_SYNC_ENCRYPTION_KEY` (API only), `INFRAHUB_SYNC_WORKER_TOKEN` (API and worker), `INFRAHUB_SYNC_API_URL` (worker); remove every `INFRAHUB_SYNC_CREDENTIAL_*` (contracts/environment.md, FR-024)
- [ ] T046 [US1] Move `tests/compose/` (`test_source_credentials.py`, `test_credential_leaks.py`, `test_compose_contract.py`, `test_configuration_free_startup.py`, `test_container_cli.py`, `test_external_store.py`, `test_testcontainers_fixture.py`, `conftest.py`, `lifecycle.py`), `tests/dev_stack/`, `tests/integration/` and `tests/testcontainers/test_image_selection.py` to the API helper of T020; one commit

**Checkpoint**: no test, example, task or deployment file uses `INFRAHUB_SYNC_CREDENTIAL_*` or `$credential` (`grep -rn 'INFRAHUB_SYNC_CREDENTIAL_\|\$credential' tests tasks examples infrahub_sync/testcontainers *.yml development` returns only the code removed in Phase 5).

---

## Phase 5: Remove the old path (part of US1)

- [ ] T047 [US1] Write `tests/configuration/test_removed_credential_format.py`: a package with a `credentials` block or a `$credential` reference is refused at parse time with a message that names `credential: <name>`; a stored version in the old format is refused at run start with `credential-format-removed` (FR-021)
- [ ] T048 [US1] Remove `CredentialReference`, `CredentialReferenceNode`, `ConfigurationPackage.credentials` and the `/credentials` diagnostics from `infrahub_sync/configuration/models.py`; add the parse refusal of T047
- [ ] T049 [US1] Remove `EnvironmentCredentialProvider`, `CredentialProvider`, `provider_for`, `resolve_reference`, `ENV_CREDENTIAL_PREFIX`, `COMPOSE_WORKER_CREDENTIAL_SUFFIXES` and `select_runtime_credential`'s environment branch from `infrahub_sync/configuration/credentials.py`, and their exports from `infrahub_sync/configuration/__init__.py`; remove the `$credential` path from `configuration/runtime.py`
- [ ] T050 [US1] Remove the reference findings that no longer apply (`malformed-credential-reference`, `unknown-credential-provider`, `unknown-credential-reference`) from `infrahub_sync/configuration/validation.py`, keep `inline-credential-value` and `setting-contains-credential-material`
- [ ] T051 [US1] Remove `infrahub_sync/configuration/worker_credentials.py`, the publishing in `infrahub_sync/service/worker.py:382-437`, `publish_worker_environment`, `worker_environments`, `live_worker_environments` and the `worker_environments` statement in `infrahub_sync/product_store/store.py`, `WorkerEnvironment` in `product_store/models.py`, and `credential-not-set-in-worker` and `worker-environment-unknown` in `product_store/configs.py:827-910`; delete `tests/service/test_worker_environment.py`, `tests/product_store/test_worker_environments.py` and `tests/configuration/test_conversion_worker_variables.py` (FR-022)
- [ ] T052 [US1] Add `credential-format-removed` to the run-start check of T035 and the worker route of T028

**Checkpoint**: `grep -rn 'INFRAHUB_SYNC_CREDENTIAL_\|\$credential\|worker_environment' infrahub_sync tests tasks examples` returns nothing except refusal messages and their tests.

---

## Phase 6: User Story 2 — change a value in one place (P2)

**Goal**: changing a value creates a revision, no new version, and each run records the revision it used.

**Independent test**: quickstart scenario 4; FR-003, SC-002.

- [ ] T053 [P] [US2] Write `tests/service/test_credential_revisions_on_runs.py`: 5 packages name `netbox-lab`; one value change registers no version; the next run of each records revision 2; a plan with revision 3, a change, and the apply records revision 4 for the apply and 3 for the plan (FR-003, SC-002, spec edge case)
- [ ] T054 [US2] Add `credentials_used: [{stage, block, name, revision}]` to the run detail answer in `infrahub_sync/service/service.py` and `infrahub_sync/client/models.py`, read from `run_credential_uses`
- [ ] T055 [US2] Show a "Credentials used" section in `infrahub-sync runs show` in `infrahub_sync/cli.py`, and add a CLI test to `tests/cli/test_credentials_command.py`

---

## Phase 7: User Story 4 — delete a credential that nothing current uses (P3)

**Goal**: deletion is refused while a latest version or a schedule that is not deleted names the
credential, and the refusal lists them.

**Independent test**: spec User Story 4; FR-010, FR-011.

- [ ] T056 [P] [US4] Write `tests/service/test_credential_deletion.py`: refused by a latest version (lists the configuration), refused by a schedule that is not deleted whose pinned version names it (lists the schedule), allowed when only an older version names it, then a run of that older version refused before it starts; no route renames a credential (FR-010, FR-011)
- [ ] T057 [US4] Implement the in-use query in `infrahub_sync/product_store/stored_credentials.py` over `configuration_version_credentials`, the latest `registry_version` of each configuration and `schedules` with `state <> 'deleted'` (research R8), and the deletion with the row lock of T034
- [ ] T058 [US4] Implement `DELETE /credentials/{name}` with `409 credential-in-use` and `details` in `infrahub_sync/service/credential_routes.py` and `credential_actions.py`, with the audit event `credential.deleted`
- [ ] T059 [US4] Add `credentials delete NAME [--yes]` to `infrahub_sync/cli_credentials.py` with confirmation and the in-use listing, and its tests in `tests/cli/test_credentials_command.py`

---

## Phase 8: User Story 3 — register a converted v2 file (P3)

**Goal**: conversion writes `credential: <name>` in each authenticated block and lists the credentials
to create.

**Independent test**: quickstart scenario 7; FR-009, SC-005.

- [ ] T060 [P] [US3] Change `tests/configuration/test_conversion.py`, `test_conversion_examples.py` and `test_conversion_secrets.py` and `tests/cli/test_convert_command.py`: every v2 example in `examples/` converts to a package with `credential:` in each authenticated block, no `credentials` block, no `env` reference and no value; the report lists each credential, its fields and the `credentials create` command (FR-009, SC-005)
- [ ] T061 [US3] Change `infrahub_sync/configuration/conversion.py` (`:144-161`, `:481`, `:661-680`, `:747-780`): write `credential: <adapter>-<setting>` on the block, no `credentials` block; `CredentialEntry` holds the fields needed instead of an environment variable; remove `COMPOSE_WORKER_CREDENTIAL_VARIABLES`
- [ ] T062 [US3] Change the report printed by `configs convert` in `infrahub_sync/cli.py:916-994` to list the credentials to create with the command for each, and name the Redis username `default` when the store block needs one
- [ ] T063 [US3] Check that `configs register` of a converted package before its credentials exist names each missing credential and its fields; add the case to `tests/cli/test_convert_command.py`

---

## Phase 9: Web interface

Plan step 5. `pnpm` commands run in `frontend/`. Regenerate `frontend/src/api/types.generated.ts` with
`pnpm codegen:openapi` against a running API first.

- [ ] T064 [US1] Regenerate `frontend/src/api/types.generated.ts`, and add `credentialsQuery`, `credentialQuery`, the create, change-value and describe actions, and the error descriptions to `frontend/src/api/queries.ts` and `frontend/src/api/actions.ts`
- [ ] T065 [US1] Create `frontend/src/routes/credentials.tsx` and `frontend/src/features/credentials/` (list, "New credential", "Change value", "Edit description"), password inputs cleared after each request, administrator actions hidden for other accounts with the note of contracts/web-interface.md; add the navigation entry in `frontend/src/routes/__root.tsx`
- [ ] T066 [US4] Add "Delete" with confirmation and the in-use listing with links to `frontend/src/features/credentials/`
- [ ] T067 [US2] Add the "Credentials used" section to the run details in `frontend/src/features/runs/`
- [ ] T068 [US3] If spec 025 is merged on the base, add the offer "Create credential `<name>`" for each `credential-not-found` finding in the drop area, and "Ask an administrator to create the credential `<name>`" for other accounts; otherwise record it as a follow-up of spec 025 in `dev/specs/027-stored-credentials/notes.md`
- [ ] T069 Run `pnpm check`, `pnpm typecheck` and `pnpm build` in `frontend/`, and the web scenarios of `quickstart.md`

---

## Phase 10: Polish and cross-cutting concerns

- [ ] T070 [P] Write `tests/test_stored_credential_marker.py`: one test that creates a credential with a marker value, runs a plan and an apply on the unit tier, and searches every API answer except the worker route, the CLI output, the captured logs, the run records and a dump of the SQLite store for the marker (SC-004, FR-002, FR-007, critique P6)
- [ ] T071 [P] Write `dev/adr/0054-the-sync-api-stores-credentials-and-releases-them-to-the-worker-account.md` (decision, the worker account, encryption, rollback, the later external store, the validate route, research R12) and mark ADR 0039 superseded, ADR 0026 and ADR 0040 changed by 0054; update `dev/adr/README.md`
- [ ] T072 [P] Amend `.specify/memory/constitution.md` principle VI and the security requirements to allow values encrypted in the Sync database and released only through the worker route, version 1.3.0, last amended date; change the matching line of `AGENTS.md` "Configuration and Examples" and add `credentials` to "CLI Commands" (FR-026)
- [ ] T073 [P] Write `docs/docs/guides/credentials.mdx` (create, change, delete, the worker account, the key, losing the key, the Redis username) and add it to the sidebar
- [ ] T074 [P] Update `docs/docs/configuration-package.mdx`, `compose-deployment.mdx`, `kubernetes-deployment.mdx`, `v2-to-v3.mdx`, `reference/durable-product-records.mdx`, `reference/sync-http-api.mdx`, `guides/accounts.mdx`, `development-stack.mdx`, `running-a-sync.mdx` and each `docs/docs/adapters/*.mdx` and `README.md`; the deployment pages say that changing the key is not supported, that losing it means re-entering every credential, and that the worker's call is plain HTTP inside Compose (FR-025, critique E7)
- [ ] T075 Run `uv run invoke docs.generate` for the CLI reference and `uv run invoke docs.docusaurus`
- [ ] T076 [P] Add `changelog/+stored-credentials.added.md` and `changelog/+credential-env-provider.removed.md`
- [ ] T077 Open the Helm chart pull request in `opsmill/infrahub-helm` (`charts/infrahub-sync/values.yaml`: add `credentials.encryptionKey` (API only) and `credentials.workerToken` (API and worker), remove `credentials.infrahubApiToken`, `worker.credentials` and `worker.credentialsExistingSecrets`, and the matching templates and tests); only when the user asks for a pull request (FR-024)
- [ ] T078 Run the full verification (`uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, the CLI sanity commands of `AGENTS.md`, `uv run ty check .`) and the development-stack scenarios 3 to 7 of `quickstart.md`

---

## Dependencies and execution order

### Phase dependencies

- Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5: strictly in order (plan, "Delivery order").
- Phases 6, 7 and 8 depend on Phase 5 and are independent of one another.
- Phase 9 depends on Phases 6 to 8 for the parts it shows.
- Phase 10 depends on every earlier phase; T071 to T074 can start once Phase 5 is done.

### User story dependencies

- **US1 (P1)**: Phases 2 to 5. Every other story depends on it.
- **US2 (P2)**: needs `run_credential_uses` from T028.
- **US4 (P3)**: needs `configuration_version_credentials` from T034.
- **US3 (P3)**: needs the format of T015 and the removal of T048.

### Within each phase

- Tests before implementation; models before routes; routes before the CLI and the web page.

### Parallel opportunities

- T002 and T003; T004 with T006, T010, T014 and T016 (different files).
- T021 to T025 (tests in different files).
- T053, T056 and T060 once Phase 5 is done.
- T070 to T074 and T076.

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

Phases 1 to 5 with the CLI. An administrator creates credentials with the CLI, registers a package and
runs it on any worker. Stop and run quickstart scenarios 1 to 3, 5 and 6.

### Incremental delivery

1. US1 (Phases 1 to 5): stored credentials replace `env`.
2. US2 (Phase 6): revisions on runs.
3. US4 (Phase 7): deletion.
4. US3 (Phase 8): conversion.
5. Web interface (Phase 9), then documentation, decision record, constitution and changelog (Phase 10).
6. The Helm pull request in `opsmill/infrahub-helm` (T077), on request.

## Notes

- Commit with explicit paths (`git commit -- <paths>`); the checkout may be shared.
- Never print a value in a test failure message; compare digests instead.
- The direct Prefect flow (`infrahub_sync/orchestration/`) does not change.
