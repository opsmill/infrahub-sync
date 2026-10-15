# Feature Specification: Fewer images in the Compose deployment

**Feature Branch**: `013-compose-images`

**Created**: 2026-10-15

**Status**: Draft

**Release**: MVP (the testing release). Every requirement in this specification belongs to the MVP.
Work it names as out of scope belongs to 3.0 or 3.x, as listed in
[V3 release scope](../v3-release-scope.md).

**Input**: User description: "Fewer images in the Compose deployment for the V3 testing release. (1) Prefect server from the Sync image: every compose file (root docker-compose.yml operator deployment, development/docker-compose.dev.yml, the preview stack development/docker-compose.preview.yml) runs the Prefect server from the Sync image instead of the stock prefecthq/prefect:3.8.6 image, as a separate service, through a factory module (for example infrahub_sync.service.prefect_server, wrapping Prefect's create_app) started with uvicorn. This follows Infrahub's pattern: since commit 7a22217bf (2025-01-22, 'use infrahub image to run prefect server', reason: avoid client/server version incompatibility and CI workarounds) Infrahub's task-manager runs the Infrahub image with `uvicorn --factory infrahub.prefect_server.app:create_infrahub_prefect` (../infrahub/docker-compose.yml:281-295, backend/infrahub/prefect_server/app.py). Client and server Prefect versions can then never drift. The Prefect port is no longer published on the host by default (the Sync UI of spec 011 replaces the Prefect UI for the customer); an opt-in remains for OpsMill engineers. Prefect never runs inside the sync-api container, because that would put the Prefect database credentials in the API container (root docker-compose.yml lines ~19-25 isolate them) and needs a process supervisor. ADR 0019 gets an amendment recording which image the server uses. (2) MinIO: replace the temporary MinIO build that INFP-692 lists as open with a maintained MinIO-compatible image pinned by digest. Verification: compose contract tests find no prefecthq/prefect image and every non-Sync image pinned by digest; the compose lifecycle tier passes. Scope = brief FR-009, FR-010."

## Summary

Problem: a customer team that deploys V3 with the root `docker-compose.yml` pulls four images
(PostgreSQL, a temporary MinIO build, the stock Prefect image and the Sync image) and runs a
Prefect server whose version is pinned separately from the Prefect client inside the Sync image.
The object store runs a build that the compose file itself calls temporary
(`docker-compose.yml`, `object-store` service comment), and INFP-692 lists it as open
("An operator still runs a temporary MinIO build instead of a maintained S3-compatible store",
`infrahub-sync-process/product/path-to-3.0.md`).

What is needed for the testing release:

- The Prefect server runs from the Sync image, as its own service, in all three compose files.
  The Prefect client and server then always come from the same release.
- The operator deployment no longer publishes the Prefect port on the host. The customer team
  uses the Sync UI (spec 011) and the CLI. OpsMill engineers keep an opt-in to open the Prefect UI.
- The object store runs a maintained MinIO-compatible image, pinned by digest.
- The worker receives the credential variables of every bundled adapter and a custom CA
  certificate bundle, without an edit to the shipped file.
- The compose lifecycle tier passes reliably, and each release publishes the Sync image digest
  and its SBOM.

The last two items are MVP blockers that `infrahub-sync-process/product/path-to-3.0.md` lists
under INFP-692: "An operator using Compose cannot add a custom CA or pass credentials for most
adapters to the worker", "Engineering cannot rely on the Compose lifecycle qualification as a
regression gate while it fails at random", and that an evaluator can neither pull the Sync image
by digest nor be sure the SBOM is published with the release.

Decided by the product owner on 2026-10-15: if no maintained image reads the existing data
volume, the update may require operators to reset the object store, stated in the release notes
(FR-019). The alpha already requires a reset on upgrade.

Decisions confirmed by the product owner and not reopened here (idea brief of 2026-10-15,
FR-009 and FR-010):

- The Prefect server never runs inside the `sync-api` container.
- MinIO stays for now. Replacing it with another storage product (AD-3) is decided later.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy V3 with the Prefect server from the Sync image (Priority: P1)

A customer team deploys V3 on one host from the root `docker-compose.yml`. They write `.env`,
run `docker compose up -d --wait`, and view every service as healthy. The deployment pulls the
Sync image, PostgreSQL and the object store image, and no separate Prefect image. When the team
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
   password, and `sync-api` holds none of it.

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

### User Story 3 - Run the object store from a maintained image (Priority: P2)

A customer team deploys or updates V3 and the object store pulls a maintained MinIO-compatible
image, named by a version tag and a digest. The team's existing plans and artifacts remain
readable after the update.

**Why this priority**: INFP-692 lists the temporary build as open. A customer that scans images
or mirrors them into a private registry needs an image that receives fixes and that keeps a
fixed version name.

