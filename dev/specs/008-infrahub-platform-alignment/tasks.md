# Tasks: Infrahub platform alignment

**Input**: Design documents from `dev/specs/008-infrahub-platform-alignment/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Included. The constitution (Principle V) and `AGENTS.md` require targeted tests for new
behavior; integration tests are marked `-m integration`.

**Organization**: One phase per user story, in the delivery order of the plan. Each story ships
on its own and leaves Sync working.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2 or US3 from spec.md

---

## Phase 1: Setup

- [x] T001 Pin `prefect==3.8.6` in the `prefect` and `service` extras of `pyproject.toml` and refresh `uv.lock` (research R1)
- [x] T002 Raise the floor to `infrahub-sdk>=1.20.1,<2` (base and `[ctl]`) in `pyproject.toml` and refresh `uv.lock` (research R11)
- [x] T003 [P] Create the package `infrahub_sync/platform/__init__.py` with a module docstring describing its boundary
- [x] T004 [P] Create `tests/platform/__init__.py` and a fake async SDK client fixture (nodes, schema, branches, file upload and download) in `tests/platform/conftest.py` — moved to US2: the fake SDK client has no consumer until `infrahub_sync/platform/` gains code — done as per-module fakes instead of one fixture: `tests/platform/test_records.py` (`_Store`/`_Node`, nodes and branches), `tests/platform/test_files.py` (`_FileStore`/`_FileNode`, file upload and download) and `tests/platform/test_retention.py` (`_AgedStore`, metadata filters)

---

## Phase 2: Foundational (blocks all stories)

- [x] T005 [P] Unit tests for the Prefect server version check (equal, different, unreachable) in `tests/service/test_prefect_version.py` — implemented as `tests/service/test_prefect_server.py`
- [x] T006 Implement `require_prefect_server_version()` reading the Prefect API `/version` route and refusing on mismatch with both versions named, in `infrahub_sync/service/prefect_version.py` (FR-002) — implemented in `infrahub_sync/service/prefect_server.py`, with the access-mode check of T013
- [x] T007 Call the version check at API startup in `infrahub_sync/service/serve.py` and at worker startup in `infrahub_sync/service/worker.py`
- [x] T008 [P] Add the Prefect drift check that compares Sync's pin with the Prefect version locked by the newest supported Infrahub tag, in `tasks/prefect_alignment.py`, and run it from `.github/workflows/workflow-linter.yml` (research R1)

**Checkpoint**: Sync refuses to run against a Prefect server of another version.

---

## Phase 3: User Story 1 – Run Sync on the Infrahub deployment I already have (P1) 🎯 MVP

**Goal**: Sync runs on Infrahub's task manager, keeps its records in a database on
task-manager-db, and its runs show in Infrahub's task list. No Sync Prefect server or PostgreSQL
container.

**Independent test**: [quickstart.md](quickstart.md) scenarios 1 and 2, except the per-node Tasks
tab, which needs US2's nodes.

### Tests for US1

- [x] T009 [P] [US1] Unit tests for per-run tags (`infrahub.app`, branch, workflow type) applied after flow-run creation in `tests/service/test_orchestration_tags.py`
- [x] T010 [P] [US1] Unit tests for the optional Prefect credential and the `prefect_auth=` startup log in `tests/service/test_prefect_auth_mode.py` — covered by `tests/service/test_prefect_server.py` and the optional-credential contract test in `tests/compose/test_compose_contract.py`
- [x] T011 [P] [US1] Unit tests for lock-database convergence (creates `infrahub_sync` and its tables, never touches `prefect`) in `tests/service/test_bootstrap_lock_database.py` — superseded: the existing `db-bootstrap` shell job is retargeted to task-manager-db instead of a new Python step; `tests/compose/test_compose_contract.py` asserts the database host and that the script never touches `prefect`

### Implementation for US1

- [x] T012 [US1] Add `run_tags(operation, branch)` and update the created flow run's tags through the Prefect client right after `submit`, without changing the vendored executor, in `infrahub_sync/service/orchestration.py` (contracts/task-manager.md)
- [x] T013 [US1] Make `INFRAHUB_SYNC_PREFECT_AUTH_STRING` optional: map it to `PREFECT_API_AUTH_STRING` only when set, and log `prefect_auth=credential|network-isolation` at startup in `infrahub_sync/service/serve.py` and `infrahub_sync/service/worker.py` (FR-018)
- [x] T014 [US1] Add `converge_lock_database()` that creates database `infrahub_sync` on the server named by `INFRAHUB_SYNC_DATABASE_ADMIN_URL` and applies the product-store DDL, in `infrahub_sync/service/bootstrap.py` — superseded, see T011
- [x] T015 [US1] Point pool and deployment convergence at Infrahub's task manager (`PREFECT_API_URL`) and keep pool `infrahub-sync` and deployment `infrahub-sync-service/run`, in `infrahub_sync/service/bootstrap.py` and `infrahub_sync/service/deploy.py`
- [x] T016 [US1] Remove `postgres` and `prefect-server` from `docker-compose.yml`, retarget `db-bootstrap` to task-manager-db, join Infrahub's network as an external network, make the Prefect credential optional, and point `sync-api` and `sync-worker` at `task-manager:4200`
- [x] T017 [P] [US1] Use Infrahub's `task-manager` and `task-manager-db` from `development/docker-compose.infrahub.yml` instead of Sync's own in `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml` and `development/preview.env`
- [x] T018 [US1] Update stack orchestration for the removed services in `tasks/preview.py` and `tasks/dev.py` — `tasks/preview.py` drops the Prefect image settings; `tasks/dev.py` runs on the preview's task manager in pool `infrahub-sync-dev` and drops its `infrahub_sync_dev` database on destroy
- [x] T019 [P] [US1] Update the expected service lists and environment contracts in `tests/compose/` and `tests/preview/`
- [x] T020 [P] [US1] Document running on Infrahub's task manager, the minimum Infrahub release 1.11.3 and the two access modes in `docs/docs/compose-deployment.mdx`, `docs/docs/development-stack.mdx` and `docs/docs/develop/knowledge/orchestration-prefect.md`
- [x] T021 [US1] Integration test: a plan run on a local Infrahub task manager appears in the `InfrahubTask` query with branch and workflow-type tags, in `tests/integration/test_task_manager_runs.py` — skips without `PREFECT_API_URL`, `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN`; not yet run against a live stack

**Checkpoint**: US1 ships alone. Records still live in the product store, now inside task-manager-db.

---

## Phase 4: User Story 2 – Edit and review configurations in Infrahub (P2)

**Goal**: Configurations, versions, runs and approvals are Infrahub nodes; callers use their
Infrahub token; versions are created at run start; validation works on any branch.

**Independent test**: [quickstart.md](quickstart.md) scenarios 2 (Tasks tab on nodes), 3 and 4
steps 2–3.

### Tests for US2

- [x] T022 [P] [US2] Unit tests for the structural schema check (all present, missing kind, missing attribute, missing generic) in `tests/platform/test_schema_check.py`
- [x] T023 [P] [US2] Unit tests for lazy versions (new content, unchanged content, several merges, two concurrent runs, invalid content) in `tests/platform/test_records_versions.py` — written in `tests/platform/test_records.py`; concurrent runs rely on the version lock, which the tests check is taken, not on a parallel run
- [x] T024 [P] [US2] Unit tests for run and approval records and baselines in `tests/platform/test_records_runs.py` — written in `tests/platform/test_records.py` and `tests/service/test_run_records.py`
- [x] T025 [P] [US2] Unit tests for Infrahub-token authentication and the permission table in research R6 (401, 403 naming the permission, allowed) in `tests/service/test_infrahub_auth.py`
- [x] T026 [P] [US2] Unit tests for the configuration routes (removed write routes, read routes, `POST /configs/{id}/validate?branch=`) in `tests/service/test_config_routes.py` — written in `tests/service/test_run_records.py`
- [x] T027 [P] [US2] Unit tests for the CLI changes (removed `configs register` and `configs version` with a pointer to Infrahub, optional `--version`, `configs validate --branch`) in `tests/cli/test_configs_commands.py` — written in `tests/cli/test_client_commands.py`

### Implementation for US2

- [x] T028 [US2] Write the schema extension with `SyncConfiguration`, `SyncConfigurationVersion`, `SyncRun` and `SyncApproval` as in data-model.md, in `schema/sync.yml`
- [x] T029 [US2] Implement the service-account client (`INFRAHUB_SYNC_INFRAHUB_ADDRESS`, `INFRAHUB_SYNC_INFRAHUB_TOKEN`), per-caller clients and default-branch lookup from `client.branch.all()`, in `infrahub_sync/platform/client.py`
- [x] T030 [US2] Implement the startup structural check against `/api/schema/{kind}` in `infrahub_sync/platform/schema_check.py`, and call it in `infrahub_sync/service/serve.py` and `infrahub_sync/service/worker.py` (FR-005)
- [x] T031 [US2] Implement configuration reads and get-or-create versions under an advisory lock in a key space distinct from the write lock, in `infrahub_sync/platform/records.py` and `infrahub_sync/service/apply_guard.py` (FR-006, research R9)
- [x] T032 [US2] Implement `SyncApproval` writes and the best-effort `SyncRun` mirror (written after each state change, never failing the run) in `infrahub_sync/platform/records.py` (research R8, option B)
- [x] T033 [US2] Replace bearer principals with an Infrahub-token resolver and the permission table, and remove `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, in `infrahub_sync/service/auth.py` and `infrahub_sync/service/app.py` (FR-021)
- [x] T034 [US2] Create runs from the configuration's current content with an optional version, refuse invalid content with `422` and no version, and create a `SyncApproval` on apply, in `infrahub_sync/service/service.py`
- [x] T035 [US2] Mirror run state and outcome to `SyncRun` after each state change in `infrahub_sync/service/service.py` and `infrahub_sync/service/flow.py`; `product_runs` stays the authority
- [x] T036 [US2] Add `infrahub.app/node/<SyncRun id>` and `infrahub.app/node/<SyncConfiguration id>` to the per-run tags in `infrahub_sync/service/orchestration.py`
- [x] T037 [US2] Serve configuration reads from Infrahub, remove the write routes and add the branch validation route in `infrahub_sync/service/config_routes.py`; delete `infrahub_sync/product_store/configs.py` — option B keeps `product_store/configs.py`: it is the validation service, and Sync without Infrahub still registers locally. With Infrahub, `POST /configs` and `POST /configs/{id}/versions` answer `410 configurations-in-infrahub`
- [ ] T038 [US2] Drop `configurations` and `configuration_versions` from `infrahub_sync/product_store/store.py`; keep every run table (research R8, option B) — deferred: Sync without Infrahub and the unit tests still use these tables; drop them with option A
- [x] T039 [P] [US2] Make `registry_version` optional, send the caller token as `X-INFRAHUB-KEY`, remove `register_config` and `create_config_version`, and add `validate_config_on_branch`, in `infrahub_sync/client/models.py` and `infrahub_sync/client/client.py` — `register_config` and `create_config_version` stay for Sync without Infrahub; an Infrahub-connected Sync refuses them with `410`
- [x] T040 [US2] Remove `configs register` and `configs version` with a message pointing to Infrahub, make `--version` optional, add `configs validate --branch`, and read `INFRAHUB_SYNC_TOKEN`, in `infrahub_sync/cli.py` — `configs register` and `configs version` stay for Sync without Infrahub; an Infrahub-connected Sync refuses them with the pointer to Infrahub
- [x] T041 [P] [US2] Add an Infrahub object file for the NetBox example configuration, loadable with `infrahubctl object load`, in `examples/netbox_to_infrahub/sync-configuration.yml`
- [x] T042 [P] [US2] Document configurations in Infrahub, the schema load step, versions at run start, branch validation and the permission table in `docs/docs/configuration-package.mdx`, `docs/docs/running-a-sync.mdx`, `docs/docs/reference/sync-http-api.mdx` and `docs/docs/reference/durable-product-records.mdx`
- [x] T043 [US2] Integration test for quickstart scenario 3 against a local Infrahub in `tests/integration/test_configurations_in_infrahub.py`

