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

## Start

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke preview.up
```

The final summary prints the Infrahub UI, Prefect UI, and Sync API
addresses, the bearer principals, and where runtime state lives. Requires
Docker and Python 3.11+.

Other commands: `preview.status`, `preview.seed`, `preview.smoke`, `preview.logs`
(`--name sync-api|prefect-worker`), `preview.down` (add `--volumes` to reset
all data).

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
| `docker-compose.infrahub.yml` | Official Infrahub compose file, downloaded pristine from `https://infrahub.opsmill.io/<VERSION>`. Do not edit; replace from the source URL and update `VERSION` in `preview.env` together. |
| `docker-compose.preview.yml` | Preview overrides: collision-free host ports and the dedicated `sync-prefect` service pinned to the repository's Prefect version. |
| `preview.env` | Shipped defaults — ports, image tags, and local-only tokens. Nothing here is a secret; never point these values at a shared or internet-facing instance. |
| `preview.local.env` | Your personal overrides (gitignored). Tokens you mint while testing belong here, not in `preview.env`. |

Runtime state (process pids, logs, sync and product caches) lives under
`.preview/` at the repository root, also gitignored. The smoke suite is
`tests/preview/`, opt-in via `pytest -m preview`.
