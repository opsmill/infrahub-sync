# Development stack

`development/docker-compose.yml` runs the whole Sync service, built from this checkout, and a
disposable Infrahub to sync into: PostgreSQL, the object store, the Prefect server, the Sync
API, the worker and Infrahub. The published page is
[Local development stack](../docs/docs/development-stack.mdx). It covers every `dev.*` task,
the addresses, the development-only credentials, both modes (everything in containers, or the
Sync API and the worker on the host with `dev.deps`), and how to run the tests.

## Start

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke dev.start
```

`dev.start` builds `infrahub-sync:dev` when the image is missing or its build inputs changed,
starts every service and waits until each is healthy, then prints the addresses. The stack
mounts the checkout's code into its containers, so after a code change run
`uv run invoke dev.restart`. Requires Docker and Python 3.11 to 3.13.

Two tasks write to Infrahub, and each says what it will write before writing:

- `dev.seed` loads the example schema, creates the `InfraDevice` named `core01` on `main`,
  then forks the `preview-smoke` branch from it. Running it again on a stack that already holds
  the branch changes nothing.
- `dev.smoke` seeds, then runs the smoke suite (`tests/dev_stack/smoke/`, `pytest -m smoke`).
  That suite mutates `core01` on `main` and creates and applies real Sync runs against
  `preview-smoke`.

Other tasks: `dev.status`, `dev.logs`, `dev.stop` (keeps the data), `dev.destroy` (deletes
it). See [Testing tiers](../docs/docs/development/guidelines/testing-tiers.md) for what each
suite needs.

## Files

| File | Role |
| --- | --- |
| `docker-compose.yml` | The development stack. Every credential in it is a development default. |
| `docker-compose.infrahub.yml` | Based on the official Infrahub compose file at `https://infrahub.opsmill.io/<VERSION>`, with image digests added and every service under the `infrahub` profile. When refreshing from upstream, restore and verify every image pin and the profile before using the file. `.github/scripts/update_infrahub.py` moves the pinned version and digest together. |
| `docker-compose.infrahub-ports.yml` | Publishes `infrahub-server` at `${INFRAHUB_BIND_ADDRESS:-127.0.0.1}:${INFRAHUB_PORT:-8000}` and nothing else of Infrahub. |

Runtime state that the tasks and tests write (the Python 3.10 check environments, smoke
evidence, nightly reports) lives under `.dev/` at the repository root, which is gitignored.

## Local NetBox

`development/netbox/` provisions a disposable, local NetBox. It loads one of two datasets. Each dataset replaces the whole
NetBox database, so only one is loaded at a time.

| Dataset | Contents | Used by |
| --- | --- | --- |
| `seed` (default) | A fixed, deterministic dataset: sites `site-a`/`site-b`/`site-c`, racks `rack-site-<x>-<n>`, devices `dev-01`…`dev-40`, tags `tag-01`…`tag-10`. | `tests/integration/test_saved_plan_apply_integration.py` |
| `demo` | The official NetBox demo data from `netbox-community/netbox-demo-data` (MIT license), pinned to one commit and one SHA-256 checksum. | The `from-netbox` example check |

```bash
uv run invoke netbox.seed                  # the `seed` dataset
uv run invoke netbox.seed --dataset demo   # the `demo` dataset
```

`netbox.seed` resets the database and loads the dataset. The `seed` dataset accepts
`--tier S|M|L` (default S) and reports the loaded tier. Both datasets print the URL and
development-token banner. See [the tier guide](../docs/docs/development/guides/netbox-benchmark-tiers.md)
for dumps, restores, and fixed changes. The task starts NetBox itself, so you do not need
to run `netbox.start` first.
Doing so anyway makes NetBox run its first migration twice; on a small host that first
migration can take 15 minutes or more. The `demo` dataset skips most of that wait, because
NetBox starts against the restored data. Run `netbox.start` on its own only
when you want an empty NetBox with nothing loaded, or to reprint the banner later without
touching the already-running containers. Every load resets the database first, so you can
run `netbox.seed` again at any time. `uv run invoke netbox.destroy` removes the containers and
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
[Testing tiers](../docs/docs/development/guidelines/testing-tiers.md#integration) for the full
sequence.

### The `from-netbox` example check

The check runs the shipped `examples/netbox_to_infrahub` package against the `demo` dataset
and a fresh development stack. The shipped package names the public NetBox demo, so
`netbox.demo-package` writes a copy to `.netbox/from-netbox.local.yml` that differs only in
its two `url` settings. [Testing tiers](../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check)
lists every command, from a fresh Infrahub to `diff` and `sync`.

| File | Role |
| --- | --- |
| `netbox/docker-compose.netbox.yml` | A pinned, disposable NetBox instance (image pinned by digest). The `demo` dump matches this image's NetBox version; change the dump and the image together. |
| `netbox/netbox.env` | Shipped defaults — the host port and development-only NetBox credentials. Nothing here is a secret; never point these values at a shared or internet-facing instance. |
| `netbox/netbox.local.env` | Your personal overrides (gitignored). |
| `netbox/datasets/seed_netbox.py` | The `seed` dataset's seeder script. Asserts the instance is empty before writing and never deletes. |
| `netbox/datasets/tier_data.py` | Deterministic tier counts, skip cases, and object payloads. |
| `netbox/datasets/netbox_api.py` | Bounded REST batches, relationship resolution, progress logging, and count checks. |
| `netbox/datasets/change_netbox.py` | Fixed 1% tier mutations and expected-change files. |
| `../tasks/netbox.py` | The `demo` dataset's pinned commit, URL, and SHA-256, and the restore steps. |
| `../.netbox/` | Gitignored. The downloaded demo dump, generated `from-netbox.local.yml`, tier dumps and metadata in `dumps/`, expected and partial mutations in `changes/`, and loaded-tier state in `tier.json`. The restore writes a changed copy of the dump here and deletes it when the restore ends. |
