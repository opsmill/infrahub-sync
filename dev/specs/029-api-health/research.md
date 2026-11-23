# Research: Health of the Sync API and each worker

Each entry records a decision, why it was taken, and what else was considered. Sources are the
code on `feature/v3-develop` at `44743494` and Infrahub PR
[opsmill/infrahub#11473](https://github.com/opsmill/infrahub/pull/11473) (open on 2026-11-23).

## R-1 Response shape follows Infrahub

**Decision**: the response has `status` (`healthy` or `unhealthy`), `dependencies` (a list of
`{name, status, error}`), `timestamp` (when the round ran), and a Sync-only `workers` object.
The error categories are the seven of Infrahub, with the same names.

**Rationale**: the product owner asked for the Infrahub approach. A monitoring rule written for
Infrahub's endpoint then works for Sync's with a different URL. Infrahub's PR names the list
`dependencies` (renamed from `checks`) so that "a later section on worker liveness does not clash
with it"; Sync adds that section.

**Alternatives considered**: a map keyed by component name (shorter, but differs from Infrahub);
keeping the `/status` shape (`service`, `worker.state`) and adding fields (keeps `busy`, whose
meaning the documentation already gets wrong).

## R-2 The four component checks

**Decision**:

| Component | Check | Why this check |
| --- | --- | --- |
| `database` | A new connection to the product database with `connect_timeout`, `SELECT 1`, close | The product store opens a connection per operation through `PsycopgConnectionFactory` (`infrahub_sync/service/storage.py:59`); a separate connection cannot be starved by a busy request. Read only, as the product owner decided. |
| `object_store` | `head_bucket` on the configured bucket, with a client built from `s3_client_arguments` (`storage.py:251`) and `connect_timeout`/`read_timeout` equal to the time limit and no retries | The same call `sync-bootstrap` already makes (`infrahub_sync/service/bootstrap.py:182`). It proves the endpoint, the credentials and the bucket. |
| `task_manager` | `GET {PREFECT_API_URL}/health` | As in Infrahub. |
| `task_manager_db` | `GET {PREFECT_API_URL}/ready`, which answers 503 when the Prefect server cannot reach its database | As in Infrahub. The Sync API holds no credential for the Prefect database. |

Prefect 3.8.6, the locked version, serves `/health` and `/ready` without authentication even when
`PREFECT_SERVER_API_AUTH_STRING` is set (`prefect/server/api/server.py:519-535`: "Allow
unauthenticated health/ready probes"). The Compose file sets that string, so this matters. The
two Prefect checks use a plain `httpx.AsyncClient`, not the Prefect client, because the Prefect
client retries a 503 before it returns (Infrahub PR, same finding).

**Alternatives considered**: reusing the product projection's connection for `database` (none is
pooled, so nothing is gained); `list_objects_v2` for the object store (needs list permission the
deployment does not otherwise require).

## R-3 Error categories for each client

**Decision**: one `classify_error` walks the exception chain (cause, context and exception
groups), as Infrahub's does, with these additions for the Sync clients:

- `TimeoutError`, `asyncio.TimeoutError`, `httpx.TimeoutException`, botocore
  `ConnectTimeoutError` and `ReadTimeoutError` → `timeout`.
- `socket.gaierror` → `name_resolution_failed`; `ConnectionRefusedError` → `connection_refused`;
  `ConnectionResetError`, `ConnectionAbortedError`, `BrokenPipeError`, `httpx.RemoteProtocolError`,
  botocore `ConnectionClosedError` → `connection_closed`.
- botocore `EndpointConnectionError`: classified from the error it wraps.
- An HTTP answer that is not 2xx from Prefect, and a botocore `ClientError` (bucket missing,
  credentials refused, server error) → `unavailable`.
- `psycopg.OperationalError`: libpq reports connection failures as text, without an `OSError` in
  the chain. The classifier reads fixed libpq markers: `could not translate host name` →
  `name_resolution_failed`; `Connection refused` → `connection_refused`; `timeout expired` →
  `timeout`; `server closed the connection` → `connection_closed`; any other → `unavailable`. The
  text is read only to choose a category and never leaves the process. The Sync image runs with
  the C locale, so libpq messages are in English.
- Anything else → `unknown_error`.

**Rationale**: the categories must be specific for real outages (SC-004). Component tests against
real services pin each mapping, as Infrahub's PR did.

**Alternatives considered**: `unknown_error` for every psycopg error (fails SC-004 for the most
important component); checking the PostgreSQL port with a raw socket first (a second check that
can disagree with the first).

## R-4 Time limit and the one-round-per-second rule

**Decision**: each check runs under `asyncio.wait_for(check, limit)`. Blocking clients (psycopg,
boto3) run in `asyncio.to_thread`; their own timeouts are set to the same limit so that the thread
also ends. A `HealthChecker` holds an `asyncio.Lock` and the last report with its monotonic time;
a caller within 1 second of the last round receives it, and callers during a round wait for it.
This is Infrahub's `HealthChecker`, adapted.

`INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT`: whole seconds, 1 to 60, default 3.
`INFRAHUB_SYNC_HEALTH_MIN_WORKERS`: whole number, at least 1, default 1. Both are read by
`validate_settings()` (`infrahub_sync/service/settings.py:200`), which `create_app` already calls,
and refuse start with a `ValueError` that names the variable, as `session_idle_seconds()` does.
libpq raises a `connect_timeout` below 2 to 2 seconds; the outer `wait_for` still ends the check at
the limit.

## R-5 Worker list and the shared liveness rule

**Decision**:

- `PoolWorker` (`infrahub_sync/service/orchestration.py:155`) gains `name: str | None = None`,
  filled by `_pool_worker` from the Prefect record. Nothing else reads a worker name today.
- A new function `worker_is_live(worker: PoolWorker, now: datetime) -> bool` in
  `orchestration.py` holds the rule "status `online` and heartbeat within
  `max(3 × interval, 30)` seconds". `liveness._owner_is_fresh` (`liveness.py:270`) and the health
  checker both call it; `_service_status` (`service.py:178`), the only other copy, is removed with
  `/status`.
- The worker list keeps the records whose last heartbeat is within 24 hours of the round. A
  record with no heartbeat is left out.
- The workers are read with the existing `pool_status` (one call to `read_workers_for_work_pool`
  and one to `get_scheduled_flow_runs_for_work_pool`), under the same time limit. When it returns
  `detail_available=False`, or times out, the worker state is `unknown`.

**Rationale**: FR-006 requires one rule; the automatic-resume work (session `infrahub-sync-8a`)
will be a third reader. `pool_status` already turns every Prefect failure into one unavailable
value.

## R-6 Overall status

**Decision**: `healthy` when every component is `up`, the worker state is `known` and
`live_workers ≥ INFRAHUB_SYNC_HEALTH_MIN_WORKERS`; `unhealthy` otherwise. An `unknown` worker state
with every component `up` (for example, the work pool was deleted) is `unhealthy`, because the
minimum cannot be confirmed.

## R-7 The worker container's healthcheck

**Decision**: after each `sync_with_backend` that ends with a resolved identity
(`ServiceProcessWorker`, `infrahub_sync/service/worker.py:293`), the worker writes the current time
to `/tmp/infrahub-sync/worker-heartbeat` (overwrite through a temporary file and `os.replace`). A
new module `infrahub_sync/service/worker_health.py`, run as
`python -m infrahub_sync.service.worker_health`, exits 0 when the file's time is within
`max(3 × PREFECT_WORKER_HEARTBEAT_SECONDS, 30)` seconds, and 1 otherwise (missing file,
unreadable file, old time). It imports only the standard library, so it starts fast.

`/tmp/infrahub-sync` is a tmpfs of the image's declared writable roots (`docker-compose.yml:34-37`),
so the file disappears with the container and needs no volume. Compose: `interval: 10s`,
`timeout: 5s`, `retries: 3`, `start_period: 60s`. The same healthcheck is added to the worker of
`development/docker-compose.yml`.

**Alternatives considered**: calling `/api/health` from the worker container (makes the worker's
health depend on the Sync API, which FR-011 forbids); asking Prefect for this worker's record (the
container does not know its own UUID before identity resolution, and depends on Prefect).

