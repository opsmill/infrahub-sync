# Feature Specification: Fewer images in the Compose deployment

**Feature Branch**: `013-compose-images`

**Created**: 2026-10-15

**Revised**: 2026-10-15, after the product owner's review of the first plan (see
"Product owner decisions of 2026-10-15")

**Status**: Draft

**Release**: MVP (the testing release). Every requirement in this specification belongs to the MVP.
Work it names as out of scope belongs to 3.0 or 3.x, as listed in
[V3 release scope](../v3-release-scope.md).

**Input**: User description: "Fewer images in the Compose deployment for the V3 testing release. (1) Prefect server from the Sync image: every compose file (root docker-compose.yml operator deployment, development/docker-compose.dev.yml, the preview stack development/docker-compose.preview.yml) runs the Prefect server from the Sync image instead of the stock prefecthq/prefect:3.8.6 image, as a separate service, through a factory module (for example infrahub_sync.service.prefect_server, wrapping Prefect's create_app) started with uvicorn. This follows Infrahub's pattern: since commit 7a22217bf (2025-01-22, 'use infrahub image to run prefect server', reason: avoid client/server version incompatibility and CI workarounds) Infrahub's task-manager runs the Infrahub image with `uvicorn --factory infrahub.prefect_server.app:create_infrahub_prefect` (../infrahub/docker-compose.yml:281-295, backend/infrahub/prefect_server/app.py). Client and server Prefect versions can then never drift. The Prefect port is no longer published on the host by default (the Sync UI of spec 011 replaces the Prefect UI for the customer); an opt-in remains for OpsMill engineers. Prefect never runs inside the sync-api container, because that would put the Prefect database credentials in the API container (root docker-compose.yml lines ~19-25 isolate them) and needs a process supervisor. ADR 0019 gets an amendment recording which image the server uses. (2) MinIO: replace the temporary MinIO build that INFP-692 lists as open with a maintained MinIO-compatible image pinned by digest. Verification: compose contract tests find no prefecthq/prefect image and every non-Sync image pinned by digest; the compose lifecycle tier passes. Scope = brief FR-009, FR-010."

Part (2) of the input is replaced by the product owner decisions below.

## Summary

Problem: a customer team that deploys V3 with the root `docker-compose.yml` pulls four images
(PostgreSQL, a MinIO build, the stock Prefect image and the Sync image) and runs a Prefect server
whose version is pinned separately from the Prefect client inside the Sync image. Sync can store
its plans and run files only in the MinIO service that the file starts: the endpoint is fixed to
`http://object-store:9000` (`docker-compose.yml:49`), so a team that already runs an
S3-compatible store cannot use it. The worker receives the credentials of three adapters only,
and cannot trust a private CA.

What is needed for the testing release:

- The Prefect server runs from the Sync image, as its own service, in all three compose files.
  The Prefect client and server then always come from the same release.
- The operator deployment no longer publishes the Prefect port on the host. The customer team
  uses the Sync UI (spec 011) and the CLI. OpsMill engineers keep an opt-in to open the Prefect UI.
- The bundled object store stays the current MinIO build, pinned by digest.
- A team can point Sync at an S3-compatible store it already runs, from `.env`, and leave the
  bundled MinIO out.
- The worker receives the credential variables of every bundled adapter and a custom CA
  certificate bundle, without an edit to the shipped file. Credential values reach only the
  services that read them, and no part of this change relies on replacing credential values in
  text.
- The compose lifecycle tier passes reliably, and each release publishes the Sync image digest
  and its SBOM.

The credential, CA, lifecycle and image items are MVP blockers that
`infrahub-sync-process/product/path-to-3.0.md` lists under INFP-692: "An operator using Compose
cannot add a custom CA or pass credentials for most adapters to the worker", "Engineering cannot
rely on the Compose lifecycle qualification as a regression gate while it fails at random", and
that an evaluator can neither pull the Sync image by digest nor be sure the SBOM is published
with the release.

Not decided yet: whether a different S3 server replaces the bundled MinIO, in 3.0 or later
(AD-3 and the [V3 release scope](../v3-release-scope.md)).

### Product owner decisions of 2026-10-15

These decisions replace part (2) of the input and parts of the first plan:

1. The MVP keeps the current MinIO build,
   `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`,
   pinned by digest. No replacement S3 server (such as `pgsty/silo`) is added in the MVP, and the
   requirement to replace "the temporary build" is dropped. A different S3 server may come in 3.0
   or later.
2. Sync stays S3-compatible: it must work with an S3-compatible store that a customer already
   runs, and the bundled MinIO can then be left out.
