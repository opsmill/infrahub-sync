# Run Infrahub Sync remotely with Prefect (developer preview)

This example starts a Prefect server, serves the packaged `infrahub-sync` flow, and
invokes a **plan** (read-only) and then a **confirmed sync** (writes) over Prefect's
REST API. It reuses the `examples/custom_adapter` sync project as its source: five
devices in a small JSON file, synchronized into Infrahub.

Everything you need is in this directory and the commands below. You do not need to
read any package source to follow it.

> **Trusted development environment only.** The default self-hosted Prefect server has
> no authentication. Bind it to localhost and never expose it to the public internet.
> This is a developer preview: run it on a machine and against an Infrahub instance you
> are willing to experiment with.

## What you end up with

- A Prefect server on `http://127.0.0.1:4200` with its built-in UI, using its default
  embedded SQLite database — no Postgres, no work pool, no worker process.
- One served deployment, `infrahub-sync/run`, with four parameters: `sync_name`,
  `operation` (`plan` or `sync`), `confirm_writes`, `branch`.
- A remote plan run whose log ends with
  `summary=create:5,update:0,delete:0` — five devices to create.

## Prerequisites

1. **Python 3.10–3.13.** The preview was exercised on 3.12.
2. **This repository checked out**, on the branch carrying the preview.
3. **Working directory: the repository root.** Every command below — and the serve
   process in particular — starts from the root of this repository. The sync project's
   `config.yml` uses `./`-relative paths, and they are resolved against the working
   directory of the process running the sync. Start the serve process somewhere else
   and the run still "succeeds", with an empty plan instead of five creates.
4. **A reachable Infrahub instance** with the `InfraDevice` node (attributes `name` and
   `type`) in its schema. [`schemas/infra_device.yml`](schemas/infra_device.yml) is that
   schema; loading it is a step below.
5. **An empty destination: zero `InfraDevice` objects.** The demonstration's expected
   output is five creates, which only happens if the destination starts empty. Verify
   and reset below.
6. **Port 4200 free** for the Prefect server. If something already listens there,
   `prefect server start` exits with an "address already in use" error instead of
   printing its banner — stop the other process (or point `PREFECT_API_URL` at it and
   skip starting a second server).
