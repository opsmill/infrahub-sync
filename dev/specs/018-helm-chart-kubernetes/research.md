# Research: A Helm chart to run Infrahub Sync 3.x on Kubernetes

Paths that start with `infrahub-helm/` are in `opsmill/infrahub-helm` at `origin/stable`
(`25d87a8`, read on 2026-11-16). Other paths are in this repository at `e8f05899`.

## R-1. What each Sync process does when its dependencies are not ready

Findings, from the code:

- **Service bootstrap** (`infrahub_sync/service/bootstrap.py`). Order: object store settings and
  bucket, create-only check, work pool, Prefect deployment, product schema (`:304-353`). It does
  not wait for or retry a dependency: every error ends the process with exit code 1 (`:367-384`).
  A second run changes nothing except the check key, which it writes and deletes (`:198-220`).
- **Sync API** (`service/serve.py:60-109`). It connects to PostgreSQL at start and exits when the
  database cannot be reached. It creates the product schema itself, under an advisory lock
  (`product_store/store.py:417, 902-938`). It does not contact Prefect or the object store at
  start. It listens on port 8000; no setting changes the port (`serve.py:109`).
  `GET /version` needs no token and no dependency (`service/app.py:394-399`).
- **Worker** (`service/worker.py:554-571`). It starts with `create_pool_if_not_found=False`. When
  the work pool does not exist, it logs and keeps running. It has no health endpoint.
- **Prefect server** (`service/prefect_server.py:32-39`). Its lifespan runs the database migration.
  `/api/health` answers without the API credential.

Consequence: nothing in the image orders the start. The chart has to.

Update of 2026-11-16: since commits `adfbeef9` and `dfe60856`, the image takes part in the order.
The bootstrap writes a bootstrap record as its last step, and the Sync API and the worker check
it when they start (R-2, mechanism 3).

## R-2. How the chart orders the start

**Decision**: three mechanisms.

1. **Database bootstrap**: the bundled PostgreSQL runs the role and database script from
   `/docker-entrypoint-initdb.d` when it first initializes its volume. The script is the one in
   `docker-compose.yml:390-424`, with the shell `$$` written `$`. The PostgreSQL pod is not ready
   until the script has run.
2. **Service bootstrap**: a plain Kubernetes Job (not a Helm hook) named
   `<fullname>-bootstrap-<hash>`. The hash is the first 8 characters of a SHA-256 over what the
   bootstrap reads: the image reference, the settings it receives, and the checksums of the
   Secret and the ConfigMaps the chart creates. A change of any of them gives a new name, so the
   upgrade creates a new Job, and Helm deletes the Job of the previous name because it is no
   longer in the manifest. No Job is ever patched. An upgrade that changes none of them keeps the
   completed Job, which is equivalent to running it again because the bootstrap changes nothing
   on a converged deployment (R-1). The Job has `restartPolicy: OnFailure` and a `backoffLimit`,
   and one init container that waits for the Prefect server's `/api/health`.
3. **Bootstrap record and start check** (FR-007b, clarification of 2026-11-16). As its last
   step, after the product schema, the bootstrap writes a one-row table `bootstrap_record` in the
   product database with the Sync version, the work pool name and the completion time
   (`infrahub_sync/service/bootstrap.py`, `product_store/store.py`). A repeat for the same version
   and pool writes nothing. The Sync API and the worker read the record when they start, before
   the product schema step (`infrahub_sync/service/start_check.py`). When it is missing or names
   another version or pool, they log what they found and what they expected and exit with code 1,
   for example:

   ```text
   refusing to start: the bootstrap record names Sync version 3.0.0a5 and work pool infrahub-sync; expected Sync version 3.0.0a6 and work pool infrahub-sync. Run the bootstrap for this version.
   ```

   Kubernetes restarts them with back-off until the Job has written the record. The Sync API and
   the worker have no init container.