**Checkpoint**: US1 and US2 work together; plan files still go to the object store.

---

## Phase 5: User Story 3 – Review a plan from Infrahub (P3)

**Goal**: Plan files and internal bundles are `CoreFileObject` nodes in Infrahub storage; the
object store and `boto3` are gone; retention cleans old runs.

**Independent test**: [quickstart.md](quickstart.md) scenario 4.

### Tests for US3

- [x] T044 [P] [US3] Unit tests for plan file and bundle upload, download, SHA-256 check and mismatch refusal in `tests/platform/test_files.py`
- [x] T045 [P] [US3] Unit tests for retention (age cut-off, bounded batches, runs with an unresolved write admission skipped) in `tests/platform/test_retention.py`
- [x] T046 [P] [US3] Update checkpoint publish and rehydrate tests to use the file nodes in `tests/service/test_checkpoints.py` — the checkpoint tests run the product-store contract suite against `InfrahubArtifactStore`; see `tests/platform/test_files.py` and `tests/product_store/test_contract.py`

### Implementation for US3

- [x] T047 [US3] Add `SyncPlanFile` and `SyncInternalBundle` (both inheriting `CoreFileObject`) to `schema/sync.yml` and the structural check in `infrahub_sync/platform/schema_check.py`
- [x] T048 [US3] Implement upload and download with SHA-256 verification in `infrahub_sync/platform/files.py`
- [x] T049 [US3] Publish and rehydrate checkpoints through `infrahub_sync/platform/files.py` in `infrahub_sync/service/checkpoints.py`, and publish the review artifact as a `SyncPlanFile` in `infrahub_sync/service/flow.py`
- [x] T050 [US3] Read review artifacts from Infrahub in `infrahub_sync/service/service.py` (artifact lookups) and drop `artifact_refs`, `FileArtifactStore` and `S3ArtifactStore` from `infrahub_sync/product_store/store.py` — `artifact_refs` stays: Sync's database keeps the artifact references and Infrahub keeps the bytes (`InfrahubArtifactStore`); `FileArtifactStore` stays for local use
- [x] T051 [US3] Remove `Boto3S3Client` and the `INFRAHUB_SYNC_S3_*` settings from `infrahub_sync/service/storage.py`, `converge_bucket` from `infrahub_sync/service/bootstrap.py`, and `boto3` from `pyproject.toml`
- [x] T052 [US3] Implement retention with `INFRAHUB_SYNC_RETENTION_DAYS` (default 90) in `infrahub_sync/platform/retention.py` and call it at the end of `service_sync_run` in `infrahub_sync/service/flow.py` (FR-016)
- [x] T053 [US3] Remove `object-store` and `object-store-init` from `docker-compose.yml`, and the MinIO services from `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml` and `development/preview.env`
- [x] T054 [P] [US3] Update `tests/compose/` and `tests/preview/` for the removed object store
- [x] T055 [P] [US3] Document plan review from Infrahub and the required `view` grant on `SyncInternalBundle` to the service account only, in `docs/docs/running-a-sync.mdx` and `docs/docs/develop/guidelines/secret-redaction.md`
- [x] T056 [US3] Integration test for quickstart scenario 4 in `tests/integration/test_plan_files_in_infrahub.py`

