# Quickstart: check the feature on the development stack

## Prerequisites

The stack from the 2026-11-05 test must be running:

- Infrahub from Infrahub's own Compose file on `http://localhost:8000` (project `infrahub`);
- the local NetBox with the `demo` dataset;
- the Marketplace schema, loaded into Infrahub;
- the registered configuration `20261105T1514-9d8722ad`;
- the `netbox-import` branch.

See [the development stack guide](../../../docs/docs/development-stack.mdx#run-a-first-sync).

## 1. Rebuild and restart the Sync stack

```bash
export INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN=nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000
export INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN=06438eb2-8019-4776-878c-0941b1f1d1ec
export INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK=infrahub_default
uv run invoke dev.build
uv run invoke dev.start
```

## 2. Run a plan and read the link

```bash
export INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 INFRAHUB_SYNC_API_TOKEN=infrahub-sync-dev-token
uv run infrahub-sync plan --config-id 20261105T1514-9d8722ad --version 1 --branch netbox-import --reason "016 check"
uv run infrahub-sync runs show <run-id>
```

Expected: `runs show` prints `prefect_flow_run_url: http://127.0.0.1:4230/runs/flow-run/<flow-run-id>`. Opening it in
a browser shows that flow run (see [contracts/cli-output.md](./contracts/cli-output.md)).

## 3. Read the artifact and the last log line in Prefect

In the Prefect UI, open the flow run, then its Artifacts tab. Expected: one markdown artifact with the key
`sync-run-<lowercased run-id>-plan` (see [contracts/prefect-surfaces.md](./contracts/prefect-surfaces.md)).

The worker's last line in the flow run's Logs tab reads
`service run <run-id> stage=plan outcome=planned; next step: ` followed by `` `infrahub-sync runs show <run-id>` ``.
Prefect's own `Finished in state ...` line follows it.

The same check can be run without a browser, through Prefect's API:
`POST http://127.0.0.1:4230/api/artifacts/filter`.

## 4. Check the CLI hint

```bash
env -u INFRAHUB_SYNC_API_URL uv run infrahub-sync runs list; echo "exit $?"
INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 env -u INFRAHUB_SYNC_API_TOKEN uv run infrahub-sync runs list
```

Expected: the first command prints `error: client-input`, `argument: service_url` and a `hint:` line that names
`INFRAHUB_SYNC_API_URL` and `--api-url`, with the exit code that the command printed before this change. The second
command prints a `hint:` line that names `INFRAHUB_SYNC_API_TOKEN`.

## 5. Optional: a refused Prefect retry

In the Prefect UI, select Retry on a completed service flow run. Expected: the new flow run fails, and its log holds
the ERROR line from [contracts/prefect-surfaces.md](./contracts/prefect-surfaces.md).