The Prefect server has an init container that waits for the bundled PostgreSQL with `pg_isready`,
from the PostgreSQL image. Nothing makes the Sync API or the worker wait for PostgreSQL, so on a
fresh install their restarts also include failures to reach the product database while
PostgreSQL starts.

**Rationale**:

- A pod restart runs the start check again, which only reads. It does not run the bootstrap
  (FR-008).
- The record names the version, so the check is exact on an upgrade to another Sync version,
  where a wait for the Prefect deployment passes at once because the previous release created it.
- A plain Job is created with the other resources of the release, so it works when PostgreSQL is
  part of the release.
- A name from a hash of the Job's inputs, rather than from `.Release.Revision`, also works for a
  tool that applies `helm template` output: such a tool renders the revision as 1 every time, so
  a revision-named Job would keep its name, get a new pod template after an image change, and be
  refused because a Job's template is immutable.
- Nothing needs access to the Kubernetes API, so the chart creates no Role.

**Limit**: the record covers the Sync version and the work pool name, and no other bootstrap
setting, such as the bucket. On an upgrade that changes another setting at the same version, the
Job gets a new name and runs again, but the record already matches, so the new Sync API and worker
pods can start before that Job completes. The end-to-end test measured this: after its upgrade,
which changes only `s3.prefix`, the new pods restarted 0 times ([verification.md](verification.md),
T046 and T047).

**Measured on minikube** ([verification.md](verification.md), T046 and T047), one run each:

- Fresh install: the Sync API restarted 3 times and was ready 23 s after the Job completed; the
  worker restarted 2 times. One of the worker's failures was an unreachable product database.
- Upgrade to an image of another version: the new Sync API pod restarted 2 times and was ready
  5 s after the new Job completed. The previous pod answered `/version` throughout.
- The end-to-end test passed in 126 s.
- Observed and not investigated: on the upgrade, the previous worker did not stop on `SIGTERM` and
  was killed at the end of its 30-second grace period.

**Alternatives considered**:

- **Helm hook `post-install,pre-upgrade`**, as `infrahub-helm/charts/infrahub/templates/infrahub-upgrade-job.yaml`
  does. Rejected for three reasons. On install the Sync API and worker serve before the hook
  runs. On upgrade the hook runs before Helm applies the new Secret and ConfigMap, so it runs
  with the previous credentials. A hook is not part of the release, so `helm uninstall` and
  `helm rollback` do not manage it.
- **Bootstrap as an init container of the Sync API and worker**. Rejected: it runs on every
  restart, which the Compose file rules out (`docker-compose.yml:218-222`).
- **Init container that waits for the Job through the Kubernetes API**. Rejected: it needs a
  Role, a RoleBinding, a service account token in the pod and a `kubectl` image.
- **Database bootstrap as a Job**, as in Compose. Rejected: the Prefect server would need a
  second gate. The cost of the init script is that it runs only on an empty volume; the
  documentation states it.
- **Job named after `.Release.Revision`**. Rejected for the reason above.
- **Init container on the Sync API and the worker that waits for the Prefect deployment**
  `infrahub-sync-service/run` in the configured work pool, read with the Prefect client. This was
  the first design, and the chart had it until commit `e2e19ed` of `infrahub-helm`. Replaced by
  the bootstrap record: on an upgrade, the deployment of the previous release already exists, so
  the wait ended at once and the new pods could start before the new Job completed (clarification
  of 2026-11-16).

**Details of the init script**: the PostgreSQL entrypoint runs an executable `*.sh` file from
`/docker-entrypoint-initdb.d` and *sources* one that is not executable
(`docker-entrypoint.sh:180-187` in `postgres:16-alpine`), so the script is mounted with mode
`0555`. During initialization the server listens only on its local socket
(`docker-entrypoint.sh:297`), so the container sets `PGUSER` and `PGDATABASE` to the
administrator role and does not set `PGHOST`.

**Update strategies**: the Prefect server runs its database migration when it starts
(`service/prefect_server.py:36-37`), so its Deployment uses `Recreate` and two servers never run
at once. The worker also uses `Recreate`, to keep the one worker that is the only tested value.
The Sync API uses `RollingUpdate`; its schema initialization runs under an advisory lock
(`product_store/store.py:417`).

