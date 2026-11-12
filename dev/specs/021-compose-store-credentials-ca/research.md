# Research: an external object store, every adapter's credentials and a private CA

Checked against `feature/v3-develop` at `7d7aabba`. Old specification 013's research
(`tmp-sync-specs/013-compose-images/research.md`, R6 to R13) was written against the MinIO build and
`development/docker-compose.dev.yml` at `4646b716`. Its decisions are reused below where they still
hold; every path and line was checked again on `7d7aabba`.

## R1. External S3-compatible store settings (FR-001, FR-002)

### What the code does today

| Concern | Code at `7d7aabba` | Gap |
| --- | --- | --- |
| Endpoint URL | `INFRAHUB_SYNC_S3_ENDPOINT_URL`, validated by `_endpoint` (`infrahub_sync/service/storage.py:193-205`): absolute `http` or `https`, no user name or password | fixed to `http://object-store:9000` in the root file (`docker-compose.yml:49`) and the development file (`development/docker-compose.yml:81`) |
| Bucket, prefix, region | `docker-compose.yml:47-48`, `:50`, read in `storage.py:217-221` | none |
| Access key and secret key | `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from `INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY` (`docker-compose.yml:51-52`) | the same two values are the bundled store's root credential (`docker-compose.yml:133-134`) |
| Addressing style | clients are built with `endpoint_url` and `region_name` only (`storage.py:224`, `bootstrap.py:146-150`). With an endpoint URL, botocore uses path-style requests unless the client `Config` sets `s3.addressing_style` to `virtual`; that setting has no environment variable | missing |
| Bundled store dependency | `sync-bootstrap` waits for `object-store` to be healthy (`docker-compose.yml:216-217`) | missing |

### Decision

1. **Settings** in `x-sync-storage` of the root and development files:
   - `INFRAHUB_SYNC_S3_ENDPOINT_URL: "${INFRAHUB_SYNC_S3_ENDPOINT_URL:-http://object-store:9000}"`
   - `INFRAHUB_SYNC_S3_ADDRESSING_STYLE: "${INFRAHUB_SYNC_S3_ADDRESSING_STYLE:-path}"`, values `path`
     and `virtual`
   - bucket, prefix, region and both keys keep their names and defaults.
2. **One S3 client builder.** `storage.py` gains `build_s3_client(values, builder=boto3.client)`.
   It validates the endpoint with `_endpoint` (FR-005), reads the region, validates the addressing
   style (any other value raises `ValueError` with a fixed message that names the setting, not the
   value), and passes `botocore.config.Config(s3={"addressing_style": style})`.
   `service_product_projection` and bootstrap's `_converge` both call it, so the API, the worker and
   bootstrap build the client the same way, and bootstrap checks the endpoint before any request.
3. **Leaving out the bundled store.** `object-store-init` and `object-store` get
   `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}`, and `sync-bootstrap`'s dependency on
   `object-store` gets `required: false`. With `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, Compose creates
   neither container, so no Silo process receives the credential, and bootstrap starts without
   waiting.
4. **Guard against a half-set external store.** Compose cannot tie the endpoint setting to the scale
   setting. Before any S3 request, bootstrap checks whether the endpoint host is something other than
   `object-store` while the name `object-store` resolves (`socket.getaddrinfo`). If so, it stops with
   the new fixed family `bundled-object-store-not-left-out`. Silo has already received the credential
   at that point; the guard makes the operator correct `.env` at the first start instead of running in
   that state.

Old 013 probed `scale: ${VAR:-1}` with `depends_on: {condition: service_healthy, required: false}` on
Compose 5.1.2: unset creates both services; `0` creates only the dependent one. The repository's
minimum is Compose 2.24.0 (`tests/compose/test_compose_minimum.py:42`). `depends_on.required` exists
since Compose 2.20; whether 2.24.0 honors an interpolated `scale` of `0` the same way is not
verified. The first implementation task runs the probe on the pinned 2.24.0 image through
`test_compose_minimum.py`'s helper. If 2.24.0 does not honor it, the fallback is to raise the minimum
version, recorded in ADR 0026.

**How this fits ADR 0016**: every new value is an optional `.env` setting of the one file.

**Alternatives considered**:

- A Compose profile for the bundled store. Rejected: a service with a profile starts only when
  `COMPOSE_PROFILES` names it, so the bundled store would stop starting for every existing `.env`.