3. Secrets are kept out of text by construction, in one layer that spec 009 owns. Search and
   replace of credential values in messages, plan files and logs, and masking spread over
   several components, are rejected. This specification passes credential values only to the
   services that read them and does not rely on value masking.

Decisions confirmed by the product owner earlier and not reopened here (idea brief of
2026-10-15, FR-009 and FR-010):

- The Prefect server never runs inside the `sync-api` container.
- Replacing MinIO with another storage product (AD-3) is decided later.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy V3 with the Prefect server from the Sync image (Priority: P1)

A customer team deploys V3 on one host from the root `docker-compose.yml`. They write `.env`,
run `docker compose up -d --wait`, and view every service as healthy. The deployment pulls the
Sync image, PostgreSQL and the MinIO image, and no separate Prefect image. When the team
moves to the next Sync release by changing `VERSION`, the Prefect server moves with it, because
it runs from the same image as the Sync API and worker.

**Why this priority**: the testing release adds several services over v2 (idea brief, "Users and
value"). One image fewer to pull, mirror and scan lowers that cost. A Prefect server that always
matches the client removes a class of version mismatch that Infrahub met and removed in commit
`7a22217bf` (2025-01-22): "This will avoid incompatibility issues between the Prefect client used
by Infrahub and the Prefect server."

**Independent Test**: start the root compose file with a Sync image built from the branch, plan
and apply a run, and check that no running container uses a `prefecthq/prefect` image.

**Acceptance Scenarios**:

1. **Given** a host with Docker Compose and a complete `.env`, **When** the team runs
   `docker compose up -d --wait`, **Then** `prefect-server` becomes healthy, and its image is the
   same `${INFRAHUB_SYNC_DOCKER_IMAGE}:${VERSION}` reference that `sync-api`, `sync-worker`,
   `sync-bootstrap` and `cli` use.
2. **Given** that running deployment, **When** the team registers a configuration, plans, approves
   and applies, **Then** the run completes as it does today with the stock Prefect image.
3. **Given** a deployment that ran with the stock Prefect 3.8.6 image, **When** the team updates
   to a release that runs Prefect from the Sync image and runs `docker compose up -d --wait`,
   **Then** the Prefect server starts on the existing `prefect` database, and existing work pools,
   deployments and run history remain.
4. **Given** the running deployment, **When** the team lists the containers and their
   environments, **Then** only `prefect-server` and `db-bootstrap` hold the Prefect database
   password, and `prefect-server` holds no object store or adapter credential.

---

### User Story 2 - Use the Sync UI, with the Prefect UI closed by default (Priority: P1)

A customer team views runs and plans in the Sync UI (spec 011) and the CLI. The deployment
publishes only the Sync API port on the host. When an OpsMill engineer supports the team and
needs the Prefect UI, they turn on a documented opt-in, which publishes the Prefect port on the
loopback address of that host only.

**Why this priority**: the Sync UI replaces the Prefect UI for the customer. A second published
port is a second surface to secure and to explain. It can also lead a customer to change Prefect
state that Sync owns (ADR 0019, "Sync owns its Prefect configuration").

**Independent Test**: start the root compose file without the opt-in and check the published
ports; then turn on the opt-in, open the Prefect UI on `127.0.0.1`, and sign in with the Prefect
API credential.

**Acceptance Scenarios**:

1. **Given** the root compose file with default settings, **When** the team starts it, **Then**
   `sync-api` is the only service that publishes a host port.
2. **Given** an OpsMill engineer on the deployment host, **When** they turn on the documented
   opt-in, **Then** the Prefect UI and API answer on `127.0.0.1` at the documented port, ask for
   `INFRAHUB_SYNC_PREFECT_AUTH_STRING`, and show the Sync work pool and deployment.
3. **Given** the opt-in is on, **When** the engineer turns it off and restarts the deployment,
   **Then** the Prefect port is no longer published.

---

### User Story 3 - Store plans and run files in the team's own S3-compatible store, or in the bundled MinIO (Priority: P1)

A customer team already runs an S3-compatible object store, and its policy is to keep
application data there. The team creates a bucket and a credential for Sync in that store, and
sets in `.env` the endpoint URL, the bucket, the region, the credential, the addressing style
the store expects, and, if the store's certificate comes from a private CA, the CA bundle of
User Story 5. It sets one more `.env` setting to leave out the bundled MinIO. It starts the
deployment, and Sync stores plans and run files in the team's store.

A team without its own store sets nothing and keeps the bundled MinIO, which runs the same
pinned build as today.

**Why this priority**: the product owner requires Sync to stay S3-compatible and to work with
the store a customer already has (decision 2). The bundled MinIO stays for teams that have no
store; a different bundled server may come in 3.0 or later (decision 1).

**Independent Test**: start an S3-compatible store outside the deployment, over TLS with a
certificate from a test CA, with a bucket that already exists. Set the external store settings
in `.env`, start the root compose file, plan and apply a run, then read the run's plan with
`runs show`. Check that no bundled object store container exists and that the run's objects are
in the external bucket.

**Acceptance Scenarios**:

1. **Given** `.env` with no object store setting beyond the access key and secret key, **When**
   the team starts the deployment, **Then** `object-store-init` and `object-store` run
   `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`,
   and Sync stores its files there, as today.
2. **Given** a deployment that ran the bundled MinIO with the current release, **When** the team
   updates and starts the deployment, **Then** `runs show` on an earlier run reads its plan and
   artifacts.
3. **Given** `.env` that names an external endpoint, bucket, region, credential and addressing
   style and leaves out the bundled store, **When** the team starts the deployment, **Then** no
   container is created for `object-store-init` or `object-store`, `sync-bootstrap` does not wait
   for them, and a plan and apply complete with their files in the external bucket.
4. **Given** an external bucket that already exists and a credential that may not create buckets,
   **When** `sync-bootstrap` runs, **Then** it finds the bucket and completes.
5. **Given** an external endpoint over HTTPS whose certificate is signed by a CA named in `.env`
   (FR-024), **When** a Sync service connects, **Then** the connection succeeds with certificate
   checks on.
6. **Given** an external store that does not refuse a second create-only write of the same key,
   **When** `sync-bootstrap` runs, **Then** it stops with a fixed failure name that says the
   store lacks a behaviour Sync relies on, and prints no credential or endpoint value.
7. **Given** the preview stack, **When** an engineer starts it, **Then** the bucket is created as
   today, with `mc` from the same MinIO build.

---

### User Story 4 - Develop and preview with the same Prefect server (Priority: P3)

An OpsMill engineer starts the development stack (`uv run invoke start`) or the preview stack
(`uv run invoke preview.up`). Both run the Prefect server from a Sync image built from the
checkout, so the server and the code under development use the same Prefect version.

**Why this priority**: the development and preview stacks are where a Prefect upgrade is tested
first. A stock image there pins the server version in a second place (`preview.env`,
`PREVIEW_PREFECT_IMAGE_TAG`), which an engineer has to keep in step by hand.

**Independent Test**: change the Prefect pin in `pyproject.toml` on a branch, rebuild, and start
each stack; the server reports the new version with no other file changed.

**Acceptance Scenarios**:

1. **Given** the development stack, **When** an engineer runs `uv run invoke start`, **Then**
   `prefect-server` runs from `infrahub-sync:dev` and stays published on `127.0.0.1:4230`.
2. **Given** the preview stack, **When** an engineer runs `uv run invoke preview.up`, **Then**
   `sync-prefect` runs from a Sync image built from the checkout and stays published on
   `127.0.0.1:${PREVIEW_PREFECT_PORT}`, so the Sync API and worker that the preview starts on the
   host reach it.
3. **Given** `development/preview.env`, **When** an engineer reads it, **Then** it holds no
   Prefect image tag or digest setting.

---

### User Story 5 - Pass every adapter's credentials and a custom CA to the worker (Priority: P1)

A customer team converted its v2 configuration for a bundled adapter other than NetBox, such as
Cisco ACI (spec 012). The convert command printed the names of the credential variables to set.
The team sets them in `.env`, and adds the certificate of the CA that signed their NetBox,
Infrahub and object store certificates. They start the deployment, register the package and
plan, without editing `docker-compose.yml`.

**Why this priority**: the root compose file passes only the Infrahub, NetBox and Nautobot
tokens to `sync-worker` (`docker-compose.yml:275-284`), so a package for any other bundled
adapter cannot run, and a source behind a private CA cannot be reached.

**Independent Test**: set the ACI credential variables and a test CA bundle in `.env`, start the
root compose file, and check that the worker reads both and that a plan reaches a test server
that uses a certificate from that CA.

**Acceptance Scenarios**:

1. **Given** `.env` with `INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME` and
   `INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD`, **When** the team starts the deployment, **Then**
   `sync-worker` holds both variables, no other service holds them, and a run of an ACI package
   resolves both references.
2. **Given** a CA certificate bundle that the team names in `.env`, **When** a run connects to a
   source, to Infrahub or to the object store, and that server presents a certificate signed by
   that CA, **Then** the connection succeeds without turning off certificate checks.
3. **Given** no CA bundle in `.env`, **When** the team starts the deployment, **Then** every
   service starts as it does today.
4. **Given** a file in the CA directory that holds a private key, **When** a Sync service starts,
   **Then** it stops with an error that names the file and prints none of its content.

### Edge Cases

- **Read-only root filesystem.** Every Sync service runs with `read_only: true` and three tmpfs
  scratch roots owned by UID 10001 (`/var/lib/infrahub-sync`, `/var/lib/infrahub-sync/prefect`,
  `/tmp/infrahub-sync`). `PREFECT_HOME` is already `/var/lib/infrahub-sync/prefect` in the
  `Dockerfile`. Prefect 3.8.6 copies the UI's static files at startup into
  `prefect/server/ui_build` inside the installed package unless `PREFECT_UI_STATIC_DIRECTORY` is
  set (`prefect/server/api/server.py`, `create_ui_app`). In the Sync image that directory is owned
  by root and read-only, so the copy fails; Prefect logs the error and serves no UI, while the API
  keeps working. FR-005 requires the copy to go to a scratch root.
- **Restart on tmpfs.** The scratch roots are empty after each container restart, so the UI files
  are copied again at each start. The startup grace period of the health check covers that copy.
- **Port 4200 held on the host.** The default deployment no longer needs host port 4200, so the
  "`up` fails on a port publication" row in `operations/compose-troubleshooting.mdx` applies to
  the Sync API port, and to the Prefect port only when the opt-in is on.
- **An operator who set `INFRAHUB_SYNC_PREFECT_PORT`.** After the update the setting has no effect
  unless the opt-in is on. The upgrade notes say so.
- **A custom Sync image.** An operator who selects another image with `INFRAHUB_SYNC_DOCKER_IMAGE`
  and `VERSION` changes the Prefect server image too. An image without the `service` extra cannot
  start the Prefect server, and the health check fails with the startup error in the container
  log.
- **Lifecycle tests reach Prefect inside the network.** `tests/compose/lifecycle.py` calls
  `http://prefect-server:4200/api/...` from inside the Compose network, so removing the host
  port does not affect it.
- **Object store volume ownership.** `object-store-init` changes the owner of the volume to
  UID 65532 when it differs (`docker-compose.yml:102-118`). The build does not change, so the
  repair stays as it is.
- **Moving from the bundled MinIO to an external store.** Earlier plans and run files stay in the
  `object-store-data` volume. Sync does not copy them to the external store, so `runs show`
  cannot read the files of runs made before the move. The documentation says so.
- **An external store that does not support a behaviour Sync relies on.** Sync writes some
  objects only if the key is absent (`If-None-Match: *`, `infrahub_sync/service/storage.py:251-269`)
  and treats a missing key as `NoSuchKey` with status 404 (`storage.py:271-299`). A store that
  ignores the create-only condition would let a second write replace a stored artifact. FR-032
  requires bootstrap to detect this.
- **An external endpoint over plain HTTP.** `INFRAHUB_SYNC_S3_ENDPOINT_URL` accepts `http` and
  `https` URLs (`storage.py:193-205`). Request signing never sends the secret key, but object
  content crosses the network unencrypted over `http`. The documentation recommends `https` for
  an external store.
- **An endpoint URL that carries a user name or password.** `storage.py:203-204` refuses it in
  the API and worker. `sync-bootstrap` builds its client from the raw setting
  (`infrahub_sync/service/bootstrap.py:146-150`) without that check. FR-034 closes the gap.

## Requirements *(mandatory)*

### Functional Requirements

#### Prefect server image and process

- **FR-001**: Every compose file MUST run the Prefect server from the Sync image:
  - root `docker-compose.yml`: the service `prefect-server` uses
    `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release>}`,
    the same reference as the other Sync services, so the release tasks keep one pin.
  - `development/docker-compose.dev.yml`: `prefect-server` uses `infrahub-sync:dev`, built from
    the checkout like the other Sync services.
  - `development/docker-compose.preview.yml`: `sync-prefect` uses a Sync image built from the
    checkout.
- **FR-002**: The Prefect server MUST run as its own Compose service. It MUST NOT run inside the
  `sync-api` container or any other Sync service. The Prefect database connection string and
  password MUST stay limited to `prefect-server` and `db-bootstrap`, as today. `prefect-server`
  MUST receive no object store credential and no adapter credential.
- **FR-003**: The Sync package MUST provide a factory that returns Prefect's server application,
  started by the ASGI server already in the image, in the way Infrahub runs `task-manager`
  (`uvicorn --factory infrahub.prefect_server.app:create_infrahub_prefect`). The suggested module
  name is `infrahub_sync.service.prefect_server`. The factory MUST NOT add routes or bootstrap
  steps: `sync-bootstrap` stays the only service that creates the work pool and the deployment.
- **FR-004**: The server started through the factory MUST behave as `prefect server start` does
  today for Sync:
  - it migrates the `prefect` database on start
  - it runs Prefect's background services (scheduler, late runs and the others that
    `prefect server start` enables by default)
  - it enforces `PREFECT_SERVER_API_AUTH_STRING` on the whole API, leaving the health probe open
  - it keeps analytics off (`PREFECT_SERVER_ANALYTICS_ENABLED=false`)
  - it keeps no state of its own: PostgreSQL holds its records, and the service mounts no volume