**Service links**: every pod sets `enableServiceLinks: false`. Otherwise Kubernetes adds
`<SERVICE>_PORT` and `<SERVICE>_SERVICE_HOST` variables for each Service of the namespace, which
gives `INFRAHUB_SYNC_API_*` variables for a release named `infrahub-sync`, and `PREFECT_SERVER_*`
variables when a Prefect Service named `prefect-server` shares the namespace.

## R-3. The bundled object store and the bootstrap's own check

`bootstrap.py:109-136` decides whether the bundled store runs by resolving the absolute DNS name
`object-store.`. In Kubernetes that name never resolves, so the bootstrap always concludes that
the bundled store is left out, and it refuses an endpoint whose host is exactly `object-store`.
No Python code reads `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE`; only Compose does.

**Decision**: the chart's object store Service is named `<fullname>-object-store`, never
`object-store`. The chart itself enforces FR-020 at render time with `fail`: the endpoint value
must be empty when `objectStore.enabled` is true and set when it is false.

`storage.py:158-166` refuses an optional S3 setting that is set to an empty string, so the chart
leaves an unset optional variable out of the pod instead of passing `""`.

## R-4. Credentials

**Decision**: one Secret holds the deployment's credentials. Its keys are the variable names of
the Compose `.env` file (`docs/docs/compose-deployment.mdx:98-117`), so an operator can create it
with `kubectl create secret generic <name> --from-env-file=.env`. The operator either names an
existing Secret (`credentials.existingSecret`) or sets the values, from which the chart creates
the Secret. Each container reads only the keys it needs, with `secretKeyRef`.

- The connection strings of the bundled PostgreSQL are built in the pod environment from the
  password variable (`$(VAR)` expansion), so no template renders a password. As in Compose, such
  a password must be URL-safe.
- With an external PostgreSQL the Secret holds the two whole connection strings
  (`INFRAHUB_SYNC_DATABASE_URL`, `INFRAHUB_SYNC_PREFECT_DATABASE_URL`).
- With values, a missing required credential stops the render with `required`. With an existing
  Secret the chart cannot read the keys at render time; Kubernetes then reports the missing key
  on the pod (`CreateContainerConfigError`). The specification is corrected to say so (FR-012).
- Worker credentials: the code accepts any variable that matches
  `^INFRAHUB_SYNC_CREDENTIAL_[A-Za-z0-9_]+$` (`configuration/worker_credentials.py:24`). The fixed
  list in Compose exists because Compose can pass only named variables. The chart passes a map of
  values and a list of existing Secrets to the worker with `envFrom`, and refuses a key that does
  not match the pattern. The destination credential `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`
  is an optional key of the main Secret, given to the Sync API and the worker.

**Alternatives considered**: one value and one Secret per credential (more values, no gain);
default passwords as the `infrahub` chart has (ruled out by ADR 16 and the constitution).

## R-5. PostgreSQL and the object store

No chart in `infrahub-helm` holds its own PostgreSQL; the `infrahub` chart gets one through the
upstream `prefect-server` chart and Bitnami.

**Decision**: two StatefulSets written in the chart, each with one replica and a
`volumeClaimTemplate`, from the images that Compose pins by digest
(`docker-compose.yml:76, 120-121`). PostgreSQL runs as uid 70 with `fsGroup: 70`; the object store
runs as 65532 with `fsGroup: 65532`, which replaces the `object-store-init` ownership job.
`helm uninstall` does not delete the claims of a `volumeClaimTemplate`, so the data survives an
uninstall until the operator deletes the claims.

**Alternatives considered**: the Bitnami `postgresql` chart as a dependency. Rejected: FR-005
asks for the digest-pinned images of the Compose file, the chart needs two roles and two
databases that the Bitnami chart does not create without an init script anyway, and a dependency
adds a registry pull to every lint.

## R-6. Versions

