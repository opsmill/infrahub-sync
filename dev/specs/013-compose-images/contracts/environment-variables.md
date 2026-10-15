# Contract: Environment variables

## New and changed `.env` settings (root `docker-compose.yml`)

| Setting | Default | Meaning | Change |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_PREFECT_PORT` | `4200` | Host port of the Prefect UI and API when the opt-in file is used; also the port in `PREFECT_SERVER_UI_API_URL`. | Has no effect without the opt-in. |
| `INFRAHUB_SYNC_S3_ENDPOINT_URL` | `http://object-store:9000` | The S3-compatible endpoint. Absolute `http` or `https` URL, no user name or password. | Was fixed in the file; now read from `.env`. |
| `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` | `path` | `path` or `virtual`: how requests name the bucket. | New. |
| `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` | `1` | `1` starts the bundled MinIO; `0` leaves it out. | New. |
| `INFRAHUB_SYNC_CA_CERTIFICATES` | unset | Host directory of PEM CA certificates that `sync-bootstrap`, `sync-api` and `sync-worker` trust in addition to the public roots. Absolute path or a path that starts with `./`. | New. |
| `INFRAHUB_SYNC_CREDENTIAL_*` (17, below) | empty | Credentials a registered package references through `$credential` with provider `env`. | 14 new; 3 existing. |

Unchanged: `INFRAHUB_SYNC_S3_BUCKET`, `INFRAHUB_SYNC_S3_PREFIX`, `INFRAHUB_SYNC_S3_REGION`,
`INFRAHUB_SYNC_S3_ACCESS_KEY`, `INFRAHUB_SYNC_S3_SECRET_KEY`. Their meaning is the same for the
bundled and an external store. See [object-store.md](object-store.md).

`INFRAHUB_SYNC_BIND_ADDRESS` keeps its meaning for the `sync-api` port. It no longer affects the
Prefect server.

## Credential variables of the bundled adapters

Derived from `credential_setting_paths` in `infrahub_sync/configuration/capabilities.py:510-582`
and the naming rule of spec 012 FR-006.

| Adapter | Setting | Variable |
| --- | --- | --- |
| `aci` | `username` | `INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME` |
| `aci` | `password` | `INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD` |
| `genericrestapi` | `token` | `INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_TOKEN` |
| `genericrestapi` | `username` | `INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_USERNAME` |
| `genericrestapi` | `password` | `INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_PASSWORD` |
| `infrahub` | `token` | `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` (existing) |
| `ipfabricsync` | `auth` | `INFRAHUB_SYNC_CREDENTIAL_IPFABRICSYNC_AUTH` |
| `nautobot` | `token` | `INFRAHUB_SYNC_CREDENTIAL_NAUTOBOT_TOKEN` (existing) |
| `netbox` | `token` | `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN` (existing) |
| `peeringmanager` | `token` | `INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_TOKEN` |
| `peeringmanager` | `username` | `INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_USERNAME` |
| `peeringmanager` | `password` | `INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_PASSWORD` |
| `prometheus` | `token` | `INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_TOKEN` |
| `prometheus` | `username` | `INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_USERNAME` |
| `prometheus` | `password` | `INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_PASSWORD` |
| `slurpitsync` | `api_key` | `INFRAHUB_SYNC_CREDENTIAL_SLURPITSYNC_API_KEY` |
| `slurpitsync` | `token` | `INFRAHUB_SYNC_CREDENTIAL_SLURPITSYNC_TOKEN` |

A package that names any other identifier does not receive a value on the Compose deployment.
The documentation says so.

## Receivers of each credential (root file)

The contract test checks that each row's receiver set is exact: a credential reaches these
services and no other.

| Credential (`.env` setting) | As | Receivers |
| --- | --- | --- |
| `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` | `POSTGRES_PASSWORD`, `PGPASSWORD` | `postgres`, `db-bootstrap` |
| `INFRAHUB_SYNC_PRODUCT_PASSWORD` | `INFRAHUB_SYNC_DATABASE_URL`, and itself in `db-bootstrap` | `db-bootstrap`, `sync-bootstrap`, `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_PREFECT_PASSWORD` | `PREFECT_SERVER_DATABASE_CONNECTION_URL`, and itself in `db-bootstrap` | `db-bootstrap`, `prefect-server` |
| `INFRAHUB_SYNC_PREFECT_AUTH_STRING` | `PREFECT_SERVER_API_AUTH_STRING`, `PREFECT_API_AUTH_STRING` | `prefect-server`, `sync-bootstrap`, `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_S3_ACCESS_KEY`, `INFRAHUB_SYNC_S3_SECRET_KEY` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`; `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | `sync-bootstrap`, `sync-api`, `sync-worker`; `object-store` only while `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=1` |
| `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` | itself | `sync-api` |
| `INFRAHUB_SYNC_API_TOKEN` | itself | `cli` |
| `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` | itself | `sync-api`, `sync-worker` |
| The other 16 `INFRAHUB_SYNC_CREDENTIAL_*` variables | themselves | `sync-worker` |

`object-store-init` runs with `network_mode: none` and receives no credential.

## Other environment per service (root file)

| Variable | Receivers |
| --- | --- |
| `PREFECT_SERVER_UI_API_URL` = `http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}/api` | `prefect-server` |
| `PREFECT_SERVER_API_HOST=0.0.0.0`, `PREFECT_SERVER_API_PORT=4200`, `PREFECT_SERVER_ANALYTICS_ENABLED=false` | `prefect-server` |
| `INFRAHUB_SYNC_S3_ENDPOINT_URL`, `INFRAHUB_SYNC_S3_ADDRESSING_STYLE`, `INFRAHUB_SYNC_S3_BUCKET`, `INFRAHUB_SYNC_S3_PREFIX`, `INFRAHUB_SYNC_S3_REGION` | `sync-bootstrap`, `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_CA_DIRECTORY=/etc/infrahub-sync/ca` | `sync-bootstrap`, `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_API_URL` | `cli` (unchanged) |

## Variables set by a process at start

Set by `infrahub_sync.service.trust_store` in `sync-bootstrap`, `sync-api` and `sync-worker`
when the CA directory holds at least one certificate file, each only when not already set, and
inherited by flow-run child processes:

| Variable | Read by |
| --- | --- |
| `SSL_CERT_FILE` | `httpx` (IP Fabric, Prometheus), Python `ssl` default context |
| `REQUESTS_CA_BUNDLE` | `requests` (NetBox, Nautobot, ACI); botocore fallback |
| `AWS_CA_BUNDLE` | botocore (object store) |
| `INFRAHUB_TLS_CA_FILE` | Infrahub SDK `Config.tls_ca_file` |

All four hold a file path, not a secret.

## Variables set by the image

| Variable | Value |
| --- | --- |
| `PREFECT_UI_STATIC_DIRECTORY` | `/var/lib/infrahub-sync/prefect/ui` (new) |

## Removed

| Variable | Where |
| --- | --- |
| `PREVIEW_PREFECT_IMAGE_TAG` | `development/preview.env`, `tasks/preview.py`, tests |
| `PREVIEW_PREFECT_IMAGE_DIGEST` | same |

## Keeping values out of text

No row above relies on masking. The receiver table limits where a value exists; research R13
lists how each place that produces text stays free of credential values.
