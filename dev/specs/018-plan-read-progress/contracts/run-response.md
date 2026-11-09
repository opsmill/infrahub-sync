# Contract: `reads` in each orchestration entry

Routes: every route that returns a run resource, including `GET /runs/{run_id}` and `GET /runs`. No route is added.

## Shape

```json
{
  "flow_run_id": "6f1c...",
  "purpose": "plan",
  "state": "Running",
  "terminal_state": null,
  "prefect_flow_run_url": null,
  "reads": {
    "source": {
      "state": "succeeded",
      "started_at": "2026-11-09T10:00:01Z",
      "ended_at": "2026-11-09T10:00:13Z",
      "read_mode": "full",
      "nodes_by_kind": {"DcimDevice": 3, "LocationSite": 2},
      "error_class": null
    },
    "destination": {
      "state": "reading",
      "started_at": "2026-11-09T10:00:01Z",
      "ended_at": null,
      "read_mode": null,
      "nodes_by_kind": {},
      "error_class": null
    }
  }
}
```

Other fields of the entry are unchanged and are left out above.

## Rules

1. `reads` is `null` for an execution recorded before this feature, and for `verify` and `apply` executions.
2. For a `plan` or `sync` execution, both sides are present from the moment the worker starts the stage, in state
   `waiting`.
3. When `terminal_state` is set:
   - a side stored as `waiting` is returned as `not_started`;
   - a side stored as `reading` is returned as `interrupted`;
   - `ended_at` is `null` for both.
4. `nodes_by_kind` is present only for `succeeded`, and `error_class` only for `failed`. They are `{}` and `null`
   otherwise.
5. No field holds an exception message, an address, a secret or configuration content.
6. The change is additive. Existing fields keep their meaning and order.
