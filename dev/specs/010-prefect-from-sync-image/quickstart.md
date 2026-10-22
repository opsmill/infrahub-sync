# Quickstart: check the feature by hand

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Each section checks one user story. The automated checks are listed in the contracts:
[compose-services.md](contracts/compose-services.md),
[invoke-tasks.md](contracts/invoke-tasks.md),
[testcontainers-api.md](contracts/testcontainers-api.md).

## Prerequisites

- Docker with Compose v2.20 or later, about 8 GB of memory for Docker.
- A checkout of this branch, set up with
  `uv sync --extra dev --extra prefect --extra service`.
- No Sync volumes left from `3.0.0a5`. If there are, run `docker compose down -v` in that
  deployment first; this removes the Prefect work pool, deployments and run history, the saved
  plans and run files, and the `infrahub_sync` database (D-14).

## User Story 1: deploy the root file

```bash
# write .env beside docker-compose.yml, as in docs/docs/compose-deployment.mdx ("The .env file")
docker compose up -d --wait --wait-timeout 420
docker compose ps --format '{{.Service}} {{.Image}} {{.Ports}}'
```

Expected:

- Every service is healthy.
- `prefect-server`, `sync-api` and `sync-worker` show the same Sync image.
- `object-store` shows `pgsty/silo`; no line shows `prefecthq/prefect`.
- Only `sync-api` shows a published port.

Then register, plan and apply a configuration with the CLI, as in
[`running-a-sync.mdx`](../../../docs/docs/running-a-sync.mdx).

## User Story 2: everything in containers

```bash
uv run invoke dev.start
uv run invoke dev.smoke
```

Expected: `dev.smoke` passes. Edit a log message in `infrahub_sync/service/serve.py`, run
`uv run invoke dev.restart`, and check that `uv run invoke dev.logs --name sync-api` shows the
new message without an image rebuild.

Stale image check: change one line in `uv.lock` (for example on a scratch branch), run
`uv run invoke dev.start`, and check that it rebuilds `infrahub-sync:dev` before starting.

## User Story 3: Sync API and worker on the host

```bash
uv run invoke dev.deps          # prints the URLs, then returns
uv run invoke dev.api           # terminal 2, stays in the foreground
uv run invoke dev.worker        # terminal 3, stays in the foreground
uv run invoke dev.smoke         # terminal 1
```

Expected: `dev.smoke` passes. Stop `dev.worker` and run `dev.smoke` again: it fails and names
`dev.worker`.

## User Story 4: open the Prefect UI on a deployment

Add the override snippet from the operator documentation as `docker-compose.override.yml`,
then:

```bash
docker compose up -d
```

Expected: `http://127.0.0.1:4200` (or `INFRAHUB_SYNC_PREFECT_PORT`) shows the Prefect UI and
asks for the Prefect API credential. Remove the file and run `docker compose up -d`: the port
no longer answers.

## User Story 5: end-to-end test class

```bash
uv sync --extra dev --extra prefect --extra service --extra testcontainers
INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=dev \
  uv run pytest -m compose tests/compose -x
```

Expected: Infrahub starts from `infrahub-testcontainers`, Sync from the packaged root file with
the local image, and the plan and apply test passes. Then run `uv run invoke tests.tests-unit`
with Docker stopped: it passes, and the `infrahub-testcontainers` plugin is not loaded.

## Task names

```bash
uv run invoke --list
```

Expected: `format` and `lint` are the only names without a namespace; `lint` runs rumdl first
and then ruff, pylint, yamllint and ty.
