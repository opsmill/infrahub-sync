# Contract: the values of the `infrahub-sync` chart

This is the interface an operator sets. Each value is described in `values.yaml` with a `# --`
comment. Defaults are shown; `""` means "not set".

## Image

| Value | Default | Meaning |
| --- | --- | --- |
| `image.repository` | `registry.opsmill.io/opsmill/infrahub-sync` | The Sync image, for the Sync API, the worker, the Prefect server and the bootstrap Job |
| `image.tag` | `""` | The image tag. Empty means the chart's `appVersion` |
| `image.pullPolicy` | `IfNotPresent` | |
| `imagePullSecrets` | `[]` | Applied to every pod of the release |
| `nameOverride`, `fullnameOverride` | `""` | As in `infrahub-mcp` |

## Credentials

| Value | Default | Secret key |
| --- | --- | --- |
| `credentials.existingSecret` | `""` | Name of a Secret that holds the keys below. When set, the values below are not read |
| `credentials.bearerTokens` | `""` | `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` (a JSON object of principals) |
| `credentials.prefectAuthString` | `""` | `INFRAHUB_SYNC_PREFECT_AUTH_STRING` |
| `credentials.s3AccessKey` | `""` | `INFRAHUB_SYNC_S3_ACCESS_KEY` |
| `credentials.s3SecretKey` | `""` | `INFRAHUB_SYNC_S3_SECRET_KEY` |
| `credentials.postgresAdminPassword` | `""` | `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` |
| `credentials.productPassword` | `""` | `INFRAHUB_SYNC_PRODUCT_PASSWORD` |
| `credentials.prefectPassword` | `""` | `INFRAHUB_SYNC_PREFECT_PASSWORD` |
| `credentials.databaseUrl` | `""` | `INFRAHUB_SYNC_DATABASE_URL` (external PostgreSQL only) |
| `credentials.prefectDatabaseUrl` | `""` | `INFRAHUB_SYNC_PREFECT_DATABASE_URL` (external PostgreSQL only) |
| `credentials.infrahubApiToken` | `""` | `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` (optional) |

## Settings

| Value | Default | Variable |
| --- | --- | --- |
| `settings.requireApproval` | `false` | `INFRAHUB_SYNC_REQUIRE_APPROVAL` |
| `settings.runAdmissionTtlSeconds` | `300` | `INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS` |
| `settings.workPool` | `infrahub-sync` | `INFRAHUB_SYNC_SERVICE_WORK_POOL` and the worker's `--pool` |
| `settings.workerQuerySeconds` | `10` | `PREFECT_WORKER_QUERY_SECONDS` |
| `settings.workerHeartbeatSeconds` | `10` | `PREFECT_WORKER_HEARTBEAT_SECONDS` |
| `settings.prefectUiUrl` | `""` | `INFRAHUB_SYNC_PREFECT_UI_URL` |

## Object store

| Value | Default | Meaning |
| --- | --- | --- |
| `objectStore.enabled` | `true` | Deploy the bundled store |
| `objectStore.image.repository`, `.tag`, `.digest` | the Silo image and digest of `docker-compose.yml` | |
| `objectStore.persistence.size` | `10Gi` | |
| `objectStore.persistence.storageClass` | `""` | Empty means the cluster default |
| `objectStore.resources`, `.nodeSelector`, `.tolerations`, `.affinity`, `.podAnnotations`, `.podLabels` | empty | |
| `s3.bucket` | `infrahub-sync` | `INFRAHUB_SYNC_S3_BUCKET` |
| `s3.prefix` | `infrahub-sync` | `INFRAHUB_SYNC_S3_PREFIX` |
| `s3.region` | `us-east-1` | `INFRAHUB_SYNC_S3_REGION` |
| `s3.endpointUrl` | `""` | `INFRAHUB_SYNC_S3_ENDPOINT_URL`. Required when `objectStore.enabled` is false; refused when it is true |
| `s3.addressingStyle` | `""` | `INFRAHUB_SYNC_S3_ADDRESSING_STYLE`: `path` or `virtual`. Empty means `path` |

## PostgreSQL

| Value | Default | Meaning |
| --- | --- | --- |
| `postgresql.enabled` | `true` | Deploy the bundled PostgreSQL |
| `postgresql.image.repository`, `.tag`, `.digest` | the PostgreSQL image and digest of `docker-compose.yml` | |
| `postgresql.adminRole` | `postgres` | |
| `postgresql.productRole`, `postgresql.productDatabase` | `infrahub_sync` | |
| `postgresql.prefectRole`, `postgresql.prefectDatabase` | `prefect` | |
| `postgresql.persistence.size` | `10Gi` | |
| `postgresql.persistence.storageClass` | `""` | |
| `postgresql.resources`, `.nodeSelector`, `.tolerations`, `.affinity`, `.podAnnotations`, `.podLabels` | empty | |

## CA certificates

| Value | Default | Meaning |
| --- | --- | --- |
| `caCertificates.existingConfigMap` | `""` | A ConfigMap of `*.pem` or `*.crt` files |
| `caCertificates.certificates` | `{}` | File name to PEM content; the chart creates the ConfigMap |

Mounted read-only at `/etc/infrahub-sync/ca` in the Sync API, the worker and the bootstrap Job,
with `INFRAHUB_SYNC_CA_DIRECTORY` set to that path. It holds public certificates only: the image
refuses to start when a file in the directory holds a private key.

## Components

For each of `api`, `worker`, `prefectServer` and `bootstrap`: `resources`, `nodeSelector`,
`tolerations`, `affinity`, `podAnnotations`, `podLabels`, `extraEnv`.

| Value | Default | Meaning |
| --- | --- | --- |
| `api.replicaCount` | `1` | One is the only tested value |
| `api.service.type` | `ClusterIP` | |
| `api.service.port` | `8000` | |
| `worker.replicaCount` | `1` | One is the only tested value |
| `worker.credentials` | `{}` | `INFRAHUB_SYNC_CREDENTIAL_<NAME>` to value; the chart creates a Secret |
| `worker.credentialsExistingSecrets` | `[]` | Secrets whose keys are passed to the worker as variables |
| `prefectServer.uiEnabled` | `false` | `PREFECT_UI_ENABLED`. The chart offers no route to the UI |
| `bootstrap.backoffLimit` | `6` | |
| `waitTimeoutSeconds` | `600` | How long the init containers of the Prefect server and the bootstrap Job wait before they fail |
| `serviceAccount.create`, `.name`, `.annotations` | `true`, `""`, `{}` | The token is not mounted in any pod |
| `podSecurityContext`, `securityContext` | non-root 10001, read-only root, no capabilities | For the Sync containers |

## Exposure

`ingress.*` and `gatewayApi.*` have the shape of the same blocks in `infrahub-mcp`, and both
default to off. They route to the Sync API only.
