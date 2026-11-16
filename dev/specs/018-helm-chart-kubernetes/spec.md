# Feature Specification: A Helm chart to run Infrahub Sync 3.x on Kubernetes

**Feature Branch**: `018-helm-chart-kubernetes`

**Created**: 2026-11-16

**Status**: Draft

**Input**: GitHub issue [#422](https://github.com/opsmill/infrahub-sync/issues/422), "deploy: Helm
chart to run Infrahub Sync 3.x on Kubernetes".

Sources:

- Issue [#422](https://github.com/opsmill/infrahub-sync/issues/422): the need and its context.
- The root [`docker-compose.yml`](../../../docker-compose.yml): the supported single-host
  deployment. It is the reference for what a deployment of Infrahub Sync 3.x contains.
- The [Compose deployment page](../../../docs/docs/compose-deployment.mdx): the operator
  documentation of that deployment.
- [ADR 16](../../adr/0016-the-compose-deployment-is-one-tag-pinned-file.md),
  [ADR 19](../../adr/0019-prefect-is-an-internal-component-of-sync.md),
  [ADR 26](../../adr/0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md)
  and [ADR 28](../../adr/0028-the-bundled-object-store-runs-from-the-silo-image.md): decisions
  about the Compose deployment that the Kubernetes deployment keeps.
- The Helm charts of Infrahub in [`opsmill/infrahub-helm`](https://github.com/opsmill/infrahub-helm),
  and that repository's `AGENTS.md`, which states its conventions.

## Summary

A team that runs Infrahub on Kubernetes cannot deploy Infrahub Sync 3.x the same way. The 3.x
documentation covers one deployment: a single host that runs the root `docker-compose.yml`. A team
on Kubernetes has to write and maintain its own manifests, and change them by hand at each
release.

This specification adds a Helm chart, `infrahub-sync`, to `opsmill/infrahub-helm`. An operator
installs it into a namespace, sets the credentials, and gets the same deployment as the Compose
file: the Sync API, one worker, the Prefect server, PostgreSQL and an S3-compatible object store.
The operator upgrades to a new release of Infrahub Sync with `helm upgrade`.

## Decisions

These were decided before this specification was written. They are not open.

- **D-1. The chart lives in `opsmill/infrahub-helm`**, as `charts/infrahub-sync`, next to
  `infrahub-mcp`. It follows that repository's conventions for values, README, changelog, tests
  and releases. Issue #422 left the repository open; the maintainer chose `infrahub-helm` on
  2026-11-16.
- **D-2. The specification, plan and tasks live in this repository.** The operator documentation
  of the Kubernetes deployment also lives in this repository, with the rest of the 3.x
  documentation.
- **D-3. The chart deploys PostgreSQL and the object store by default**, as the Compose file does.
  An operator can turn each one off and use a PostgreSQL server or an S3-compatible store that the
  team already runs.
- **D-4. The web interface is out of scope.** The Sync image does not serve it yet
  ([#421](https://github.com/opsmill/infrahub-sync/issues/421)). The documentation says so.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install Infrahub Sync on a Kubernetes cluster (Priority: P1)

An operator who runs Infrahub on Kubernetes wants to run Infrahub Sync 3.x on the same cluster.
They create the credentials, install the chart into a namespace, and wait until the release is
ready. They then call the Sync API with the `infrahub-sync` CLI, register a configuration package
and create a plan, as they would against the Compose deployment.

**Why this priority**: without an install that works from the chart's defaults, nothing else in
this specification has any use.

**Independent Test**: on an empty namespace, install the chart with only the required credentials
set. Wait for the release to be ready. Call the version endpoint of the Sync API and run
`infrahub-sync whoami` with the token of a configured principal.

**Acceptance Scenarios**:

1. **Given** an empty namespace and the required credentials, **When** the operator installs the
   chart with no other value set, **Then** the Sync API, the worker, the Prefect server,
   PostgreSQL and the object store start, the bootstrap work completes, and the Sync API answers
   on its version endpoint.
2. **Given** an installed release, **When** the operator runs `infrahub-sync whoami` against the
   Sync API with the token of a configured principal, **Then** the command reports that
   principal.
3. **Given** an installed release, **When** the operator lists the workers through the Sync API or
   the CLI, **Then** one live worker is reported.
4. **Given** a required credential is missing, **When** the operator installs the chart,
   **Then** the installation stops before any pod is created and the message names the missing
   value.
5. **Given** a private image registry, **When** the operator sets an image pull secret,
   **Then** every pod of the release uses it.

---

### User Story 2 - Upgrade to a new release of Infrahub Sync (Priority: P1)

An operator follows the releases of Infrahub Sync. For each one they run `helm upgrade` with the
new chart version. The data in PostgreSQL and in the object store is kept, the bootstrap work runs
again for the new version, and the Sync API and the worker restart on the new image.

**Why this priority**: the need in issue #422 is to "follow each release without keeping their own
Kubernetes manifests". A chart that installs once and cannot be upgraded does not meet it.

**Independent Test**: install the chart, register a configuration package, then run
`helm upgrade` with one changed value. The upgrade completes, the package is still listed, and
`infrahub-sync whoami` still answers.

**Acceptance Scenarios**:

1. **Given** an installed release, **When** the operator runs `helm upgrade` with the same
   values, **Then** the upgrade completes without an error and the release is ready.
2. **Given** an installed release that holds a registered configuration package, **When** the
   operator upgrades, **Then** the package and its runs are still listed.
3. **Given** a new release of Infrahub Sync, **When** the operator upgrades to the chart version
   for that release, **Then** every Sync pod runs the image of that release, and the bootstrap
   work has run with that image before the Sync API and the worker serve.
4. **Given** an operator who wants a Sync version other than the chart's default, **When** they
   set the image tag, **Then** every Sync pod runs that tag.

---

### User Story 3 - Use a PostgreSQL server and an object store the team already runs (Priority: P2)

A team has a managed PostgreSQL service and an S3-compatible object store, with their own backups.
The operator turns off the PostgreSQL and the object store of the chart, gives the addresses and
the credentials of the team's services, and installs. No PostgreSQL pod, object store pod or
volume claim is created.

**Why this priority**: a production team often cannot run a database inside the release. The
install of User Story 1 has value without this story, so it comes second.

**Independent Test**: install with the bundled PostgreSQL and the bundled object store turned off
and with the addresses of services that run outside the release. The release is ready, and it
contains no PostgreSQL or object store pod.

**Acceptance Scenarios**:

1. **Given** an S3-compatible store outside the release, **When** the operator turns off the
   bundled store and sets the endpoint, the addressing style and the credentials, **Then** the
   release contains no object store pod and the plans and artifacts are saved in the external
   store.
2. **Given** a PostgreSQL server outside the release with the two databases and their roles
   already created, **When** the operator turns off the bundled PostgreSQL and sets the two
   connection strings, **Then** the release contains no PostgreSQL pod and no database
   bootstrap work.
3. **Given** the bundled store is turned off and no endpoint is set, or an endpoint is set while
   the bundled store is on, **When** the operator installs, **Then** the installation stops with
   a message that names the two values.
4. **Given** an external store or a destination that presents a certificate from a private
   certificate authority, **When** the operator supplies the CA certificates, **Then** the Sync
   API, the worker and the bootstrap work trust them.

---

### User Story 4 - Reach the Sync API from outside the cluster (Priority: P3)

An operator wants to call the Sync API from a workstation or from a CI system outside the cluster.
They turn on an Ingress or a Gateway API route for the Sync API. The Prefect server, PostgreSQL
and the object store stay reachable only inside the cluster.

**Why this priority**: the operator can already reach the Sync API with a port-forward or from a
pod in the cluster, so this is a convenience.

**Independent Test**: render the chart with the Ingress turned on and check that one Ingress is
created, that it points at the Sync API, and that no other service is exposed.

**Acceptance Scenarios**:

1. **Given** the default values, **When** the operator installs, **Then** no service of the
   release is reachable from outside the cluster.
2. **Given** the Ingress or the Gateway API route is turned on, **When** the operator installs,
   **Then** the route points at the Sync API only.
3. **Given** an installed release, **When** the operator runs the `infrahub-sync` CLI from a pod
   in the cluster with the Sync image, **Then** the CLI reaches the Sync API by its service
   name, and the pod holds no storage, Prefect, source or destination credential.

---

### Edge Cases

- **A pod restarts.** When the Sync API or the worker restarts, the bootstrap work does not run
  again. The Compose file states this property: a start that converged bootstrap state "would
  make every restart a write".
- **The bootstrap work starts before PostgreSQL, the object store or the Prefect server is
  ready.** The installation still completes, and the Sync API and the worker do not serve before
  the bootstrap work has completed.
- **The operator uninstalls the release.** The documentation states whether the volumes of
  PostgreSQL and of the object store are kept or deleted, and how to delete them.
- **A PostgreSQL role password is changed in the values after the first install.** The role
  password is set only when the role is first created (ADR 16). The documentation says that the
  operator must change it in the database too.
- **A password contains a character that is not URL-safe.** The connection strings are built
  from the role, the database and the password. The documentation says that such a password
  needs the whole connection string to be set.
- **The Sync image cannot be pulled.** Until release 3.0.0, only invited testers can pull the
  image. The documentation says that the release needs an image pull secret until then.
- **The cluster does not allow a pod to run as a given user** (for example OpenShift). Whether
  the chart runs there is not known and is not tested by this specification.
- **A source credential is not set.** The release still starts. A run that selects the missing
  credential fails that run, as in the Compose deployment.

## Requirements *(mandatory)*

### Functional Requirements

**The chart and what it deploys**

- **FR-001**: The `infrahub-helm` repository MUST hold a chart named `infrahub-sync` that an
  operator can install with Helm into one namespace.
- **FR-002**: A default install MUST run the same services as the root `docker-compose.yml`: the
  Sync API, one Sync worker, the Prefect server, PostgreSQL and the S3-compatible object store.
- **FR-003**: The Sync API, the worker, the Prefect server and the bootstrap work MUST run from
  the one Sync image. The chart MUST NOT pull a Prefect image.
- **FR-004**: The chart's application version MUST name a release of Infrahub Sync, and the Sync
  image tag MUST default to it. The operator MUST be able to set another image repository and
  another tag.
- **FR-005**: Every image other than the Sync image MUST be pinned by digest in the default
  values, as in the Compose file.
- **FR-006**: The operator MUST be able to set image pull secrets that apply to every pod of the
  release.

**Order of start**

- **FR-007**: The database bootstrap (the two roles and the two databases) and the service
  bootstrap (the product schema, the artifact bucket, the work pool and the deployment in it)
  MUST complete on install and on each upgrade before the Sync API and the worker serve.
- **FR-008**: A restart of the Sync API, the worker or the Prefect server MUST NOT run the
  bootstrap work again.
- **FR-009**: `helm upgrade` on an installed release MUST complete without a manual step, such
  as deleting a finished job.
- **FR-010**: The bootstrap work MUST be safe to repeat: a second run on a converged deployment
  changes nothing.

**Credentials and configuration**

- **FR-011**: The default values MUST NOT contain a credential. The operator supplies each
  credential either in a Kubernetes Secret that already exists or as a value from which the chart
  creates a Secret.
- **FR-012**: The installation MUST stop with a message that names the value when one of these
  is missing: the Sync API principals, the Prefect API credential, the object store access key
  and secret key, and, with the bundled PostgreSQL, the administrator password and the two role
  passwords.
- **FR-013**: The worker MUST receive the source credentials and the destination credential of
  the bundled adapters, by the same variable names as in the Compose file. The Sync API MUST
  receive only the destination credential. The operator MUST be able to add further credential
  variables to the worker without a change to the chart.
- **FR-014**: A pod that runs the CLI MUST NOT need, and the documentation MUST NOT give it, a
  storage, Prefect, source or destination credential.
- **FR-015**: The operator MUST be able to set the same optional settings as in the Compose
  file: the approval requirement, the run admission lifetime, the work pool name, the worker
  query and heartbeat intervals, the bucket, the prefix and the region.
- **FR-016**: The operator MUST be able to supply CA certificates. The Sync API, the worker and
  the bootstrap work MUST mount them read-only at the path the Sync image reads.

**PostgreSQL and the object store**

- **FR-017**: With the bundled PostgreSQL and the bundled object store, the data MUST be kept
  across a pod restart and across `helm upgrade`.
- **FR-018**: The operator MUST be able to turn off the bundled PostgreSQL and give the
  connection strings of the product database and of the Prefect database. The chart then creates
  no PostgreSQL pod and runs no database bootstrap.
- **FR-019**: The operator MUST be able to turn off the bundled object store and give the
  endpoint, the addressing style and the credentials of an S3-compatible store. The chart then
  creates no object store pod.
- **FR-020**: The installation MUST stop with a message when the bundled object store is off and
  no endpoint is set, or when an endpoint is set and the bundled store is on.

**Properties kept from the Compose deployment**

- **FR-021**: The Sync API and the worker MUST share no volume.
- **FR-022**: Only PostgreSQL and the object store MUST keep data. Every other writable path
  MUST be temporary and disappear with its pod.
- **FR-023**: Every Sync container MUST run as the image's non-root user with a read-only root
  filesystem. The object store MUST run as a non-root user.
- **FR-024**: The Prefect server MUST refuse an API call without the Prefect API credential, and
  its UI MUST be off by default.

**Network**

- **FR-025**: By default, no service of the release MUST be reachable from outside the cluster.
- **FR-026**: The operator MUST be able to turn on an Ingress or a Gateway API route for the
  Sync API. The chart MUST NOT offer a route to the Prefect server, PostgreSQL or the object
  store.
- **FR-027**: The Sync API and the Prefect server MUST have readiness and liveness checks, so
  that the release is reported ready only when both answer.

**Conventions of `infrahub-helm`**

- **FR-028**: Each value MUST be described in `values.yaml`, and the chart MUST have a README
  generated from it, a changelog configuration and a changelog fragment.
- **FR-029**: The repository's CI MUST lint the chart when it changes, and the repository MUST
  hold an end-to-end test of the chart that installs it, checks the Sync API, and upgrades it.
- **FR-030**: The chart MUST be selectable in the repository's publish workflow, so that a
  maintainer can release it to `oci://registry.opsmill.io/opsmill/chart`.

**Documentation**

- **FR-031**: The Infrahub Sync documentation MUST have a page for the Kubernetes deployment. It
  covers: what the release contains, the required credentials, the install, how to call the CLI,
  the upgrade, an external PostgreSQL and object store, CA certificates, exposing the Sync API,
  and the uninstall with what happens to the data.
- **FR-032**: The page MUST state what is not supported or not known: the web interface
  (issue #421), more than one replica of the Sync API or of the worker unless the product
  documentation already states it is supported, and the restricted access to the image before
  3.0.0.
- **FR-033**: The documentation MUST state how a chart version relates to a release of Infrahub
  Sync, so that an operator can pick the chart version for a release.

### Key Entities

- **Release**: one installation of the chart in a namespace. It has a name, a chart version and a
  set of values.
- **Chart version and application version**: the chart has its own version; its application
  version names the release of Infrahub Sync that it deploys by default.
- **Credentials Secret**: the Kubernetes Secret, created by the chart or supplied by the
  operator, that holds the credentials of the release.
- **Bootstrap work**: the database bootstrap and the service bootstrap, which create everything
  durable the deployment needs and which are safe to repeat.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On an empty namespace, an operator who has the required credentials gets a ready
  release with one `helm install` command, and the release is ready within 10 minutes on a
  single-node test cluster.
- **SC-002**: After the install, `infrahub-sync whoami` succeeds on the first call, and one live
  worker is listed.
- **SC-003**: `helm upgrade` on an installed release succeeds with no manual step, and 100% of
  the configuration packages and runs that were listed before the upgrade are listed after it.
- **SC-004**: With the bundled PostgreSQL and object store turned off, the release contains zero
  PostgreSQL pods, zero object store pods and zero volume claims.
- **SC-005**: An install with a missing required credential fails before any pod is created, and
  the message names the missing value in 100% of the cases listed in FR-012.
- **SC-006**: An operator can install, call the CLI, upgrade and uninstall by following the
  documentation page alone, without reading the chart's templates.
- **SC-007**: The rendered default release exposes zero services outside the cluster, and zero
  credentials appear in the default values.

## Assumptions

- **Kubernetes and Helm versions.** The chart targets the Kubernetes and Helm versions that the
  other charts of `infrahub-helm` target. The minimum versions are stated in the plan.
- **One replica.** The Sync API and the worker each default to one replica. The Compose
  deployment runs one of each, and this specification does not add support for more.
- **The external PostgreSQL is prepared by the operator.** With the bundled PostgreSQL off, the
  operator creates the two databases and the two roles. The chart does not hold the
  administrator credential of a server it does not run.
- **Storage.** The cluster has a default storage class that can provision the two volumes of the
  bundled PostgreSQL and object store. The operator can set another storage class and size.
- **Access to the Sync image.** Until 3.0.0 the image is restricted to invited testers. The CI of
  `infrahub-helm` pulls images without credentials, so the end-to-end test of this chart cannot
  pull the image there until the image is public. The plan states how the test behaves until
  then. Whether the release date of 3.0.0 is set is not known.
- **Release of the chart.** Publishing the chart to the registry is done by a maintainer with the
  repository's publish workflow. This specification makes the chart publishable; it does not
  publish it.
- **OpenShift.** Not tested and not claimed.
- **Backups, high availability, autoscaling, network policies and metrics** are out of scope.
- **The Prefect UI** stays off by default. Whether the chart offers a value to turn it on is
  decided in the plan; no route to it is offered (FR-026).
