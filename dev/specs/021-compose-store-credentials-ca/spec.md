# Feature Specification: An external object store, every adapter's credentials and a private CA in the Compose deployment

**Feature Branch**: `021-compose-store-credentials-ca`

**Created**: 2026-11-12

**Status**: Draft

**Release**: not settled. Old specification 013 placed these items in the testing release.
Specification 010 calls them "the remaining 3.0 blockers listed under Jira card INFP-692"
([010 spec.md](../010-prefect-from-sync-image/spec.md), Summary and decision D-4). No source says
which of the two releases they ship in.

**Input**: rebuild of what is left of old specification 013 "Fewer images in the Compose
deployment" on `feature/v3-develop` at `7d7aabba`. Specification 010 calls itself "the first of
two" features and defers to a second feature an external S3 store, credentials for every adapter,
a custom certificate authority, lifecycle reliability, and the release digest and SBOM. This
specification is that second feature.

Sources:

- Old specification 013 (`tmp-sync-specs/013-compose-images/spec.md`, 39 FRs and 13 SCs, and its
  `plan.md`, `research.md`, `data-model.md` and `contracts/`). It is the source of the
  requirements below, unless a decision in "Decisions" or a merged specification changes them.
- The gap report of 2026-11-10 (`tmp-sync-specs/gap-reports/gap-013.md`) and the recheck of
  2026-11-12 (`tmp-sync-specs/recheck-013.md`), which compare old 013 with `feature/v3-develop`.
  The recheck corrects the gap report in nine places; where they differ, this specification
  follows the recheck.
- Merged specifications this one builds on:
  [010](../010-prefect-from-sync-image/spec.md),
  [archive/007](../archive/007-harbor-image-publishing/spec.md),
  [014](../014-import-lineage/spec.md), [019](../019-plan-approval/spec.md), and the decision
  records in [`dev/adr/`](../../adr/README.md), in particular
  [ADR 0016](../../adr/0016-the-compose-deployment-is-one-tag-pinned-file.md).
- Specification 020 (plan problem reporting), which exists only on branch
  `020-plan-problem-reporting` and is not merged.

## Background

### What `feature/v3-develop` already does

Specification 010 and specification 007 delivered most of old 013:

- The Prefect server runs from the Sync image, as its own service, in the root and development
  compose files (010 FR-001 to FR-004).
- The bundled object store is `pgsty/silo`, pinned by a dated tag and the digest of its
  multi-architecture index (010 decision D-5, FR-011). Old 013's decision to keep the MinIO build
  is reversed.
- `sync-api` is the only service that publishes a host port; an OpsMill engineer opens the Prefect
  UI through a documented override file (010 FR-006, FR-007).
- `sync-bootstrap` creates the bucket over the S3 API, without `mc` (010 FR-012).
- The Sync image is built for linux/amd64 and linux/arm64, signed, and published with an SBOM
  (007 FR-002 to FR-004).

### What a customer team cannot do today

- **Use its own S3-compatible store.** The bucket, key prefix, region and both keys come from
  `.env`, but the endpoint is fixed to `http://object-store:9000` (`docker-compose.yml:49`), no
  setting chooses the addressing style, and `sync-bootstrap` always waits for the bundled
  `object-store` (`docker-compose.yml:216-217`).
- **Use a store credential that may not create buckets.** `sync-bootstrap` calls `create_bucket`
  first and accepts only the "already exists" answers (`infrahub_sync/service/bootstrap.py:52`,
  `:79-90`).
- **Run a package for most bundled adapters.** `sync-worker` receives three credential variables:
  the Infrahub, NetBox and Nautobot tokens (`docker-compose.yml:294-303`). A package for Cisco ACI,
  IP Fabric, Slurp'it, Peering Manager, Prometheus or the generic REST API adapter references
  variables that never reach the worker. The shipped examples name, for instance,
  `INFRAHUB_SYNC_CREDENTIAL_CISCO_APIC_USERNAME` and `INFRAHUB_SYNC_CREDENTIAL_SLURPIT_API_KEY`.
- **Trust a private certificate authority.** `docs/docs/custom-certificates.mdx:30-33` states that
  a custom CA cannot be set for the Compose worker in this release.

### What OpsMill engineers and evaluators cannot do today

