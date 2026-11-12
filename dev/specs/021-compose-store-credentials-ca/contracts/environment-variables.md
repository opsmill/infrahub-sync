# Contract: `.env` settings and which service receives each credential

Applies to the root `docker-compose.yml`, its packaged copy
`infrahub_sync/testcontainers/docker-compose.yml`, and `development/docker-compose.yml`. The
compose contract tests check every row.

## New `.env` settings

| Setting | Default | Values | Read by |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_S3_ENDPOINT_URL` | `http://object-store:9000` | absolute `http` or `https` URL, no user name or password | `sync-bootstrap`, `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` | `path` | `path`, `virtual` | `sync-bootstrap`, `sync-api`, `sync-worker` |
| `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` | `1` | `1` (run the bundled store), `0` (leave it out) | Compose (`scale` of `object-store-init` and `object-store`) |
| `INFRAHUB_SYNC_CA_CERTIFICATES` | unset (empty named volume `sync-ca-certificates`) | a host directory, absolute or starting with `./` | Compose (read-only mount at `/etc/infrahub-sync/ca` on `sync-bootstrap`, `sync-api`, `sync-worker`) |
| `INFRAHUB_SYNC_CREDENTIAL_<NAME>` for each name below | empty | the credential value | `sync-worker` (and `sync-api` for the Infrahub token) |

Existing settings `INFRAHUB_SYNC_S3_BUCKET`, `INFRAHUB_SYNC_S3_PREFIX`, `INFRAHUB_SYNC_S3_REGION`,
`INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY` keep their names, defaults and
receivers.

## Credential variables passed to `sync-worker`

Every name is passed as `"${INFRAHUB_SYNC_CREDENTIAL_<NAME>:-}"`. The prefix
`INFRAHUB_SYNC_CREDENTIAL_` is left out of the table.

| Adapter | Credential setting | Name | Source of the name |
| --- | --- | --- | --- |
| `aci` | `username` | `CISCO_APIC_USERNAME` | `examples/aci_to_infrahub/package.yml`, `docs/docs/adapters/aci.mdx` |
| `aci` | `password` | `CISCO_APIC_PASSWORD` | same |
| `genericrestapi` | `token` | `REST_API_TOKEN` | `docs/docs/adapters/genericrestapi.mdx` |
| `genericrestapi` | `username` | `REST_API_USERNAME` | added for the setting |
| `genericrestapi` | `password` | `REST_API_PASSWORD` | added for the setting |
| `genericrestapi` | `username` | `DEVICE42_USERNAME` | `docs/docs/adapters/device42.mdx` |
| `genericrestapi` | `password` | `DEVICE42_PASSWORD` | same |
| `genericrestapi` | `token` | `LIBRENMS_TOKEN` | `docs/docs/adapters/librenms.mdx` |
| `genericrestapi` | `username` | `OBSERVIUM_USERNAME` | `docs/docs/adapters/observium.mdx` |
| `genericrestapi` | `password` | `OBSERVIUM_PASSWORD` | same |
| `genericrestapi` | `token` | `PEERINGDB_API_KEY` | `docs/docs/adapters/peeringdb.mdx` |
| `infrahub` | `token` (destination) | `INFRAHUB_API_TOKEN` | every example package |
| `infrahub` | `token` (source) | `SOURCE_INFRAHUB_TOKEN` | `docs/docs/adapters/infrahub.mdx` |
| `ipfabricsync` | `auth` | `IPF_TOKEN` | `docs/docs/adapters/ipfabric.mdx` |
| `nautobot` | `token` | `NAUTOBOT_TOKEN` | existing |
| `netbox` | `token` | `NETBOX_TOKEN` | existing |
| `peeringmanager` | `token` | `PEERING_MANAGER_TOKEN` | `docs/docs/adapters/peering-manager.mdx` |
| `peeringmanager` | `username` | `PEERING_MANAGER_USERNAME` | added for the setting |
| `peeringmanager` | `password` | `PEERING_MANAGER_PASSWORD` | added for the setting |
| `prometheus` | `token` | `PROMETHEUS_TOKEN` | added for the setting |
| `prometheus` | `username` | `PROMETHEUS_USERNAME` | added for the setting |
| `prometheus` | `password` | `PROMETHEUS_PASSWORD` | added for the setting |
| `slurpitsync` | `api_key` | `SLURPIT_API_KEY` | `examples/slurpit_to_infrahub/package.yml` |
| `slurpitsync` | `token` | `SLURPIT_TOKEN` | added for the setting |

24 names. A contract test rebuilds the expected set from the capability registry, the example
packages and the adapter pages, and fails when the file differs.

## Receiver list

| Credential | `postgres` | `db-bootstrap` | `object-store-init` | `object-store` | `prefect-server` | `sync-bootstrap` | `sync-api` | `sync-worker` | `cli` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PostgreSQL administrator password | yes | yes | | | | | | | |
| Product database password (in the database URL) | | yes | | | | yes | yes | yes | |
| Prefect database password | | yes | | | yes | | | | |
| Prefect API credential | | | | | yes | yes | yes | yes | |
| S3 access key and secret key | | | | yes, only when the bundled store runs | | yes | yes | yes | |
| `INFRAHUB_API_TOKEN` | | | | | | | yes | yes | |
| Every other credential variable above | | | | | | | | yes | |
| Sync API client token | | | | | | | yes | | yes |

Rows other than the S3 keys and the credential variables describe today's file and do not change.
The plan does not change the `cli` service.