- **FR-005**: In the root compose file, `prefect-server` MUST run with `read_only: true` and the
  same three tmpfs scratch roots as the other Sync services, and MUST write the Prefect UI's static
  files under a scratch root (for example by setting `PREFECT_UI_STATIC_DIRECTORY`). The Prefect UI
  MUST load when the opt-in of FR-008 is on.
- **FR-006**: The health check of the Prefect server MUST use a tool present in the Sync image
  (today a `python -c` call to `/api/health`) and keep its current interval, retries and startup
  grace period, or longer ones if the UI copy of FR-005 needs them.

#### Published ports

- **FR-007**: The root compose file MUST NOT publish the Prefect port by default. `sync-api` MUST
  be the only service that publishes a host port, and every published address MUST stay
  `127.0.0.1` by default.
- **FR-008**: An OpsMill engineer MUST be able to publish the Prefect UI and API on the loopback
  address of the deployment host without editing the shipped `docker-compose.yml`. The opt-in MUST
  be documented, MUST bind to `127.0.0.1` only, and MUST set `PREFECT_SERVER_UI_API_URL` to the
  address the browser uses. ADR 0016 keeps the operator deployment to one self-contained file, so
  the plan MUST state how the opt-in fits that decision.
- **FR-009**: The development and preview stacks MUST keep publishing the Prefect port on
  `127.0.0.1` (`4230` and `${PREVIEW_PREFECT_PORT:-4210}`). The preview starts the Sync API and
  worker on the host, and they reach Prefect through that port.