- Rely on the compose lifecycle tier as a regression check. It fails at random; one cause was
  fixed (commit `bbdd103e`), and whether other causes remain is not known. The tier runs only in
  the `compose-suite` job of `.github/workflows/workflow-nightly-e2e.yml` (`:189-254`), on
  linux/amd64 only. That workflow starts only through `workflow_dispatch` with a commit SHA, and the
  file exists on `feature/v3-develop` but not on `main`. GitHub starts a `workflow_dispatch` workflow
  only when the file exists on the default branch, so how these runs are started today is not
  known.
- Pull the released image by digest from the release notes. `ci-docker-image.yml` outputs the
  digest (`:45-47`), but the release body holds only the changelog section
  (`.github/workflows/release-publish.yml:124-139`). The documented `cosign` check accepts any
  workflow of the repository (`docs/docs/container-image.mdx:136`, `:144`, `:149`).
- Name every image of the development stack by digest. `development/docker-compose.yml:96` uses
  `postgres:16-alpine` without a digest.

## Decisions

### Caller decisions of 2026-11-12

1. **Specification 010 "PR 2" comes first.** Its open tasks (T026, T028, T036, T037, T039,
   T061 to T064, T066: delete the preview stack, `development/preview.env` and
   `tasks/preview.py`, and rename the Invoke tasks) are a prerequisite of this specification and
   not part of it. They are not finished on `feature/v3-develop` today. See "Dependencies".
2. **The short-credential leak stays out of scope.** Specification 020 records that a credential
   shorter than six characters can still appear in the worker container log and the Prefect log,
   because the masking pass of today skips such values, and calls the fix 3.0 work (SYNC-262 and
   the rest of SYNC-82). This specification does not fix it, and its leak checks state that
   boundary (FR-018, SC-005).
3. **The development PostgreSQL image is pinned by digest** in this specification (FR-024).

### Earlier decisions that still apply

These decisions of old 013 are not reopened:

- Sync stays S3-compatible: it must work with an S3-compatible store a customer already runs,
  and the bundled store can then be left out (product owner, 2026-10-15).
- Credential values are kept out of text by construction. Search and replace of credential values
  in messages and logs, and masking spread over several components, are rejected. This
  specification passes each credential value only to the services that read it and adds no value
  masking (product owner, 2026-10-15).
- Each Sync service logs, at start, the number of CA certificates it loaded and the bundle path,
  and warns about each trust variable that was already set and so overrides the bundle, naming the
  variable and not its value (product decision of 2026-10-17).
- The `cosign` identity accepted by the documentation and the release notes is
  `ci-docker-image.yml` at a release tag (product decision of 2026-10-17).

### Decisions taken while writing this specification, to confirm at review

- **A fixed list of credential variable names.** Compose cannot pass every variable that starts
  with a prefix. Passing all of `.env` to the worker would give it the PostgreSQL administrator and
  Prefect database passwords, and a separate worker credential file is the kind of secret file
  ADR 0016 removed. The root compose file therefore names each credential variable it passes
  (FR-008). The list follows the names the shipped examples and adapter pages already use, such as
  `INFRAHUB_SYNC_CREDENTIAL_CISCO_APIC_USERNAME` and `INFRAHUB_SYNC_CREDENTIAL_SLURPIT_API_KEY`,
  and not old specification 012's naming rule, which the gap report of 012 found in conflict with
  those packages. This settles gap-012 open question 6 for the Compose deployment.
- **Three services receive the CA bundle**: `sync-bootstrap`, `sync-api` and `sync-worker`, the
  Sync services that make outgoing TLS connections. `prefect-server` and `cli` also run the Sync
  image but connect to no source, Infrahub or object store.

### Changes from old 013 made by merged specifications

- The bundled object store is Silo, not MinIO (010 D-5). Every old 013 requirement that names the
  MinIO build applies to Silo.
- The preview stack is removed (010 FR-015). Old 013 requirements on the preview stack do not
  apply.
- No in-place update from `3.0.0a5` is supported; operators run `docker compose down -v`
  (010 D-14). Old 013's requirement to keep the object store data across an update does not apply.

### Terms used in this specification

- **Bundled store**: the `object-store-init` and `object-store` services of the root compose file,
  which run Silo.
- **External store**: an S3-compatible store that the customer team runs outside the deployment,
  with a bucket and a credential for Sync.
- **Credential variable**: an environment variable named `INFRAHUB_SYNC_CREDENTIAL_<NAME>` that a
  registered package references with `$credential` (`infrahub_sync/configuration/credentials.py:29`).
  The package author chooses the name; the Compose deployment passes only the names on the
  documented list of FR-008.