**Checkpoint**: All three stories work. Sync adds only `sync-api` and `sync-worker` to Infrahub.

---

## Phase 6: Polish and cross-cutting

- [x] T057 Write the ADRs from this spec and research.md, and add them to the index — split into `dev/adr/0017-sync-runs-on-infrahubs-task-manager.md` and `dev/adr/0018-configurations-live-in-infrahub-and-infrahub-identifies-callers.md` (0015 and 0016 were taken), indexed in `dev/adr/README.md`; plan files (US3) get their own record when built
- [x] T058 [P] Update the architecture pages for the new layout in `docs/docs/develop/knowledge/sync-architecture.md`, `docs/docs/develop/knowledge/apply-guard.md` and `docs/docs/develop/knowledge/repository-tour.md`
- [x] T059 [P] Add news fragments `changelog/+infrahub-platform-alignment.changed.md` and `changelog/+config-registration-commands.removed.md` — `+plan-files-in-infrahub.changed.md` and `+configurations-in-infrahub.changed.md` cover both; the two retired S3/MinIO fragments were removed
- [x] T060 Regenerate the CLI reference with `uv run invoke docs.generate`
- [x] T061 Run `uv run invoke format`, `uv run invoke lint` and `uv run invoke tests.tests-unit`, and fix every finding
- [x] T062 Run quickstart scenarios 5 and 6 (task-manager restart during 20 applies; version and schema guards) and record the results in `dev/specs/008-infrahub-platform-alignment/quickstart.md`

