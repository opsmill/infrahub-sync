# Contract: CLI output

## Missing or invalid Sync API address

```text
error: client-input
argument: service_url
hint: set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API
```

## Missing Sync API token

```text
error: client-input
argument: token
hint: set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token
```

- Both errors go to standard error, with the exit code that `client-input` uses today.
- Every other `client-input` argument prints no `hint` line.

## `infrahub-sync runs show <run_id>`

The selected orchestration entry gains one line after `flow_run_id`:

```text
flow_run_id: ba01ef74-f225-4568-aae4-b9131985d16d
prefect_flow_run_url: http://127.0.0.1:4230/runs/flow-run/ba01ef74-f225-4568-aae4-b9131985d16d
```

When the Sync API has no Prefect UI URL, or when there is no selected entry, the line reads
`prefect_flow_run_url: <none>`.