- **CA bundle**: one or more public CA certificates that the operator names in `.env`. It never
  holds a private key.
- **Lifecycle tier**: the compose lifecycle tests, `uv run pytest -m compose tests/compose`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Store plans and run files in the team's own S3-compatible store (Priority: P1)

A customer team already runs an S3-compatible object store, and its policy is to keep application
data there. The team creates a bucket and a credential for Sync in that store. In `.env` it sets
the endpoint URL, the bucket, the key prefix, the region, the credential and the addressing style
the store expects, and one setting that leaves the bundled store out. If the store's certificate
comes from a private CA, it also names the CA bundle of User Story 3. It starts the deployment, and
Sync stores plans and run files in the team's store.

A team without its own store sets none of these and keeps the bundled Silo store, as today.

**Why this priority**: the product owner requires Sync to work with the store a customer already
runs (decision of 2026-10-15). Today the endpoint is fixed, so this is not possible.

**Independent Test**: start an S3-compatible store outside the deployment, with a bucket that
already exists and a credential that may not create buckets. Set the external store settings in
`.env`, start the root compose file, plan and apply a run, and read its plan with `runs plan`.
Check that no bundled store container exists and that the run's objects are in the external bucket.

**Acceptance Scenarios**:

1. **Given** `.env` with no object store setting beyond the access key and the secret key,
   **When** the team starts the deployment, **Then** the bundled store starts and Sync stores its
   files there, as today.
2. **Given** `.env` that names an external endpoint, bucket, prefix, region, credential and
   addressing style, and leaves the bundled store out, **When** the team starts the deployment,
   **Then** Compose creates no `object-store-init` or `object-store` container, `sync-bootstrap`
   does not wait for them, and a plan and an apply complete with their files in the external
   bucket.
3. **Given** an external bucket that already exists and a credential that may not create buckets,
   **When** `sync-bootstrap` runs, **Then** it finds the bucket and completes without trying to
   create it.
4. **Given** an external bucket that does not exist and a credential that may create buckets,
   **When** `sync-bootstrap` runs, **Then** it creates the bucket and completes.
5. **Given** an external store that does not refuse a second create-only write of the same key,
   **When** `sync-bootstrap` runs, **Then** it stops with a fixed failure name that says the store
   lacks a behavior Sync relies on, prints no credential or endpoint value, and leaves no test
   object in the bucket.
6. **Given** an endpoint URL in `.env` that carries a user name or a password, **When**
   `sync-bootstrap` starts, **Then** it stops before any S3 request with the fixed message that the
   Sync API and the worker already use, which names the setting and not its value.
7. **Given** an external endpoint over HTTPS whose certificate a CA in the CA bundle signed,
   **When** a Sync service connects to it, **Then** the connection succeeds with certificate checks
   on.

---

### User Story 2 - Pass the credentials of every bundled adapter to the worker (Priority: P1)

A customer team registers a package for a bundled adapter other than NetBox or Nautobot, such as
Cisco ACI. The package references credential variables with `$credential`, using the names the
adapter's documentation page gives. The team sets those variables in `.env`, starts the
deployment, and plans, without editing `docker-compose.yml`. The credential values reach
`sync-worker`, which runs the adapter, and no other service.

**Why this priority**: a package for most bundled adapters cannot run on the root compose file
today, because its credential variables never reach the worker. INFP-692 lists this as a blocker:
"An operator using Compose cannot ... pass credentials for most adapters to the worker".

**Independent Test**: register the shipped Cisco ACI example package, set its credential
variables in `.env`, start the root compose file, and check that `sync-worker` resolves both
references and that no other service holds either variable.

**Acceptance Scenarios**:

1. **Given** `.env` with `INFRAHUB_SYNC_CREDENTIAL_CISCO_APIC_USERNAME` and
   `INFRAHUB_SYNC_CREDENTIAL_CISCO_APIC_PASSWORD`, **When** the team starts the deployment and
   plans the ACI example package, **Then** `sync-worker` resolves both references, and no other
   service holds either variable.
2. **Given** any shipped example package or adapter documentation page, **When** the team sets the
   credential variables it names in `.env` and starts the deployment, **Then** `sync-worker`
   receives every one of them with no edit to `docker-compose.yml`.
3. **Given** `.env` without the variable that a registered package references, **When** the team
   plans, **Then** the run fails with the existing refusal that names the variable and not a
   value, and every service still starts.
4. **Given** the running deployment, **When** an engineer lists the environment of each service,
   **Then** only the services that the receiver list of FR-009 names hold each credential
   variable.

