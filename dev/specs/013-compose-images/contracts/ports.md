# Contract: Published ports

## Per stack

| Stack | Service | Host address and port | Container port | Default |
| --- | --- | --- | --- | --- |
| Root | `sync-api` | `${INFRAHUB_SYNC_BIND_ADDRESS:-127.0.0.1}:${INFRAHUB_SYNC_API_PORT:-8000}` | 8000 | published |
| Root | `prefect-server` | `127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}` | 4200 | not published; opt-in only |
| Development | `sync-api` | `127.0.0.1:8030` | 8000 | published (unchanged) |
| Development | `prefect-server` | `127.0.0.1:4230` | 4200 | published (unchanged) |
| Development | `postgres` | `127.0.0.1:5440` | 5432 | published (unchanged) |
| Preview | `sync-prefect` | `127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}` | 4200 | published (unchanged) |
| Preview | `sync-minio` | `127.0.0.1:${PREVIEW_MINIO_PORT:-9010}` | 9000 | published (unchanged) |
| Preview | `sync-postgres` | `127.0.0.1:${PREVIEW_STORAGE_POSTGRES_PORT:-5439}` | 5432 | published (unchanged) |

Services inside the root deployment reach Prefect at `http://prefect-server:4200/api` over the
Compose network, whether or not the port is published.

## Prefect opt-in (root file)

File `prefect-ui.yml`, written by the engineer in the directory that holds `docker-compose.yml`
and `.env`:

```yaml
services:
  prefect-server:
    ports:
      - "127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}:4200"
```

| Action | Command | Result |
| --- | --- | --- |
| Open | `docker compose -f docker-compose.yml -f prefect-ui.yml up -d --wait` | `prefect-server` is recreated with the port; UI at `http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}`, asking for `INFRAHUB_SYNC_PREFECT_AUTH_STRING` |
| Close | `docker compose up -d --wait` | `prefect-server` is recreated without the port |

Rules:

- The host address is `127.0.0.1`, written in the file, not taken from `INFRAHUB_SYNC_BIND_ADDRESS`.
- The file is not shipped with a release. The documentation holds the snippet.
- An engineer on another computer reaches the port through an SSH port forward, as spec 011
  FR-031 describes for the Sync UI.