**Independent Test**: start the root compose file on a volume written by the current build,
then read an artifact that a previous run stored.

**Acceptance Scenarios**:

1. **Given** the root compose file, **When** the team reads the `object-store` image line,
   **Then** it names a version tag and a `sha256` digest from the selected maintained image.
2. **Given** an `object-store-data` volume written by
   `cgr.dev/chainguard/minio:latest-dev@sha256:d7c90699…`, **When** the team updates and starts
   the deployment, **Then** `runs show` on an earlier run can still read its plan and artifacts
   (subject to FR-019).
3. **Given** the preview stack, **When** an engineer starts it, **Then** the bucket is created as
   today, with the same image or a documented bootstrap tool.

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
Cisco ACI (spec 012). The convert command printed the credential variables to set. The team
sets them in `.env`, and adds the certificate of the CA that signed their NetBox and Infrahub
certificates. They start the deployment, register the package and plan, without editing
`docker-compose.yml`.

**Why this priority**: the root compose file passes only the Infrahub, NetBox and Nautobot
tokens to `sync-worker` (`docker-compose.yml:275-284`), so a package for any other bundled
adapter cannot run, and a source behind a private CA cannot be reached.

**Independent Test**: set the ACI credential variables and a test CA bundle in `.env`, start the
root compose file, and check that the worker reads both and that a plan reaches a test server
that uses a certificate from that CA.

**Acceptance Scenarios**:

1. **Given** `.env` with `INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME` and
   `INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD`, **When** the team starts the deployment, **Then**
   `sync-worker` holds both variables and a run of an ACI package resolves both references.
2. **Given** a CA certificate bundle that the team names in `.env`, **When** a run connects to a
   source or to Infrahub that presents a certificate signed by that CA, **Then** the connection
   succeeds without turning off certificate checks.
3. **Given** no CA bundle in `.env`, **When** the team starts the deployment, **Then** every
   service starts as it does today.

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
  UID 65532 for the current build. A replacement image that runs as another user needs the same
  repair for its user.

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
  password MUST stay limited to `prefect-server` and `db-bootstrap`, as today.
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

#### Object store image

- **FR-012**: The root, development and preview compose files MUST run the object store from one
  maintained MinIO-compatible image, replacing
  `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`
  in every service that uses it (`object-store-init`, `object-store`, `sync-minio`,
  `sync-minio-bootstrap`).
- **FR-013**: The selected image MUST meet each of these conditions, and the plan MUST record the
  evidence for each one:
  - it can be pulled without authentication
  - it is published for linux/amd64 and linux/arm64
  - it has a version tag that names one release, not a moving tag such as `latest` or
    `latest-dev`
  - its publisher states that it publishes security fixes, and released an update in the 90 days
    before the selection
  - it serves the MinIO S3 API and the `/minio/health/live` health endpoint that the compose
    health checks use
  - it runs as a non-root user
- **FR-014**: Every compose file MUST name the object store image by a version tag and a `sha256`
  digest of its multi-architecture index.
- **FR-015**: The preview bucket creation MUST keep working, either with a client tool in the
  selected image or with a documented replacement for the `mc` call in `sync-minio-bootstrap`.
- **FR-016**: The `object-store` comments, and the paragraph in `docs/docs/compose-deployment.mdx`
  that calls the image "a temporary, pinned MinIO build", MUST describe the selected image and why
  it was selected.

#### Tests, records and documentation

- **FR-017**: The compose contract tests (`tests/compose/test_compose_contract.py`) MUST check, for
  the root, development and preview compose files:
  - no service uses a `prefecthq/prefect` image
  - the Prefect server service uses the Sync image reference (in the root file, `prefect-server`
    joins the services checked like `ALL_SYNC_SERVICES`)
  - every image other than the Sync image is pinned by digest
  - the root file publishes a host port only for `sync-api`
  - `prefect-server` runs read-only over the image's scratch roots and mounts no volume
- **FR-018**: The compose lifecycle tier (`uv run pytest -m compose tests/compose`) MUST pass with
  the new Prefect service and object store image. The image tests (`tests/image`) MUST check that
  the Sync image starts the Prefect server with a read-only root filesystem and serves
  `/api/health`.
- **FR-019**: An update from a deployment that runs the current object store build MUST keep the
  data in `object-store-data` when the new image reads that volume. If no image that meets FR-013 reads
  it, the update MAY require operators to reset the object store, which deletes stored plans and
  run files. The release notes MUST then say so and give the reset steps.
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
  image, the unpublished Prefect port, and the new object store image.

#### Worker credentials and custom CA (INFP-692)

- **FR-023**: In the root compose file, every `INFRAHUB_SYNC_CREDENTIAL_` variable that a
  package for a bundled adapter can reference (the identifiers of spec 012 FR-005 and FR-006)
  MUST reach `sync-worker` when the operator sets it in `.env`, without an edit to the shipped
  `docker-compose.yml`. The plan MUST state which other services receive these variables, and
  MUST keep the Prefect database credential rule of FR-002. ADR 0016 keeps the operator
  deployment to one self-contained file, so the plan MUST state how this fits that decision.