---

### User Story 3 - Trust a private certificate authority (Priority: P1)

A customer team's NetBox, Infrahub and object store present certificates that the team's private
CA signed. The team places the CA certificates in a directory that `.env` names, starts the
deployment, and runs a plan with certificate checks on.

**Why this priority**: INFP-692 lists this as a blocker: "An operator using Compose cannot add a
custom CA". Today the only way to reach such a source is `verify_ssl: false`, which turns off
verification.

**Independent Test**: start a test server with a certificate from a test CA, name the test CA
bundle in `.env`, start the root compose file, and check that a plan reaches the server with
certificate checks on, and that `sync-bootstrap`, `sync-api` and `sync-worker` each log the number
of certificates and the path.

**Acceptance Scenarios**:

1. **Given** a CA bundle that the team names in `.env`, **When** a run connects to a source, to
   Infrahub or to the object store, and that server presents a certificate the CA signed, **Then**
   the connection succeeds without turning off certificate checks.
2. **Given** no CA bundle in `.env`, **When** the team starts the deployment, **Then** every
   service starts as it does today and trusts only the image's default trust store.
3. **Given** a file in the CA directory that holds a private key, **When** a Sync service starts,
   **Then** it stops with an error that names the file and prints none of its content.
4. **Given** a CA bundle and a trust variable such as `SSL_CERT_FILE` that is already set, **When**
   a Sync service starts, **Then** it logs a warning that names the variable and not its value.
5. **Given** a team that changes the files in the CA directory, **When** it restarts `sync-api` and
   `sync-worker`, **Then** the new certificates are trusted; the documentation says that
   `docker compose up -d` alone starts only `sync-bootstrap` again.

---

### User Story 4 - Rely on the compose lifecycle tier as a regression check (Priority: P2)

An OpsMill engineer changes the compose file or a Sync service and relies on the lifecycle tier to
say whether the deployment still works. The tier passes or fails for a reason in the change, not at
random, on linux/amd64 and on linux/arm64.

**Why this priority**: INFP-692 lists it as a blocker: "Engineering cannot rely on the Compose
lifecycle qualification as a regression gate while it fails at random". It protects the three P1
stories after release, but customer teams do not see it directly.

**Independent Test**: run the lifecycle tier 10 times in a row on each architecture in CI and
check that every run passes.

**Acceptance Scenarios**:

1. **Given** the lifecycle tier on `feature/v3-develop`, **When** CI runs it 10 times in a row on
   linux/amd64 and 10 times on linux/arm64, **Then** all 20 runs pass.
2. **Given** a cause of a random failure found while working on this specification, **When** an
   engineer reads `research.md`, **Then** the cause, the fix or removal, and the test that shows it
   are recorded.

---

### User Story 5 - Pull and verify a released image by digest (Priority: P2)

An evaluator reads the release notes of a Sync release, pulls the Sync image by the digest the
notes name, and verifies its signature and SBOM with the `cosign` command the documentation gives.

**Why this priority**: INFP-692 lists it as a blocker. Specification 007 already builds, signs and
attaches the SBOM; what is missing is the digest in the release notes and an identity check that
accepts only the release workflow.

**Independent Test**: on a release candidate, read the release notes, pull
`registry.opsmill.io/opsmill/infrahub-sync@<digest>`, and run the documented `cosign verify` and
SBOM commands.

**Acceptance Scenarios**:

1. **Given** a published release, **When** the evaluator reads its release notes, **Then** they
   name the pull address `registry.opsmill.io/opsmill/infrahub-sync` and the multi-architecture
   digest.
2. **Given** that digest, **When** the evaluator runs the documented `cosign verify`, **Then** it
   accepts only a signature whose certificate identity is `ci-docker-image.yml` at a release tag.

### Edge Cases

- **Moving from the bundled store to an external store.** Earlier plans and run files stay in the
  bundled store's volume. Sync does not copy them, so `runs plan` and `runs artifacts` cannot read
  the files of runs made before the move. The documentation says so.
- **An external endpoint over plain HTTP.** Sync accepts `http` and `https` endpoints
  (`infrahub_sync/service/storage.py:193-205`). Request signing never sends the secret key, but
  object content crosses the network unencrypted over `http`. The documentation recommends `https`
  for an external store.
- **An external store that ignores create-only writes.** Sync writes some objects only when the key
  is absent (`If-None-Match: *`, `storage.py:251-269`). A store that ignores the condition would let
  a second write replace a stored artifact. FR-004 requires `sync-bootstrap` to detect this.