- `--scale object-store=0` on each command. Rejected: forgetting it on one `up` starts Silo with the
  customer store's credential as its root credential.
- A second Compose file for the external store. Rejected: it cannot remove a service.
- An `auto` addressing style. Not added: with an endpoint URL botocore treats `auto` as `path`.
- Separate `.env` names for the external credential. Not added: the two existing settings already
  mean "the credential Sync uses for its bucket".

## R2. Bootstrap bucket check and create-only check (FR-003, FR-004)

### S3 operations and behaviors Sync relies on

From `infrahub_sync/service/storage.py:245-324` and `infrahub_sync/service/bootstrap.py:79-90`. A
search of `infrahub_sync/` finds no `list_objects`, multipart upload or presigned URL call.

| Operation | Behavior Sync relies on | Where |
| --- | --- | --- |
| `PutObject` | single request, whole body | `storage.py:251-256` |
| `PutObject` with `If-None-Match: *` | a second write of an existing key is refused with 412 `PreconditionFailed`; 409 `ConditionalRequestConflict` is retried | `storage.py:257-269` |
| `GetObject` | whole body; a missing key answers `NoSuchKey` with 404 | `storage.py:271-316` |
| `HeadObject` | `ContentLength`; a missing key answers 404 | `storage.py:288-299` |
| `CopyObject` | copy within the bucket | `storage.py:318-320` |
| `DeleteObject` | delete one key | `storage.py:322-324` |
| `HeadBucket` | bootstrap only, new | this decision |
| `CreateBucket` | bootstrap only, when `HeadBucket` reports the bucket absent | this decision |

A missing key must answer 404, not 403. On stores with S3-style access policies that usually means
the credential may list the bucket; the documentation states the behavior, not a product's
permission names.

### Decision

1. `converge_bucket` calls `HeadBucket` first. A 2xx answer means present. A 404 means absent: it then
   calls `CreateBucket`, with `CreateBucketConfiguration={"LocationConstraint": region}` when the
   region is set and is not `us-east-1`, and keeps the two "already present" codes
   (`bootstrap.py:52`). A 403 on `HeadBucket` stops with the new fixed family
   `object-store-access-refused`.
2. After the bucket step, bootstrap writes `<prefix>/.infrahub-sync/bootstrap-check/<uuid4>` twice
   through `Boto3S3Client.put(if_absent=True)`. The second write must raise `DuplicateArtifactError`.
   If it does not, bootstrap stops with the new fixed family `object-store-unsupported`. The key is
   deleted in a `finally` block. The check runs at every bootstrap and costs four requests.

The old implementation of both steps is commit `05b1fbf5` on branch `013-compose-images`; it is
reused, with Silo in place of MinIO in tests and comments.

## R3. Which service receives which credential (FR-008, FR-009)

**Decision**: the root and development files pass each credential only to the services that read it.
The full table is in [contracts/environment-variables.md](contracts/environment-variables.md).

- `sync-worker` receives 24 `INFRAHUB_SYNC_CREDENTIAL_*` variables, each as `"${NAME:-}"`. The list is
  built from three sources, in this order:
  1. every name that a shipped example package or an adapter documentation page uses
     (`examples/*/package.yml`, `docs/docs/adapters/*.mdx`): 16 names, among them
     `CISCO_APIC_USERNAME`, `SLURPIT_API_KEY`, `IPF_TOKEN`, `REST_API_TOKEN` and the generic REST API
     pages for Device42, LibreNMS, Observium and PeeringDB
  2. for each credential setting of each bundled adapter in
     `infrahub_sync/configuration/capabilities.py:522-606` that step 1 does not cover, one name
     `<ADAPTER>_<SETTING>` following the adapter page's prefix: `REST_API_USERNAME`,
     `REST_API_PASSWORD`, `PEERING_MANAGER_USERNAME`, `PEERING_MANAGER_PASSWORD`, `PROMETHEUS_TOKEN`,
     `PROMETHEUS_USERNAME`, `PROMETHEUS_PASSWORD`, `SLURPIT_TOKEN`
  3. the test-only names in `tests/compose` are not added.
- `sync-api` keeps only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, as today, because the
  configuration validation route reads the destination schema there.
