# Implementation Plan: Infrahub platform alignment

**Branch**: `008-infrahub-platform-alignment` | **Date**: 2026-10-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/008-infrahub-platform-alignment/spec.md`

## Summary

Sync stops running its own Prefect server, product PostgreSQL and object store. It runs on
Infrahub's task manager on Infrahub's Prefect version, with flow runs tagged so they appear in
Infrahub's task views. Configurations, versions and approvals become Infrahub nodes of a `Sync`
schema extension, and plan files and internal bundles become `CoreFileObject` nodes. Run state
stays in the PostgreSQL database `infrahub_sync` on Infrahub's task-manager-db server, which keeps
the `product_runs`, `mutation_receipts`, `write_admissions`, `prefect_executions`,
`configuration_baselines` and `audit_events` tables and the advisory write lock (research R8);
each run is mirrored to a `SyncRun` node so it appears in Infrahub's task views. The Sync HTTP
API stays the entry point and authorizes callers with their Infrahub token and permissions.
Research: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11–3.13 for the service (unchanged; 3.10 keeps the direct profile)

**Primary Dependencies**: `prefect==3.8.6` (from 3.8.1), `infrahub-sdk>=1.20.1,<2` (from 1.17),
FastAPI, psycopg. `boto3` leaves the `service` extra. Vendored `opsmill_prefect_extras` unchanged.

**Storage**: Infrahub nodes and Infrahub file storage for records and files; PostgreSQL database
`infrahub_sync` on Infrahub's task-manager-db server for run state, receipts, admissions,
executions, baselines, audit events and advisory locks.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`); integration tier against a local
Infrahub 1.11.3 or 1.11.4 (Prefect 3.8.6) with its task manager (`-m integration`); compose lifecycle tests.

**Target Platform**: Linux containers next to an Infrahub deployment (Compose).

**Project Type**: Service (HTTP API plus Prefect worker) with a CLI client.

**Performance Goals**: A run visible in Infrahub's task list within 10 seconds (SC-002); plan files
for 88,000 objects (about 3 MB) stored and read intact (SC-006).

**Constraints**: an Infrahub release that ships Prefect 3.8.6, today 1.11.3 and 1.11.4 (Sync refuses any other Prefect version; `CoreFileObject` since 1.8.0); file size ≤ 50 MB
by default; Sync's records only on the default branch; Sync needs no schema-admin rights.

**Scale/Scope**: One Sync deployment per Infrahub; tens of configurations; hourly runs (about 8,700
runs a year per configuration before retention).

## Constitution Check

*GATE: checked before Phase 0 and again after Phase 1.*

| Principle | Check | Result |
|---|---|---|
| I. Read-only and dry-run by default | Plan and validate stay non-applying. Apply and sync require a `SyncApproval` created under the caller's account with the matching checksum, plus `confirm_writes` | Pass |
| II. Idempotency and safety | Receipts, admissions and the per-config lock stay in PostgreSQL; version creation is serialized; a damaged bundle refuses the apply | Pass |
| III. Adapter symmetry | No adapter changes; writes still go through saved-plan apply (ADR 0014) | Pass |
| IV. Type safety | New code typed; `ty` clean with no overrides | Pass (enforced at review) |
| V. Test discipline | Unit tests per new module; integration tests behind `-m integration` | Pass |
| VI. Secrets and input boundaries | Credentials stay as worker-environment references; caller tokens are forwarded to Infrahub, never logged or stored. Internal bundles are unredacted (ADR 0011) and now live in Infrahub, protected by a kind-level `view` grant to the service account only | Pass with risk; see Complexity Tracking |
| VII. Simplicity | Removes three services and `boto3`; adds no dependency | Pass |

Post-design re-check: unchanged.

## Project Structure

### Documentation (this feature)

