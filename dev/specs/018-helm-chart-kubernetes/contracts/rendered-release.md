# Contract: properties of a rendered release

Each property is checked by a render test (`helm template`, no cluster) in `infrahub-helm`. They
are the Kubernetes form of the properties that `tests/compose/test_compose_contract.py` holds for
the Compose file. "Sync pods" means the Sync API, the worker, the Prefect server and the
bootstrap Job.

## Default release

- **RR-01**: The release holds exactly these workloads: Deployments `api`, `worker` and
  `prefect-server`; StatefulSets `postgresql` and `object-store`; one Job `bootstrap-<revision>`.
- **RR-02**: Three image repositories appear: Sync, PostgreSQL and Silo. Every Sync container,
  init containers included, uses `image.repository` with `image.tag`, or `appVersion` when the
  tag is empty. No Prefect image appears.
- **RR-03**: The PostgreSQL and Silo images are referenced by digest.
- **RR-04**: `imagePullSecrets` appears in every pod when set.
- **RR-05**: Every Sync container sets `readOnlyRootFilesystem: true`,
  `allowPrivilegeEscalation: false`, drops all capabilities, and runs as 10001:10001 non-root.
- **RR-06**: The only volumes of a Sync pod are three memory-backed `emptyDir` volumes at
  `/var/lib/infrahub-sync`, `/var/lib/infrahub-sync/prefect` and `/tmp/infrahub-sync`, and the CA
  ConfigMap when one is set. No Sync pod mounts a claim, a host path or a Secret volume.
- **RR-07**: The Sync API and the worker share no volume source.
- **RR-08**: Only the two StatefulSets have a `volumeClaimTemplate`.
- **RR-09**: No pod mounts a service account token.
- **RR-10**: No rendered manifest holds a credential value when `credentials.existingSecret` is
  set. No ConfigMap and no container `env` `value` ever holds one: every credential reaches a
  container through `secretKeyRef` or `envFrom.secretRef`.
- **RR-11**: The Prefect API credential reaches the Prefect server, the Sync API, the worker and
  the bootstrap Job, and nothing else.
- **RR-12**: The Prefect database password or connection string reaches only PostgreSQL and the
  Prefect server.
- **RR-13**: The API principals (`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`) reach only the Sync API.
- **RR-14**: `worker.credentials` and `worker.credentialsExistingSecrets` reach only the worker.
  The destination credential reaches only the Sync API and the worker, and it is optional.
- **RR-15**: The Prefect server runs
  `uvicorn --host 0.0.0.0 --port 4200 --factory infrahub_sync.service.prefect_server:create_prefect_app`,
  with `PREFECT_UI_ENABLED=false`, `PREFECT_SERVER_ANALYTICS_ENABLED=false` and the API
  credential set.
- **RR-16**: The worker runs `python -m infrahub_sync.service.worker --pool <settings.workPool>`,
  and the Sync API, the worker and the bootstrap Job get the same pool name.
- **RR-17**: The Sync API and the Prefect server have readiness and liveness probes, on
  `/version` (port 8000) and `/api/health` (port 4200).
- **RR-18**: Every Service has type `ClusterIP`. No Ingress, Gateway or HTTPRoute is rendered.
- **RR-19**: The Sync API and the worker each have an init container that waits for the
  bootstrap result. The bootstrap is not an init container of any pod.
- **RR-20**: The S3 endpoint is `http://<fullname>-object-store:9000`, and its host is never
  exactly `object-store`.
- **RR-21**: The CA mount and `INFRAHUB_SYNC_CA_DIRECTORY` appear only in the Sync API, the
  worker and the bootstrap Job, and only when a CA value is set.
- **RR-22**: An optional setting that is not set is absent from the pod environment; it is not
  passed as an empty string.

## Variants

- **RR-30**: With `postgresql.enabled: false`: no PostgreSQL workload, Service or ConfigMap; the
  two connection strings come from the Secret; no container reads a PostgreSQL password key.
- **RR-31**: With `objectStore.enabled: false` and an endpoint: no object store workload or
  Service; the endpoint and addressing style are the values given.
- **RR-32**: With both off: zero `volumeClaimTemplate`.
- **RR-33**: With the Ingress on: one Ingress, whose only backend is the Sync API Service. With
  the HTTPRoute on: the same for the route.
- **RR-34**: Two renders with different release revisions give different Job names.

## Refusals

Each render fails with a message that names the value:

- **RR-40**: each required credential of FR-012 empty, without an existing Secret.
- **RR-41**: `objectStore.enabled: true` with `s3.endpointUrl` set.
- **RR-42**: `objectStore.enabled: false` with `s3.endpointUrl` empty.
- **RR-43**: a `worker.credentials` key that does not start with `INFRAHUB_SYNC_CREDENTIAL_`.
- **RR-44**: `postgresql.enabled: false` without an existing Secret and without the two
  connection strings.
- **RR-45**: an `s3.addressingStyle` other than `path` or `virtual`.