- **A store that needs virtual-hosted-style requests.** Some stores accept only one addressing
  style. FR-001 makes it a setting; the default stays path-style, which the bundled store uses.
- **A credential variable name outside the documented list.** It does not reach the worker, and
  a run that selects it fails with the existing refusal that names the variable. The same holds
  for two packages of one adapter that need different credentials under names the list does not
  hold. The documentation says to use the listed names.
- **An external endpoint set without the setting that leaves the bundled store out.** The bundled
  store takes `INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY` as its own root
  credential (`docker-compose.yml:133-134`), so it would receive the credential of the team's
  store. FR-002 requires `sync-bootstrap` to stop in that case.
- **The bundled store left out while the endpoint keeps its default.** No service answers at
  `object-store`. FR-002 requires `sync-bootstrap` to stop with a failure name that says to set
  the endpoint, instead of the general "object store unavailable" name.
- **A credential variable with an empty value.** It reaches the worker as today; the resolver
  refuses an absent or empty value by name when a run selects it, so the container still starts.
- **The CA directory exists but holds no certificate file.** Each Sync service logs one info line
  and starts with the default trust store.
- **The CA directory cannot be read by the container user (UID 10001).** The service stops with
  an error that names the directory. The documentation says which permissions it needs.
- **A short credential value.** A credential shorter than six characters can still appear in the
  worker container log and the Prefect log (decision 2). The leak checks of FR-018 use credentials
  of six characters or more for those two logs, and say so.
- **No linux/arm64 runner for the lifecycle tier in CI.** Today the arm64 runner is used only for
  the image build (`ci-docker-image.yml:108-109`). Whether it can run Docker Compose with the tier's
  services is not known; the plan finds out.

## Requirements *(mandatory)*

### Functional Requirements

#### External S3-compatible store

- **FR-001**: An operator MUST be able to point the Sync services that use the object store at an S3-compatible store
  outside the deployment from `.env`, without an edit to the shipped `docker-compose.yml`. Next to
  the bucket, key prefix, region, access key and secret key that `.env` already sets, `.env` MUST
  accept:
  - the endpoint URL
  - the addressing style: path-style or virtual-hosted-style requests

  When neither is set, Sync uses the bundled store with path-style requests, as today.
- **FR-002**: A documented `.env` setting MUST leave the bundled store out. With it, Compose
  creates no `object-store-init` or `object-store` container, no Sync service waits for them, and
  the external store's credential reaches no Silo process. Without it, the bundled store starts as
  today. When the endpoint names a host other than the bundled store while the bundled store
  runs, `sync-bootstrap` MUST stop before any S3 request with a fixed failure name that says to
  leave the bundled store out, because the bundled store has then received the external store's
  credential as its root credential. When the bundled store is left out while the endpoint still
  names it, `sync-bootstrap` MUST stop before any S3 request with a fixed failure name that says
  to set the endpoint.
- **FR-003**: `sync-bootstrap` MUST check whether the bucket exists before it tries to create it,
  and create it only when the store reports it absent. It MUST complete when the bucket exists and
  the credential may not create buckets.
- **FR-004**: `sync-bootstrap` MUST check that the store refuses a second create-only write of the
  same key, and stop with a fixed failure name when it does not. The check MUST leave no object
  behind once it completes, whether it passes or fails.
- **FR-005**: `sync-bootstrap` MUST refuse an endpoint URL that carries a user name or a password
  before it sends any S3 request, with a fixed failure name and the fixed message that
  `storage.py:198` uses for the Sync API and the worker. The message names the setting and not its
  value.
- **FR-006**: Each service of FR-011 MUST verify the certificate of an `https` endpoint against the
  image's default trust store plus the CA bundle of FR-011. No Sync setting turns certificate
  checks off for the object store.
- **FR-007**: `docs/docs/compose-deployment.mdx` MUST:
  - list the settings of FR-001 and FR-002 and show them set together
  - list the S3 operations and behaviors Sync relies on, as the plan records them
  - say that moving from the bundled store to an external store does not copy earlier plans and
    run files
  - recommend `https` for an external store
  - name the store OpsMill tests the deployment with, and name no other product as supported
    unless a test covers it
  - say, in the paragraph on the bundled Silo store, that a team can use its own S3-compatible
    store instead

#### Credentials of every bundled adapter

