# Contract: what a service flow run shows in Prefect

## Last log line of a stage (INFO)

```text
service run <run_id> stage=<stage> outcome=<outcome>; next step: <action>
```

`<action>` is chosen in this order:

1. the next action of the failure that the worker recorded;
2. the next action of the failure that the stage's result carries;
3. `` `infrahub-sync runs show <run_id>` ``.

## Run summary artifact

- Key: `sync-run-<lowercased run_id>-<stage>`. A later flow run of the same stage adds a version to the same key.
- Type: markdown. It is published after the last log line, only inside a Prefect flow run.
- Example for a plan:

```markdown
# Sync run 20261105T1514-f62dfb83

- Stage: plan
- Outcome: planned
- Configuration: 20261105T1514-9d8722ad@1
- Destination branch: netbox-import
- Operations by action: create 1687, delete 1
- Operations by kind: BuiltinTag 26, DcimCircuit 29, ...

## Next steps

- `infrahub-sync runs show 20261105T1514-f62dfb83`
- `infrahub-sync runs plan 20261105T1514-f62dfb83 --detail`
```

- If publishing fails, the worker logs one WARNING line,
  `run summary artifact could not be published (<ErrorType>)`, and the run's outcome is unchanged.

## Refused claim (ERROR, then the flow run fails)

```text
service worker execution claim was refused: this Prefect flow run is not an execution the Sync API submitted
for run <run_id> and is waiting for. A Prefect Retry, a run started by hand from the deployment, and an
execution whose admission expired are refused. Nothing was read or written. A Sync run is not replayed from
Prefect: check it with `infrahub-sync runs show <run_id>`, then start a new run with `infrahub-sync plan` or
`infrahub-sync sync`, or apply a reviewed plan with
`infrahub-sync apply <run_id> --expected-checksum <checksum>`.
```

The message is one line in the log. It is wrapped here for reading.