#### Stock Prefect image removal

- **FR-010**: The compose files in the repository MUST NOT reference a `prefecthq/prefect` image.
- **FR-011**: `PREVIEW_PREFECT_IMAGE_TAG` and `PREVIEW_PREFECT_IMAGE_DIGEST` MUST be removed from
  `development/preview.env`, from the checks in `tasks/preview.py`, and from the compose contract
  tests, because no Prefect image is pinned separately any more.

#### Bundled object store image

- **FR-012**: The root, development and preview compose files MUST keep running the bundled
  object store from
  `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`
  in every service that uses it today (`object-store-init`, `object-store`, `sync-minio`,
  `sync-minio-bootstrap`). This specification does not replace the image.
- **FR-013**: The bundled object store image MUST stay pinned by the `sha256` digest of its
  multi-architecture index, which lists linux/amd64 and linux/arm64. A change to that digest is a
  separate, reviewed change; it is not part of this specification.
- **FR-014**: Every compose file MUST name every image other than the Sync image with a
  `sha256` digest.
- **FR-015**: The preview bucket creation MUST keep working with the `mc` client in the same
  MinIO build (`sync-minio-bootstrap`).
- **FR-016**: The `object-store` comments, and the paragraph in `docs/docs/compose-deployment.mdx`
  that calls the image "a temporary, pinned MinIO build", MUST describe the image as the bundled
  MinIO build pinned by digest, say that a team can use its own S3-compatible store instead
  (FR-028), and say that a different bundled server may come in a later release.