- **FR-008**: The root and development compose files MUST pass to `sync-worker`, when the operator
  sets them in `.env` and without an edit to the shipped file, every credential variable on one
  documented list. The list MUST hold:
  - every credential variable name that a shipped example package (`examples/*/package.yml`) or
    an adapter documentation page (`docs/docs/adapters/*.mdx`) uses, so those packages run
    unedited
  - at least one name for each credential setting of each bundled adapter: `aci`,
    `genericrestapi`, `infrahub`, `ipfabricsync`, `nautobot`, `netbox`, `peeringmanager`,
    `prometheus` and `slurpitsync` (`infrahub_sync/configuration/capabilities.py:522-606`)

  A variable that is not on the list does not reach the worker. ADR 0016 keeps the operator
  deployment to one self-contained file with no secret files, so the list is part of that file
  and adds no second file.
- **FR-009**: The plan MUST list which services receive each credential variable and each storage,
  database and Prefect credential. A service that does not read a credential MUST NOT receive it.
  `prefect-server` MUST receive no object store credential and no adapter credential, as today
  (010 FR-003). The Infrahub token keeps reaching `sync-api` as today.
- **FR-010**: `docs/docs/compose-deployment.mdx` MUST list the credential variables of FR-008 by
  adapter, say that a package for the Compose deployment uses those names, say which services
  receive which credentials, and say how this relates to the
  check of specification 020 FR-035, which reports a `$credential` reference that no live worker
  holds.

#### Private certificate authority

- **FR-011**: The operator MUST be able to name in `.env` a CA bundle that the Sync services that
  make outgoing TLS connections (`sync-bootstrap`, `sync-api` and `sync-worker`) trust for
  connections to sources, to Infrahub and to the object store, in addition to the image's
  default trust store, without an edit to the shipped `docker-compose.yml` and without turning off
  certificate checks.
- **FR-012**: Each service of FR-011 MUST refuse to start when a file in the CA bundle holds a
  private key. The error MUST name the file and print none of its content.
- **FR-013**: At every start, each service of FR-011 MUST log:
  - the number of certificates it loaded and the bundle path
  - one info line when the directory exists but holds no certificate file
  - for each of `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` and `INFRAHUB_TLS_CA_FILE`
    that was already set and so overrides the bundle, a warning that names the variable and not its
    value
- **FR-014**: `docs/docs/compose-deployment.mdx` MUST say how to add a CA bundle, that user ID
  10001 must be able to list the CA directory and read its files, and that a change to the files
  takes effect only after `sync-api` and `sync-worker` restart, because `docker compose up -d`
  starts only `sync-bootstrap` again.
- **FR-015**: `docs/docs/custom-certificates.mdx` MUST replace the section "Current limit of the
  Compose deployment" (`:28-35`) with the setting of FR-011, for both the root and the development
  compose files.

#### Credential values kept out of text

- **FR-016**: Each credential value MUST reach only the services that read it (FR-002, FR-009).
  No code this specification adds MAY write a credential value, or text that could contain one, to
  a log, a message, a file or a Prefect record. The CA setup logs a certificate count, a path and
  the names of trust variables, never their values.
- **FR-017**: No credential value MAY pass through Prefect deployment or flow-run job variables or
  parameters, which the Prefect server stores. The documentation MUST NOT tell an operator to run a
  command that prints interpolated credential values, such as `docker compose config` without
  `--quiet`, or to share `docker inspect` output.
- **FR-018**: The lifecycle tier MUST check, for each credential this specification passes (store
  keys, adapter credential variables, the Infrahub token), that its value appears:
  - in the output of no documented command
  - in no container log, with one exception: a credential shorter than six characters in the
    `sync-worker` and `prefect-server` logs, which specification 020 leaves to 3.0 work (decision
    2). The checks for those two logs use credentials of six characters or more and say so.
  - in the environment of no service outside the receiver list of FR-009

#### Compose lifecycle tier

- **FR-019**: The causes of the random failures of the lifecycle tier MUST be found and fixed or
  removed. Each cause MUST be recorded in `research.md` with its fix and the test that shows it.
- **FR-020**: CI MUST run the lifecycle tier on linux/amd64 and on linux/arm64. The tier MUST cover
  the bundled store and an external store, the latter with path-style and with virtual-hosted-style
  requests.

#### Released image

- **FR-021**: The release notes of each Sync release MUST name the image by the address users pull,
  `registry.opsmill.io/opsmill/infrahub-sync`, and its multi-architecture digest, which
  `ci-docker-image.yml` already outputs, and the `cosign verify-attestation` command that reads the
  SBOM attested to that digest, so that an evaluator can find the SBOM of that release.
