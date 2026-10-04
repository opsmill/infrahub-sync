# Quickstart: validating Infrahub platform alignment

Run these scenarios against a local Infrahub 1.11.3 or later started from
`development/docker-compose.infrahub.yml`, which provides `task-manager` and `task-manager-db`.
Contracts: [sync-http-api.md](contracts/sync-http-api.md), [task-manager.md](contracts/task-manager.md).
Entities: [data-model.md](data-model.md).

## Prerequisites

1. Infrahub running, with an admin account to load the schema and set permissions.
2. The Sync schema extension loaded by the operator:
   `infrahubctl schema load schema/sync.yml` (path set in the plan).
3. Two Infrahub accounts with API tokens: `planner` (`create` on `SyncRun`, and `view` on
   `SyncConfiguration` with decision `allow_all`, because Scenario 3 validates on a branch) and
   `approver` (`create` on `SyncRun` and `SyncApproval`), plus the Sync service account.
4. Sync API and worker started with `PREFECT_API_URL` pointing at Infrahub's task manager and
   `INFRAHUB_SYNC_DATABASE_URL` pointing at database `infrahub_sync` on task-manager-db.

## Scenario 1: no Sync infrastructure of its own (US1, SC-001)

- List the running containers. Expected: Infrahub's services plus `sync-api` and `sync-worker`
  only; no Sync Prefect server, PostgreSQL or object store.
- Check the startup logs. Expected: `prefect_auth=network-isolation` (no credential set) and a
  successful schema check.

## Scenario 2: a run shows in Infrahub (US1, SC-002)

1. In Infrahub, create a `SyncConfiguration` named `netbox-demo` with a package document.
2. `infrahub-sync diff --config-id netbox-demo --reason "first plan"` with the `planner` token.
3. Expected within 10 seconds (US1): the run appears in Infrahub's task list with its logs, its
   branch and the `infrahub.app/workflow-type/sync-plan` tag.
4. Expected once US2 adds the node tags: the run also appears in the Tasks tab of both the
   `netbox-demo` configuration and the new `SyncRun`.

## Scenario 3: versions are created at run start (US2)

1. Change the mapping on an Infrahub branch, merge it, and run `diff` without `--version`.
   Expected: version 2 is created and used; version 1 is unchanged.
2. Run `diff` again with no change. Expected: version 2 is reused.
3. Start two `diff` runs at the same time after a new merge. Expected: both use version 3.
4. Merge an invalid document and run `diff`. Expected: refused with findings; no version 4.
5. `infrahub-sync configs validate netbox-demo --branch my-branch` on an unmerged change.
   Expected: findings returned; nothing created.

## Scenario 4: plan review and apply from Infrahub (US3, SC-005, SC-006)

1. Open the `SyncRun` from the configuration's page. Expected: the summary, the plan checksum and
   a downloadable `SyncPlanFile`.
2. With the `planner` token, `infrahub-sync apply <run> --expected-checksum <c>`. Expected: `403`
   naming `create` on `SyncApproval`.
3. With the `approver` token. Expected: the apply reads the stored bundle, does not read the
   source, and records a `SyncApproval` naming `approver`.
4. Replace the stored internal bundle with different bytes and apply another plan. Expected:
   refused, nothing written.

## Scenario 5: write safety (SC-004)

Restart the task manager during 20 applies against the same configuration. Expected: no two
writes to the same configuration overlap (the write guard in the lock database serializes them;
the apply runs themselves may overlap), and no apply writes twice after its request is retried
with the same idempotency key.

### Result, 2026-10-03: pass

Preview stack (`uv run invoke preview.up`, then `preview.seed`): Infrahub 1.11.4 with its task
manager (Prefect 3.8.6, `127.0.0.1:4210`), the Sync API (`127.0.0.1:8010`) and one process worker
as host processes, product database `infrahub_sync` on `task-manager-db` (`127.0.0.1:5439`). Times
are UTC.

1. One `SyncConfiguration`, `chaos-sc004-34e893b19a7f`, holding the smoke package (Infrahub `main`
   to branch `preview-smoke`, kind `InfraDevice`). One pending update planted by setting
   `core01.type` on `main` to `chaos-sc004-a056b96cc332`.
2. 20 × `POST /runs` (`operation: plan`, five at a time, a fresh `Idempotency-Key` each). All 20
   reached `planned` in 41.8 s, every summary `{"update": 1}`, one plan checksum.
3. 20 × `POST /runs/{id}/apply` (`expected_checksum`, `confirm_writes: true`) sent at once at
   23:27:44; all 20 answered `202` within 3.6 s. A sampler read `pg_locks` every 100 ms for the
   configuration's advisory key (`advisory_lock_key(config_id)` split into `classid` and `objid`,
   `objsubid = 1`, the way `apply_guard._OWNERSHIP` reads it).
4. `docker restart infrahub-sync-preview-task-manager-1`, issued at 23:27:53.59 when the first
   guard hold appeared; returned at 23:27:56.04; `/api/health` answered 200 again at 23:27:58.24
   (4.6 s outage); the container reported `healthy` at 23:28:01.33.

Evidence:

- `product_runs`: 20 of 20 `applied`/`applied`, `reconciliation_required` false, every summary
  `update=1`. `core01.type` on `preview-smoke` holds the planted value after the first write and
  the same value after the twentieth. Infrahub mirrors 20 `SyncRun` nodes in phase `applied` and
  exactly 20 `SyncApproval` nodes.