#### External S3-compatible store

- **FR-028**: An operator MUST be able to point every Sync service at an S3-compatible store
  outside the deployment by setting these values in `.env`, without an edit to the shipped
  `docker-compose.yml`:
  - the endpoint URL
  - the bucket and the key prefix
  - the region
  - the access key and the secret key
  - the addressing style: path-style or virtual-hosted-style requests
  When none of them is set, Sync uses the bundled MinIO, as today.
- **FR-029**: When an operator uses an external store, a documented `.env` setting MUST leave the
  bundled object store out: Compose creates no `object-store-init` or `object-store` container,
  no Sync service waits for them, and the external store's credential reaches no MinIO process.
  Without the setting, the bundled object store starts as today.
- **FR-030**: Every Sync service MUST verify the certificate of an `https` endpoint against the
  image's default trust store plus the CA bundle of FR-024. No Sync setting turns certificate
  checks off for the object store.
- **FR-031**: `sync-bootstrap` MUST complete when the bucket already exists and the credential
  may not create buckets. It MUST create the bucket only when the store reports it absent.
- **FR-032**: `sync-bootstrap` MUST check that the store refuses a second create-only write of the
  same key, and stop with a fixed failure name when it does not, because Sync's artifact store
  relies on that refusal (`storage.py:251-269`). The check MUST leave no object behind once it
  completes.