- `sync-bootstrap`, `prefect-server`, `cli`, `postgres` and `db-bootstrap`: unchanged, no adapter
  credential.
- `object-store`: the S3 credential, only when the bundled store runs (R1).

A contract test derives the expected names from the capability registry, the example packages and the
adapter pages, and fails when a name is missing from the worker or reaches another service. It
replaces the per-name tests at `tests/compose/test_compose_contract.py:1046` and `:1077`.

**How this fits ADR 0016**: the variables are optional settings of the one file, read from the `.env`
beside it. No second file.

**Alternatives considered**:

- `env_file: .env` on `sync-worker`. Rejected: it passes every `.env` value, including the PostgreSQL
  administrator and Prefect database passwords, to the worker.
- A worker-only credential file named in `.env`. Rejected: it is the kind of secret file ADR 0016
  removed, and it would let any name through without a review.
- Old specification 012's naming rule (`<ADAPTER>_<SETTING>` for every adapter). Rejected: it does not
  match the names the shipped packages use, such as `CISCO_APIC_USERNAME` instead of `ACI_USERNAME`.

## R4. Private certificate authority (FR-006, FR-011 to FR-015)

**Decision**:

- New optional `.env` setting `INFRAHUB_SYNC_CA_CERTIFICATES`: a host directory that holds one or more
  PEM files (`*.pem`, `*.crt`). The value must be an absolute path or start with `./`.
- `sync-bootstrap`, `sync-api` and `sync-worker` mount it read-only at `/etc/infrahub-sync/ca`:
  `"${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro"`. When unset, the
  source is the empty named volume `sync-ca-certificates`.