7. **`curl` and `jq`** for the REST calls, and the Infrahub credentials described under
   [Configure the runner](#3-configure-the-runner).

## 1. Install the optional Prefect integration

The Prefect integration is an optional extra. Install it **from this repository
checkout** — the preview is not published to PyPI, so `pip install
'infrahub-sync[prefect]'` would install a released version that does not contain it.
Use the local path (this is deliberate; please do not "correct" it to the PyPI form):

```bash
# from the repository root
uv sync --extra dev --extra prefect
```

Keep `--extra dev` in that command. `uv sync --extra prefect` alone *removes* the
development tooling (pytest, ruff, invoke, ty), because `uv sync` makes the environment
match exactly the extras you name.

Or, with plain pip in a virtual environment of your own:

```bash
# from the repository root
pip install -e '.[prefect]'
```

Check what you got — the extra pins exactly one version:

```bash
uv run python -c "import prefect; print(prefect.__version__)"    # 3.8.1
```

Installing the extra changes nothing about ordinary CLI use: `infrahub-sync` never
imports or contacts Prefect on its own.

## 2. Load the destination schema

```bash
# from the repository root
export INFRAHUB_ADDRESS="<your-infrahub-address>"
export INFRAHUB_API_TOKEN="<your-api-token>"

uv run infrahubctl schema load examples/prefect_remote_run/schemas/infra_device.yml --branch main
```

Replace the two placeholders with your own values (for a local Infrahub the address is
typically `http://localhost:8000`). If your shell already exports them, skip the two
`export` lines. Loading the same schema twice is harmless.

### Verify the destination is empty

```bash
curl -s -H "X-INFRAHUB-KEY: $INFRAHUB_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "{ InfraDevice { count } }"}' "$INFRAHUB_ADDRESS/graphql/main" | jq -c .
```

Expected — this is the documented starting state:

```json
{"data":{"InfraDevice":{"count":0}}}
```

If the count is not zero (for instance because you already ran the confirmed sync
below), reset the destination by deleting the objects. On a disposable instance:

```bash
# list the ids
curl -s -H "X-INFRAHUB-KEY: $INFRAHUB_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "{ InfraDevice { edges { node { id name { value } } } } }"}' \
  "$INFRAHUB_ADDRESS/graphql/main" | jq -r '.data.InfraDevice.edges[].node.id'

# delete one, repeating for each id
curl -s -H "X-INFRAHUB-KEY: $INFRAHUB_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "mutation { InfraDeviceDelete(data: {id: \"<object-id>\"}) { ok } }"}' \
  "$INFRAHUB_ADDRESS/graphql/main"
```

## 3. Configure the runner

This example uses **three terminals**, and environment variables do not travel between
them. Two of the commands below run forever (the server and the serve process), so you
cannot reuse their terminal for anything else. Export what each terminal needs, in that
terminal, before running its command:

| Terminal | What runs there | Must have exported |
|---|---|---|
| **A** | `prefect server start` (blocks) | `PREFECT_API_URL` |
| **B** | `python -m infrahub_sync.orchestration.serve` (blocks) | **all four** — the API URL, the config directory, and **both Infrahub credentials**; see the block below |
| **C** | the `curl` calls | `PREFECT_API_URL` |

**Terminal B is the one that needs the Infrahub credentials.** The flow executes inside
the serve process and inherits *its* environment, so credentials missing there produce a
run that reaches `FAILED` with:

```text
RunExecutionError: Failed to initialize the Sync Instance: Error initializing
InfrahubAdapter: Both url and token must be specified! Set the runner-environment
variables INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN.
```

That is the single most common way to get stuck here. Exporting the credentials in
Terminal C does nothing — remote callers cannot supply them, by design: flow parameters
carry no paths and no secrets.

This is the full block for **Terminal B**:

```bash
# from the repository root, in Terminal B
export PREFECT_API_URL="http://127.0.0.1:4200/api"
export INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"
export INFRAHUB_ADDRESS="<your-infrahub-address>"
export INFRAHUB_API_TOKEN="<your-api-token>"

# verify all four are set, without printing the token
printenv PREFECT_API_URL >/dev/null || echo "MISSING PREFECT_API_URL"
printenv INFRAHUB_SYNC_CONFIG_DIRECTORY >/dev/null || echo "MISSING config directory"
printenv INFRAHUB_ADDRESS >/dev/null || echo "MISSING INFRAHUB_ADDRESS"
printenv INFRAHUB_API_TOKEN >/dev/null || echo "MISSING the API token"
```

Each line prints nothing when the variable is set and a `MISSING …` line when it is not.
Check that block before starting serve — a missing credential does not surface until a
run fails, one step later.

Terminals A and C need only `export PREFECT_API_URL="http://127.0.0.1:4200/api"`.

`INFRAHUB_SYNC_CONFIG_DIRECTORY` is the allow-list for remote runs: point it only at a
directory containing the sync configurations you intend to expose remotely. Scoping it
to `examples/custom_adapter` exposes exactly the one `custom-example` configuration
rather than every example in `examples/`.

Set both credential variables **before** starting the serve process — it passes its own
environment to each run. Keep their real values in your shell or secret manager only:
never in a file, a flow parameter, or a request body.

## 4. Start the Prefect server

**Terminal A** — this command does not return. It runs the server in the foreground until
you `Ctrl-C` it, so this terminal is occupied from here on:

```bash
# from the repository root, in Terminal A
export PREFECT_API_URL="http://127.0.0.1:4200/api"
uv run prefect server start
```

It prints a banner ending with `Check out the dashboard at http://127.0.0.1:4200`.

To check that it is answering, open **Terminal C** — a fresh terminal has none of your
exports, so set the API URL there first:

```bash
# in Terminal C
export PREFECT_API_URL="http://127.0.0.1:4200/api"
curl -s "$PREFECT_API_URL/health"      # true
```

If `curl` prints nothing at all rather than `true`, `PREFECT_API_URL` is empty in this
terminal: `curl -s` is silent about a malformed URL, so an unset variable looks like a
dead server. Guard every command in this terminal with the variable check shown in step 6.

## 5. Serve the flow

**Terminal B**, **from the repository root** — this is the working-directory requirement
from the prerequisites, and it is the one step where getting it wrong produces a
plausible-looking wrong answer (an empty plan) rather than an error. This command also
does not return; it serves until interrupted.

Export step 3's **full** Terminal B block here first — all four variables, including both
Infrahub credentials. Repeating it so this step is self-contained:

```bash
# from the repository root, in Terminal B
export PREFECT_API_URL="http://127.0.0.1:4200/api"
export INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"
export INFRAHUB_ADDRESS="<your-infrahub-address>"
export INFRAHUB_API_TOKEN="<your-api-token>"

printenv PREFECT_API_URL >/dev/null || echo "MISSING PREFECT_API_URL"
printenv INFRAHUB_SYNC_CONFIG_DIRECTORY >/dev/null || echo "MISSING config directory"
printenv INFRAHUB_ADDRESS >/dev/null || echo "MISSING INFRAHUB_ADDRESS"
printenv INFRAHUB_API_TOKEN >/dev/null || echo "MISSING the API token"

uv run python -m infrahub_sync.orchestration.serve
```

Omitting the two credentials here is the failure described in step 3: the serve process
starts happily, the deployment registers, and the first run fails on adapter
initialization. The serve process does not validate them at startup — only
`INFRAHUB_SYNC_CONFIG_DIRECTORY` is checked that early.

Expected:

```text
Your flow 'infrahub-sync' is being served and polling for scheduled runs!
```

The process serves until you interrupt it. If `INFRAHUB_SYNC_CONFIG_DIRECTORY` is unset
or is not a directory, it refuses to start with a single error line naming the variable,
before registering anything.

Confirm the deployment is ready in **Terminal C** — the third terminal, which needs its
own `PREFECT_API_URL` and nothing else:

```bash
# in Terminal C
export PREFECT_API_URL="http://127.0.0.1:4200/api"
printenv PREFECT_API_URL >/dev/null || echo "set PREFECT_API_URL first"

curl -s "$PREFECT_API_URL/deployments/name/infrahub-sync/run" \
  | jq '{id, name, status, enforce_parameter_schema,
         operations: .parameter_openapi_schema.properties.operation.enum}'
```

Expected:

```json
{
  "id": "<deployment-id>",
  "name": "run",
  "status": "READY",
  "enforce_parameter_schema": true,
  "operations": ["plan", "sync"]
}
```

## 6. Run a remote plan

The request bodies live in [`requests/`](requests/README.md), which lists every
endpoint used here.

All of these run in **Terminal C**. The guard on the first line turns an unset
`PREFECT_API_URL` into a named error instead of a silent empty result:

```bash
# in Terminal C
printenv PREFECT_API_URL >/dev/null || echo "set PREFECT_API_URL first"

DEP_ID=$(curl -s "$PREFECT_API_URL/deployments/name/infrahub-sync/run" | jq -r .id)
[ "$DEP_ID" != "null" ] && [ -n "$DEP_ID" ] || { echo "no deployment — is Terminal B serving?"; }

RUN_ID=$(curl -s -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d @examples/prefect_remote_run/requests/create-plan-flow-run.json | jq -r .id)

echo "flow run: $RUN_ID"
```

The flow-run id comes back synchronously. Watch it reach a terminal state (a few
seconds; the serve process polls for scheduled runs):

```bash
curl -s "$PREFECT_API_URL/flow_runs/$RUN_ID" | jq -r '.state.type + " " + (.state.message // "")'
```

Expected: `SCHEDULED`, then `PENDING` while the serve process picks the run up, then
`RUNNING`, then `COMPLETED`. `PENDING` is normal, not a problem.

### Read the result

The run's outcome is reported as one summary line in the run log, in a fixed
`key=value` format. Read the log:

```bash
sed "s/<flow-run-id>/$RUN_ID/" examples/prefect_remote_run/requests/filter-flow-run-logs.json \
  | curl -s -X POST "$PREFECT_API_URL/logs/filter" -H "Content-Type: application/json" -d @- \
  | jq -r '.[] | .message'
```

**Checkpoint — this is what a correct run looks like.** The log contains the source
adapter's own narration and the summary line:

```text
infrahub_sync.potenda | Load: Importing data from MockDB
infrahub_sync.examples.custom_adapter | Loading 5 devices nodes
infrahub_sync.examples.custom_adapter | MockDB: Loading 5/5 InfraDevice
infrahub_sync.adapters.infrahub | Infrahub: Loading all 0 InfraDevice
infrahub_sync.potenda | diff: 5/5 models processed
infrahub_sync.execution |
InfraDevice
  InfraDevice: core01 MISSING in Infrahub
  InfraDevice: core02 MISSING in Infrahub
  InfraDevice: core03 MISSING in Infrahub
  InfraDevice: edge01 MISSING in Infrahub
  InfraDevice: edge02 MISSING in Infrahub
run <run-id> finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=<path>
```

`summary=create:5,update:0,delete:0` — **five** creates. A `COMPLETED` run reporting
`summary=create:0,update:0,delete:0` is not a success, it is a symptom: either the
destination already holds the five devices (step 2's verify), or the serve process was
not started from the repository root, so the source fixture resolved to nothing. In that
second case the log also carries
`MockDB database file not found: ... no records will be loaded`.

The same lifecycle is visible in the UI at
`http://127.0.0.1:4200/runs/flow-run/<flow-run-id>`, under the run's Logs tab.

`artifact=` points at the run's artifact directory (`run.json`, `plan.parquet`, and the
plan cache) **on the machine running the serve process** — these files are local to the
runner and are not retrievable through the Prefect API.

The run directory is named after the run id on the summary line — one directory per run,
so take the id from the log rather than assuming the directory list holds only this run:

```bash
# on the runner host, from the repository root
ls .infrahub-sync-cache/custom-example/<run-id>/     # run.json, plan.parquet, A/, B/
```

## 7. Run a confirmed sync

A `sync` writes to Infrahub, so it refuses to run unless the caller passes
`confirm_writes: true`. Try it without first, to see the refusal:

```bash
curl -s -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"sync_name": "custom-example", "operation": "sync"}}' | jq -r .id
# the run reaches FAILED, with state.message explaining confirm_writes=true is required,
# before either adapter loads; the destination is untouched
```

Then the confirmed form:

```bash
SYNC_RUN_ID=$(curl -s -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d @examples/prefect_remote_run/requests/create-sync-flow-run-confirmed.json | jq -r .id)

curl -s "$PREFECT_API_URL/flow_runs/$SYNC_RUN_ID" | jq -r .state.type    # → COMPLETED
```

Its summary line reads `status=applied changed=True summary=create:5,update:0,delete:0`,
and the five devices now exist:

```bash
curl -s -H "X-INFRAHUB-KEY: $INFRAHUB_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "{ InfraDevice { count edges { node { name { value } } } } }"}' \
  "$INFRAHUB_ADDRESS/graphql/main" | jq -c '.data.InfraDevice'
```

Submit the plan from step 6 once more and it converges: `status=no-change`,
`changed=False`, `summary=create:0,update:0,delete:0`. Run the plan, the sync, and the
follow-up plan strictly one at a time — the preview makes no guarantees about
overlapping runs of the same configuration.

An `operation` outside `plan` and `sync` is rejected when the run is created:

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d @examples/prefect_remote_run/requests/create-invalid-operation-flow-run.json
# HTTP 409, "'apply' is not one of ['plan', 'sync']" — no flow run is created at all
```

## 8. Stopping and cleaning up

1. **Stop the serve process** (Terminal B): `Ctrl-C`, or
   `pkill -f "infrahub_sync.orchestration.serve"`.
2. **Stop the server** (Terminal A): `Ctrl-C`, or `pkill -f "prefect server start"`.
3. **Confirm the port is released**: `lsof -nP -iTCP:4200 -sTCP:LISTEN` prints nothing.

What was left on disk:

- **Prefect state** — flow runs, deployments, and logs — lives in the server's SQLite
  database under `PREFECT_HOME` (`~/.prefect/prefect.db` by default; set `PREFECT_HOME`
  before starting the server to keep this example's state somewhere disposable).
  Persisted run results go to a separate location that does *not* follow `PREFECT_HOME`
  — set `PREFECT_LOCAL_STORAGE_PATH` too if you want both under one directory.
- **Sync artifacts** — one directory per run under `.infrahub-sync-cache/custom-example/`
  in the working directory the serve process ran from (the repository root). Safe to
  delete; the next run creates a fresh one.
- **Infrahub objects** — the five devices created in step 7 stay until you delete them.
  Use the reset recipe in step 2.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Run reaches `FAILED` with `Error initializing InfrahubAdapter: Both url and token must be specified!` | `INFRAHUB_ADDRESS` / `INFRAHUB_API_TOKEN` were not exported in **Terminal B**. Runs inherit the serve process's environment; exporting them in Terminal C has no effect. | `Ctrl-C` the serve process, export step 3's full Terminal B block, restart it. The server in Terminal A can keep running. |
| `curl` prints nothing where `true` or JSON was expected | `PREFECT_API_URL` is unset in this terminal — `curl -s` is silent about a malformed URL, so it looks like a dead server. | `export PREFECT_API_URL="http://127.0.0.1:4200/api"`, and use the `printenv PREFECT_API_URL` guard shown in step 6. |
| `COMPLETED` run, but `summary=create:0,update:0,delete:0` | Either the destination already holds the five devices, or the serve process was not started from the repository root, so the fixture's `./`-relative paths resolved to nothing. | Check the destination count (step 2). Look for `MockDB database file not found` in the run log — that confirms the working directory. Restart serve from the repository root. |
| Serve process exits immediately naming `INFRAHUB_SYNC_CONFIG_DIRECTORY` | The variable is unset or is not a directory. This is the one thing serve validates at startup. | Export it to an existing directory holding the sync configurations you want exposed. |
| `prefect server start` exits with "address already in use" | Something already listens on 4200. | `lsof -nP -iTCP:4200 -sTCP:LISTEN` to find it. Stop it, or point `PREFECT_API_URL` at the existing server and skip step 4. |
| `HTTP 409` from `create_flow_run` | The `operation` value is not `plan` or `sync`. This is the parameter schema rejecting the request — no flow run is created. | Expected behaviour; see the invalid-operation example in step 7. |

## Reference

- Reference page: [Prefect remote run](../../docs/docs/reference/prefect-remote-run.mdx)
- The sync project this example runs: [`examples/custom_adapter`](../custom_adapter)
- The REST corpus: [`requests/README.md`](requests/README.md)
