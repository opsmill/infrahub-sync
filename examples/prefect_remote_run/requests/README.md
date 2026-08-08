# REST request corpus

Every remote interaction the example uses, against Prefect's own API under
`$PREFECT_API_URL` (default `http://127.0.0.1:4200/api`). The walkthrough in
[`../README.md`](../README.md) runs these in order.

The JSON files in this directory are request **bodies** — pass them with
`curl -d @<file>`. Two placeholders appear in them and must be substituted:

| Placeholder | Replace with |
|---|---|
| `<flow-run-id>` | the `id` returned by a `create_flow_run` call |
| `<deployment-id>` | the `id` returned by the find-deployment call (used in the URL, not in a body) |

## The interactions

| # | Method and endpoint | Body file | Expected response |
|---|---|---|---|
| 1 | `GET /api/deployments/name/infrahub-sync/run` | none | `200` with `id`, `status: "READY"`, `enforce_parameter_schema: true`, and `parameter_openapi_schema.properties.operation.enum == ["plan", "sync"]` |
| 2 | `POST /api/deployments/<deployment-id>/create_flow_run` | [`create-plan-flow-run.json`](create-plan-flow-run.json) | `201` with the flow-run `id` returned **synchronously**, `state_type: "SCHEDULED"` |
| 3 | `GET /api/flow_runs/<flow-run-id>` | none | `state.type` progresses `SCHEDULED` → `PENDING` → `RUNNING` → `COMPLETED` (or `FAILED`, with the sanitized cause in `state.message`) |
| 4 | `POST /api/logs/filter` | [`filter-flow-run-logs.json`](filter-flow-run-logs.json) | array of log records for the run, including the bridged `infrahub_sync` lifecycle lines and the run summary line |
| 5 | `POST /api/deployments/<deployment-id>/create_flow_run` | [`create-sync-flow-run-confirmed.json`](create-sync-flow-run-confirmed.json) | `201`; the run writes to the destination because `confirm_writes` is `true` |
| 6 | `POST /api/deployments/<deployment-id>/create_flow_run` | [`create-invalid-operation-flow-run.json`](create-invalid-operation-flow-run.json) | `409` with `'apply' is not one of ['plan', 'sync']` — **no flow run is created** |

Reading the result is interaction 4: the flow logs one summary line in a fixed
`key=value` format, and that line is the supported way to read a run's outcome
remotely.

```text
run <run-id> finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=<path>
```

## Authentication

A local `prefect server start` listens on `127.0.0.1` and requires no
authentication, so the curl commands in the walkthrough send no auth header. If
your Prefect API sits behind an authenticating proxy, add the header — with your
own token, never a value committed to a file:

```bash
curl -H "Authorization: Bearer <your-api-token>" "$PREFECT_API_URL/deployments/name/infrahub-sync/run"
```

Infrahub credentials never travel through these requests. They are read from the
runner process's environment (`INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`) and never
appear in a flow parameter, a request body, a result, or a log line.