- **FR-024**: The operator MUST be able to name a CA certificate bundle in `.env` that
  `sync-worker` trusts for TLS connections to sources and to Infrahub, in addition to the
  image's default trust store, without an edit to the shipped `docker-compose.yml` and without
  turning off certificate checks.
- **FR-025**: `docs/docs/compose-deployment.mdx` MUST list the credential variables of each
  bundled adapter and describe how to add a CA certificate bundle.

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
  in the preview file. It holds the Prefect database credential and the Prefect API credential.
- **Object store image**: the MinIO-compatible image used by `object-store-init` and
  `object-store` (root), `object-store` (development), and `sync-minio` and
  `sync-minio-bootstrap` (preview).
- **Prefect opt-in**: the documented way for an OpsMill engineer to publish the Prefect UI and API
  on loopback.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The root compose file pulls three distinct images instead of four: the Sync image,
  PostgreSQL and the object store.
- **SC-002**: In every stack, the Prefect server and the Sync worker report the same Prefect
  version, in 100% of compose lifecycle runs.
- **SC-003**: With default settings, the root compose file publishes one host port (the Sync API).
- **SC-004**: The compose contract tests find zero `prefecthq/prefect` references and zero
  non-Sync images without a digest across the three compose files.
- **SC-005**: The compose lifecycle tier passes on linux/amd64 and on linux/arm64.
- **SC-006**: After an update from the current release, an earlier run's plan and artifacts can be
  read again, unless FR-019 is answered with a documented reset.
- **SC-007**: An OpsMill engineer opens the Prefect UI through the opt-in by following the
  documentation alone, in under 5 minutes.
- **SC-008**: For every bundled adapter, a package whose credential variables are set in `.env`
  resolves every credential reference in `sync-worker` on the root compose file, with no edit to
  `docker-compose.yml`.
- **SC-009**: A run reaches a source that presents a certificate from a private CA once that CA
  bundle is named in `.env`, with certificate checks on.
- **SC-010**: The compose lifecycle tier passes 10 runs in a row on linux/amd64 and on
  linux/arm64 in CI.
- **SC-011**: The release notes of the MVP release name the Sync image digest, and that digest
  pulls the image and its SBOM from the registry.

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
  port published on loopback.
- The preview stack builds the Sync image from the checkout, as the development stack does,
  instead of pulling a released image.
- Why the current Chainguard build is called temporary: the quay.io MinIO image stopped allowing
  anonymous pulls on 2026-09-24 (comment in `development/docker-compose.dev.yml`), and the
  Chainguard build was chosen "while a maintained S3 store is selected" (comment in
  `docker-compose.yml`). The sources do not say which property of that build fails the
  "maintained" test; FR-013 states the conditions the replacement must meet.
- The Sync UI from spec 011 replaces the Prefect UI for customer teams. In the MVP the Sync UI
  is reachable from the `sync-api` host only, on loopback (spec 011 FR-031), so FR-007 and FR-017
  keep `sync-api` as the only service that publishes a host port. The Prefect opt-in also stays
  on loopback.
- The 10-run threshold of SC-010 is chosen for this specification; no source sets one.

## Out of scope

- Replacing MinIO with another storage product (AD-3).
- Running the Prefect server inside the `sync-api` container.
- Sharing Infrahub's `task-manager` (rejected in ADR 0019).
- Changing the Prefect version.
- Kubernetes deployments.

## Sources

- Idea brief of 2026-10-15, FR-009 and FR-010 (confirmed by the product owner)
- `docker-compose.yml`, `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml`,
  `development/preview.env`, `Dockerfile`
- `tests/compose/test_compose_contract.py`, `tests/compose/lifecycle.py`, `tests/compose/conftest.py`
- `.github/workflows/ci-docker-image.yml`
- `dev/adr/0016-the-compose-deployment-is-one-tag-pinned-file.md`,
  `dev/adr/0019-prefect-is-an-internal-component-of-sync.md`
- `docs/docs/compose-deployment.mdx`, `docs/docs/operations/compose-troubleshooting.mdx`,
  `docs/docs/operations/day-2-operations.mdx`
- Infrahub: commit `7a22217bf`, `docker-compose.yml` (`task-manager`),
  `backend/infrahub/prefect_server/app.py`, `development/Dockerfile`
- Prefect 3.8.6: `prefect/server/api/server.py` (`create_ui_app`),
  `prefect/settings/models/server/ui.py` (`static_directory`)
- `infrahub-sync-process/product/path-to-3.0.md`, INFP-692 section
- `dev/specs/011-operator-ui/spec.md` (FR-031 and Assumptions)
