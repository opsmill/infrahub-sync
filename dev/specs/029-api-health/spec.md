# Feature Specification: Health of the Sync API and each worker

**Feature Branch**: `029-api-health`
**Created**: 2026-11-23
**Status**: Draft
**Input**: INFP-663 requirement 3, "Health checks for the Sync API and the workers on Compose"
([INFP-663](https://opsmill.atlassian.net/browse/INFP-663)). The design follows the Infrahub
endpoint `GET /api/health` of [opsmill/infrahub#11473](https://github.com/opsmill/infrahub/pull/11473).
The decisions below were taken with the product owner in a grilling session on 2026-11-23.

## Context

A platform team runs the Sync API, one or more workers, the Prefect server, PostgreSQL and the
object store on one Compose host. Today:

- `GET /api/status` always reports `service: ready`, even when PostgreSQL, the object store or
  the Prefect server cannot be reached.
- Its `worker.state` value `busy` means that runs wait to be claimed, while the documentation says
  that a worker is running work. The state `unavailable` is not documented.
- `/api/status` gives a count of live workers, not the state of each worker.
- A paused or hung worker keeps looking `running` to Docker, and the `sync-worker` container has no
  healthcheck.

`/api/status` was never part of a released version, so this feature replaces it instead of
keeping it next to the new endpoint.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A monitoring tool detects a failed component or worker (Priority: P1)

The team's monitoring tool polls one URL of the Sync API and alerts on its HTTP status code. The
answer is 200 when the deployment can accept and run work, and 503 when it cannot. An operator
who receives the alert reads the response body and finds which component or which worker failed,
without reading container logs.

**Why this priority**: it is the INFP-663 requirement. Without it, a stopped worker or an
unreachable database is found only when a run waits or fails.

**Independent Test**: on the development stack, stop or pause one container at a time and read
`GET /api/health`: the HTTP status and the body name the failed part, and the answer returns to
200 after the part is restored.

**Acceptance Scenarios**:

1. **Given** a Compose deployment with one live worker, **When** the operator pauses
   `sync-worker`, **Then** within 60 seconds `GET /api/health` answers 503 and lists that worker
   as `stale` with the time of its last heartbeat; **and When** the worker resumes, **Then**
   within 30 seconds the answer is 200 again.
2. **Given** a healthy deployment, **When** the operator stops `postgres`, **Then** within 5
   seconds `GET /api/health` answers 503 with `database` `down` and an error category; **and
   When** PostgreSQL is restored, **Then** within 15 seconds the answer is 200 again.
3. **Given** a healthy deployment, **When** the operator stops the object store, or the Prefect
   server, **Then** within 5 seconds the answer is 503 and names that component.
4. **Given** a healthy deployment with one worker, **When** the operator runs
   `docker compose restart sync-worker`, **Then** once the new worker has sent its first
   heartbeat the answer is 200, although the record of the previous worker stays in the pool as
   `stale`.
5. **Given** `INFRAHUB_SYNC_HEALTH_MIN_WORKERS=2` and two live workers, **When** one worker
   stops, **Then** within 60 seconds the answer is 503 and lists one `live` and one `stale`
   worker.
6. **Given** any state of the deployment, **When** a caller without credentials requests
   `GET /api/health`, **Then** it receives the answer, and the answer contains no host name, IP
   address, credential name or Sync version.

---

### User Story 2 - A hung worker container shows as unhealthy in Docker (Priority: P2)

An operator who runs `docker compose ps` sees the `sync-worker` container as `unhealthy` when the
worker process no longer sends heartbeats, instead of `running`.

**Why this priority**: it closes the gap the Compose documentation describes ("a paused or hung
worker keeps looking `running` to Docker") for operators who look at Docker first. The P1
endpoint already reports the same failure, so this story adds a second view of it.

**Independent Test**: on the development stack, make the worker stop sending heartbeats while its
process stays alive, then read `docker compose ps`.

**Acceptance Scenarios**:

1. **Given** a running worker, **When** it has not recorded a successful heartbeat for longer
   than the liveness threshold, **Then** Docker reports the `sync-worker` container as
   `unhealthy`.
2. **Given** an `unhealthy` worker, **When** its heartbeats resume, **Then** Docker reports it as
   `healthy` again at the next healthcheck.
3. **Given** a healthy worker and an unreachable PostgreSQL, **When** Docker runs the worker's
   healthcheck, **Then** the result depends only on the worker's own heartbeats, not on
   PostgreSQL.

---

### User Story 3 - Existing readers of the worker state keep working (Priority: P3)

The Python client, the `/api` redirect and the web interface read the worker state from
`/api/status` today. After `/api/status` is removed, each of them reads `/api/health` and shows
the same information as before.

**Why this priority**: removing `/api/status` without moving its readers would break the page
frame of the web interface and the client. It carries no new user value of its own.

**Independent Test**: open the web interface and start a run with no live worker, then with one
live worker; call `SyncClient.get_health()`; request `/api` and follow the redirect.

**Acceptance Scenarios**:

1. **Given** the web interface open on any page, **When** no worker is live, **Then** the page
   frame shows the same worker state and message as it did with `/api/status`.
2. **Given** a Python program that used `SyncClient.get_status()`, **When** it calls
   `SyncClient.get_health()`, **Then** it receives the parsed health response, including for a
   503 answer.
3. **Given** a browser, **When** it requests `/api` or `/api/`, **Then** it is redirected to
   `/api/health`.
4. **Given** the Sync API, **When** a caller requests `/api/status`, **Then** it receives 404.

---

### Edge Cases

- **A worker restart**: each worker start registers under a new name, and the pool keeps the
  previous worker's record. The previous worker is listed as `stale` and does not cause 503.
- **The Prefect server is unreachable**: `task_manager` is `down`, which causes 503. The worker
  section reports its state as `unknown`, with an empty list and no count, so that a monitoring
  tool does not read a count of 0 as "every worker stopped".
- **The Prefect server answers but its database does not**: `task_manager` is `up` and
  `task_manager_db` is `down`, which causes 503.
- **A component hangs instead of refusing the connection**: its check ends after the time limit
  with the category `timeout`; the answer never waits longer than the time limit plus the time
  to write the response.
- **Many callers poll at once**: callers that arrive while a round of checks runs share that
  round, and each Sync API process runs at most one round per second.
- **A worker that has not sent a heartbeat for more than 24 hours**: it is no longer listed.
- **No worker has ever started**: the list is empty, `live_workers` is 0, and the answer is 503
  because 0 is below the minimum of 1.
- **`INFRAHUB_SYNC_HEALTH_MIN_WORKERS` or the time limit is set to a value outside its range**:
  the Sync API refuses to start and names the setting, as for its other settings.
- **Runs wait to be claimed while workers are live**: the answer stays 200 and reports
  `queue_depth`; waiting runs alone are not a failure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Sync API MUST serve `GET /api/health`. It MUST answer 200 when every component
  in FR-003 is `up` and the number of live workers is at least the minimum of FR-007, and 503
  otherwise. There is no third, "degraded" result.
- **FR-002**: The response MUST list each component of FR-003 under `dependencies`, each with a
  status `up` or `down` and an error category, one of `timeout`, `connection_refused`,
  `connection_closed`, `name_resolution_failed`, `unavailable`, `unknown_error`, or `none` when
  the component is `up`. The response MUST carry `Cache-Control: no-store` and the time the
  checks ran.
- **FR-003**: The Sync API MUST check four components:
  - `database`: the product store in PostgreSQL, with a read that writes nothing.
  - `object_store`: the bucket that holds plans and run files.
  - `task_manager`: the Prefect server, through its own health endpoint.
  - `task_manager_db`: the database of the Prefect server, through the Prefect readiness
    endpoint, so that the Sync API needs no credential for that database.
- **FR-004**: Each check MUST end within a time limit, 3 seconds by default, set by a setting
  that accepts whole seconds from 1 to 60. The checks MUST run at the same time. Each Sync API
  process MUST run at most one round of checks per second; callers that arrive during a round
  MUST receive its result.
- **FR-005**: The response MUST report the workers of the work pool under `workers`:
  - each worker that sent a heartbeat in the last 24 hours, with its name, `live` or `stale`,
    and the time of its last heartbeat; the 24-hour period is fixed;
  - `live_workers`, the number of live workers, and `queue_depth`, the number of runs waiting to
    be claimed;
  - when the Prefect server cannot be read, a worker state of `unknown`, an empty list and no
    counts.
- **FR-006**: A worker MUST count as live when the Prefect server reports it online and its last
  heartbeat is within `max(3 × its heartbeat interval, 30)` seconds. One shared function MUST make
  this decision for `GET /api/health` and for the liveness monitor of the Sync API, so that the
  two cannot disagree.
- **FR-007**: The answer MUST be 503 when `live_workers` is below the setting
  `INFRAHUB_SYNC_HEALTH_MIN_WORKERS` (whole number, default 1, minimum 1). A `stale` worker MUST
  NOT cause 503 on its own.
- **FR-008**: `GET /api/health` MUST answer without authentication. The response MUST NOT contain
  a host name, an IP address, a credential name or value, an error message, or the Sync version.
- **FR-009**: The Sync API MUST stop serving `GET /api/status`, and every reader MUST move to
  `GET /api/health`:
  - the Python client: `SyncClient.get_status()` is replaced by `SyncClient.get_health()`, which
    returns the parsed response for a 200 or a 503 answer;
  - the redirect of `/api` and `/api/` goes to `/api/health`;
  - the web interface shows the same worker state and messages as it did from `/api/status`;
  - the documentation and the tests that use `/api/status`.
- **FR-010**: The healthcheck of the `sync-api` Compose service MUST stay on `/api/version`, so
  that the container's health reflects only the Sync API process.
- **FR-011**: The `sync-worker` Compose service MUST have a healthcheck that fails when the worker
  process has not recorded a successful heartbeat within the liveness threshold of FR-006, and
  that does not depend on PostgreSQL, the object store or the Sync API.
- **FR-012**: The documentation MUST describe `GET /api/health`: each field, each error category,
  the meaning of 200 and 503, the two settings, and an example of a monitoring check. The pages
  that describe `/api/status` MUST describe `/api/health` instead.

### Key Entities

- **Health response**: the result of one round of checks. It holds the overall status, the time
  of the checks, one entry per component and the worker section. It replaces the status
  response of `/api/status`.
- **Component**: PostgreSQL (product store), the object store, the Prefect server and its
  database. All four exist today; this feature adds a check for each.
- **Worker**: a Prefect worker in the Sync work pool. It exists today and receives a new random
  name at each start. Its state in the response is `live` or `stale`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When a worker stops or hangs, the health answer becomes 503 within 60 seconds.
- **SC-002**: When PostgreSQL, the object store or the Prefect server stops or hangs, the health
  answer becomes 503 within 5 seconds.
- **SC-003**: After a component recovers, the health answer is 200 again within 15 seconds; after
  a worker recovers, within 30 seconds.
- **SC-004**: For every failure in SC-001 and SC-002, an operator can name the failed component or
  worker from the response alone.
- **SC-005**: Restarting a worker causes no 503 once the new worker has sent its first heartbeat.
- **SC-006**: 50 concurrent callers polling for one minute receive an answer to every request,
  and each Sync API process runs no more than one round of checks per second.

## Assumptions

- A deployment uses one work pool, the one the Sync API is configured with.
- The Prefect server keeps the record of a stopped worker and reports it offline.
- Operators have a monitoring tool that polls an HTTP URL and alerts on its status code.
- The Sync API has no request metrics or tracing today, so there is nothing to exclude
  `/api/health` from. If either is added later, it should exclude `/api/health`, as Infrahub does.
- A read check on the product store finds an unreachable or refusing database. It does not find a
  database that accepts reads but refuses writes, for example with a full disk.
- The web interface health page is a separate piece of work, built on `GET /api/health` by the
  product owner.
- The planned automatic resume of an apply after its worker disappears will use the shared
  liveness function of FR-006.

## Out of Scope

- Checks on sources and destinations (Infrahub, NetBox, Nautobot): they belong to a
  configuration, not to the deployment.
- A degraded state: any failure gives 503, as in Infrahub.
- Exporting metrics, and the readiness probe of the Kubernetes Helm chart: both belong to
  [INFP-775](https://opsmill.atlassian.net/browse/INFP-775).
- Restarting an unhealthy container automatically.
- The web interface health page.
- A write check on the product store.
- A setting for the 24-hour period of stale workers.
