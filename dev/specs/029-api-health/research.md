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
| `database` | `psycopg.AsyncConnection.connect` to the product database with `connect_timeout`, `SELECT 1`, close. No `options` keyword: it would replace the `options` of `INFRAHUB_SYNC_DATABASE_URL`, and a pooler such as PgBouncer refuses it; the round's `asyncio.wait_for` limit cancels a query that does not answer, and psycopg asks the server to cancel it | The product store opens a connection per operation through `PsycopgConnectionFactory` (`infrahub_sync/service/storage.py:59`); a separate connection cannot be starved by a busy request. The async connection runs on the event loop, so no thread outlives the check (critique E3). Read only, as the product owner decided. |
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
- psycopg 3.3.6 (the locked version) raises no `OSError` in the chain; this was checked on
  2026-11-23 against an unknown host, a closed port and an unroutable address:
  - `psycopg.errors.ConnectionTimeout` (typed) → `timeout`, classified by type.
  - `OperationalError` whose text starts with `failed to resolve host` (psycopg resolves the name
    itself) or contains `could not translate host name` (libpq) → `name_resolution_failed`.
  - text containing `Connection refused` → `connection_refused`.
  - text containing `server closed the connection` or `connection to server was lost` →
    `connection_closed`.
  - text containing `timeout expired` → `timeout`.
  - any other `psycopg.Error` (login refused, database missing, statement timeout) →
    `unavailable`.

  The text is read only to choose a category and never leaves the process. The Sync image runs
  with the C locale, so libpq messages are in English. Each marker is pinned by a unit test with
  the exact recorded text, so the mapping is tested in the default CI job, and by the opt-in
  component tests against the pinned PostgreSQL image (critique E6).
- The checks catch only the exception types listed above, plus `OSError`. An exception of any
  other type is caught once, at the boundary of one check, and reported as `unknown_error`
  (justified under Complexity Tracking in `plan.md`, critique E2).
- Anything else → `unknown_error`.

**Rationale**: the categories must be specific for real outages (SC-004). Component tests against
real services pin each mapping, as Infrahub's PR did.

**Alternatives considered**: `unknown_error` for every psycopg error (fails SC-004 for the most
important component); checking the PostgreSQL port with a raw socket first (a second check that
can disagree with the first).

## R-4 Time limit and the one-round-per-second rule

**Decision**: each check runs under `asyncio.wait_for(check, limit)`. The database check is async
(R-2). boto3 has no async client in the dependencies, so `head_bucket` runs in `asyncio.to_thread`
with `connect_timeout` and `read_timeout` equal to the limit and `retries={"max_attempts": 0}`, so
the thread ends close to the limit. A `HealthChecker` holds an `asyncio.Lock` and the last report
with the monotonic time at which its round **ended**, as Infrahub's does; a caller within 1 second
of that time receives it, and a caller that waited for the lock checks the age again, so one round
serves every caller that arrived during it (critique E1). A unit test with a fake clock asserts one
round for ten concurrent callers.

`INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT`: whole seconds, 1 to 60, default 3.
`INFRAHUB_SYNC_HEALTH_MIN_WORKERS`: whole number, 1 to 999999999, default 1. Both are read by
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
  record with no heartbeat is left out. A name that is not `infrahub-sync-service-<uuid>` is shown
  as `other` (spec FR-013, critique E5), so the response can never hold `acct_` and the
  `AccountNameMiddleware` (`infrahub_sync/service/presentation.py:150`) never reads accounts for
  it; a PostgreSQL outage therefore returns the health report, not the generic 503 envelope.
- `pool_status` is not changed: one malformed Prefect record still makes the whole snapshot
  unavailable (`orchestration.py:464-467`). Skipping the record there would let liveness rule 8
  mark a running apply `interrupted` when the claiming worker's record is the malformed one
  (critique E4, not applied). The health answer reports `unknown` in that case.
  `tests/service/test_liveness_policy.py` must pass without edits.
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

**Decision** (revised after critique X1): the worker starts one asyncio task next to Prefect's
loops that, every heartbeat interval, writes `<epoch seconds> <interval seconds>` to
`$TMPDIR/infrahub-sync-worker-alive` (temporary file, then `os.replace`). The task depends on no
other service, so the file shows that the worker's process and event loop run. A paused process,
a process stopped with `SIGSTOP`, or an event loop blocked by synchronous code stops the writes.
A write failure logs one warning, once, and the worker keeps running.

The healthcheck reads the file with an inline command that imports only the standard library, so
it does not import `infrahub_sync/__init__.py` (pydantic, diffsync, jinja2) every 10 seconds
(critique E7):

```yaml
test: ["CMD", "python", "-I", "-c", "<read the file; exit 0 when now - time <= max(3 * interval, 30)>"]
```

The command text lives in one constant, `WORKER_HEALTHCHECK_COMMAND` in
`infrahub_sync/service/worker_alive.py`, next to the writer; a test asserts that both Compose
files carry exactly that command and runs it against fresh, old, missing and malformed files.

`TMPDIR` in the Sync image points into the tmpfs of the declared writable roots
(`docker-compose.yml:34-37`), so the file disappears with the container. In the development
stack, where the worker has no tmpfs, the file can survive a restart; it then holds an old time,
and the healthcheck reports `unhealthy` until the new process writes it, which is the expected
start-up state. Compose: `interval: 10s`, `timeout: 5s`, `retries: 3`, `start_period: 60s`. The
same healthcheck is added to the worker of `development/docker-compose.yml`.

Docker reports a paused container `unhealthy` (checked on Docker Desktop, 2026-11-23, with an
`alpine` container and `docker pause`).

**Alternatives considered**: recording a successful Prefect heartbeat (the first plan): in the
Compose deployment the Prefect database shares the PostgreSQL container with the product database
(`docker-compose.yml:46`, `:191`), so a PostgreSQL outage would turn the worker container
unhealthy, which the product owner ruled out; calling `/api/health` from the worker container
(depends on the Sync API); asking Prefect for this worker's record (depends on Prefect).

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