- **FR-033**: `docs/docs/compose-deployment.mdx` MUST:
  - list the settings of FR-028 and FR-029
  - list the S3 operations and behaviours Sync relies on, as recorded in the plan
  - say that moving from the bundled MinIO to an external store does not copy earlier plans and
    run files
  - say which store OpsMill tests the deployment with, and name no other product as supported
    unless a test covers it
- **FR-034**: Every Sync service, `sync-bootstrap` included, MUST refuse an endpoint URL that
  carries a user name or password, with the fixed message `storage.py:198` uses today, which
  names the setting and not its value.

#### Tests, records and documentation

- **FR-017**: The compose contract tests (`tests/compose/test_compose_contract.py`) MUST check, for
  the root, development and preview compose files:
  - no service uses a `prefecthq/prefect` image
  - the Prefect server service uses the Sync image reference (in the root file, `prefect-server`
    joins the services checked like `ALL_SYNC_SERVICES`)
  - every image other than the Sync image is pinned by digest, and the bundled object store uses
    the digest of FR-012
  - the root file publishes a host port only for `sync-api`
  - `prefect-server` runs read-only over the image's scratch roots and mounts no volume
  - with the setting of FR-029, the rendered model has no running `object-store-init` or
    `object-store`, and the Sync services still resolve
  - each credential variable reaches only the services listed in the plan
- **FR-018**: The compose lifecycle tier (`uv run pytest -m compose tests/compose`) MUST pass with
  the new Prefect service, with the bundled MinIO, and with an external S3-compatible store. The
  image tests (`tests/image`) MUST check that the Sync image starts the Prefect server with a
  read-only root filesystem and serves `/api/health`.
- **FR-019**: An update from a deployment that runs the current release MUST keep the data in
  `object-store-data` and read it again, with no reset. The bundled image does not change, so no
  reset step is needed for the object store.
- **FR-020**: ADR 0019 MUST get an amendment that records that the Prefect server runs from the
  Sync image, as a separate service, and why: the client and server versions cannot differ, and
  the Prefect database credential stays out of the API container. The amendment follows the
  repository's rule that ADRs cite no internal sources.
- **FR-021**: `docs/docs/compose-deployment.mdx` and `docs/docs/operations/*.mdx` MUST describe:
  - the services table, with `prefect-server` running from the Sync image
  - the prerequisites, which need only the Sync API port free by default
  - `INFRAHUB_SYNC_PREFECT_PORT` as a setting of the opt-in only
  - how an OpsMill engineer opens the Prefect UI through the opt-in
  - the upgrade notes for operators who set `INFRAHUB_SYNC_PREFECT_PORT`
- **FR-022**: The change MUST add a news fragment under `changelog/` that names the removed Prefect
  image, the unpublished Prefect port, the external object store settings, the new credential
  variables and the CA setting.

#### Worker credentials and custom CA (INFP-692)

- **FR-023**: In the root compose file, every `INFRAHUB_SYNC_CREDENTIAL_` variable that a
  package for a bundled adapter can reference (the identifiers of spec 012 FR-005 and FR-006)
  MUST reach `sync-worker` when the operator sets it in `.env`, without an edit to the shipped
  `docker-compose.yml`. The plan MUST list which services receive each variable; a service that
  does not read a credential MUST NOT receive it. FR-002 stays in force. ADR 0016 keeps the
  operator deployment to one self-contained file, so the plan MUST state how this fits that
  decision.
- **FR-024**: The operator MUST be able to name a CA certificate bundle in `.env` that the Sync
  services trust for TLS connections to sources, to Infrahub and to the object store, in addition
  to the image's default trust store, without an edit to the shipped `docker-compose.yml` and
  without turning off certificate checks. A Sync service MUST refuse to start when a file in that
  bundle holds a private key, naming the file and printing none of its content.
