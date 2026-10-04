# Research: Infrahub platform alignment

Sources read on 2026-10-03: `opsmill/infrahub` `develop` at `9e154a6b9`, `opsmill/infrahub-sdk-python`
`develop` at `0e11153` (1.23.0 plus 73 commits), and this branch at `ab7f038e`. Paths below are
relative to each repository.

## R1. Prefect version and server

- **Decision**: Pin `prefect==3.8.6` and run on Infrahub's task manager. The minimum supported
  Infrahub release is 1.11.3, the first that ships Prefect 3.8.6 (1.11.0 to 1.11.2 ship 3.7.5).
  A CI check compares Sync's pin with the Prefect version locked by the newest supported Infrahub
  release and fails on drift.
- **Rationale**: The owner set Infrahub's Prefect as the reference. An exact match removes
  client/server skew. Infrahub's task manager is Infrahub's own server app
  (`backend/infrahub/prefect_server/app.py`), so Sync only needs the standard Prefect API on it.
- **Alternatives considered**: Keep Sync's pin and accept any 3.8.x server (rejected: drifts
  again at the next Infrahub bump). Range pin such as `>=3.8,<3.9` (rejected: a range does not
  prove the pair was tested).

## R2. Work pool and deployment on Infrahub's task manager

- **Decision**: Keep Sync's own pool `infrahub-sync`, `ServiceProcessWorker` and deployment
  `infrahub-sync-service/run`, created by `sync-bootstrap` against Infrahub's task manager.
- **Rationale**: Infrahub's startup only upserts its own pool `infrahub-worker`, its queues and
  its deployments by name (`workflows/initialization.py:72-104`, `workflows/catalogue.py:685`).
  Nothing lists or deletes other pools or deployments (no `delete_work_pool` or
  `delete_deployment` in the code base). FR-004 holds without changes to Infrahub.
- **Alternatives considered**: Run Sync flows on Infrahub's `infrahub-worker` pool with the
  `infrahubasync` worker type (rejected: Sync's worker enforces deployment admission, and Infrahub
  workers would need Sync's code and credentials).

## R3. Task manager access (FR-018)

- **Decision**: Sync sets `PREFECT_API_AUTH_STRING` only when the operator provides
  `INFRAHUB_SYNC_PREFECT_AUTH_STRING`. At startup the API and the worker log `prefect_auth=credential`
  or `prefect_auth=network-isolation`. The compose file no longer requires the variable.
- **Rationale**: Infrahub's task manager has no auth setting (`config.py:645-670` has address,
  port and TLS only; the compose file sets only the database URL and API URL). Clarification Q1.
- **Alternatives considered**: See the spec's clarification session.

## R4. Showing runs in Infrahub's task views (FR-003)

- **Decision**: Every flow run Sync creates carries these tags:
  `infrahub.app`, `infrahub.app/branch/<target branch>`,
  `infrahub.app/workflow-type/sync-<operation>`, `infrahub.app/node/<SyncRun id>` and
  `infrahub.app/node/<SyncConfiguration id>`. Tags are added per run where the flow run is
  created (`opsmill_prefect_extras/executors.py:608`, through `service/orchestration.py`).
- **Rationale**: The task query requires the `infrahub.app` tag and ANDs one branch tag and one
  node tag (`task_manager/flow_run/filters.py:29-40`). Logs are read from Prefect for any flow run
  (`flow_run/reader.py:72-115`). The object page shows a Tasks tab for any kind that inherits
  `CoreTaskTarget` and queries it with `related_node__ids`
  (`frontend/app/src/entities/nodes/object/ui/object-details/object-details-tabs.tsx:25,43`).
  Two node tags make the run appear on both the run and the configuration.
- **Consequence**: Deleting an Infrahub branch purges terminal flow runs tagged with it
  (`flow_run/branch_cleanup.py:43-60`), and `infrahub tasks flush flow-runs` purges runs older
  than 30 days whatever their tags (`flow_run/retention.py:25-60`). This deletes Prefect logs only.
  Sync's run records and plan files are Infrahub nodes and stay, so evidence for a write survives.
- **Alternatives considered**: Tag the default branch instead of the target branch (rejected:
  the run then shows on the wrong branch in Infrahub, and only the logs are at stake).

## R5. Records as Infrahub data (FR-005)

