# Contract: `GET /runs/{run_id}/logs`

Authentication: a valid bearer token (FR-008).

## Query parameters

| Name | Type | Meaning |
| --- | --- | --- |
| `stage` | `plan`, `verify`, `apply` or `sync` | Only the lines of this stage's flow runs. |
| `limit` | integer | Page size; default 200, maximum 1000. |
| `offset` | integer, default 0 | Number of Sync lines to skip (research R9). |

## Response 200

```json
{
  "run_id": "r-...",
  "items": [
    {
      "stage": "plan",
      "attempt": 1,
      "timestamp": "2026-11-06T10:15:01Z",
      "level": "ERROR",
      "source": "infrahub_sync.service.flow",
      "message": "Branch `demo-test` does not exist in Infrahub. Next step: create it, or start the run with `--branch <existing branch>`."
    }
  ],
  "limit": 200,
  "offset": 0,
  "has_more": false,
  "run_phase": "plan-failed"
}
```

`run_phase` lets a poller stop when the run is terminal without a second request. A run with no flow run yet
returns `items: []`.

## Errors

| Status | `error` | When |
| --- | --- | --- |
| 404 | `run-not-found` (existing) | The run does not exist. |
| 410 | `log-expired` | Prefect no longer holds a flow run that the run record names (FR-010). |
| 503 | `log-unavailable` | Prefect cannot be reached (FR-013). |

No error message names Prefect or its address (FR-006). The 410 message is "the log of this run has expired"; the
503 message is "the log of this run cannot be read now; retry later".
