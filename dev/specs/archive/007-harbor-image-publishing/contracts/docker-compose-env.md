# Contract: root `docker-compose.yml` operator environment

The operator runs `docker compose up -d` next to the file. Values come from the
shell environment, or from a `.env` beside the file, which Compose loads
automatically. **Required** variables use `${VAR:?message}`, so a missing one stops
Compose before any container starts, with a message naming the variable.

## Image selection

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `INFRAHUB_SYNC_DOCKER_IMAGE` | no | `registry.opsmill.io/opsmill/infrahub-sync` | Image repository for every Sync service |
| `VERSION` | no | the release version pinned in this file | Image tag for every Sync service |

## Credentials (required, never defaulted)

| Variable | Used by |
|---|---|
| `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` | `postgres`, `db-bootstrap` (replaces the old secret file) |
| `INFRAHUB_SYNC_PRODUCT_PASSWORD` | `db-bootstrap`, Sync services (product database role) |
| `INFRAHUB_SYNC_PREFECT_PASSWORD` | `db-bootstrap`, `prefect-server` (Prefect database role) |
| `INFRAHUB_SYNC_S3_ACCESS_KEY` / `INFRAHUB_SYNC_S3_SECRET_KEY` | `object-store`, Sync services |
| `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` | `sync-api` (JSON object of API principals) |

Connection strings that were operator-supplied before
(`INFRAHUB_SYNC_DATABASE_URL`, `INFRAHUB_SYNC_PREFECT_DATABASE_URL`) are now
composed inside the file from the role names and passwords. Operators may still
override them.

## Non-secret settings (defaulted inline)

These are the former `deploy/compose/defaults.conf` values: role and database names,
bucket and prefix, loopback publication ports, and the work-pool name. Each one keeps
its current default as `${VAR:-default}`, and the docs list them.

## Removed

- `INFRAHUB_SYNC_IMAGE` (sha256-only)
- `INFRAHUB_SYNC_IMAGE_PULL_POLICY`
- `INFRAHUB_SYNC_INSTANCE`
- `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD_FILE`
- `image.bind`
- the `init` step

## Minimum tooling

Docker Compose 2.24 or later, for top-level `configs` with inline `content`, which
carries the database bootstrap script.
