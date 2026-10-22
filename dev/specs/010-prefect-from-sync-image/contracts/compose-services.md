# Contract: Compose services

What `docker compose config` must show for each stack. Contract tests in `tests/compose/` and
`tests/dev_stack/` read the resolved configuration, not the YAML source, so interpolation and
profiles are included. The entities are described in [data-model.md](../data-model.md).

## All stacks

| Check | Requirement |
|-------|-------------|
| No image reference contains `prefecthq/prefect` | FR-001, SC-004 |
| No Compose file and no `Dockerfile` line names a Prefect version string or a Prefect image | FR-005, SC-007 |
| `prefect-server.image` equals `sync-api.image` and `sync-worker.image` | FR-002 |
| `prefect-server.command` is `uvicorn --host 0.0.0.0 --port 4200 --factory infrahub_sync.service.prefect_server:create_prefect_app` | FR-001 |
| `prefect-server.healthcheck` calls `http://127.0.0.1:4200/api/health` with the image's Python | FR-001 |
| `object-store.image` and `object-store-init.image` match `pgsty/silo:RELEASE.<date>@sha256:<64 hex>` | FR-011 |
| No image reference contains `cgr.dev/chainguard/minio` or `minio/minio` | FR-011 |
| `object-store.healthcheck.test` is `["CMD", "silo", "healthcheck", "ready"]` | FR-013 |
| `object-store.user` is `65532:65532`; `object-store-init` runs `chown` only when `stat` shows another owner | FR-013 |

## Root file (`docker-compose.yml`) and the packaged copy

| Check | Requirement |
|-------|-------------|
| `sync-api` is the only service with `ports` | FR-006, SC-003 |
| `prefect-server.environment.PREFECT_UI_ENABLED == "false"` | FR-006 |
| `prefect-server.environment.PREFECT_SERVER_UI_API_URL` is set and points at `127.0.0.1` by default | FR-006 |
| `prefect-server` is `read_only: true` with the Sync tmpfs scratch mounts | R2 |
| The Prefect database password appears only in `prefect-server` and `db-bootstrap` | FR-003 |
| `cli.environment` has no `PREFECT_*` credential (`PREFECT_API_AUTH_STRING`, database URL) | FR-003 |
| No service outside the Sync-image services holds `PREFECT_API_URL` or a Prefect credential | FR-004 |
| Distinct images: PostgreSQL, Silo, Sync — three | SC-001 |
| `infrahub_sync/testcontainers/docker-compose.yml` is byte-equal to `docker-compose.yml` | FR-023 |

## Root file plus the override snippet

`docker compose -f docker-compose.yml -f <snippet> config`:

| Check | Requirement |
|-------|-------------|
| `prefect-server.environment.PREFECT_UI_ENABLED == "true"` | FR-007 |
| `prefect-server.environment.PREFECT_UI_STATIC_DIRECTORY` is under `/tmp/infrahub-sync/` | FR-007, R2 |
| `prefect-server.ports` has exactly one entry, with `host_ip: 127.0.0.1` | FR-007 |

## Development stack (`development/docker-compose.yml`)

| Check | Requirement |
|-------|-------------|
| `docker compose config --profiles` lists `infrahub` and `sync` | FR-015 |
| Without profiles: `postgres`, `object-store-init`, `object-store`, `prefect-server` only | FR-015 |
| `prefect-server.ports` is `127.0.0.1:4230:4200`; UI on | FR-015 |
| Every published port has `host_ip: 127.0.0.1` | existing loopback test |
| Every Sync-image service mounts `infrahub_sync/` and `opsmill_prefect_extras/` read-only | FR-019 |
| `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml` and `development/preview.env` do not exist | FR-015 |
