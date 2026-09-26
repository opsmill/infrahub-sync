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

## Local NetBox

`development/netbox/` provisions a disposable, local NetBox, following the same pattern as
the preview environment above. It loads one of two datasets. Each dataset replaces the whole
NetBox database, so only one is loaded at a time.

| Dataset | Contents | Used by |
| --- | --- | --- |
| `seed` (default) | A fixed, deterministic dataset: sites `site-a`/`site-b`/`site-c`, racks `rack-site-<x>-<n>`, devices `dev-01`…`dev-40`, tags `tag-01`…`tag-10`. | `tests/integration/test_saved_plan_apply_integration.py` |
| `demo` | The official NetBox demo data from `netbox-community/netbox-demo-data` (MIT license), pinned to one commit and one SHA-256 checksum. | The `from-netbox` example check |

```bash
uv run invoke netbox.seed                  # the `seed` dataset
uv run invoke netbox.seed --dataset demo   # the `demo` dataset
```

`netbox.seed` resets the database, loads the dataset, and prints the NetBox URL and
development token. It starts NetBox itself, so you do not need to run `netbox.up` first.
Doing so anyway makes NetBox run its first migration twice; on a small host that first
migration can take 15 minutes or more. The `demo` dataset skips most of that wait, because
NetBox starts against the restored data. Run `netbox.up` on its own only
when you want an empty NetBox with nothing loaded, or to reprint the banner later without
touching the already-running containers. Every load resets the database first, so you can
run `netbox.seed` again at any time. `uv run invoke netbox.down` removes the containers and
their data volumes.

For the `demo` dataset, `netbox.seed` does the following:

1. Downloads `sql/netbox-demo-v4.7.sql` at the pinned commit into `.netbox/` at the repository
   root. This directory is gitignored, so the dump is never committed. A later run reuses the
   downloaded file.
2. Checks the file's SHA-256 against the pinned value. On a mismatch it stops before it
   touches the database. A download that fails the check is deleted. A previously downloaded
   file that fails it is kept, and the error tells you to delete it.
3. Recreates the database volume and restores the dump in one transaction that stops at the
   first error. The restore uses a copy of the dump with `public` on the `search_path`. The
   unchanged dump names `ltree` operators that PostgreSQL cannot resolve with an empty
   `search_path`, and without the change the restore fails.
4. Starts NetBox, which applies only the migrations that are newer than the dump.
5. Gives the demo's own `admin` user the development password and API token from
   `netbox/netbox.env`. The image's own superuser setup skips a user that already exists.

### Saved-plan apply test

Load the `seed` dataset, then export the printed values as `NETBOX_URL` and `NETBOX_TOKEN`.
Point `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` at a disposable Infrahub with the pinned
schema library loaded, then run the test. See
[Testing tiers](../docs/docs/develop/guidelines/testing-tiers.md#integration) for the full
sequence.

### The `from-netbox` example check

The check runs the shipped `examples/netbox_to_infrahub` package against the `demo` dataset
and a fresh preview stack. The shipped package names the public NetBox demo, so
`netbox.demo-package` writes a copy to `.netbox/from-netbox.local.yml` that differs only in
its two `url` settings. [Testing tiers](../docs/docs/develop/guidelines/testing-tiers.md#the-from-netbox-example-check)
lists every command, from a fresh Infrahub to `diff` and `sync`.

| File | Role |
| --- | --- |
| `netbox/docker-compose.netbox.yml` | A pinned, disposable NetBox instance (image pinned by digest). The `demo` dump matches this image's NetBox version; change the dump and the image together. |
| `netbox/netbox.env` | Shipped defaults — the host port and development-only NetBox credentials. Nothing here is a secret; never point these values at a shared or internet-facing instance. |
| `netbox/netbox.local.env` | Your personal overrides (gitignored). |
| `netbox/datasets/seed_netbox.py` | The `seed` dataset's seeder script. Asserts the instance is empty before writing and never deletes. |
| `../tasks/netbox.py` | The `demo` dataset's pinned commit, URL, and SHA-256, and the restore steps. |
| `../.netbox/` | Gitignored. The downloaded demo dump and the generated `from-netbox.local.yml`. The restore writes a changed copy of the dump here and deletes it when the restore ends. |