- **FR-022**: The documented `cosign` verification (`docs/docs/container-image.mdx`) and the release
  notes MUST accept only the certificate identity of `ci-docker-image.yml` at a release tag:
  `^https://github\.com/opsmill/infrahub-sync/\.github/workflows/ci-docker-image\.yml@refs/tags/`.

#### Image pins

- **FR-023**: The root compose file, its packaged copy
  (`infrahub_sync/testcontainers/docker-compose.yml`) and `development/docker-compose.yml` MUST name
  every image other than the Sync image with a `sha256` digest. Out of scope: the Infrahub fixture
  files (`development/docker-compose.infrahub.yml`, `development/docker-compose.infrahub-ports.yml`),
  which `update-infrahub.yml` maintains; the NetBox file
  (`development/netbox/docker-compose.netbox.yml`), already pinned; and the preview file, which
  specification 010 deletes.
- **FR-024**: `development/docker-compose.yml` MUST pin its PostgreSQL image by the `sha256` digest
  of its multi-architecture index (decision 3).

#### Tests and records

- **FR-025**: The compose contract tests (`tests/compose/test_compose_contract.py`) MUST check:
  - with the setting of FR-002, the rendered model has no `object-store-init` or `object-store`
    service that runs, and every Sync service still resolves
  - the endpoint and addressing-style settings of FR-001 reach every Sync service that uses the
    store
  - each credential reaches only the services the receiver list of FR-009 names
  - the credential variable list of FR-008 holds every name the shipped examples and adapter
    pages use and a name for each credential setting in the adapter capability registry
  - the CA setting of FR-011 reaches `sync-bootstrap`, `sync-api` and `sync-worker`, and only
    them
  - every image other than the Sync image is pinned by digest in the three files of FR-023
- **FR-026**: The image tests (`tests/image`) MUST check that the Sync image starts the Prefect
  server with a read-only root filesystem and serves `/api/health`.
