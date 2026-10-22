# Data model: Compose stacks run the Prefect server from the Sync image

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

This feature changes no database schema. Its "data" is the shape of three Compose stacks, the
development settings, and one image label. Validation rules point at the requirement that
tests them.

## Stack

One of three ways to run Sync with Docker Compose.

| Stack | Compose file | Sync image | Who runs it |
|-------|--------------|------------|-------------|
| Root deployment | `docker-compose.yml` | `${INFRAHUB_SYNC_DOCKER_IMAGE}:${VERSION}` | Customer teams |
| Development stack | `development/docker-compose.yml` (includes `development/docker-compose.infrahub.yml`) | `infrahub-sync:dev`, built from the checkout | OpsMill engineers |
| Test fixture | `infrahub_sync/testcontainers/docker-compose.yml`, byte-equal to the root file | `registry.opsmill.io/opsmill/infrahub-sync:<installed version>`, or the image variables | CI and demo repositories |

Rules:

- In every stack, `prefect-server`, `sync-api` and `sync-worker` use the same image reference
  (FR-002), and no service uses `prefecthq/prefect` (FR-001).
- In every stack the object store image is `pgsty/silo:<dated tag>@<digest>` (FR-011).

## Service

| Service | Image | Root file | Development stack | Notes |
|---------|-------|-----------|-------------------|-------|
| `postgres` | `postgres:16-alpine@…` | core | core, `127.0.0.1:5440` | |
| `db-bootstrap` | `postgres:16-alpine@…` | core | not present | Holds the Prefect password to create the role (FR-003 exception). |
| `object-store-init` | Silo | core | core | Runs as root once; `chown -R 65532:65532 /data` when needed (R3). |
| `object-store` | Silo | core, no port | core, loopback port for `dev.deps` | Runs as `65532:65532`; health check `silo healthcheck ready`. |
| `prefect-server` | Sync image | core, no port, UI off | core, `127.0.0.1:4230`, UI on | uvicorn factory `infrahub_sync.service.prefect_server:create_prefect_app`. |
| `sync-bootstrap` | Sync image | core | profile `sync` | Creates the bucket over the S3 API, the work pool and the deployment. |
| `sync-api` | Sync image | core, the only published port | profile `sync`, `127.0.0.1:8030` | |
| `sync-worker` | Sync image | core | profile `sync` | Attached to the Infrahub network. |
| `cli` | Sync image | core | not present | No Prefect credential (FR-003). |
| Infrahub services | Infrahub images | not present | profile `infrahub`, from the included file | Dropped when `--infrahub-network` is set. |

Rules:

- Root file: `sync-api` is the only service with a `ports` entry (FR-006, SC-003).
- Root file: `PREFECT_UI_ENABLED=false` and `PREFECT_SERVER_UI_API_URL` set on
  `prefect-server` (FR-006).
- Root file: the Prefect database password appears only in the environment of
  `prefect-server` and `db-bootstrap` (FR-003).
- Development stack: the checkout's `infrahub_sync/` and `opsmill_prefect_extras/` are mounted
  read-only over the installed copies in every Sync-image service (FR-019).

## Override snippet

The documented `docker-compose.override.yml` content for the root file (FR-007).

| Field | Value |
|-------|-------|
| `services.prefect-server.environment.PREFECT_UI_ENABLED` | `"true"` |
| `services.prefect-server.environment.PREFECT_UI_STATIC_DIRECTORY` | `/tmp/infrahub-sync/prefect-ui` (on the tmpfs) |
| `services.prefect-server.ports` | `127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}:4200` |

State change: without the snippet, the UI is off and the port closed. With it and
`docker compose up -d`, the UI answers on the loopback port and asks for the Prefect API
credential. Removing it and running `up -d` again returns to the first state (User Story 4).

## Development settings

The values that `development/preview.env` holds today move into the development Compose file
as `${NAME:-default}` interpolations. Renamed keys drop the `PREVIEW_` prefix. Keys
`PREVIEW_PREFECT_IMAGE_TAG` and `PREVIEW_PREFECT_IMAGE_DIGEST` are removed (the Prefect server
uses the Sync image).

| Setting today (`preview.env`) | Where it goes | Read by |
|-------------------------------|---------------|---------|
| `COMPOSE_PROJECT_NAME` | top-level `name:` of the development file | `dev_settings()`, `bench.py` |
| `PREVIEW_INFRAHUB_PORT`, `PREVIEW_INFRAHUB_BIND_ADDRESS` | `infrahub-server` port mapping | `dev_settings()`, `bench.py` |
| `INFRAHUB_INITIAL_ADMIN_TOKEN` | `infrahub-server` environment default | `dev_settings()`, `bench.py`, `dev.seed` |
| `PREVIEW_PREFECT_PORT`, `PREVIEW_SYNC_API_PORT` | `prefect-server` and `sync-api` port mappings | `dev_settings()`, `dev.smoke` |
| `PREVIEW_STORAGE_POSTGRES_PORT`, `PREVIEW_MINIO_PORT`, `PREVIEW_MINIO_ACCESS_KEY`, `PREVIEW_MINIO_SECRET_KEY` | `postgres` and `object-store` port mappings and environment | `dev.api`, `dev.worker` |
| `VERSION`, `INFRAHUB_DOCKER_IMAGE_DIGEST` | image reference in `docker-compose.infrahub.yml` | `bench.py` overrides from the shell |
| `PREVIEW_BEARER_TOKENS`, `PREVIEW_WORK_POOL`, `PREVIEW_RUN_ADMISSION_TTL_SECONDS`, `PREVIEW_PREFECT_WORKER_QUERY_SECONDS` | Sync service environment defaults | `dev.api`, `dev.worker` (host mode) |

Rule: tasks read every value through `dev_settings()`, which runs
`docker compose … config --format json` (R6), so the tasks and the stack never disagree.

## Build-inputs label

| Field | Value |
|-------|-------|
| Label | `org.opsmill.infrahub-sync.build-inputs` |
| Value | SHA-256 over the bytes of `Dockerfile`, `pyproject.toml`, `uv.lock`, in that order |
| Set by | `dev.build`, through a build argument |
| Read by | `dev.start` and `dev.deps`, through `docker image inspect` |

State: image missing, label missing, or label different from the checkout → rebuild; label
equal → start without a build (FR-018, R5).

## Development modes

| Mode | Command | Containers | Host processes |
|------|---------|------------|----------------|
| Everything in containers | `dev.start` | core, `sync`, `infrahub` | none |
| Dependencies only | `dev.deps` | core, `infrahub` | `dev.api` and `dev.worker`, each in the foreground |

Both modes drop the `infrahub` profile when `--infrahub-network` or
`INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` names a running Infrahub; its URL then comes from
`INFRAHUB_SYNC_DEV_INFRAHUB_URL`, default `http://infrahub-server:8000` (FR-020). `dev.smoke` in
dependencies mode reads the Sync API's `GET /status`: no answer names `dev.api`, a worker that
is not available names `dev.worker` (FR-022). Only the host processes `dev.api` and
`dev.worker` call the Prefect server (D-2 as clarified on 2026-10-22).