## R-8 Removing `/status` and moving its readers

**Decision**:

- `infrahub_sync/service/app.py:484`: the route is removed; `/api/status` answers 404 through the
  existing not-found handling.
- `infrahub_sync/service/web.py:47`: `API_ROOT_TARGET` becomes `/api/health`.
- `SyncClient.get_status()` (`infrahub_sync/client/client.py:265`) is replaced by `get_health()`,
  which accepts 200 and 503 and returns `HealthResource`; other status codes raise `ProtocolError`
  as before. `ServiceStatusResource` and `WorkerStatusResource` are removed from
  `infrahub_sync/client/models.py`. No CLI command calls `get_status()`.
- The web interface (`frontend/src/api/queries.ts:46`, `routes/__root.tsx`,
  `features/runs/StartRunDialog.tsx`, `domain/run-state.ts`) reads `/health`. A 503 answer carries
  the data the page needs, so the query reads the body for 200 and 503 instead of treating 503 as
  an error. One function in `domain/run-state.ts` derives the four states the page shows today:
  `unavailable` when the worker state is `unknown`, `no-live-worker` when `live_workers` is 0,
  `busy` when `queue_depth > 0`, else `ready`. The text shown stays the same.
  `src/api/types.generated.ts` is regenerated with `pnpm codegen:openapi`.

**Rationale**: `/status` was never released (product owner, 2026-11-23). The frontend change is
the smallest that keeps the page frame and the start dialog working; the new health page is the
product owner's separate work.

## R-9 Python versions

The Sync service runs on Python 3.11–3.13 only, so `health.py` can use `StrEnum` and
`BaseExceptionGroup`. `infrahub_sync/client/models.py` is part of the base install, which supports
Python 3.10, so the client models use `Literal` types, as `WorkerStatusResource` does today.
`worker_health.py` uses only the standard library.

## R-10 Documentation and decision record

- A new ADR (next free number at implementation time; 0054 is the last on
  `feature/v3-develop`) records: `/api/health` replaces `/api/status`; the shape follows Infrahub;
  workers count toward the status through a minimum, not per record; the database check reads
  only.
- `docs/docs/reference/sync-http-api.mdx` documents the endpoint. The operator pages that show
  `/api/status` (`compose-deployment.mdx`, `operations/day-2-operations.mdx`,
  `operations/compose-troubleshooting.mdx`, `quickstart-compose.mdx`, `development-stack.mdx`, the
  three tutorials, `use-with-an-ai-agent.mdx`) and `reference/python-api.mdx` move to
  `/api/health`. `tests/compose/test_documentation.py:397` checks that the Compose page documents
  every worker state; it is changed to check the error categories and the worker states of the
  new response.
- A changelog fragment of type `changed` describes the replacement of `/status`, and one of type
  `added` the endpoint and the worker healthcheck.