- **FR-027**: A decision record MUST record the external store and CA settings and how they fit
  ADR 0016. It takes the next free number in `dev/adr/` (0026: 0024 is used, specification 020
  uses 0020 to 0022 and 0025, and old 013's 0023 exists only on its unmerged branch). It cites no
  internal sources.
- **FR-028**: The change MUST add a news fragment under `changelog/` that names the external store
  settings, the setting that leaves the bundled store out, the credential variables reaching the
  worker, the CA setting and the release notes digest.

### Key Entities

- **Bundled store**: `object-store-init` and `object-store`, running Silo pinned by digest. Can be
  left out (FR-002).
- **External store**: an S3-compatible store that the customer team runs; Sync holds one access key
  and secret key for one bucket in it.
- **Credential variable**: an `INFRAHUB_SYNC_CREDENTIAL_<NAME>` environment variable that a
  registered package references. The Compose deployment passes the names on the list of FR-008.
- **Receiver list**: the list, in the plan, of which service receives each credential (FR-009).
- **CA bundle**: public CA certificates in a directory that `.env` names; never a private key.
- **Lifecycle tier**: `uv run pytest -m compose tests/compose`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the bundled store left out, the root compose file pulls two distinct images: the
  Sync image and PostgreSQL. With the bundled store, it pulls three.
- **SC-002**: A plan and an apply complete on the root compose file against an S3-compatible store
  outside the deployment, both with path-style and with virtual-hosted-style requests, with zero
  bundled store containers.
- **SC-003**: For every bundled adapter, a package whose credential variables are set in `.env`
  resolves every credential reference in `sync-worker` on the root compose file, with no edit to
  `docker-compose.yml`.
- **SC-004**: A run reaches a source and Infrahub, and Sync reaches an external store, that present
  a certificate from a private CA once that CA bundle is named in `.env`, with certificate checks
  on.
- **SC-005**: In the lifecycle tier, the leak checks of FR-018 find zero credential values in the
  output of every documented command, in every container log, and in the environment of every
  service outside the receiver list, within the boundary of decision 2.
- **SC-006**: The lifecycle tier passes 10 runs in a row on linux/amd64 and 10 runs in a row on
  linux/arm64 in CI.
- **SC-007**: The release notes of the next release name the Sync image digest, and that digest
  pulls the image and its SBOM from `registry.opsmill.io`.
- **SC-008**: The compose contract tests find zero images other than the Sync image without a
  digest across the three compose files of FR-023.

## Assumptions

- Specification 010 "PR 2" is finished before work on this specification starts, so the preview
  stack and its `prefecthq/prefect` reference no longer exist (decision 1).
- OpsMill tests the external store with an S3-compatible server outside the deployment that stands
  in for a customer store. No source names the S3-compatible products customers run, so this
  specification promises no product by name.
- The bundled store keeps path-style requests and the endpoint `http://object-store:9000` when no
  external store setting is set.
- The CA bundle is a directory mounted read-only into the Sync services; whether a single file is
  also accepted is a plan decision.
- The 10-run threshold of SC-006 comes from old 013 SC-010; no other source sets one.
- Customer teams use only the adapters that Sync bundles in the testing release.

## Dependencies

- **Specification 010 "PR 2".** Tasks T026, T028, T036, T037, T039, T061 to T064 and T066 of
  [010 tasks.md](../010-prefect-from-sync-image/tasks.md) must be done first (decision 1). Until
  T039 is done, `development/docker-compose.preview.yml` still runs `prefecthq/prefect` and the
  bundled store from a preview file, and FR-023 cannot pass. Specification 019 still ran the
  preview tier on 2026-11-10, so the tests it runs there move under `dev.smoke` with T039.
- **Specification 020 (not merged).** Its FR-035 reports a `$credential` reference that no live
  worker holds, and its FR-014 adds the failure name `tls_certificate_rejected`. This specification
  passes the variables and the CA bundle; it does not change those reports. If 020 merges first,
  the documentation of FR-010 refers to its check; if not, FR-010 refers to it as planned.
- **A linux/arm64 CI runner that can run Docker Compose.** Not known yet (FR-020); the plan finds
  out.
- **Jira card INFP-692.** It still lists "temporary MinIO build" as a blocker (010 spec.md:534).
  Updating the card is outside this repository.

## Out of Scope

- The short-credential leak into the worker container log and the Prefect log (decision 2; 3.0
  work in specification 020).
- Finishing specification 010 "PR 2" (decision 1).
- Copying plans and run files from the bundled store to an external store.
- Testing against or naming specific S3-compatible products.
- Turning certificate checks off for any connection.
- Replacing Silo with another bundled store.
- Kubernetes deployments.

## Traceability to old specification 013

| Old 013 | This specification | Note |
| --- | --- | --- |
| FR-001 to FR-009, FR-021, FR-038, FR-039 | none | Delivered or delivered differently by 010 |
| FR-010, FR-011 | none | Close with 010 T039 (decision 1) |
| FR-012, FR-019, FR-036, FR-037 | none | Obsolete by 010 D-5, D-14, FR-015 |
| FR-013, FR-015, FR-020 | none | Delivered differently by 010 |
| FR-014 | FR-023, FR-024 | Development PostgreSQL pin added (decision 3) |
| FR-016 | FR-007 | Last bullet; Silo instead of MinIO |
| FR-017 | FR-025 | Root file items already delivered by 010 |
| FR-018 | FR-020, FR-026 | |
| FR-022 | FR-028 | Prefect image part delivered by 010 |
| FR-023 | FR-008, FR-009 | List built from the shipped examples, adapter pages and capability registry, not from old 012's naming rule |
| FR-024 | FR-011, FR-012, FR-013 | |
| FR-025 | FR-010, FR-014 | First-start wait delivered by 010 |
| FR-026 | FR-019 | |
| FR-027 | FR-021, FR-022 | Build, signing and SBOM delivered by 007 |
| FR-028 | FR-001 | Bucket, prefix, region and keys already in `.env` |
| FR-029 | FR-002 | |
| FR-030 | FR-006 | |
| FR-031 | FR-003 | |
| FR-032 | FR-004 | |
| FR-033 | FR-007 | |
| FR-034 | FR-005 | |
| FR-035 | FR-016, FR-017, FR-018 | Boundary of decision 2 |
| SC-001 | SC-001 | Silo instead of MinIO |
| SC-002, SC-003, SC-007 | none | Delivered by 010 |
| SC-004 | SC-008 | Preview part closes with 010 T039 |
| SC-005, SC-010 | SC-006 | |
| SC-006 | none | Obsolete by 010 D-14 |
| SC-008 | SC-003 | |
| SC-009 | SC-004 | |
| SC-011 | SC-007 | |
| SC-012 | SC-002 | |
| SC-013 | SC-005 | Boundary of decision 2 |