```text
dev/specs/008-infrahub-platform-alignment/
├── spec.md
├── plan.md              # this file
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── sync-http-api.md
│   └── task-manager.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
schema/
├── sync.yml                         # NEW: Sync schema extension the operator loads
└── sync-permissions.yml             # NEW: service-account grants and per-action denies (research R13; tasks Phase 7, follow-up PR after US3)

infrahub_sync/
├── platform/                        # NEW: Infrahub-side record and file access
│   ├── client.py                    # service-account and per-caller SDK clients, default-branch lookup
│   ├── schema_check.py              # startup structural check (research R10)
│   ├── records.py                   # SyncConfiguration/Version/Run/Approval read and write
│   ├── files.py                     # SyncPlanFile and SyncInternalBundle upload and download
│   └── retention.py                 # bounded deletion after each run (R12)
├── service/
│   ├── auth.py                      # REPLACED: Infrahub token and permission resolver (R6)
│   ├── app.py                       # auth dependency; config write routes removed
│   ├── config_routes.py             # read routes from Infrahub; branch validation route
│   ├── service.py                   # run creation with lazy versions (R9); approvals
│   ├── flow.py                      # mirrors run state to SyncRun; baselines stay in PostgreSQL (R8); checkpoints via files.py
│   ├── checkpoints.py               # publish/rehydrate through files.py
│   ├── orchestration.py             # per-run infrahub.app tags (R4)
│   ├── bootstrap.py                 # pool/deployment on Infrahub's task manager; lock DB; no bucket
│   ├── storage.py                   # lock-DB connection only; Boto3S3Client removed
│   └── apply_guard.py               # unchanged; DSN now the lock DB
├── product_store/
│   ├── store.py                     # drops configurations and configuration_versions; keeps every run table (R8)
│   ├── configs.py                   # REPLACED by platform/records.py
│   └── bundle.py                    # unchanged
├── client/{client.py,models.py}     # optional version, Infrahub token, validate-on-branch
└── cli.py                           # configs register/version removed; --version optional

docker-compose.yml                   # postgres, object-store, prefect-server and their init jobs removed
development/docker-compose.{dev,preview}.yml, development/preview.env, tasks/preview.py, tasks/dev.py
docs/docs/…                          # pages listed in research (compose-deployment, sync-http-api, …)

tests/
├── platform/                        # NEW: unit tests with a fake SDK client
├── service/, tests/product_store/   # updated
├── compose/, tests/preview/         # updated service lists
└── integration/                     # task-manager and Infrahub-record scenarios (quickstart)
```

**Structure Decision**: Single project. New Infrahub-facing code goes in one new package,
`infrahub_sync/platform/`, so `service/` keeps orchestration and `product_store/` shrinks to the
write-safety tables.

## Delivery order

Each phase ships on its own and leaves Sync working.

1. **Task manager (US1)**: Prefect 3.8.6; bootstrap against Infrahub's task manager; optional
   Prefect credential; flow-run tags; lock DB on task-manager-db; compose services removed except
   the object store. Records still in the shrinking product store.
2. **Records in Infrahub (US2)**: schema extension and startup check; Infrahub-token auth;
   configurations, lazy versions, runs and approvals as nodes; config write routes and CLI
   commands removed; branch validation.
3. **Files in Infrahub (US3)**: plan files and internal bundles as `CoreFileObject` nodes;
   object store and `boto3` removed; retention.

After phase 3, extract ADRs 0017 and 0018 from this spec and research, superseding process decisions AD-2,
AD-3 and AD-14.

## Complexity Tracking

| Risk or deviation | Why accepted | Simpler alternative rejected because |
|---|---|---|
| Unredacted internal bundles stored in Infrahub (Principle VI) | Kept private by a kind-level `view` grant to the service account only; documented in the install steps; download routes enforce `view` | Redacting bundles breaks ADR 0011's byte stability; keeping an object store only for bundles keeps the component this feature removes |
| Strict Prefect version equality makes Sync refuse to start after an Infrahub Prefect bump | FR-002; the CI drift check flags the bump before release | A version range does not prove the pair was tested |
| Two stores (Infrahub and lock DB) can disagree after a crash | The lock DB stays the authority for write safety; Infrahub records are evidence and are reconciled from it | A single store needs a lock Infrahub does not expose to external workers |
