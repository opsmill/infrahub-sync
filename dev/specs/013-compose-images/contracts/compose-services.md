# Contract: Compose services

What each compose file must declare after this change. `tests/compose/test_compose_contract.py`
checks every row against `docker compose config` output, rendered with generated test values.

## Root `docker-compose.yml`

| Service | Image | Command | Read-only, tmpfs scratch | Mounts | Host port |
| --- | --- | --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine@sha256:cf78e766…` (unchanged) | default | no | `postgres-data` | none |
| `db-bootstrap` | same as `postgres` | `/bin/sh /usr/local/bin/databases.sh` | no | config `db-bootstrap-script` | none |
| `object-store-init` | `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba` (unchanged); `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}` | `sh -ec` owner repair (unchanged) | read-only, no tmpfs | `object-store-data` | none |
| `object-store` | same image (unchanged), `user: "65532:65532"`; `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}` | `["server", "/data"]` | no | `object-store-data` | none |
| `prefect-server` | Sync image reference | `["python", "-m", "infrahub_sync.service.prefect_server"]` | yes | none | none |
| `sync-bootstrap` | Sync image reference | `python -m infrahub_sync.service.bootstrap` | yes | CA directory, read-only | none |
| `sync-api` | Sync image reference | image default | yes | CA directory, read-only | `${INFRAHUB_SYNC_BIND_ADDRESS:-127.0.0.1}:${INFRAHUB_SYNC_API_PORT:-8000}:8000` |
| `sync-worker` | Sync image reference | `python -m infrahub_sync.service.worker --pool …` | yes | CA directory, read-only | none |
| `cli` (profile `cli`) | Sync image reference | `infrahub-sync` | yes | none | none |

Sync image reference: `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release>}`,
identical on the five Sync services.

CA directory mount: `${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro`.

Top-level volumes: `postgres-data`, `object-store-data` (both keep data), `sync-ca-certificates`
(empty placeholder, mounted read-only only).

### Dependencies that change

- `sync-bootstrap` → `object-store`: `condition: service_healthy`, `required: false` (new). With
  the default scale it waits as today; with scale 0 it starts without the store.
- `prefect-server` → `db-bootstrap`: completed successfully (unchanged).

### `prefect-server` details

- Health check: `["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:4200/api/health', timeout=5)"]`,
  interval 5 s, timeout 10 s, retries 30, start period 20 s (unchanged).
- Environment: see [environment-variables.md](environment-variables.md).

### `object-store` health check

Unchanged: `wget --quiet --tries=1 --output-document=/dev/null http://localhost:9000/minio/health/live`,
interval 5 s, timeout 5 s, retries 30 (`docker-compose.yml:138-148`).

### Properties the contract tests hold

1. No service uses an image whose repository is `prefecthq/prefect`.
2. Every image that is not the Sync image carries `@sha256:<64 hex>`; the object store services
   carry the digest above.
3. `sync-api` is the only service with `ports`; every published address defaults to `127.0.0.1`.
4. The Sync services share no writable mount. The only mount on any Sync service is the
   read-only CA directory, on `sync-bootstrap`, `sync-api` and `sync-worker`.
5. With `INFRAHUB_SYNC_CA_CERTIFICATES` unset, no service mounts a host path.
6. Each credential reaches exactly the receivers in
   [environment-variables.md](environment-variables.md); `prefect-server` holds no object store
   or adapter credential.
7. `prefect-server` mounts no volume (it keeps no state of its own).
8. With `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, `object-store-init` and `object-store` have scale
   0, and the model still resolves.
9. `INFRAHUB_SYNC_S3_ENDPOINT_URL` and `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` follow `.env` and
   default to `http://object-store:9000` and `path`.

## `development/docker-compose.dev.yml`

| Service | Change |
| --- | --- |
| `object-store` | image unchanged; comment no longer calls the build temporary |
| `prefect-server` | `image: *sync-image`, `build: *sync-build`, command `python -m infrahub_sync.service.prefect_server`; ports and environment unchanged |
| `sync-bootstrap` | CA directory mount; `INFRAHUB_SYNC_CA_DIRECTORY` |
| `sync-api` | CA directory mount; `INFRAHUB_SYNC_CA_DIRECTORY` |
| `sync-worker` | 17 credential variables; CA directory mount; `INFRAHUB_SYNC_CA_DIRECTORY` |
| `x-sync-storage` | endpoint and addressing style read from the environment with the current values as defaults |

`postgres` keeps `postgres:16-alpine` without a digest, as today.

## `development/docker-compose.preview.yml`

| Service | Change |
| --- | --- |
| `sync-minio`, `sync-minio-bootstrap` | image and `mc` call unchanged; comments no longer call the build temporary |
| `sync-prefect` | `image: "infrahub-sync:preview"`, `build: {context: .., dockerfile: Dockerfile}`, command `python -m infrahub_sync.service.prefect_server`; ports and environment unchanged |
