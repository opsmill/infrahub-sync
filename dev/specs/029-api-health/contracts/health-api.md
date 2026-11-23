# Contract: `GET /api/health`

Served by the Sync API under `/api`. No authentication. Replaces `GET /api/status`, which answers
404 after this change.

## Request

```http
GET /api/health HTTP/1.1
```

No parameters, no body, no credential. `GET /api` and `GET /api/` redirect here with 307.

## Responses

| Status | When | Body |
| --- | --- | --- |
| 200 | `status` is `healthy` | `HealthReport` |
| 503 | `status` is `unhealthy` | `HealthReport` |

Both carry `Content-Type: application/json` and `Cache-Control: no-store`. The answer never
contains a host name, an IP address, a credential name or value, an error message, or the Sync
version. A failure inside the endpoint itself is answered with the existing 503
`service-unavailable` error envelope.

### 200, healthy

```json
{
  "status": "healthy",
  "dependencies": [
    {"name": "database", "status": "up", "error": "none"},
    {"name": "object_store", "status": "up", "error": "none"},
    {"name": "task_manager", "status": "up", "error": "none"},
    {"name": "task_manager_db", "status": "up", "error": "none"}
  ],
  "workers": {
    "state": "known",
    "minimum": 1,
    "live_workers": 1,
    "queue_depth": 0,
    "items": [
      {
        "name": "infrahub-sync-worker-3f0c2a9e-6a1b-4c55-9d3e-2b7c1e0f4a11",
        "state": "live",
        "last_heartbeat": "2026-11-23T10:15:02Z"
      },
      {
        "name": "infrahub-sync-worker-8b1d7e40-2c9f-4e0a-a6b3-5f2e9c7d1b08",
        "state": "stale",
        "last_heartbeat": "2026-11-23T09:41:57Z"
      }
    ]
  },
  "timestamp": "2026-11-23T10:15:09Z"
}
```

The second worker is the record of a worker that ran before a restart. It is listed and does not
change the status.

### 503, PostgreSQL stopped (Compose, first 30 seconds)

In the Compose deployment the Prefect database runs in the same PostgreSQL, so `task_manager_db`
is down too. After the liveness threshold (30 seconds by default) the workers also turn `stale`,
because Prefect can no longer record their heartbeats; the reading of the pool may then fail and
report `unknown`.

```json
{
  "status": "unhealthy",
  "dependencies": [
    {"name": "database", "status": "down", "error": "name_resolution_failed"},
    {"name": "object_store", "status": "up", "error": "none"},
    {"name": "task_manager", "status": "up", "error": "none"},
    {"name": "task_manager_db", "status": "down", "error": "unavailable"}
  ],
  "workers": {"state": "known", "minimum": 1, "live_workers": 1, "queue_depth": 0, "items": ["…"]},
  "timestamp": "2026-11-23T10:16:40Z"
}
```

### 503, Prefect server stopped

```json
{
  "status": "unhealthy",
  "dependencies": [
    {"name": "database", "status": "up", "error": "none"},
    {"name": "object_store", "status": "up", "error": "none"},
    {"name": "task_manager", "status": "down", "error": "name_resolution_failed"},
    {"name": "task_manager_db", "status": "down", "error": "name_resolution_failed"}
  ],
  "workers": {"state": "unknown", "minimum": 1, "live_workers": null, "queue_depth": null, "items": []},
  "timestamp": "2026-11-23T10:18:03Z"
}
```

## Field reference

See [data-model.md](../data-model.md) for each field's rule.

## Error categories

| Category | Meaning |
| --- | --- |
| `none` | The component is up. |
| `timeout` | No answer within `INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT` seconds. A paused container gives this. |
| `connection_refused` | The address was reached but nothing listens on the port. |
| `connection_closed` | The connection was opened and then closed by the other side. |
| `name_resolution_failed` | The host name does not resolve. A stopped Compose container gives this. |
| `unavailable` | The component answered but cannot serve: a Prefect 503, a missing bucket, refused object-store credentials, a refused database login. |
| `unknown_error` | Any other failure. |

## Python client

```python
from infrahub_sync.client import SyncClient

report = SyncClient(...).get_health()  # HealthResource for a 200 or a 503 answer
report.status            # "healthy" | "unhealthy"
report.workers.live_workers
```

A 503 that carries the error envelope instead of a report raises the client's existing error for
an error envelope. Any other status code, or a body that does not match, raises `ProtocolError`,
as `get_status()` did. `get_status()`, `ServiceStatusResource` and `WorkerStatusResource` are removed.

## Worker container healthcheck

```yaml
healthcheck:
  test: ["CMD", "python", "-I", "-c", "<WORKER_HEALTHCHECK_COMMAND>"]
  interval: 10s
  timeout: 5s
  retries: 3
  start_period: 60s
```

Exit 0 when the worker's own timer wrote `$TMPDIR/infrahub-sync-worker-alive` within
`max(3 × interval, 30)` seconds, the interval being the one written in the file; exit 1 otherwise
(missing, malformed or old file). The check depends on no other service: an outage of PostgreSQL
or Prefect leaves the worker container `healthy`. Prints one line naming the reason on failure,
with no credential.