- `prefect_executions` with `purpose = 'apply'`: 20 rows, all `attempt = 1`, `completed`/`succeeded`,
  no `stalled_at`; 6 claimed before the restart, 3 during the outage, 11 after it, 7 running across
  it. The flow windows (`claimed_at` to `terminal_at`) overlap: up to 8 at once, 69 overlapping
  pairs. Concurrency is bounded by the guard, not by the worker.
- Guard: 227 samples, 20 hold windows from 20 distinct PostgreSQL sessions, 0.11 to 0.53 s each
  (mean 0.26 s), never more than one holder in a sample, up to 5 waiters in a sample (57 samples
  with waiters), no two holds overlapping (consecutive holds at least 0.10 s apart, the sampling
  period), every hold inside an apply execution window. Nine holds before and during the outage
  (23:27:52.67 to 23:27:55.71), a 3.73 s pause while the task manager was down, eleven holds after
  recovery (23:27:59.44 to 23:28:03.44).
- Worker log in the window: each flow run logged `FlowRunCancellingObserver` and
  `FlowRunSuspendingObserver` `websocket failed … Switching to polling mode`, and five
  `Unable to connect to 'ws://localhost:4210/api/events/in'` at the restart; no crash, no exhausted
  retry. `GET /runs/{id}` on the Sync API answered 200 throughout. No run was left stuck: the
  outage was shorter than the stall threshold (`max(3 × PREFECT_WORKER_QUERY_SECONDS, 30)` = 30 s)
  and the admission TTL (300 s), so no liveness rule fired.
- Retries: five accepted applies re-sent with the same `Idempotency-Key` and body answered `202`
  with identical bodies and the audit outcome `replayed`; `write_admissions` for the 20 runs stayed
  at 20 (table total 42 before and after), apply `prefect_executions` stayed at 20, `SyncApproval`
  stayed at 20, the destination unchanged. A second apply on an admitted run with a different key
  answered `409 run-execution-conflict`.

Not exercised: a destination slow enough for a waiting apply to reach the guard's 30 s
`lock_timeout`, which fails that apply closed (`another writer holds this configuration's write
guard`) rather than serializing it.

## Scenario 6: version and schema guards (FR-002, FR-005)

- Point Sync at a Prefect server of another version. Expected: Sync refuses to start and names
  both versions.
- Remove an attribute from the loaded Sync schema. Expected: Sync refuses to start and lists it.

### Result, 2026-10-03: pass

Prefect version (FR-002). A server of another version, with its own home so the local Prefect
profile and database are untouched:

```bash
PREFECT_HOME=/tmp/prefect381 uvx --from prefect==3.8.1 prefect server start --host 127.0.0.1 --port 4299
```

`GET /api/admin/version` answers `"3.8.1"` there and `"3.8.6"` on the preview task manager; that is
the route `client.api_version()` reads (`/api/version` is the REST API version, `"0.8.4"` on both).
Started with the preview runtime environment and `PREFECT_API_URL=http://127.0.0.1:4299/api`:

- `uv run uvicorn --factory infrahub_sync.service.serve:build_app --host 127.0.0.1 --port 8099`
  exited 3 after 0.9 s. The lifespan traceback ends with `PrefectVersionMismatchError: the Prefect
  server runs '3.8.1', and this Sync release requires 3.8.6; use the Infrahub release that ships the
  Prefect version Sync requires`, then `ERROR: Application startup failed. Exiting.`
- `uv run python -m infrahub_sync.service.worker --pool sync-process-pool` exited 1 after 0.7 s:
  `infrahub-sync worker refused to start: the Prefect server runs '3.8.1', and this Sync release
  requires 3.8.6; …`.

Schema (FR-005). Deleting an attribute from a copy of `schema/sync.yml` and loading it changes
nothing (`infrahubctl schema load` answers "already up to date"): Infrahub schema loads are
additive, so the attribute needs `state: absent`. The optional `SyncConfiguration.description` was
used rather than `SyncRun.reason`, so that no run data was lost in the shared preview.

```bash
uv run infrahubctl schema load --wait 120 sync-without-description.yml   # description: state: absent
```

loaded in 7.7 s ("Schema updated on all workers"), after which `SyncConfiguration` listed only
`document` and `name`. With the preview runtime environment:

- the API (port 8098) exited 3 after 1.2 s: `SyncSchemaMissingError: the Sync schema extension is
  not loaded as this release needs it: missing SyncConfiguration.description; load schema/sync.yml
  from this release with …schema load`;
- the worker exited 1 after 1.1 s: `infrahub-sync worker refused to start: the Sync schema
  extension is not loaded as this release needs it: missing SyncConfiguration.description; …`.

`uv run infrahubctl schema load --wait 120 schema/sync.yml` restored the attribute in 7.4 s, and a
hand-started API then reached `Application startup complete`. The running preview API and worker
were unaffected throughout: both checks run at startup only. Removing an attribute discards its
stored values on existing nodes (all empty here), so pick an unused optional one in a shared
environment.

Observation, not a defect: the API's refusal is a uvicorn lifespan traceback (exit 3) while the
worker's is one line (exit 1); both name what the spec requires.
