# Data Model: Health of the Sync API and each worker

No table, column or stored object is added. The entities below exist only in memory and in the
HTTP response. The JSON form is in [contracts/health-api.md](./contracts/health-api.md).

## HealthReport

The result of one round of checks. Replaces `ServiceStatusResource`.

| Field | Type | Rule |
| --- | --- | --- |
| `status` | `healthy` \| `unhealthy` | `healthy` only when every dependency is `up`, `workers.state` is `known` and `workers.live_workers ≥ workers.minimum` (research R-6). |
| `dependencies` | list of `DependencyHealth` | Exactly four entries, in the order `database`, `object_store`, `task_manager`, `task_manager_db`. |
| `workers` | `WorkersHealth` | Always present. |
| `timestamp` | date-time with offset | When the round of checks started. |

HTTP status: 200 when `status` is `healthy`, 503 otherwise. Header `Cache-Control: no-store`.

## DependencyHealth

| Field | Type | Rule |
| --- | --- | --- |
| `name` | `database` \| `object_store` \| `task_manager` \| `task_manager_db` | |
| `status` | `up` \| `down` | `down` when the check raised or exceeded the time limit. |
| `error` | `none` \| `timeout` \| `connection_refused` \| `connection_closed` \| `name_resolution_failed` \| `unavailable` \| `unknown_error` | `none` exactly when `status` is `up`. Chosen by `classify_error` (research R-3). |

## WorkersHealth

| Field | Type | Rule |
| --- | --- | --- |
| `state` | `known` \| `unknown` | `unknown` when the work pool could not be read within the time limit. |
| `minimum` | integer ≥ 1 | The value of `INFRAHUB_SYNC_HEALTH_MIN_WORKERS`. |
| `live_workers` | integer ≥ 0, or null | Null exactly when `state` is `unknown`. Number of items with `state` `live`. |
| `queue_depth` | integer ≥ 0, or null | Null exactly when `state` is `unknown`. Runs due now that no worker has claimed. |
| `items` | list of `WorkerHealth` | Empty when `state` is `unknown`. Sorted by `last_heartbeat`, newest first, then by `name`. |

## WorkerHealth

| Field | Type | Rule |
| --- | --- | --- |
| `name` | string | The Prefect worker name when it is `infrahub-sync-service-<uuid>`, as the Sync worker entrypoint registers; `other` for any other name (spec FR-013). |
| `state` | `live` \| `stale` | `live` when `worker_is_live(worker, timestamp)` holds (research R-5). |
| `last_heartbeat` | date-time with offset | Only workers with a heartbeat within 24 hours of `timestamp` are listed. |

## PoolWorker (internal, changed)

`infrahub_sync/service/orchestration.py`. Gains `name: str | None = None`. `worker_is_live`
decides liveness from `status`, `last_heartbeat` and `heartbeat_interval_seconds`, and is the only
copy of the rule.

## Settings

| Variable | Values | Default | Read by |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT` | whole seconds, 1–60 | 3 | Sync API |
| `INFRAHUB_SYNC_HEALTH_MIN_WORKERS` | whole number from 1 to 999999999 | 1 | Sync API |
| `PREFECT_WORKER_HEARTBEAT_SECONDS` | existing | 10 in Compose | worker healthcheck (threshold) |

A value outside its range stops the Sync API at start with a message that names the variable.

## Worker alive file (internal)

`$TMPDIR/infrahub-sync-worker-alive` in the worker container (`TMPDIR=/tmp/infrahub-sync` in the
image, a tmpfs in Compose): `<epoch seconds> <interval seconds>`, written by the worker's own timer
every heartbeat interval, independent of Prefect. Read only by `WORKER_HEALTHCHECK_COMMAND`, which
passes when `now - time <= max(3 × interval, 30)`. Lost when the container stops.
