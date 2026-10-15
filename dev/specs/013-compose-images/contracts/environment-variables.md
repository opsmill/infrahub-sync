# Contract: Environment variables

## New and changed `.env` settings (root `docker-compose.yml`)

| Setting | Default | Meaning | Change |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_PREFECT_PORT` | `4200` | Host port of the Prefect UI and API when the opt-in file is used; also the port in `PREFECT_SERVER_UI_API_URL`. | Has no effect without the opt-in. |
| `INFRAHUB_SYNC_CA_CERTIFICATES` | unset | Host directory of PEM CA certificates that `sync-api` and `sync-worker` trust in addition to the public roots. Absolute path or a path that starts with `./`. | New. |
| `INFRAHUB_SYNC_CREDENTIAL_*` (17, below) | empty | Credentials a registered package references through `$credential` with provider `env`. | 14 new; 3 existing. |

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

## Environment per service (root file)

| Variable | Receivers |
| --- | --- |
| Prefect database URL (`PREFECT_SERVER_DATABASE_CONNECTION_URL`) and `INFRAHUB_SYNC_PREFECT_PASSWORD` | `prefect-server`; `db-bootstrap` (password only) |
| `PREFECT_SERVER_API_AUTH_STRING` | `prefect-server` |
| `PREFECT_API_AUTH_STRING` | `sync-bootstrap`, `sync-api`, `sync-worker` |
| `PREFECT_SERVER_UI_API_URL` = `http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}/api` | `prefect-server` |
| `PREFECT_SERVER_API_HOST=0.0.0.0`, `PREFECT_SERVER_API_PORT=4200`, `PREFECT_SERVER_ANALYTICS_ENABLED=false` | `prefect-server` |
| `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` | `sync-api`, `sync-worker` |
| The other 16 credential variables | `sync-worker` |
| `INFRAHUB_SYNC_CA_DIRECTORY=/etc/infrahub-sync/ca` | `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_API_URL`, `INFRAHUB_SYNC_API_TOKEN` | `cli` (unchanged) |

## Variables set by a process at start

Set by `infrahub_sync.service.trust_store` in `sync-api` and `sync-worker` when the CA directory
holds at least one certificate file, and inherited by flow-run child processes:

| Variable | Read by |
| --- | --- |
| `SSL_CERT_FILE` | `httpx` (IP Fabric, Prometheus), Python `ssl` default context |
| `REQUESTS_CA_BUNDLE` | `requests` (NetBox, Nautobot, ACI) |
| `INFRAHUB_TLS_CA_FILE` | Infrahub SDK `Config.tls_ca_file` |

## Variables set by the image

| Variable | Value |
| --- | --- |
| `PREFECT_UI_STATIC_DIRECTORY` | `/var/lib/infrahub-sync/prefect/ui` (new) |

## Removed

| Variable | Where |
| --- | --- |
| `PREVIEW_PREFECT_IMAGE_TAG` | `development/preview.env`, `tasks/preview.py`, tests |
| `PREVIEW_PREFECT_IMAGE_DIGEST` | same |

## Masking

All `INFRAHUB_SYNC_CREDENTIAL_*` names contain `CREDENTIAL`, which
`infrahub_sync/execution.py:139` treats as a secret name. No CA variable matches a secret name
shape, so paths are shown in messages.
