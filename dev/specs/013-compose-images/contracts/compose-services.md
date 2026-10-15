# Contract: Compose services

What each compose file must declare after this change. `tests/compose/test_compose_contract.py`
checks every row against `docker compose config` output.

## Root `docker-compose.yml`

| Service | Image | Command | Read-only, tmpfs scratch | Mounts | Host port |
| --- | --- | --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine@sha256:cf78e766…` (unchanged) | default | no | `postgres-data` | none |
| `db-bootstrap` | same as `postgres` | `/bin/sh /usr/local/bin/databases.sh` | no | config `db-bootstrap-script` | none |
| `object-store-init` | `pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb…` | `sh -ec` owner repair (unchanged) | read-only, no tmpfs | `object-store-data` | none |
| `object-store` | same as `object-store-init`, `user: "65532:65532"` | `["server", "/data"]` | no | `object-store-data` | none |
| `prefect-server` | Sync image reference | `["python", "-m", "infrahub_sync.service.prefect_server"]` | yes | none | none |
| `sync-bootstrap` | Sync image reference | `python -m infrahub_sync.service.bootstrap` | yes | none | none |
| `sync-api` | Sync image reference | image default | yes | CA directory, read-only | `${INFRAHUB_SYNC_BIND_ADDRESS:-127.0.0.1}:${INFRAHUB_SYNC_API_PORT:-8000}:8000` |
| `sync-worker` | Sync image reference | `python -m infrahub_sync.service.worker --pool …` | yes | CA directory, read-only | none |
| `cli` (profile `cli`) | Sync image reference | `infrahub-sync` | yes | none | none |

Sync image reference: `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release>}`,
identical on the five Sync services.

CA directory mount: `${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro`.

Top-level volumes: `postgres-data`, `object-store-data` (both keep data), `sync-ca-certificates`
(empty placeholder, mounted read-only only).

### `prefect-server` details

- `depends_on`: `db-bootstrap` completed successfully (unchanged).
- Health check: `["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:4200/api/health', timeout=5)"]`,
  interval 5 s, timeout 10 s, retries 30, start period 20 s (unchanged).
- Environment: see [environment-variables.md](environment-variables.md).

### `object-store` health check

`["CMD", "curl", "-fsS", "-o", "/dev/null", "http://localhost:9000/minio/health/live"]`, interval
5 s, timeout 5 s, retries 30.

### Properties the contract tests hold

1. No service uses an image whose repository is `prefecthq/prefect`.
2. Every image that is not the Sync image matches `<repository>:<tag>@sha256:<64 hex>`.
3. `sync-api` is the only service with `ports`; every published address defaults to `127.0.0.1`.
4. `sync-api`, `sync-worker` and the other Sync services share no writable mount. The only mount
   on any Sync service is the read-only CA directory, on `sync-api` and `sync-worker`.
5. With `INFRAHUB_SYNC_CA_CERTIFICATES` unset, no service mounts a host path.
6. Only `prefect-server` and `db-bootstrap` hold the Prefect database password.
7. `prefect-server` mounts no volume (it keeps no state of its own).

## `development/docker-compose.dev.yml`

| Service | Change |
| --- | --- |
| `object-store` | image as root `object-store`; `user: "65532:65532"`; `curl` health check |
| `prefect-server` | `image: *sync-image`, `build: *sync-build`, command `python -m infrahub_sync.service.prefect_server`; ports and environment unchanged |
| `sync-worker` | 17 credential variables; CA directory mount; `INFRAHUB_SYNC_CA_DIRECTORY` |
| `sync-api` | CA directory mount; `INFRAHUB_SYNC_CA_DIRECTORY` |

`postgres` keeps `postgres:16-alpine` without a digest, as today; FR-014 covers the object store
image in this file.

## `development/docker-compose.preview.yml`

| Service | Change |
| --- | --- |
| `sync-minio` | image as root `object-store`; `user: "65532:65532"`; `curl` health check |
| `sync-minio-bootstrap` | same image; `mc alias set` and `mc mb --ignore-existing` unchanged |
| `sync-prefect` | `image: "infrahub-sync:preview"`, `build: {context: .., dockerfile: Dockerfile}`, command `python -m infrahub_sync.service.prefect_server`; ports and environment unchanged |