- The three services get `INFRAHUB_SYNC_CA_DIRECTORY: "/etc/infrahub-sync/ca"`.
- New module `infrahub_sync/service/trust_store.py`, function `install_custom_ca_bundle()`.
  `bootstrap.main()` (`bootstrap.py:182`), `serve.main()` (`serve.py:94`) and `worker.main()`
  (`worker.py:465`) call it first, before any client exists. When the directory holds at least one
  PEM file, it:
  1. refuses a file that contains a `PRIVATE KEY` PEM block, and stops the process with an error that
     names the file and prints none of its content
  2. checks that each file loads with `ssl.SSLContext.load_verify_locations`, and otherwise stops with
     an error that names the file and the `ssl` error class
  3. writes the `certifi.where()` content followed by the operator's files to
     `$TMPDIR/infrahub-sync-ca-bundle.pem` (`TMPDIR=/tmp/infrahub-sync`, tmpfs, `Dockerfile:109`), mode
     `0600`
  4. sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` and `INFRAHUB_TLS_CA_FILE` in
     `os.environ` to that file, unless the variable is already set, in which case it logs a warning
     that names the variable
  5. logs the number of certificates and the bundle path with `structlog`

  An empty directory logs one info line and changes nothing; an absent directory changes nothing.

**Rationale** (library versions from `uv.lock`):

- `requests` 2.34.2 (NetBox, Nautobot, ACI): `REQUESTS_CA_BUNDLE` when `verify` is true.
- `httpx` 0.28.1 (IP Fabric, Prometheus, the generic REST API adapter where it uses `httpx`):
  `SSL_CERT_FILE` when `trust_env` is true.
- Infrahub SDK 1.23.2: `INFRAHUB_TLS_CA_FILE`, otherwise `ssl.create_default_context()`, which reads
  `SSL_CERT_FILE`. `docs/docs/custom-certificates.mdx:25-26` documents both.
- botocore 1.43.118: `AWS_CA_BUNDLE`, falling back to `REQUESTS_CA_BUNDLE`.
- Each variable replaces the default store instead of adding to it, so the bundle starts from
  `certifi` (2026.10.19). FR-011 asks for the operator's CA in addition to the default store.
- Flow runs inherit the worker's environment: the Prefect process worker builds the child environment
  from `os.environ`.
- No Sync setting passes `verify=False` to the S3 client (FR-006).

Which adapters build their HTTP client with `trust_env` off, if any, is not verified; a task checks
each bundled adapter against the test CA in the lifecycle tier (SC-004).

**Change to existing contract properties**: these tests hold properties that the CA mount narrows.
Each is narrowed to writable mounts, with the CA mount as the one read-only exception on the three
services:

- `test_the_api_and_the_worker_share_no_mount_source` (`tests/compose/test_compose_contract.py:171`)
- `test_a_sync_service_mounts_nothing_at_all` (`:185`)
- `test_only_postgresql_and_the_object_store_keep_a_named_volume` (`:211`)
- `test_no_service_mounts_a_host_path` (`:252`)

The compose file header that states the first two properties is updated with them. ADR 0026 records
the change.

**Alternatives considered**: a file setting bound to a file target that defaults to `/dev/null`
(Docker Desktop does not share `/dev`); a default host directory (Compose creates a missing bind
source as root); PEM content in a `.env` variable (hard to write correctly); pointing `SSL_CERT_FILE`
at the operator's file directly (drops the public roots); installing the CA into the system store at
start (read-only root filesystem, non-root user).

## R5. Keeping credential values out of text (FR-016 to FR-018)

**Decision**: this specification relies on no value masking. Each measure keeps a credential out of
text by construction:

| Where text is produced | Measure |
| --- | --- |
| Container environments | each credential reaches only the services that read it (R3); with the bundled store left out, no Silo container exists (R1) |
| Compose output | a missing required setting is reported by name through `:?`; `up` prints no environment |
| Documented commands | the documentation uses `docker compose config --quiet` and says not to share `docker compose config` or `docker inspect` output |
| Bootstrap failures | fixed family names and fixed messages, no provider text (`bootstrap.py:55-77`); the new families `bundled-object-store-not-left-out`, `object-store-access-refused` and `object-store-unsupported` follow the same rule |
| Prefect records | the worker refuses a flow run that carries job variables (`worker.py:125`); this plan adds no deployment parameter |
| CA setup | logs a count, a path and variable names; errors name a file and an `ssl` error class |
| Endpoint URL | a user name or password in the URL is refused in every service that builds an S3 client, bootstrap included (R1) |

**Leak checks** (FR-018): the lifecycle tier generates a distinct value of 24 or more characters for
each credential it sets, then searches the output of each documented command, every container log
(`docker compose logs`) and each service's environment (`docker inspect`, read by the test and never
printed) for those values, and reports the names it finds. The existing helper
`tests/compose/redaction.py` already searches raw output for generated values; the new checks extend
it to logs and environments. The worker and Prefect logs are checked with long values only, because a
value shorter than six characters is not masked there (decision 2 of the spec).

## R6. Compose lifecycle tier reliability (FR-019, FR-020)

**What the sources say**:

- The tier runs only in the `compose-suite` job of `.github/workflows/workflow-nightly-e2e.yml:189-254`,
  on `ubuntu-22.04`, with `-x`, once a night.
- One cause was fixed: commit `bbdd103e` "Make the Compose preflight interruption test tolerate a busy
  machine".
- No record of other failures was found in the repository.
- The arm64 runner `ubuntu-24.04-arm` is used for the image build (`ci-docker-image.yml:108-109`).
  Whether it runs Docker Compose with the tier's services is not known.

**Candidate causes found by reading the tier** (confirmed or ruled out by measurement first):

| # | Candidate | Where |
| --- | --- | --- |
| C1 | Fixed host ports shared by test modules; a project left over from an interrupted run holds the port | `tests/compose/lifecycle.py`, `test_configuration_free_startup.py` |
| C2 | `up --quiet-pull` pulls from several registries in every test deployment; a registry error or an anonymous pull limit fails `up` | `tests/compose/conftest.py` |
| C3 | One readiness bound covers a cold Infrahub fixture start and the deployment start on a shared runner | `tests/compose/lifecycle.py`, `test_readiness_bounds.py` |
| C4 | Teardown removes a project only when its fixture finishes; an aborted session leaves containers and volumes with the same project names | `tests/compose/lifecycle.py` |

**Decision**: the first lifecycle task adds a manual dispatch with a repeat count and an architecture
choice to `workflow-nightly-e2e.yml`, and runs the tier 10 times on each architecture before any
reliability change. Each failure is classified and recorded in this file under "Lifecycle failure
causes" with its fix and test. SC-006 is then measured with the same dispatch after the changes. The
nightly job gains an arm64 leg.

### Lifecycle failure causes

To be filled in during implementation.

## R7. Release notes digest and cosign identity (FR-021, FR-022)

**What exists**: `ci-docker-image.yml` builds both platforms, merges the manifest list and exposes its
digest as the workflow output `digest` (`:44-45`, `:233-234`), signs it and attaches SBOMs.
`workflow-publish.yml:178-184` calls it on release. The GitHub Release is created earlier, by
`release-publish.yml:214-217`, with the changelog section as its body.

**Decision**: add a job `record_image` to `workflow-publish.yml` after `publish_docker_image`, run only
when the release is published. It appends a "Container image" section to the GitHub Release body with
`registry.opsmill.io/opsmill/infrahub-sync:<version>@<digest>` and the `cosign verify` command with
`--certificate-identity-regexp '^https://github\.com/opsmill/infrahub-sync/\.github/workflows/ci-docker-image\.yml@refs/tags/'`.
The job needs `contents: write` (today `workflow-publish.yml:60` grants `contents: read`). It handles
no credential beyond the workflow's `GITHUB_TOKEN`, and prints none. `docs/docs/container-image.mdx:136`,
`:144` and `:149` use the same identity.

**Alternatives considered**: writing the digest into the changelog fragment (the digest does not exist
then); creating the release after the image (reorders the release process of ADR 0015 and
`RELEASING.md`).

## R8. Image pins (FR-023, FR-024)

**Decision**: `development/docker-compose.yml:96` names `postgres:16-alpine` with the `sha256` digest
of its multi-architecture index, resolved with `docker buildx imagetools inspect` at implementation
time and checked to list linux/amd64 and linux/arm64. `test_every_image_other_than_sync_is_named_by_digest`
(`tests/compose/test_compose_contract.py:636`) runs over every compose file in the repository, not only
the root file. The root file's PostgreSQL pin is the reference for the tag.

## R9. Packaged copy of the root compose file

`infrahub_sync/testcontainers/docker-compose.yml` must equal the root file
(`tests/testcontainers/test_packaging.py:20-26`). Every change to the root file is copied there in the
same commit.

## R10. Decision record (FR-027)

Next free number: 0026. 0024 is used on `feature/v3-develop`; specification 020 uses 0020, 0021, 0022
and 0025 on its branch; 0017 and 0018 are held by drafts named elsewhere (specification 020 decision
5); old 013's draft 0023 exists only on its unmerged branch. Draft text, to be written into
`dev/adr/0026-...md` during implementation:

> # 26. The Compose deployment takes an external object store, adapter credentials and CA certificates from `.env`
>
> **Status**: Proposed
>
> ## Context
>
> ADR 16 makes the operator deployment one file and a `.env`. Sync could store its files only in the
> bundled store of that file, the worker received the credentials of three adapters, the Sync
> services trusted only the image's certificate authorities, and no service mounted a host path.
>
> ## Decision
>
> - An operator may point Sync at an S3-compatible store with `.env` settings for the endpoint and
>   addressing style, beside the existing bucket, prefix, region and credential settings, and leave
>   the bundled store out with `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`. Left out, Compose creates no
>   bundled store container, so the store's credential reaches no bundled store process.
> - The worker receives a fixed list of credential variables, named in the file, that covers the
>   credential settings of every bundled adapter and the names the shipped examples use. A name that
>   is not on the list does not reach the worker.
> - An operator may name a host directory of PEM files with `INFRAHUB_SYNC_CA_CERTIFICATES`.
>   `sync-bootstrap`, `sync-api` and `sync-worker` mount it read-only. Unset, they mount an empty
>   named volume. At start, each process builds one bundle from the public roots and those files on
>   its scratch root. A file that holds a private key stops the process.
>
> ## Consequences
>
> - A deployment with none of these settings runs as before.
> - The Sync services share one read-only mount source. They still share no writable mount.
> - An unreadable or invalid certificate file stops the process at start, naming the file.
> - Moving to an external store does not move earlier files.
> - A package for the Compose deployment uses the listed credential names.
>
> ## Alternatives considered
>
> A Compose profile for the bundled store; a per-command scale flag; a worker-only credential file;
> passing all of `.env` to the worker; a `.env` value holding the PEM text; a file setting that
> defaults to `/dev/null`; a fixed host directory created on first start.

The record cites no internal sources.

## R11. Agent context file

The SPECKIT block in `CLAUDE.md` names the active specification. It is updated to this plan on this
branch.
