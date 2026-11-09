# Contract: run response orchestration entries

Applies to `GET /runs/{run_id}`, and to every response that embeds a run's `orchestration` list. No route is
added.

```json
{
  "orchestration": [
    {
      "flow_run_id": "ba01ef74-f225-4568-aae4-b9131985d16d",
      "purpose": "plan",
      "attempt": 1,
      "state": "completed",
      "terminal_state": "completed",
      "terminal_outcome": "succeeded",
      "prefect_flow_run_url": "http://127.0.0.1:4230/runs/flow-run/ba01ef74-f225-4568-aae4-b9131985d16d"
    }
  ]
}
```

- `prefect_flow_run_url` is `null` when `INFRAHUB_SYNC_PREFECT_UI_URL` is not set.
- `state` equals `terminal_state` whenever `terminal_state` is not `null`.
- The other fields are unchanged and omitted here.

## Setting

| Variable | Read by | Valid value | Invalid value |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_PREFECT_UI_URL` | Sync API at start-up | Empty or unset, which turns the link off, or an absolute `http`/`https` URL with a host and no user info, query or fragment. A path prefix is kept. | The Sync API refuses to start with `INFRAHUB_SYNC_PREFECT_UI_URL must be an absolute http or https URL without credentials, query or fragment`. The value is not printed. |
