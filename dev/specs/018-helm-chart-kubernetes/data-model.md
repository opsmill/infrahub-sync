# Data model: the values and the resources of the `infrahub-sync` chart

The chart stores no data of its own. Its model is the set of values an operator sets and the
Kubernetes resources those values produce. The values are specified in
[contracts/values.md](contracts/values.md); the resources and their properties in
[contracts/rendered-release.md](contracts/rendered-release.md).

## Resources of a default release

`<fullname>` is the release's full name, built as in `infrahub-mcp` (`_helpers.tpl`).

| Resource | Kind | From Compose service | Image | Keeps data |
| --- | --- | --- | --- | --- |
| `<fullname>-api` | Deployment, Service | `sync-api` | Sync | no |
| `<fullname>-worker` | Deployment | `sync-worker` | Sync | no |
| `<fullname>-prefect-server` | Deployment, Service | `prefect-server` | Sync | no |
| `<fullname>-bootstrap-<hash>` | Job | `sync-bootstrap` | Sync | no |
| `<fullname>-postgresql` | StatefulSet, Service, ConfigMap (init script) | `postgres`, `db-bootstrap` | PostgreSQL | yes |
| `<fullname>-object-store` | StatefulSet, Service | `object-store`, `object-store-init` | Silo | yes |
| `<fullname>` | Secret | the `.env` file | n/a | n/a |
| `<fullname>-worker-credentials` | Secret | the worker's credential variables | n/a | n/a |
| `<fullname>-ca` | ConfigMap | the CA directory | n/a | n/a |
| `<fullname>` | ServiceAccount | n/a | n/a | n/a |

Optional: an Ingress, or a Gateway and an HTTPRoute, for `<fullname>-api`.

Not created when turned off:

- `postgresql.enabled: false` removes the PostgreSQL StatefulSet, Service and ConfigMap, and the
  `pg_isready` init container of the Prefect server.
- `objectStore.enabled: false` removes the object store StatefulSet and Service.
- `credentials.existingSecret` set removes the `<fullname>` Secret.
- No worker credential value removes `<fullname>-worker-credentials`; no CA certificate value
  removes `<fullname>-ca`.

## Keys of the credentials Secret

The key names are the variable names of the Compose `.env` file.

| Key | Required | Read by |
| --- | --- | --- |
| `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` | always | Sync API |
| `INFRAHUB_SYNC_PREFECT_AUTH_STRING` | always | Prefect server, Sync API, worker, bootstrap |
| `INFRAHUB_SYNC_S3_ACCESS_KEY` | always | Sync API, worker, bootstrap, bundled object store |
| `INFRAHUB_SYNC_S3_SECRET_KEY` | always | Sync API, worker, bootstrap, bundled object store |
| `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD` | bundled PostgreSQL | PostgreSQL |
| `INFRAHUB_SYNC_PRODUCT_PASSWORD` | bundled PostgreSQL | PostgreSQL, Sync API, worker, bootstrap |
| `INFRAHUB_SYNC_PREFECT_PASSWORD` | bundled PostgreSQL | PostgreSQL, Prefect server |
| `INFRAHUB_SYNC_DATABASE_URL` | external PostgreSQL | Sync API, worker, bootstrap |
| `INFRAHUB_SYNC_PREFECT_DATABASE_URL` | external PostgreSQL | Prefect server |
| `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` | never | Sync API, worker |

## Order of start

```text
PostgreSQL (init script on an empty volume) ─┐
                                             ├─> Prefect server ─> bootstrap Job ─> Sync API
object store ────────────────────────────────┘                                  └─> worker
```

- Prefect server: init container waits for PostgreSQL (bundled only).
- Bootstrap Job: init container waits for the Prefect server; the Job retries on failure.
- Sync API and worker: init container waits until the deployment `infrahub-sync-service/run`
  exists in the work pool.
- `<hash>`: the first 8 characters of a SHA-256 over the image reference, the bootstrap's
  settings and the checksums of the Secret and ConfigMaps the chart creates.

## Validation rules (render time)

| Rule | Message names |
| --- | --- |
| A required credential is empty and `credentials.existingSecret` is empty | the value |
| `objectStore.enabled` is true and `s3.endpointUrl` is set | both values |
| `objectStore.enabled` is false and `s3.endpointUrl` is empty | both values |
| `s3.addressingStyle` is not empty, `path` or `virtual` | the value |
| A key of `worker.credentials` does not match `^INFRAHUB_SYNC_CREDENTIAL_[A-Za-z0-9_]+$` | the key |
| `postgresql.enabled` is false, `credentials.existingSecret` is empty, and a connection string value is empty | the value |
| `ingress.enabled` and `gatewayApi.httpRoute.enabled` are both true | both values |