- **Decision**: A schema extension in namespace `Sync` defines `SyncConfiguration` and
  `SyncRun` (both inherit `CoreTaskTarget`), `SyncConfigurationVersion`, `SyncApproval`, and
  `SyncPlanFile` and `SyncInternalBundle` (both inherit `CoreFileObject`). Sync writes them on the
  default branch with a dedicated service account (`INFRAHUB_SYNC_INFRAHUB_TOKEN`), resolving the
  default branch from `client.branch.all()` (`is_default`), never from the SDK's static default.
- **Rationale**: `CoreTaskTarget` is an attribute-less generic for task association
  (`core/schema/definitions/core/core.py:11-17`). `CoreFileObject` gives upload, download, size and
  SHA-1 checksum (`core/schema/definitions/core/file_object.py:6-64`). The SDK's
  `Config.default_branch` is a static `"main"` (`config.py:47`), so the default branch must be read
  from the server.
- **Alternatives considered**: Structured mapping nodes (rejected in clarification Q2 of the
  `/speckit-clarify` session). One kind for all files (rejected: see R7).

## R6. Caller identity and permissions (FR-021)

- **Decision**: The Sync API takes the caller's token from `X-INFRAHUB-KEY` or
  `Authorization: Bearer`, builds a per-request SDK client with it, reads the identity with
  `client.get_user()` (`AccountProfile`) and the permissions with `InfrahubPermissions`. Rights map
  to Infrahub object permissions:

  | Sync action | Required Infrahub permission |
  |---|---|
  | Read configurations, versions, runs | `view` on the kind |
  | Validate a configuration on a branch | `view` on `SyncConfiguration` |
  | Plan | `create` on `SyncRun` |
  | Verify a saved plan | `create` on `SyncRun` |
  | Apply a saved plan | `create` on `SyncApproval` |
  | Sync (plan, verify and apply) | `create` on `SyncRun` and `create` on `SyncApproval` |
  | Cancel a run | `update` on `SyncRun` |

  Run records name the caller's Infrahub account. `service/auth.py`
  (`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`) and the admin gating in `config_routes.py` are removed.
- **Rationale**: Infrahub has no token-introspection endpoint (`api/auth.py` has login, refresh,
  logout only), so forwarding the token to GraphQL is the supported path. Object permissions are
  per kind: `object:<namespace>:<name>:<action>:<decision>` (`core/account.py:28,50`). A separate
  `SyncApproval` kind lets an operator grant plan rights without apply rights.
- **Alternatives considered**: Map apply to `update` on `SyncRun` (rejected: the service account
  also updates runs, and an approval record is useful audit data).

## R7. Plan files and internal bundles (FR-010, FR-011)

- **Decision**: Review artifacts are `SyncPlanFile` nodes. Internal run bundles (ADR 0011:
  private and never redacted) are `SyncInternalBundle` nodes. The documented permission set grants
  `view` on `SyncInternalBundle` to the Sync service account only. Sync's SHA-256 plan checksum
  stays an attribute of `SyncRun` and is what an apply approves. Infrahub's SHA-1 checksum is used
  only to detect a damaged upload.
- **Rationale**: Download routes require `view` permission on the node
  (`api/storage/file_object.py:109-215`), so a separate kind is how the private bundle stays
  private. The default size limit is 50 MB (`storage.max_file_size`, `config.py:375`); bundles
  measured about 3 MB for 88,000 objects (VAL-18). `CoreFileObject` arrived in Infrahub 1.8.0,
  below the 1.11.3 floor set by R1.
- **Alternatives considered**: One file kind with a visibility attribute (rejected: Infrahub
  permissions are per kind, not per attribute value).

## R8. Lock and duplicate-protection database (FR-012 to FR-014)

- **Decision** (owner, 2026-10-03, option B): The database `infrahub_sync` on Infrahub's
  `task-manager-db` server stays the authority for run state: `product_runs`, `mutation_receipts`,
  `write_admissions`, `prefect_executions`, `configuration_baselines` and `audit_events` keep their
  tables, and `service/apply_guard.py` its advisory locks. Each run is mirrored to an Infrahub
  `SyncRun` node, written best-effort after each state change; a failed mirror write is logged and
  never fails the run, and the mirror can be rebuilt from `product_runs`. `configurations` and
  `configuration_versions` move to Infrahub (`SyncConfiguration`, `SyncConfigurationVersion`) and
  their tables are dropped. `artifact_refs` stays until US3.
- **Rationale**: Run state drives the worker claim, liveness, replay and the write guard, all built
  on PostgreSQL transactions. Infrahub nodes are not written in one transaction, so moving that
  state is the riskiest part of the feature; mirroring gives the visibility now and keeps the move
  (option A) for a later version.
