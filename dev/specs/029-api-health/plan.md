# Implementation Plan: Health of the Sync API and each worker

**Branch**: `029-api-health` | **Date**: 2026-11-23 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `dev/specs/029-api-health/spec.md`

## Summary

Add `GET /api/health` to the Sync API, shaped like Infrahub's endpoint of
[opsmill/infrahub#11473](https://github.com/opsmill/infrahub/pull/11473): four component checks
(product database, object store, Prefect server, Prefect database) that run at the same time under
one time limit, at most one round per second per process, plus a Sync-only `workers` section that
lists each worker of the pool. The answer is 503 when a component is down or fewer workers are live
than `INFRAHUB_SYNC_HEALTH_MIN_WORKERS`. The liveness rule moves to one function used by the
endpoint and the liveness monitor. `GET /api/status`, never released, is removed and its readers
(Python client, `/api` redirect, web interface) move to `/api/health`. The worker container gets a
Compose healthcheck that reads a heartbeat file the worker writes after each heartbeat.

## Technical Context

**Language/Version**: Python 3.11–3.13 for the service; the client models stay valid on 3.10.
TypeScript for the web interface.
**Primary Dependencies**: FastAPI (existing), psycopg 3 (existing), boto3/botocore (existing),
httpx (existing), Prefect 3.8.6 client (existing). No new dependency.
**Storage**: none added. Reads only: product PostgreSQL, the object-store bucket, Prefect.
**Testing**: pytest unit tests (`tests/service`, `tests/client`); component tests against real
PostgreSQL, object store and Prefect through the existing testcontainers fixtures, marked opt-in;
the Compose lifecycle and documentation tests (`tests/compose`); Vitest for the frontend function
that derives the worker state.
**Target Platform**: Linux containers (Compose: root `docker-compose.yml`, development stack
`development/docker-compose.yml`).
**Project Type**: web service with a bundled web interface and a Python client.
**Performance Goals**: one round of checks per second per Sync API process at most; 50 concurrent
callers answered (SC-006).
**Constraints**: answer within the time limit (default 3 s) plus response time; no host, address,
credential, message or version in the answer; no authentication.
**Scale/Scope**: one work pool, a handful of workers, Prefect records of the last 24 hours.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design: still passes.*

| Principle | Status | Note |
| --- | --- | --- |
| I. Read-only and dry-run by default | Pass | Every check reads. The database check writes nothing (product owner decision). |
| II. Sync idempotency and safety | Pass | No sync behavior changes. Timeouts and network errors are handled explicitly and reported as categories. |
| III. Adapter symmetry | N/A | No adapter changes. |
| IV. Type safety and explicit contracts | Pass | Pydantic response models, typed checker, contract in `contracts/health-api.md`, regenerated frontend types. |
| V. Test discipline | Pass | Unit tests for classification, round sharing, status rule and settings; component tests per real service; Compose test for the worker healthcheck. |
| VI. Security, secrets, input boundaries | Pass with care | Unauthenticated endpoint by decision (as Infrahub and the former `/status`). The response carries only fixed categories and worker names; error text is used only to choose a category. Reviewed against `secret-redaction.md`. |
| VII. Simplicity | Pass | One new module (`health.py`) and one small entrypoint (`worker_health.py`). `worker_is_live` has two real callers. No new dependency. |
| Documentation | Pass | Reference, operator pages, Python API page, ADR and changelog in the same change. |

## Project Structure

### Documentation (this feature)

```text
dev/specs/029-api-health/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/health-api.md
├── checklists/requirements.md
└── tasks.md            # created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── client/
│   ├── client.py              # get_status() → get_health(), accepts 200 and 503
│   └── models.py              # HealthResource and children replace ServiceStatusResource, WorkerStatusResource
└── service/
    ├── health.py              # NEW: ErrorCategory, classify_error, the four checks, HealthChecker
    ├── worker_health.py       # NEW: `python -m` entrypoint for the worker container healthcheck
    ├── orchestration.py       # PoolWorker.name, worker_is_live()
    ├── liveness.py            # _owner_is_fresh() calls worker_is_live()
    ├── service.py             # remove status() and _service_status(); expose the pool read to the checker
    ├── app.py                 # GET /health route; remove GET /status
    ├── serve.py               # build the HealthChecker with the storage settings and the orchestration
    ├── settings.py            # INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT, INFRAHUB_SYNC_HEALTH_MIN_WORKERS
    ├── web.py                 # /api redirect target → /api/health
    └── worker.py              # write the heartbeat file after each heartbeat with a resolved identity
frontend/src/
├── api/queries.ts             # healthQuery() reads 200 and 503 bodies; statusQuery removed
├── api/types.generated.ts     # regenerated
├── domain/run-state.ts        # workerState(health) derives ready/busy/no-live-worker/unavailable
├── routes/__root.tsx          # page frame reads healthQuery
└── features/runs/StartRunDialog.tsx
docker-compose.yml             # sync-worker healthcheck; new settings passed to sync-api
development/docker-compose.yml # same
docs/docs/                     # reference/sync-http-api.mdx, reference/python-api.mdx, operator pages
dev/adr/00NN-the-health-endpoint-replaces-status.md
changelog/                     # +api-health.added.md, +api-status-removed.changed.md
tests/
├── service/test_health.py                 # NEW: unit
├── service/test_health_components.py      # NEW: component, opt-in, real services
├── service/test_worker_health.py          # NEW: unit
├── service/test_http_api.py, test_web_site.py, test_liveness_policy.py, test_account_presentation.py
├── client/test_client.py, test_not_sync_api.py
├── compose/test_documentation.py, test_lifecycle.py, lifecycle.py
└── dev_stack/…                            # move /status uses to /health
```

**Structure Decision**: the existing single repository layout. The checker lives in the service
package next to `orchestration.py` and `storage.py`, whose settings and clients it reuses.

## Design

### Request flow

1. `GET /api/health` → `app.py` route → `HealthChecker.report()`.
2. If the last report is under 1 second old, return it. Otherwise take the lock; a caller that
   waited re-checks the age, so one round serves everyone who arrived during it.
3. A round runs five tasks with `asyncio.gather`, each under `asyncio.wait_for(limit)`:
   `database`, `object_store`, `task_manager`, `task_manager_db`, and the pool read.
4. Each component task returns `up/none` or `down/<category>`; each failure logs one warning line
   with the component and category, never the error text.
5. The pool read returns a `PoolStatus`; `known` when `detail_available`, else `unknown`. Items are
   the workers with a heartbeat within 24 hours, each `live` when `worker_is_live(worker, now)`.
6. `status` follows research R-6; the route answers 200 or 503 with `Cache-Control: no-store`.

### Construction

`build_app` (`serve.py`) reads the two settings through `settings.py`, builds the checks from the
same environment as `service_product_projection` (`_database_url`, `s3_client_arguments`, bucket)
and `PREFECT_API_URL`, and passes the `HealthChecker` to `create_app`. Tests build a checker with
fake checks, as `create_app` already takes fakes for the run service. A Sync API built by a test
without a checker answers `/api/health` from fakes only; no test reaches real services by default.

### The worker heartbeat file

`ServiceProcessWorker.sync_with_backend` writes `time.time()` to the file after
`super().sync_with_backend()` returns with `self.backend_id is not None`. A write failure logs one
warning and never stops the worker: the healthcheck then reports unhealthy, which is the truth
for an operator who cannot see the heartbeat.

### What is removed

`GET /status`, `RunService.status()`, `_service_status()`, `ServiceStatusResource`,
`WorkerStatusResource`, `SyncClient.get_status()`, `statusQuery`. Nothing outside the repository
uses them: `/status` was never released.

## Risks

- **libpq message text** (research R-3): a libpq release could change a message and turn a specific
  category into `unavailable`. Component tests against the pinned PostgreSQL image pin the current
  mapping.
- **A thread that outlives its check**: `wait_for` returns at the limit while a blocking psycopg or
  boto3 call may continue in its thread until its own timeout, set to the same limit. At most one
  round per second bounds the number of such threads.
- **Web interface ownership**: the frontend change is limited to moving the four existing readers;
  the product owner builds the new health page and agrees to this change (2026-11-23).

## Complexity Tracking

No constitution violation to justify.
