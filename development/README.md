# Preview environment

One command from a fresh clone to a complete, testable Infrahub Sync v3 stack:
a disposable Infrahub instance, a dedicated Prefect server, the managed Sync
HTTP API, a Prefect worker running the managed deployment, a loaded example
schema, and a first saved plan — finished with an automatic smoke run across
every preview surface so you never start from a broken environment.

The smoke run writes its five example devices only to the `preview-smoke`
Infrahub branch. `main` stays empty, so the custom-example CLI walkthrough
starts with five creates; after applying that reviewed plan, the next plan has
zero operations.

`preview.up` checks that pristine `main` state during startup. After completing
the walkthrough, rerun `preview.smoke`, or reset volumes before running
`preview.up` again.

## Start

```bash
uv sync --extra dev --extra prefect --extra managed
uv run invoke preview.up
```

The final summary prints the Infrahub UI, Prefect UI, and managed Sync API
addresses, the bearer principals, and where runtime state lives. Requires
Docker and Python 3.11+.

Other commands: `preview.status`, `preview.smoke`, `preview.logs`
(`--name sync-api|prefect-worker`), `preview.down` (add `--volumes` to reset
all data).

## What to test

The preview exists to gather feedback on the two new v3 interfaces:

- **Managed HTTP API** — the primary focus. Consume the native endpoints
  ([reference](../docs/docs/reference/managed-http-api.mdx)) and drive
  executions through Prefect directly (deployment `infrahub-sync-managed/run`,
  Prefect UI address in the summary).
- **Python API** — the documented plan → verify → apply cycle
  ([reference](../docs/docs/reference/python-api.mdx)).
- **CLI** — plan, offline review, checksum-gated apply
  ([guide](../examples/custom_adapter/README.md)).

## Files

| File | Role |
| --- | --- |
| `docker-compose.infrahub.yml` | Official Infrahub compose file, downloaded pristine from `https://infrahub.opsmill.io/<VERSION>`. Do not edit; replace from the source URL and update `VERSION` in `preview.env` together. |
| `docker-compose.preview.yml` | Preview overrides: collision-free host ports and the dedicated `sync-prefect` service pinned to the repository's Prefect version. |
| `preview.env` | Shipped defaults — ports, image tags, and local-only tokens. Nothing here is a secret; never point these values at a shared or internet-facing instance. |
| `preview.local.env` | Your personal overrides (gitignored). Tokens you mint while testing belong here, not in `preview.env`. |

Runtime state (process pids, logs, sync and product caches) lives under
`.preview/` at the repository root, also gitignored. The smoke suite is
`tests/preview/`, opt-in via `pytest -m preview`.