- **Alternatives considered**: Move run state fully to Infrahub (option A, deferred to a later
  version); drop `prefect_executions` for Prefect's own state (deferred).

## R9. Version creation at run start (FR-006)

- **Decision**: At run start, the API reads the configuration's current document on the default
  branch, computes its checksum, and inside an advisory lock on the configuration (lock database,
  key space distinct from the write lock) reuses the version with that checksum or creates the
  next number. `SyncConfigurationVersion` also carries uniqueness constraints on
  (configuration, checksum) and (configuration, number) as a second guard.
- **Rationale**: The SDK has no get-or-create and surfaces a uniqueness conflict as a
  `GraphQLError` (`exceptions.py:60`). A short lock in the database Sync already uses is simpler
  than catching and retrying on conflicts.

## R10. Schema extension check (FR-005)

- **Decision**: At startup, Sync reads `/api/schema/{kind}` for each Sync kind and checks that the
  required attributes, relationships and inherited generics exist. It refuses to start with the
  list of what is missing and the schema file shipped with that Sync release.
- **Rationale**: Infrahub does not persist a schema extension version (`SchemaRoot.version` is
  accepted on load but never exposed). Per-kind hashes (`/api/schema/summary`) change with
  Infrahub's own hash computation, so a structural check is more stable.

## R11. Dependency floors

- **Decision**: `infrahub-sdk>=1.20.1,<2` (file upload and download from 1.19.0; SHA-1 helpers
  from 1.20.1, SDK `CHANGELOG.md:125-170`). Infrahub releases shipping Prefect 3.8.6 (1.11.3, 1.11.4); a later Prefect needs a Sync release. `boto3` leaves the `service` extra.
  `psycopg` stays.

## R12. Retention (FR-016)

- **Decision**: The worker deletes `SyncRun`, `SyncApproval`, `SyncPlanFile` and
  `SyncInternalBundle` nodes older than `INFRAHUB_SYNC_RETENTION_DAYS` (default 90) at the end of
  each run, in bounded batches, never touching a run that has an unresolved write admission.
- **Rationale**: Scheduling is out of scope, and Infrahub's flow-run purge does not cover Sync's
  nodes.

## R13. Namespace of Sync's kinds (owner question, 2026-10-04)

- **Question**: Could Sync's kinds live in Infrahub's protected `Builtin` namespace instead of
  `Sync`?
- **Facts**: `Builtin` and `Core` are restricted only at schema-write time
  (`RESTRICTED_NAMESPACES`, `core/constants/__init__.py:414-427`): `POST /schema/load` refuses any
  node or generic in them (`core/schema/__init__.py:103-108`), the dropdown and enum mutations
  refuse them (`graphql/mutations/schema.py:297-299`), and the UI hides the option editors
  (`user_editable`). Kinds exist there only because Infrahub ships them in `core_models`. No
  namespace protects data rows, and the restricted gate skips `extensions:` blocks, which merge
  with no namespace check (opsmill/infrahub#10418). `Builtin` differs from `Core` only in
  auto-generated profiles and plain-node registration, which workflow records do not want.
- **Decision** (owner, 2026-10-04): a staged path, floor unchanged at Infrahub 1.11.3.
  1. **Protect in the `Sync` namespace now.** Ship `schema/sync-permissions.yml`, an object file
     with the service account's grants (including `global:edit_default_branch`, which Infrahub
     requires for every write on the default branch) and per-action denies on the kinds Sync
     writes; document moving `manage_schema` to a schema-administrator role; check the service
     account's grants at startup; use a dedicated `infrahub-sync` account in the preview and
     development stacks rather than the administrator, so records carry Sync as their author.
  2. **Propose to Infrahub** a `protected_namespaces` setting and a `manage_protected_schema`
     global permission gating load, extensions and choice mutations for the listed namespaces,
     which also closes #10418. Adopt when it ships; Sync then warns at startup when `Sync` is
     not protected.
  3. **Infrahub-owned kinds, in `Core`,** only if the owner wants them: an Infrahub release
     carries the kinds, with a namespace-rename graph migration for existing installs and a
     floor above 1.11.x. Judges scored it lowest on reversibility (no migration downgrade).
- **Alternatives considered**: shipping the kinds in Infrahub first (rejected for now: lock-step
  upgrades and the floor move); a visibility attribute instead of two file kinds (rejected,
  permissions are per kind).

## Deferred

- Replacing the vendored `opsmill_prefect_extras` with plain Prefect calls.
- Dropping `prefect_executions` in favour of Prefect's own state.