- **FR-025**: `docs/docs/compose-deployment.mdx` MUST list the credential variables of each
  bundled adapter, which service receives each one, and how to add a CA certificate bundle.
- **FR-035**: Credential values MUST be kept out of text by construction, not by replacing them
  afterwards. For this specification that means:
  - each credential value reaches only the services that read it (FR-002, FR-023, FR-029)
  - no code that this specification adds writes a credential value, or text that could contain
    one, to a log, a message, a file or a Prefect record; the CA setup logs a certificate count
    and a path only
  - no credential value is passed through Prefect deployment or flow-run job variables or
    parameters, which the Prefect server stores
  - the documentation never tells an operator to run a command that prints interpolated
    credential values, such as `docker compose config` without `--quiet`, or to share
    `docker inspect` output
  - this specification adds no value masking, and does not depend on spec 009's masking helpers
    for any credential that it passes. Spec 009 owns the one layer that keeps secrets out of run
    records, plans and API responses.

#### Compose lifecycle reliability and published image (INFP-692)

- **FR-026**: The causes of the random failures of the compose lifecycle tier
  (`uv run pytest -m compose tests/compose`) MUST be found and fixed or removed, so that the tier
  can be used as a regression gate for the MVP release. Each cause found MUST be recorded in the
  plan of this specification.
- **FR-027**: The MVP release MUST publish the Sync image for linux/amd64 and linux/arm64 with
  its multi-architecture digest and its SBOM, through the existing image workflow
  (`.github/workflows/ci-docker-image.yml`, which already merges, signs and attaches an SBOM),
  and the release notes MUST name the digest, so that an evaluator can pull the image by digest
  and find the SBOM of that release.

### Key Entities

- **Sync image**: the one image that runs `sync-bootstrap`, `sync-api`, `sync-worker`, `cli`, and
  after this change the Prefect server. Its `service` extra pins `prefect==3.8.6`.
- **Prefect server service**: `prefect-server` in the root and development files, `sync-prefect`
  in the preview file. It holds the Prefect database credential and the Prefect API credential,
  and nothing else secret.
- **Bundled object store**: the MinIO build used by `object-store-init` and `object-store` (root),
  `object-store` (development), and `sync-minio` and `sync-minio-bootstrap` (preview), pinned by
  digest. It can be left out of the root deployment.
- **External object store**: an S3-compatible store that the customer team runs and names in
  `.env`. Sync holds an access key and a secret key for one bucket in it.
- **CA bundle**: public CA certificates that the operator names in `.env`; never a private key.
- **Prefect opt-in**: the documented way for an OpsMill engineer to publish the Prefect UI and API
  on loopback.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The root compose file pulls three distinct images instead of four with the bundled
  object store (the Sync image, PostgreSQL and MinIO), and two when the bundled object store is
  left out.
- **SC-002**: In every stack, the Prefect server and the Sync worker report the same Prefect
  version, in 100% of compose lifecycle runs.
- **SC-003**: With default settings, the root compose file publishes one host port (the Sync API).
- **SC-004**: The compose contract tests find zero `prefecthq/prefect` references and zero
  non-Sync images without a digest across the three compose files.
- **SC-005**: The compose lifecycle tier passes on linux/amd64 and on linux/arm64.
- **SC-006**: After an update from the current release, an earlier run's plan and artifacts in the
  bundled MinIO can be read again, with no reset.
- **SC-007**: An OpsMill engineer opens the Prefect UI through the opt-in by following the
  documentation alone, in under 5 minutes.
- **SC-008**: For every bundled adapter, a package whose credential variables are set in `.env`
  resolves every credential reference in `sync-worker` on the root compose file, with no edit to
  `docker-compose.yml`.
- **SC-009**: A run reaches a source, and Sync reaches an external object store, that present a
  certificate from a private CA once that CA bundle is named in `.env`, with certificate checks
  on.
- **SC-010**: The compose lifecycle tier passes 10 runs in a row on linux/amd64 and on
  linux/arm64 in CI.
- **SC-011**: The release notes of the MVP release name the Sync image digest, and that digest
  pulls the image and its SBOM from the registry.
- **SC-012**: With the external store settings, a plan and apply complete on the root compose
  file against an S3-compatible endpoint outside the deployment, both with path-style and with
  virtual-hosted-style addressing, and zero bundled object store containers exist.
- **SC-013**: In the lifecycle tier, the leak checks find zero credential values in the output of
  every documented command, in every container log, and in the environment of every service that
  the plan does not list as a receiver of that credential.

