# Data model: Fewer images in the Compose deployment

This change adds no database table, no API field and no stored record. The entities below are
deployment entities: what the Compose files declare and what a process builds at start.

## Sync image

- **Reference**: `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release>}`
  (root); `infrahub-sync:dev` (development); `infrahub-sync:preview` (preview).
- **Used by**: `sync-bootstrap`, `sync-api`, `sync-worker`, `cli`, and after this change
  `prefect-server` (root, development) and `sync-prefect` (preview).
- **Command forms**: `python -m infrahub_sync.service.serve` (default), `.worker`, `.bootstrap`,
  `.prefect_server` (new), `infrahub-sync`.
- **Environment set by the image**: `PREFECT_HOME=/var/lib/infrahub-sync/prefect`,
  `TMPDIR=/tmp/infrahub-sync`, `PREFECT_UI_STATIC_DIRECTORY=/var/lib/infrahub-sync/prefect/ui` (new).
- **Rule**: an image without the `service` extra cannot start the Prefect server.

## Prefect server service

- **Name**: `prefect-server` (root, development), `sync-prefect` (preview).
- **State**: none of its own. PostgreSQL database `prefect` in the root and development stacks;
  SQLite under `PREFECT_HOME` in the preview stack, as today.
- **Credentials held**: Prefect database connection string (root and development), Prefect API
  credential (root).
- **Writable paths**: the three tmpfs scratch roots (root). The UI files go to
  `/var/lib/infrahub-sync/prefect/ui/v1` and `/v2`.
- **Lifecycle**: on each start it migrates the database, registers block types, copies the UI
  files, starts the background services, then serves.

## Object store image

- **Reference**: `pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46`.
- **Used by**: `object-store-init`, `object-store` (root); `object-store` (development);
  `sync-minio`, `sync-minio-bootstrap` (preview).
- **Run as**: UID and GID 65532 for the server; root only in the one-shot volume repair
  `object-store-init`, which has `network_mode: none`, a read-only root and only `CHOWN`.
- **Volume**: `object-store-data`, unchanged; data written by the current Chainguard build stays
  readable (research R5).

## Worker credential variable

- **Form**: `INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_<SETTING>`, from spec 012 FR-005 and FR-006, with
  the exception `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`.
- **Source of truth**: `credential_setting_paths` of each adapter in
  `infrahub_sync/configuration/capabilities.py:510-582`.
- **Value**: empty when not set in `.env`; the credential resolver refuses an empty value by name
  when a run selects it.
- **Receivers**: see [contracts/environment-variables.md](contracts/environment-variables.md).

## Custom CA directory and CA bundle

| Field | Value |
| --- | --- |
| Host setting | `INFRAHUB_SYNC_CA_CERTIFICATES`: absolute path or `./` path of a directory |
| Container path | `/etc/infrahub-sync/ca`, read-only, in `sync-api` and `sync-worker` |
| Placeholder | named volume `sync-ca-certificates`, empty, used when the setting is unset |
| Files read | `*.pem` and `*.crt`, in name order; other files are ignored |
| Bundle file | `$TMPDIR/infrahub-sync-ca-bundle.pem` (tmpfs), mode `0600` |
| Bundle content | `certifi.where()` content, then each operator file |
| Variables set | `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE`, each only when not already set |

Validation rules:

- each file must load with `ssl.SSLContext.load_verify_locations(cafile=...)`;
- a file that fails stops the process at start with an error that names the file;
- a directory with no matching file, or no directory, changes nothing.

States: **absent** (no directory or no file; nothing changes) → **installed** (bundle written,
variables set). There is no other state; the bundle is rebuilt at each process start.

## Prefect opt-in file

- **Name**: `prefect-ui.yml`, written by an OpsMill engineer beside `docker-compose.yml`, not
  shipped.
- **Content**: one `ports` entry on `prefect-server`, bound to `127.0.0.1`.
- **States**: **closed** (default; `docker compose up` without `-f prefect-ui.yml`) and **open**
  (`docker compose -f docker-compose.yml -f prefect-ui.yml up`). Each `up` decides the state.
