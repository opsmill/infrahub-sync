# Contract: read state in `infrahub-sync runs show` and the run log

## `runs show`

The read fields come from the latest orchestration entry whose `purpose` is `plan` or `sync`, even when `verify`
or `apply` entries follow it. The other fields keep reading the latest entry. When that entry has `reads`, two
fields follow `prefect_flow_run_url`:

```text
source_read: state=succeeded mode=full started_at=2026-11-09T10:00:01+00:00 ended_at=2026-11-09T10:00:13+00:00 nodes=DcimDevice:3,LocationSite:2
destination_read: state=failed started_at=2026-11-09T10:00:01+00:00 ended_at=2026-11-09T10:00:04+00:00 error=ConnectionError
```

- Keys appear in this order: `state`, `mode`, `started_at`, `ended_at`, `nodes`, `error`. A key with no value is
  left out.
- A side that is `null` prints `<none>`.
- When `reads` is `null`, neither field is printed. Every other field and the exit code are unchanged.

## Run log

When a side's read ends, Potenda logs one INFO line on the `infrahub_sync.potenda` logger:

```text
Load: source read succeeded (full): 5 nodes; DcimDevice 3, LocationSite 2
Load: destination read failed (ConnectionError)
```

The line holds no exception text, no address and no configuration content. In a service run, the log bridge
forwards it to the run's log, where `GET /runs/{run_id}/logs` and `infrahub-sync runs logs` return it.