## Assumptions

- The Sync image already contains the Prefect server: the `Dockerfile` installs the `service`
  extra, which pins `prefect==3.8.6`, and the installed package includes the UI bundle. The
  Python version (3.13) matches the stock image `prefecthq/prefect:3.8.6-python3.13`, so the
  update needs no Prefect database migration beyond what Prefect runs on start.
- Prefect's server application from `create_app` runs its background services inside the
  application when started through a factory, as it does for Infrahub's `task-manager`. The
  lifecycle tier confirms this for Sync.
- Infrahub's factory also runs Infrahub-specific bootstrap and adds routes, and its image sets
  Prefect tuning variables (`development/Dockerfile`, for example
  `PREFECT_SERVER_EVENTS_MAXIMUM_RELATED_RESOURCES=500`). Sync needs none of them, because
  `sync-bootstrap` creates Sync's Prefect state and no source shows a need for the tuning.
- `PREFECT_HOME` already points to a scratch root in the Sync image, so only the UI static files
  need a new location (FR-005).
- The Prefect UI stays enabled in the operator deployment, unpublished. The opt-in then needs no
  restart with other Prefect settings.
- The development and preview stacks are used by OpsMill engineers only, so they keep the Prefect
  port published on loopback, and keep the bundled MinIO.
- The preview stack builds the Sync image from the checkout, as the development stack does,
  instead of pulling a released image.
- The pinned MinIO digest stays pullable without authentication from `cgr.dev`. On 2026-10-15 it
  could be inspected without a login, and the `latest-dev` tag already pointed to another digest.
  No source states how long `cgr.dev` keeps a digest that no tag names; the plan lists this as a
  risk.
- OpsMill tests the external store support with a MinIO instance outside the deployment that
  stands in for a customer store. No source names the S3-compatible products customers run, so
  this specification promises no product by name.
- The Sync UI from spec 011 replaces the Prefect UI for customer teams. In the MVP the Sync UI
  is reachable from the `sync-api` host only, on loopback (spec 011 FR-031), so FR-007 and FR-017
  keep `sync-api` as the only service that publishes a host port. The Prefect opt-in also stays
  on loopback.
- The 10-run threshold of SC-010 is chosen for this specification; no source sets one.

## Out of scope

- Replacing the bundled MinIO with another S3 server, such as `pgsty/silo` (3.0 or later, AD-3).
- Copying plans and run files from the bundled MinIO to an external store.
- Testing against or naming specific S3-compatible products beyond the bundled MinIO build.
- Turning certificate checks off for any connection.
- Running the Prefect server inside the `sync-api` container.
- Sharing Infrahub's `task-manager` (rejected in ADR 0019).
- Changing the Prefect version.
- Kubernetes deployments.

## Sources

- Idea brief of 2026-10-15, FR-009 and FR-010 (confirmed by the product owner)
- Product owner decisions of 2026-10-15 on the first plan of this specification
- `docker-compose.yml`, `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml`,
  `development/preview.env`, `Dockerfile`
- `infrahub_sync/service/storage.py`, `infrahub_sync/service/bootstrap.py`,
  `infrahub_sync/product_store/store.py` (`S3Client`)
- `tests/compose/test_compose_contract.py`, `tests/compose/lifecycle.py`, `tests/compose/conftest.py`
- `.github/workflows/ci-docker-image.yml`
- `dev/adr/0016-the-compose-deployment-is-one-tag-pinned-file.md`,
  `dev/adr/0019-prefect-is-an-internal-component-of-sync.md`
- `docs/docs/compose-deployment.mdx`, `docs/docs/custom-certificates.mdx`,
  `docs/docs/operations/compose-troubleshooting.mdx`, `docs/docs/operations/day-2-operations.mdx`
- Infrahub: commit `7a22217bf`, `docker-compose.yml` (`task-manager`),
  `backend/infrahub/prefect_server/app.py`, `development/Dockerfile`
- Prefect 3.8.6: `prefect/server/api/server.py` (`create_ui_app`),
  `prefect/settings/models/server/ui.py` (`static_directory`)
- botocore 1.43.83 (`uv.lock:243-244`): `args.py` (addressing style), `endpoint.py` (CA bundle),
  `configprovider.py` (environment settings)
- `infrahub-sync-process/product/path-to-3.0.md`, INFP-692 section
- `dev/specs/009-plan-problem-reporting/spec.md`, `dev/specs/011-operator-ui/spec.md` (FR-031),
  `dev/specs/012-v2-configuration-conversion/spec.md` (FR-005, FR-006)
