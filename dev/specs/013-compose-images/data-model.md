# Data model: Fewer images in the Compose deployment

This change adds no database table, no API field and no stored record. The entities below are
deployment entities: what the Compose files declare and what a process builds at start. The
only object Sync writes for this change is the short-lived create-only check key, deleted by the
same bootstrap run.

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
  credential (root). Nothing else.
- **Writable paths**: the three tmpfs scratch roots (root). The UI files go to
  `/var/lib/infrahub-sync/prefect/ui/v1` and `/v2`.
- **Lifecycle**: on each start it migrates the database, registers block types, copies the UI
  files, starts the background services, then serves.

## Bundled object store

- **Reference**: `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`
  (unchanged; multi-architecture index for linux/amd64 and linux/arm64).
- **Used by**: `object-store-init`, `object-store` (root); `object-store` (development);
  `sync-minio`, `sync-minio-bootstrap` (preview).
- **Run as**: UID and GID 65532 for the server; root only in the one-shot volume repair
  `object-store-init`, which has `network_mode: none`, a read-only root and only `CHOWN`.
- **Volume**: `object-store-data`, unchanged.
- **States** (root file): **running** (`INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` unset or `1`) and
  **left out** (`0`; no container, the volume stays on the host until removed).

## Object store settings

Read by `build_s3_client` in `infrahub_sync/service/storage.py` in `sync-bootstrap`, `sync-api`
and `sync-worker`:

| Field | Source | Validation |
| --- | --- | --- |
| endpoint | `INFRAHUB_SYNC_S3_ENDPOINT_URL` | absolute `http` or `https` URL without user name or password (`storage.py:193-205`) |
| region | `INFRAHUB_SYNC_S3_REGION` | optional |
| addressing style | `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` | `path` or `virtual`; default `path` |
| bucket | `INFRAHUB_SYNC_S3_BUCKET` | required (`storage.py:218`) |
| prefix | `INFRAHUB_SYNC_S3_PREFIX` | default `infrahub-sync`, `/` stripped |
| credential | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | read by boto3 only; never formatted into text |

A validation failure raises a fixed message that names the setting and not its value.

## Bootstrap object store convergence

Steps, in order, each ending in success or one fixed failure family:

1. guard: external endpoint while `object-store` resolves → `bundled-object-store-not-left-out`
2. build the client → `object-store-unavailable` on failure
3. `HeadBucket`: 2xx present; 404 absent → `CreateBucket`; 403 → `object-store-access-refused`;
   other → `object-store-unavailable`
4. create-only check on `<prefix>/.infrahub-sync/bootstrap-check/<uuid4>`: second write refused →
   continue; accepted → `object-store-unsupported`; the key is deleted on every path

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
| Container path | `/etc/infrahub-sync/ca`, read-only, in `sync-bootstrap`, `sync-api` and `sync-worker` |
| Placeholder | named volume `sync-ca-certificates`, empty, used when the setting is unset |
| Files read | `*.pem` and `*.crt`, in name order; other files are ignored |
| Bundle file | `$TMPDIR/infrahub-sync-ca-bundle.pem` (tmpfs), mode `0600` |
| Bundle content | `certifi.where()` content, then each operator file |
| Variables set | `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE`, each only when not already set |

Validation rules:

- a file that contains a `PRIVATE KEY` PEM block stops the process; the error names the file and
  holds none of its content;
- each file must load with `ssl.SSLContext.load_verify_locations(cafile=...)`; a file that fails
  stops the process with an error that names the file and the `ssl` error class;
- a directory with no matching file, or no directory, changes nothing.

States: **absent** (no directory or no file; nothing changes) → **installed** (bundle written,
variables set). The bundle is rebuilt at each process start.

## Prefect opt-in file

- **Name**: `prefect-ui.yml`, written by an OpsMill engineer beside `docker-compose.yml`, not
  shipped.
- **Content**: one `ports` entry on `prefect-server`, bound to `127.0.0.1`. No credential.
- **States**: **closed** (default; `docker compose up` without `-f prefect-ui.yml`) and **open**
  (`docker compose -f docker-compose.yml -f prefect-ui.yml up`). Each `up` decides the state.