---

## Phase 7: Protect the Sync namespace (owner decision 2026-10-04, research R13, stage 1)

**Goal**: Sync's kinds stay in namespace `Sync`, and the protection the owner asked for comes from
Infrahub's permissions and a dedicated service account, with no Infrahub change. Follow-up PR
after the three story PRs.

- [ ] T063 [P7] Ship `schema/sync-permissions.yml`, an Infrahub object file creating the `infrahub-sync` service account (type Script), its role and group with every grant the service needs (`view` on all Sync kinds; `create` on ConfigurationVersion, Run, Approval, PlanFile, InternalBundle; `update` on Run; `delete` on Run, Approval, PlanFile, InternalBundle; `global:edit_default_branch:allow_all`), and a `Sync Records Guard` role with per-action `deny` on the kinds Sync writes, attached to the default user group
- [ ] T064 [P7] Check the service account's grants at API and worker startup (`client.get_user()` permissions against the list in T063) and refuse to start naming each missing grant, in `infrahub_sync/platform/client.py` and the two startup checks
- [ ] T065 [P7] Use the dedicated `infrahub-sync` account in the preview and development stacks instead of the administrator token (`tasks/preview.py` creates it from T063 after the schema load; `development/docker-compose.dev.yml` default token documented as the preview's `infrahub-sync` token), so records carry Sync as their author and the grants are exercised by every live tier
- [ ] T066 [P7] Document moving `global:manage_schema` from "General Access" to a schema-administrator role, and creating the lineage source account or group (`netbox`, `nautobot`) that the Infrahub adapter uses for `source`/`owner` metadata, in `docs/docs/compose-deployment.mdx` and `docs/docs/adapters/infrahub.mdx`
- [ ] T067 [P7] File the Infrahub proposal for `protected_namespaces` and `manage_protected_schema` (research R13 stage 2) as an opsmill/infrahub issue, and link it from `docs/docs/develop/knowledge/sync-architecture.md`

## Dependencies and execution order

- **Setup (T001–T004)** → **Foundational (T005–T008)** → user stories.
- **US1 (T009–T021)** depends only on Foundational.
- **US2 (T022–T043)** depends on US1: it adds node tags to T012 and shrinks the store that T014 created.
- **US3 (T044–T056)** depends on US2: file nodes link to `SyncRun` nodes.
- **Polish (T057–T062)** after the stories you ship.

Within a story: tests first and failing, then schema and platform modules, then service changes,
then CLI, deployment and docs.

## Parallel examples

```text
US1 tests together:   T009, T010, T011
US1 side work:        T017, T019, T020 (after T016)
US2 tests together:   T022–T027
US2 side work:        T039, T041, T042
US3 tests together:   T044, T045, T046
```

## Implementation strategy

1. **MVP**: Setup, Foundational and US1. Stop and validate quickstart scenarios 1 and 2. This
   already removes Sync's Prefect server and PostgreSQL container.
2. Add US2 and validate scenario 3. This removes the Sync-specific registration commands.
3. Add US3 and validate scenario 4. This removes the object store.
4. Polish, including ADRs 0017 and 0018.
