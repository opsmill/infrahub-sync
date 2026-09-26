# Preview environment

One command from a fresh clone to a complete, testable Infrahub Sync v3 stack:
a disposable Infrahub instance, a dedicated Prefect server, the Sync HTTP API,
and a Prefect worker running the service deployment.

`preview.up` starts that stack and stops there. It writes nothing to Infrahub
and admits no Sync run, so repeating it against an environment somebody is
already using changes none of their data.

Two commands write, and each says what it will write before writing:

- `preview.seed` loads the example schema, creates the `InfraDevice` named
  `core01` on `main`, then forks the `preview-smoke` branch from it. Running it
  again on an environment that already holds the branch changes nothing.
- `preview.smoke` seeds, then runs the smoke suite. That suite mutates `core01`
  on `main` and creates and applies real Sync runs against `preview-smoke`.

The published page for this stack is
[Local development stack](../docs/docs/development-stack.mdx). It covers the same
commands plus the service development loop, the addresses, the development-only credentials,
the startup refusal on retired state, and how to run the tests.

## Start

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke preview.up
```

The final summary prints the Infrahub UI, Prefect UI, and Sync API
addresses, the bearer principals, and where runtime state lives. Requires
Docker and Python 3.11+.

Other commands: `preview.status`, `preview.seed`, `preview.smoke`, `preview.logs`
(`-n sync-api|prefect-worker`), `preview.down` (add `--volumes` to reset
all data).

Changed service code does not reach a running stack. The Sync API has no auto-reload, and
`preview.up` leaves an already-running process alone, so run `preview.down` and then
`preview.up`. The plain `preview.down` keeps every data volume; only `--volumes` deletes them.
The published page has the full loop.

## What to test

The preview exists to gather feedback on the two new v3 interfaces:

- **Sync HTTP API** — the primary focus. Consume the native endpoints
  ([reference](../docs/docs/reference/sync-http-api.mdx)) and drive
  executions through Prefect directly (deployment `infrahub-sync-service/run`,
  Prefect UI address in the summary).
- **Python API** — the documented plan → verify → apply cycle
  ([reference](../docs/docs/reference/python-api.mdx)).
- **CLI** — plan, offline review, checksum-gated apply
  ([guide](../examples/custom_adapter/README.md)).

## Files

| File | Role |
| --- | --- |
| `docker-compose.infrahub.yml` | Based on the official Infrahub compose file at `https://infrahub.opsmill.io/<VERSION>`, with image digests added. When refreshing from upstream, restore and verify every image pin before using the file. |
| `docker-compose.preview.yml` | Preview overrides: collision-free host ports and the dedicated `sync-prefect` service pinned to the repository's Prefect version. |
| `preview.env` | Shipped defaults — ports, paired image tags and digests, and local-only tokens. Change a tag and its digest together; a tag-only change still pulls the prior image. Nothing here is a secret; never point these values at a shared or internet-facing instance. |
| `preview.local.env` | Your personal overrides (gitignored). Tokens you mint while testing belong here, not in `preview.env`. |

The preview tasks also read image overrides exported in the shell, except for
the generic `VERSION` variable; use `preview.local.env` to change the Infrahub
version. Changing an Infrahub or Prefect tag requires a matching digest;
changing only the Infrahub image name drops the shipped digest so a local build
can run. For a registry mirror, set its digest explicitly, even if it matches
the shipped digest. With direct Docker Compose, a digest-only override keeps
the shipped Infrahub tag and replaces its digest.

Runtime state (process pids, logs, sync and product caches) lives under
`.preview/` at the repository root, also gitignored. The worker runs from its own empty
`.preview/worker-cwd` so it imports the installed distribution rather than this checkout. The
smoke suite is `tests/preview/`, opt-in via `pytest -m preview` and driven by
`preview.smoke`; see
[Testing tiers](../docs/docs/develop/guidelines/testing-tiers.md) for what each suite needs.

## Local NetBox for the saved-plan apply test

`tests/integration/test_saved_plan_apply_integration.py` needs a source NetBox seeded with a
fixed, deterministic dataset — sites `site-a`/`site-b`/`site-c`, racks
`rack-site-<x>-<n>`, devices `dev-01`…`dev-40`, tags `tag-01`…`tag-10`. `development/netbox/`
provisions exactly that, disposable and local, following the same pattern as the preview
environment above.

```bash
uv run invoke netbox.seed        # starts NetBox, resets it, loads the `seed` dataset, and prints the URL and token
```

`netbox.seed` starts NetBox itself and prints its URL and development token when it
finishes — there is no need to run `netbox.up` first. Doing so anyway makes NetBox run its
first migration twice; on a small host that first migration can take 15 minutes or more. Run
`netbox.up` on its own only when you want an empty NetBox with nothing loaded, or to reprint
the banner later without touching the already-running containers. `netbox.seed` takes
`--dataset` (default `seed`; the task structure leaves room for a second, `demo`, dataset).
Starting any dataset resets the database first, so re-running `netbox.seed` is safe to
repeat. Export the printed values as `NETBOX_URL` and `NETBOX_TOKEN`, point
`INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` at a disposable Infrahub with the pinned schema
library loaded, then run the test — see
[Testing tiers](../docs/docs/develop/guidelines/testing-tiers.md#integration) for the full
sequence. `uv run invoke netbox.down` removes the containers and their data volumes.

| File | Role |
| --- | --- |
| `netbox/docker-compose.netbox.yml` | A pinned, disposable NetBox instance (image pinned by digest). |
| `netbox/netbox.env` | Shipped defaults — the host port and development-only NetBox credentials. Nothing here is a secret; never point these values at a shared or internet-facing instance. |
| `netbox/netbox.local.env` | Your personal overrides (gitignored). |
| `netbox/datasets/seed_netbox.py` | The `seed` dataset's seeder script. Asserts the instance is empty before writing and never deletes. |
