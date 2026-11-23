# 55. `GET /api/health` replaces `GET /api/status`

**Status**: Accepted
**Date**: 2026-11-24
**Source**: `dev/specs/029-api-health/spec.md` (FR-001 to FR-013),
`dev/specs/029-api-health/research.md` (R-1 to R-8),
`dev/specs/029-api-health/contracts/health-api.md`

Amends [ADR 0043](0043-the-web-interface-is-served-at-the-root-and-the-api-under-api.md): the
unauthenticated route that 0043 names, `/api/status`, is replaced by `/api/health`, and the
development tasks that read `/api/status` read `/api/health` instead.

## Context

Before this decision, the Sync API served `GET /api/status`. It reported `service` and one
`worker.state` (`ready`, `busy`, `no-live-worker` or `unavailable`), read from the Prefect work
pool. It did not check PostgreSQL, the object store or the Prefect server, so an outage of one of
them was not visible in the answer. The route returned its resource with status `200` whenever it
answered, so a monitoring tool had to parse the body. `/api/status` was never in a release.

Infrahub adds a health endpoint with the same purpose in
[opsmill/infrahub#11473](https://github.com/opsmill/infrahub/pull/11473): a `status` of `healthy`
or `unhealthy`, a list of `dependencies` with `{name, status, error}`, seven error categories and a
`timestamp`.

Two facts about the Compose deployment shape the worker side:

- One PostgreSQL container holds both the product database and the Prefect database. A Prefect
  worker heartbeat is written to the Prefect database, so a PostgreSQL outage stops every
  heartbeat even when the worker process runs.
- A paused or hung worker container keeps the Docker state `running`. Before this decision the
  `sync-worker` service had no healthcheck.

## Decision

- **`/api/health` replaces `/api/status`.** `GET /api/status` is removed and answers `404`.
  `SyncClient.get_status()`, `ServiceStatusResource` and `WorkerStatusResource` are removed;
  `SyncClient.get_health()` returns `HealthResource`. `GET /api` redirects to `/api/health`. Because
  `/api/status` was never released, no deprecation period applies.
- **The shape follows Infrahub.** The answer has `status`, `dependencies` (`database`,
  `object_store`, `task_manager`, `task_manager_db`, in this order), `timestamp` and the same
  seven error categories with the same names. Sync adds a `workers` section. The status code is
  `200` when `status` is `healthy` and `503` otherwise, so a monitoring check reads only the code.
  A monitoring rule written for Infrahub's endpoint then works for Sync's with a different
  address.
- **Workers count through a minimum.** The answer is `503` when fewer than
  `INFRAHUB_SYNC_HEALTH_MIN_WORKERS` (default 1) workers are live, or when the work pool cannot be
  read. Each worker that sent a heartbeat in the last 24 hours is listed as `live` or `stale`, but
  a `stale` record never makes the answer `503`. Each worker start registers a new Prefect name,
  so the record of the previous worker stays in the pool after a restart; counting stale records
  would make every restart an outage.
- **One liveness rule.** `worker_is_live` in `infrahub_sync/service/orchestration.py` decides
  whether a worker is live: Prefect reports it online and its last heartbeat is within
  `max(3 × heartbeat interval, 30)` seconds. The health report and the run liveness policy
  (`_owner_is_fresh` in `infrahub_sync/service/liveness.py`) both call it.
- **The database check reads only.** It opens its own connection, runs `SELECT 1` under a
  statement timeout, and closes it. The product owner decided that the check reads only; the
  specification and the research record no further reason.
- **The worker container healthcheck reads a file that the worker's own timer writes.** The
  worker writes `<epoch seconds> <interval seconds>` to `$TMPDIR/infrahub-sync-worker-alive`
  every heartbeat interval, on a timer that calls no other service. The Compose healthcheck of
  `sync-worker` fails when the file is older than `max(3 × interval, 30)` seconds. A paused or
  hung process stops the writes; an outage of PostgreSQL or Prefect does not, because one
  PostgreSQL holds both databases in Compose and a check based on the Prefect heartbeat would
  turn the worker container `unhealthy` during every PostgreSQL outage.
- **`/api/health` is a readiness signal, not a liveness probe.** The `sync-api` container
  healthcheck stays on `/api/version`. The components are shared by every Sync API process, so a
  restart triggered by `/api/health` would restart all of them during an outage that a restart
  does not fix.
- **Each check has a time limit, and one round serves many callers.** Each check ends within
  `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT` seconds (default 3). An API process reuses the result of a
  round for one second after it ended, so polling does not multiply the load on the components.
- **The answer holds no identifying text.** No host name, address, credential, error message or
  Sync version reaches the unauthenticated caller. A worker name that is not
  `infrahub-sync-worker-<uuid>` is shown as `other`.

## Consequences

- An operator can name the failed component, or the missing worker, from the answer alone.
- In Compose, a PostgreSQL outage shows `database` and `task_manager_db` as `down` together,
  and after 30 seconds the workers turn `stale`, because their heartbeats cannot be recorded. The
  `sync-worker` container stays `healthy` during that outage.
- A script or page that read `/api/status` must move to `/api/health`. The web interface,
  `SyncClient` and the `dev.smoke` task moved with this change.
- A probe's own timeout must be longer than `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT`; otherwise a slow
  component is reported as a probe timeout instead of as `down` in the body.
- The recovery times are design targets (503 within 60 seconds after a worker stops, within
  5 seconds after a component stops; 200 again within 15 or 30 seconds after recovery). They are
  not measured in this record.

## Alternatives considered

- **Keeping the `/status` shape and adding fields.** Rejected: it keeps `busy`, whose meaning the
  documentation already described wrongly, and it differs from Infrahub.
- **A map of components keyed by name.** Rejected: shorter, but differs from Infrahub's list.
- **Counting every listed worker, or failing on any stale record.** Rejected: a restart leaves
  the previous worker's record, so every restart would answer `503`.
- **A worker healthcheck based on the Prefect heartbeat, on `/api/health`, or on this worker's
  Prefect record.** Rejected: each depends on PostgreSQL, the Sync API or the Prefect server, so
  an outage of a shared component would turn the worker container `unhealthy`.