- The chart `version` starts at `0.1.0`. `appVersion` is the Sync release, `3.0.0a5` today: the
  package version in `pyproject.toml`, which is also the pin of the Compose file (ADR 16). A
  chart version must be SemVer and `3.0.0a5` is not, so the two cannot be the same string.
- On 2026-11-16 the registry answered "artifact opsmill/infrahub-sync:3.0.0a5 not found" for this
  machine's login, while `registry.opsmill.io/opsmill/infrahub:1.11.12` resolved. The Prefect
  server moved into the Sync image after `3.0.0a5` was cut
  (`changelog/+prefect-from-sync-image.changed.md`), so an older image would not run this chart
  either. **The chart is therefore verified with an image built from this repository, and it is
  published only after the first Sync release that contains this code is in the registry.**
- `infrahub-helm/scripts/bump_chart.py app-version` sets `appVersion`, bumps the chart version and
  adds a changelog fragment. It accepts only final `MAJOR.MINOR.PATCH` versions.
  `infrahub-helm/.github/workflows/update-infrahub-mcp.yml` runs it when `opsmill/infrahub-mcp`
  sends a `repository_dispatch` event.

**Decision**: add `update-infrahub-sync.yml` to `infrahub-helm`, a copy of the infrahub-mcp
workflow for the event `infrahub-sync-released`. It works for final releases, so from 3.0.0.
Until then a maintainer sets `appVersion` by hand. Sending the event from this repository's
release workflow needs a token with access to `infrahub-helm`; whether that secret exists is not
known, so the sending step is a follow-up and the workflow can be run by hand.

The documentation tells the operator to read the Sync release from the chart's `appVersion`
(`helm show chart`).

## R-7. Tests, and the restricted image

- `infrahub-helm` tests charts in a throwaway vcluster with pytest (`infrahub-helm/tests/e2e`),
  one marker per chart, and has a `unit` job in CI. Images are pulled without credentials, and
  the harness has no image pull secret.
- The Sync image is restricted to invited testers until 3.0.0.

**Decision**:

- **Render tests** (no cluster): pytest tests that run `helm template` and assert the contract in
  [contracts/rendered-release.md](contracts/rendered-release.md). They run in CI for every change
  to the chart.
- **End-to-end test** with marker `sync`: install, wait for the bootstrap Job with
  `kubectl wait --for=condition=complete`, call `/version` and `/whoami`, read the worker state
  from `/status`, upgrade with a changed setting, wait for the new Job, check again. The test
  takes the image from `INFRAHUB_SYNC_E2E_IMAGE` when set, and registry credentials from
  `HARBOR_USERNAME` and `HARBOR_PASSWORD` when set. Before it
  installs, it checks that the image can be resolved. When it cannot and no credential is given,
  the test is skipped with a message that says why. Once the image resolves, a pod that does not
  become ready fails the test; it is never a skip. The CI job exists from the start and reports a
  skip until the image is public or the repository has the credentials.
- **Lint**: `helm lint` with no values fails on the required credentials, and both
  `infrahub-helm/Makefile:23-28` and the `helm-lint-*` CI jobs lint with no values. The chart
  holds `ci/lint-values.yaml`, which names an existing Secret; `.helmignore` keeps `ci/` out of
  the package; the Makefile loop and the CI job pass the file when it exists. It is not named
  `values.<x>.yaml` at the chart root, because `package-helm-chart.yml:94` publishes each such
  file as a chart flavor.
- **Local verification** for this specification: install on a local cluster with the image
  pulled with this machine's registry login, or built from this repository's `Dockerfile`.

## R-8. Open points recorded, not resolved here

- Whether the chart runs on OpenShift: not known, not tested.
- More than one replica of the Sync API or the worker: no document states it is supported, so
  the chart fixes neither at one but documents one as the only tested value.
- Real AWS S3 without an endpoint: the chart requires an endpoint when the bundled store is off,
  as the Compose documentation does. Whether Sync is tested against AWS S3 is not known.
