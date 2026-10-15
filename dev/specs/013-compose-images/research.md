# Research: Fewer images in the Compose deployment

All file references are to commit `4646b716` unless stated otherwise. The code, compose files,
tests, documentation and workflows cited here are unchanged between `d9e83cb2`, where the first
plan's probes ran, and `4646b716`. Probes used the local image `infrahub-sync:dev` (Prefect
3.8.6, uvicorn 0.52.0, boto3 and botocore 1.43.83) on Docker 29.4.0 with Compose 5.1.2,
linux/arm64. Probe files lived in the session scratchpad and are not part of the repository.

This is the second version of this research. The product owner's decisions of 2026-10-15
(spec, "Product owner decisions of 2026-10-15") removed the replacement object store image
(former R5) and the reliance on credential masking (former R6 and R7, "Masking"). R5 to R8 and
R13 are new or rewritten; R1 to R4 and R9 to R11 carry over from the first version.

## R1. How the Prefect server starts from the Sync image

**Decision**: add `infrahub_sync/service/prefect_server.py` with two entry points:

- `create_prefect_server() -> FastAPI`, which returns `prefect.server.api.server.create_app(final=True)`
  and adds no route and no bootstrap step (FR-003).
- `main()`, run as `python -m infrahub_sync.service.prefect_server`, which calls
  `uvicorn.run("infrahub_sync.service.prefect_server:create_prefect_server", factory=True, ...)`
  with the host, port, keep-alive and WebSocket ping values that `prefect server start` passes.

Every compose file runs the `python -m` form. `uvicorn --factory infrahub_sync.service.prefect_server:create_prefect_server`
also works and is the form the image test exercises once.

**Rationale**:

- `prefect server start` with one worker calls `uvicorn.run(app=create_app(final=True, webserver_only=no_services), ...)`
  (`prefect/cli/_server_utils.py:322-352`). With no `--no-services` flag, `webserver_only` is
  false, so the factory reproduces the same call.
- `create_app` runs the database migration in its lifespan when
  `PREFECT_API_DATABASE_MIGRATE_ON_START` is true (the default), registers block types, and
  enters `Service.running()`, which starts every enabled background service
  (`prefect/server/api/server.py:896-955`). FR-004 is met without extra code.
- `create_api_app` adds the basic-authentication middleware when
  `PREFECT_SERVER_API_AUTH_STRING` is set, and leaves `GET /api/health` and `GET /api/ready` open
  (`prefect/server/api/server.py:519-556`).
- The `python -m` form matches the other command forms of the image
  (`python -m infrahub_sync.service.serve`, `.worker`, `.bootstrap`; `Dockerfile:109-112`,
  `docker-compose.yml:201,257-262`), so no compose file depends on the `uvicorn` console script.
  It also keeps the keep-alive and ping values of the CLI, which a bare `uvicorn --factory`
  call would take from uvicorn's own defaults instead.

**Planning probe** (factory module mounted into `infrahub-sync:dev`, PostgreSQL 16, read-only
root, the three tmpfs scratch roots, `PREFECT_UI_STATIC_DIRECTORY` on tmpfs):

| Check | Result |
| --- | --- |
| `docker compose up --wait` until healthy | 8 seconds |
| `GET /api/health` without credential | 200 |
| `POST /api/work_pools/filter` without credential | 401 |
| `POST /api/work_pools/filter` with credential | 200 |
| `GET /api/admin/version` | `"3.8.6"` |
| `GET /ui-settings` | `"auth":"BASIC"`, `"available_uis":["v1","v2"]` |
| Size of the copied UI files on tmpfs | 57 MB (v1 and v2) |
| Wrong database password | `uvicorn.error` logs `InvalidPasswordError` and "Application startup failed"; the container exits with code 3 |
| Container log after a successful start | empty; `prefect server start` prints only its welcome banner |

**Alternatives considered**:

- `uvicorn --factory prefect.server.api.server:create_app` with no Sync module. Rejected: FR-003
  asks for a Sync factory, and `create_app()` without `final=True` differs from what the CLI runs.
- Copying Infrahub's `create_infrahub_prefect` (`../infrahub/backend/infrahub/prefect_server/app.py`).
  Rejected: it runs Infrahub bootstrap and adds `/infrahub` routes. Sync's bootstrap stays in
  `sync-bootstrap` (FR-003).
- `prefect server start` inside the Sync image. Rejected: it works, but it is not the factory
  pattern FR-003 names.
- Infrahub's Prefect tuning variables (`../infrahub/development/Dockerfile:128-132`). Not added:
  no Sync source shows a need (spec Assumptions).

## R2. Where the Prefect UI files go on a read-only root filesystem

**Decision**: set `PREFECT_UI_STATIC_DIRECTORY=/var/lib/infrahub-sync/prefect/ui` in the
`Dockerfile` runtime `ENV` block, next to `PREFECT_HOME` (`Dockerfile:101-104`).

**Rationale**:

- Without the setting, `create_ui_app` copies the v1 bundle to `prefect/server/ui_build` and the
  v2 bundle to `prefect/server/ui_v2_build` inside the installed package
  (`prefect/__init__.py:64,71`), which is owned by root in the image (`Dockerfile:98`).
- With the setting, the copies go to `<root>/v1` and `<root>/v2`, and the file lock sits in
  `<root>` (`prefect/server/api/server.py:589-594,726-731`). `/var/lib/infrahub-sync/prefect` is a
  tmpfs scratch root in the root compose file (`docker-compose.yml:34-37`) and a directory owned
  by UID 10001 in the image (`Dockerfile:91-96`), so the copy works in all three stacks.
- A copy error is caught, logged and skipped, and the server then serves no UI
  (`prefect/server/api/server.py:741-743`). The image test therefore checks that the UI answers.
- Only the server application reads the setting.

**Memory note**: tmpfs pages count against the container's memory. The UI copy adds about
57 MB to `prefect-server` and is made again at each start.

**Alternatives considered**: setting the variable in each compose file (three places to keep in
step); turning the UI off (`PREFECT_SERVER_UI_ENABLED=false`), rejected because FR-008 needs it.

## R3. Health check and start timing

**Decision**: keep the current health check of `prefect-server` unchanged
(`docker-compose.yml:178-187`): `python -c` with `urllib.request.urlopen('http://localhost:4200/api/health', timeout=5)`,
interval 5 s, timeout 10 s, 30 retries, start period 20 s. The probe in R1 reached healthy in
8 seconds, UI copy included (FR-006).

## R4. Published ports and the Prefect opt-in (FR-007, FR-008)

**Decision**:

- The root `docker-compose.yml` removes `ports` from `prefect-server` (`docker-compose.yml:176-177`).
  `sync-api` is the only service with `ports`.
- `PREFECT_SERVER_UI_API_URL` stays in the shipped file, fixed to loopback:
  `http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}/api`.
- The opt-in is a second Compose file that the OpsMill engineer writes on the deployment host
  from the snippet in `docs/docs/compose-deployment.mdx`, named `prefect-ui.yml`, and passes
  explicitly with `-f` ([contracts/ports.md](contracts/ports.md)). It adds a port and nothing
  else, so it carries no credential.

**How the opt-in fits ADR 0016**: ADR 0016 makes the operator deployment one self-contained file
with no wrapper, no mounted scripts and no secret files. The deployment stays that one file.
`prefect-ui.yml` is a support-time addition that is not shipped and not needed to run Sync. It is
named on the command line, so it applies only to the command that names it. ADR 0016 rejected an
auto-loaded `docker-compose.override.yml` because Compose merges it silently into every command;
an explicit `-f` file has no such effect, and forgetting it closes the port.

**Alternatives considered**: a port line driven by a variable (Compose has no empty port entry);
an auto-loaded override file (ADR 0016); a forwarding container (a fifth image); a second
Prefect service in a profile (the background services would run twice).

## R5. Bundled object store image (FR-012 to FR-016, FR-019)

**Decision**: keep
`cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba`
in every service that uses it today:

- root: `object-store-init` and `object-store` (`docker-compose.yml:103-104,122-123`)
- development: `object-store` (`development/docker-compose.dev.yml:88`)
- preview: `sync-minio` and `sync-minio-bootstrap` (`development/docker-compose.preview.yml:33,48`)

Only the comments change: the root comment at `docker-compose.yml:121`, the development comment
at `development/docker-compose.dev.yml:85` and the preview comments at
`development/docker-compose.preview.yml:32,47` stop calling the build temporary, and say that a
team can use its own store (R6). The health checks keep `wget`, which the `-dev` build contains
(`docker-compose.yml:139-145`). `object-store-init` keeps its owner repair. `sync-minio-bootstrap`
keeps its `mc` call (FR-015).

**Evidence for the pin** (`docker buildx imagetools inspect`, 2026-10-15, no login):

| Property | Evidence |
| --- | --- |
| Pulled without authentication | the index and both platform manifests were read with no registry login |
| Multi-architecture index | media type `application/vnd.oci.image.index.v1+json`, digest `sha256:d7c90699…`, listing `linux/amd64` (`sha256:ba8e9797…`) and `linux/arm64` (`sha256:a7dd71d1…`) |
| The tag moves, the digest does not | `cgr.dev/chainguard/minio:latest-dev` resolved to `sha256:7b7c9393…` on the same day, so the compose files run the pinned build and not the current tag |
| Server version in the build | `minio RELEASE.2026-09-22T19-25-18Z`, recorded by the first plan's probe |

**FR-019**: the image does not change, so the data in `object-store-data` stays readable with no
reset. The upgrade test (Tests in plan.md) keeps this checked.

**Alternatives considered**: `pgsty/silo` and the other candidates of the first plan. Rejected by
the product owner on 2026-10-15 (decision 1). A different bundled S3 server may come in 3.0 or
later; the [V3 release scope](../v3-release-scope.md) records it under AD-3.

**Risk**: no source states how long `cgr.dev` keeps a digest that no tag names. If the pinned
digest stops being pullable, every compose file fails to pull the object store. The image
workflow does not mirror it. See plan.md, "Open risks".

## R6. External S3-compatible store (FR-028 to FR-034)

### What the code supports today

| Concern | Code at `4646b716` | Status |
| --- | --- | --- |
| Endpoint URL | `INFRAHUB_SYNC_S3_ENDPOINT_URL`, read and validated by `_endpoint` (`infrahub_sync/service/storage.py:193-205`): absolute `http` or `https`, no user name or password | supported in the code; fixed to `http://object-store:9000` in the root and development files (`docker-compose.yml:49`, `development/docker-compose.dev.yml:44`), so `.env` cannot change it |
| Bucket, key prefix | `INFRAHUB_SYNC_S3_BUCKET`, `INFRAHUB_SYNC_S3_PREFIX` (`storage.py:28-29,218-219`) | supported; overridable from `.env` (`docker-compose.yml:47-48`) |
| Region | `INFRAHUB_SYNC_S3_REGION` (`storage.py:31,221`) | supported; overridable (`docker-compose.yml:50`) |
| Credential | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` from `INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY` (`docker-compose.yml:51-52`), read by boto3's default credential chain | supported; the same two values are also MinIO's root credential (`docker-compose.yml:131-132`) |
| Addressing style | the client is built with `endpoint_url` and `region_name` only (`storage.py:224`, `bootstrap.py:146-150`). botocore forces path-style requests when an endpoint URL is given, unless the `s3.addressing_style` setting is `virtual` (`botocore/args.py:833-838`), and that setting has no environment variable (`botocore/configprovider.py:242`) | missing: virtual-hosted-style requests cannot be selected |
| TLS trust | botocore verifies with `AWS_CA_BUNDLE` (`configprovider.py:77`) or, when unset, `REQUESTS_CA_BUNDLE` (`botocore/endpoint.py:449-453`), otherwise its default store | missing in Compose: no CA mount, neither variable set |
| Leaving out the bundled store | `object-store-init` and `object-store` have no condition; `sync-bootstrap` waits for `object-store` to be healthy (`docker-compose.yml:205-206`) | missing |
| Bucket creation | `converge_bucket` calls `CreateBucket` with no location constraint and treats `BucketAlreadyOwnedByYou` and `BucketAlreadyExists` as present (`bootstrap.py:52,79-90`) | partial: a credential that may not create buckets fails bootstrap with `object-store-unavailable`; a store that needs a location constraint for a region other than its default refuses the call |
| Endpoint check in bootstrap | `bootstrap.py:148` passes the raw setting to boto3 | missing: the user-name and password check of `storage.py:203-204` does not run there |
| Create-only write | `Boto3S3Client.put(if_absent=True)` sends `IfNoneMatch="*"` and maps 412 `PreconditionFailed` to `DuplicateArtifactError` (`storage.py:251-269`) | relied on, never checked against the store |
| Documentation | `docs/docs/compose-deployment.mdx:88-89,118-119` lists the bundled store's settings only | missing |

### S3 operations and behaviours Sync relies on

From the product store's S3 protocol (`infrahub_sync/product_store/store.py:2476-2497`), its
boto3 adapter (`infrahub_sync/service/storage.py:245-324`) and bootstrap (`bootstrap.py:79-90`).
A search of `infrahub_sync/` finds no `list_objects`, multipart upload or presigned URL call.

| Operation | Behaviour Sync relies on | Where |
| --- | --- | --- |
| `PutObject` | single request, whole body | `storage.py:251-256` |
| `PutObject` with `If-None-Match: *` | a second write of an existing key is refused with 412 `PreconditionFailed`; 409 `ConditionalRequestConflict` is retried up to 3 times | `storage.py:257-269` |
| `GetObject` | returns the whole body; a missing key answers `NoSuchKey` with 404; a bounded read stops reading the stream after `limit + 1` bytes (no range request) | `storage.py:271-316` |
| `HeadObject` | returns `ContentLength`; a missing key answers 404 | `storage.py:288-299` |
| `CopyObject` | copy within the bucket | `storage.py:318-320` |
| `DeleteObject` | delete one key | `storage.py:322-324` |
| `CreateBucket` | bootstrap only; after this plan, only when `HeadBucket` reports the bucket absent | `bootstrap.py:79-90` |
| `HeadBucket` | bootstrap only, new in this plan | R6 decision |
| Request signing and checksums | botocore 1.43.83 defaults: Signature Version 4, and request checksums `when_supported` (`configprovider.py:195-206`) | boto3 defaults |

A missing key must answer 404, not 403. On stores with S3-style access policies that usually
means the credential may list the bucket; the documentation states the behaviour, not a
product's permission names.

### Decision

1. **Settings** (root and development compose files, `x-sync-storage`):
   - `INFRAHUB_SYNC_S3_ENDPOINT_URL: "${INFRAHUB_SYNC_S3_ENDPOINT_URL:-http://object-store:9000}"`
   - `INFRAHUB_SYNC_S3_ADDRESSING_STYLE: "${INFRAHUB_SYNC_S3_ADDRESSING_STYLE:-path}"`, new; values
     `path` and `virtual`. `path` keeps today's behaviour.
   - bucket, prefix, region and the two credential settings keep their names and defaults.
2. **One client builder.** `storage.py` gets `build_s3_client(values, builder=boto3.client)`. It
   validates the endpoint with `_endpoint` (FR-034), reads the region, validates the addressing
   style (any other value raises `ValueError` with a fixed message that names the setting, not
   the value), and passes `botocore.config.Config(s3={"addressing_style": style})`.
   `service_product_projection` (`storage.py:224`) and bootstrap's `_converge`
   (`bootstrap.py:146-150`) both call it, so the API, the worker and bootstrap build the client the
   same way.
3. **Bucket check before creation (FR-031).** `converge_bucket` calls `HeadBucket` first. A 2xx
   answer means present. A 404 means absent: it then calls `CreateBucket`, with
   `CreateBucketConfiguration={"LocationConstraint": region}` when the region is set and is not
   `us-east-1` (the S3 API's rule for the default region), and keeps the two "already present"
   codes. A 403 on `HeadBucket` stops with the new fixed family `object-store-access-refused`.
4. **Create-only check (FR-032).** After the bucket step, bootstrap writes
   `<prefix>/.infrahub-sync/bootstrap-check/<uuid4>` twice through `Boto3S3Client.put(if_absent=True)`.
   The second write must raise `DuplicateArtifactError`. If it does not, bootstrap stops with the
   new fixed family `object-store-unsupported`. The key is deleted in a `finally` block. The check
   runs at every bootstrap and costs three requests.
5. **Leaving out the bundled store (FR-029).** `object-store-init` and `object-store` get
   `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}`, and `sync-bootstrap`'s dependency on
   `object-store` gets `required: false`. With `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, Compose
   creates neither container, so no MinIO process receives the credential, and bootstrap starts
   without waiting. With the default `1`, bootstrap waits for `object-store` to be healthy, as
   today.
6. **Guard against a half-configured external store.** Compose cannot tie the endpoint setting to
   the scale setting. Before any object store request, bootstrap checks whether the endpoint host
   is something other than `object-store` while the name `object-store` resolves
   (`socket.getaddrinfo`). If so, it stops with the new fixed family
   `bundled-object-store-not-left-out`, so the deployment does not start with an unused MinIO
   that holds the external store's credential as its root credential. MinIO has already
   received the credential at that point; the guard makes the operator correct `.env` at the
   first start instead of running in that state.
7. **TLS (FR-030).** R8 sets `AWS_CA_BUNDLE` and `REQUESTS_CA_BUNDLE` in `sync-bootstrap`,
   `sync-api` and `sync-worker` before any boto3 client exists. No setting passes `verify=False`.
8. **Documentation (FR-033).** `compose-deployment.mdx` gets a section "Use your own
   S3-compatible store" with the settings, the operation table above, the note that earlier runs'
   files stay in the old store, and the statement that OpsMill tests with the bundled MinIO build
   and with a MinIO instance outside the deployment.

**Planning probe** (Compose 5.1.2, a two-service file with `scale: ${VAR:-1}` and
`depends_on: {condition: service_healthy, required: false}`):

| Setting | Result |
| --- | --- |
| unset | both services created; the dependent service waits for the healthy store, then runs |
| `0` | only the dependent service is created and runs; no store container exists |

Profiles were also probed: `profiles: ["${VAR:-object-store}"]` is interpolated, but a service
with any profile starts only when `COMPOSE_PROFILES` names it, so the bundled store would stop
starting for every existing `.env`.

**How this fits ADR 0016**: every new value is an optional `.env` setting of the one file. No
second file and no secret file.

**Alternatives considered**:

- A profile for the bundled store. Rejected: the store would stop starting unless every operator
  adds `COMPOSE_PROFILES` to `.env` (probe above).
- `--scale object-store=0` on each command. Rejected: forgetting it on one `up` starts MinIO with
  the customer store's credential as its root credential.
- A second Compose file for the external store. Rejected: same problem as the per-command flag,
  and it cannot remove a service.
- An `auto` addressing style. Not added: with an endpoint URL botocore treats `auto` as `path`
  (`botocore/args.py:833-836`), so it adds a value with no new behaviour.
- Separate `.env` names for an external credential. Not added: the existing
  `INFRAHUB_SYNC_S3_ACCESS_KEY` and `INFRAHUB_SYNC_S3_SECRET_KEY` already mean "the credential
  Sync uses for its bucket", and the bundled store is left out when they belong to another store.

## R7. Which service receives which credential (FR-002, FR-023, FR-029, FR-035)

**Decision**: the root and development compose files pass each credential only to the services
that read it. The full table is in
[contracts/environment-variables.md](contracts/environment-variables.md). In short:

- `sync-worker`: the 17 `INFRAHUB_SYNC_CREDENTIAL_*` variables of the bundled adapters, each as
  `"${NAME:-}"`. The list follows spec 012 FR-005 and FR-006 applied to each adapter's
  `credential_setting_paths` (`infrahub_sync/configuration/capabilities.py:510-582`).
- `sync-api`: only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, as today
  (`docker-compose.yml:234`), because the configuration validation route reads the destination
  schema there (`infrahub_sync/configuration/capabilities.py:186-211`; spec 009 FR-032,
  `infrahub_sync/service/config_routes.py:368`, confirmed by replan-009).
- `sync-bootstrap`: no adapter credential.
- `prefect-server`: the Prefect database connection string and the Prefect API credential only.
- `object-store`: the S3 credential, only when the bundled store runs (R6).
- `cli`, `postgres`, `db-bootstrap`: unchanged.

A contract test derives the expected receivers from the capability registry and the table, and
fails when a credential reaches any other service.

**How this fits ADR 0016**: the variables are optional settings of the one file, read from the
`.env` beside it.

**Alternatives considered**: `env_file: .env` on `sync-worker` (passes every `.env` value,
including the PostgreSQL administrator and Prefect database passwords, to the worker); a second,
worker-only env file (a second credential file, which ADR 0016 removed); passing only the
credentials a package uses (Compose fixes the environment when the container is created).

## R8. Custom CA certificates (FR-024, FR-025, FR-030)

**Decision**:

- New optional `.env` setting `INFRAHUB_SYNC_CA_CERTIFICATES`: a host directory that holds one or
  more PEM files (`*.pem`, `*.crt`). The value must be an absolute path or start with `./`.
- `sync-bootstrap`, `sync-api` and `sync-worker` mount it read-only at `/etc/infrahub-sync/ca`:
  `"${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro"`. When the
  setting is unset, the source is the empty named volume `sync-ca-certificates`.
- The three services get `INFRAHUB_SYNC_CA_DIRECTORY: "/etc/infrahub-sync/ca"`.
- New module `infrahub_sync/service/trust_store.py`, function `install_custom_ca_bundle()`.
  `bootstrap.main()`, `serve.main()` and `worker.main()` call it first, before any client exists.
  When the directory holds at least one PEM file, it:
  1. refuses a file that contains a `PRIVATE KEY` PEM block, and stops the process with an error
     that names the file and prints none of its content;
  2. checks that each file loads with `ssl.SSLContext.load_verify_locations`, and otherwise stops
     with an error that names the file and the `ssl` error class;
  3. writes `certifi.where()` content followed by the operator's files to
     `$TMPDIR/infrahub-sync-ca-bundle.pem` (`TMPDIR=/tmp/infrahub-sync`, tmpfs), mode `0600`;
  4. sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` and `INFRAHUB_TLS_CA_FILE` in
     `os.environ` to that file, unless the variable is already set;
  5. logs the number of certificates and the bundle path with `structlog`, and nothing else.

  An empty or absent directory changes nothing (US5 acceptance scenario 3).

**Rationale**:

- Which variable each client reads, at this commit:
  - `requests` (NetBox through pynetbox, Nautobot through pynautobot, ACI): `REQUESTS_CA_BUNDLE`
    when `verify` is `True` or unset (`requests/sessions.py:771-772`); the adapters pass
    `verify=True` unless `verify_ssl` is false (`infrahub_sync/adapters/netbox.py:83-92`,
    `nautobot.py:100-106`, `aci.py:122`).
  - `httpx` 0.28.1 (IP Fabric, Prometheus, the CLI): `SSL_CERT_FILE` when `trust_env` is true
    (`httpx/_config.py:31-40`).
  - Infrahub SDK 1.23.2: `tls_ca_file` from `INFRAHUB_TLS_CA_FILE`, otherwise
    `ssl.create_default_context()` (`infrahub_sdk/config.py:269-285`).
    `pin_infrahub_sdk_authority` pins only the address, credentials and `tls_insecure`
    (`infrahub_sync/configuration/credentials.py:54-66`).
  - botocore 1.43.83 (the object store): `AWS_CA_BUNDLE` (`configprovider.py:77`), falling back to
    `REQUESTS_CA_BUNDLE` (`endpoint.py:449-453`). Setting both makes the choice explicit.
- Each variable replaces the default store rather than adding to it, so the bundle starts from
  `certifi`. FR-024 asks for the operator's CA in addition to the default store.
- Flow runs inherit the worker's environment: the process worker builds the child environment
  from `os.environ` (`prefect/workers/process.py:95`, `prefect/runner/_starter_engine.py:115`).
- `sync-bootstrap` and `sync-api` are included because both build boto3 clients
  (`bootstrap.py:146`, `storage.py:224`), and the API also reads the destination schema (R7).
- A CA certificate is public. The private-key refusal stops an operator from mounting a key into
  three containers by mistake; the check reads the PEM block header only.
- The first plan's probe confirmed both mount forms in a read-only container.
- `docs/docs/custom-certificates.mdx:28-33` ("Current limit of the Compose deployment") documents
  the gap this closes.

**How this fits ADR 0016**: the mount is optional, read-only, and holds public certificates, not
secrets and not scripts. ADR 0023 (draft in R12) records the change to the "no host path"
property.

**Change to existing properties**: `tests/compose/test_compose_contract.py` holds that the API
and the worker share no mount source (`:131`), that a Sync service mounts nothing (`:145`), that
no service mounts a host path (`:210`) and that only PostgreSQL and the object store keep a named
volume (`:171`). The compose file header states the first two (`docker-compose.yml:19-25`). The
plan narrows them to writable mounts: the CA mount is the one read-only exception, on
`sync-bootstrap`, `sync-api` and `sync-worker` only, and the empty placeholder volume is never
written.

**Alternatives considered**: a file setting bound to a file target defaulting to `/dev/null`
(Docker Desktop does not share `/dev`); a default host directory (Compose creates a missing bind
source as root); PEM content in a `.env` variable (hard to write correctly); pointing
`SSL_CERT_FILE` at the operator's file directly (drops the public roots); installing the CA into
the system store at start (read-only root, non-root user).

## R9. Development and preview stacks (FR-001, FR-009, FR-011)

**Decision**:

- `development/docker-compose.dev.yml`: `prefect-server` uses `*sync-image` and `*sync-build`
  (`infrahub-sync:dev`), runs `python -m infrahub_sync.service.prefect_server`, and keeps
  `127.0.0.1:4230:4200` and its environment. `sync-worker` receives the 17 credential variables,
  and the three Sync services the CA mount, so the development stack can test FR-023 and FR-024.
  The development stack keeps its bundled MinIO and its fixed development credentials
  (`development/docker-compose.dev.yml:42-47`); it gets the endpoint and addressing style
  settings so an engineer can point it at another store.
- `development/docker-compose.preview.yml`: `sync-prefect` uses image `infrahub-sync:preview` with
  `build: {context: .., dockerfile: Dockerfile}` and the same command. The project directory is
  `development/` because `docker-compose.infrahub.yml` is the first file (`tasks/preview.py:44-47`),
  so `..` is the repository root. It keeps `127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}:4200`.
- `tasks/preview.py` `up` adds `--build` to its `up` call (`tasks/preview.py:524`).
- `PREVIEW_PREFECT_IMAGE_TAG` and `PREVIEW_PREFECT_IMAGE_DIGEST` are removed from
  `development/preview.env:43-47`, from `tasks/preview.py:116-117,139-140,155-158`, from
  `tests/compose/test_compose_contract.py` (`test_preview_env_tags_match_compose_pin_defaults`)
  and from `tests/preview/test_preview_configuration.py`.

**Note**: the preview `sync-prefect` keeps Prefect's SQLite database under `PREFECT_HOME`, as the
stock image did. The nightly integration job logged `sqlite3.OperationalError: database is
locked` from a Prefect telemetry task (GitHub Actions runs 36651980135, 36595139169,
36570554009). Whether that came from `sync-prefect` is not known; this specification does not
change it.

## R10. Compose lifecycle tier reliability (FR-026, SC-005, SC-010)

**What the sources say**:

- `infrahub-sync-process/product/path-to-3.0.md:235`: "Engineering cannot rely on the Compose
  lifecycle qualification as a regression gate while it fails at random." It names no failure.
- The tier runs only in the `compose-suite` job of `.github/workflows/workflow-nightly-e2e.yml:189-260`,
  on `ubuntu-22.04` (linux/amd64) only. `gh run list --workflow workflow-nightly-e2e.yml` showed
  seven runs, the last on 2026-10-02; none of the five runs checked lists a `compose-suite` job.
  The four failed runs failed in the integration job
  (`tests/integration/test_destination_schema_live_read.py:69`), not in the compose tier.
- No recorded failure of the compose tier was found in the repository, in CI history or in the
  archived specifications.

**Candidate causes found by reading the tier** (to be confirmed or ruled out by measurement):

| # | Candidate | Where | Planned change |
| --- | --- | --- | --- |
| C1 | Fixed host ports shared by test modules (`API_PORT=8061`, `PREFECT_PORT=4261`; `test_lifecycle.py:55` uses 4231; `test_configuration_free_startup.py:46` uses 4251). A project left over from an interrupted run holds the port. | `tests/compose/lifecycle.py:28-33` | Prefect ports removed with FR-007; API port chosen free per deployment. |
| C2 | `up --quiet-pull` pulls from three registries in every test deployment; a registry error or the Docker Hub anonymous limit fails `up`. | `tests/compose/conftest.py:215` | Pull once per session with retries before the first `up`; `prefecthq/prefect` removed. |
| C3 | One readiness bound (420 s) covers a cold Infrahub fixture start and the deployment start on a shared runner. | `tests/compose/lifecycle.py:41`, `conftest.py:316-322` | Record start times per service in the diagnostics; set the bound from measured times. |
| C4 | Teardown removes a project only when its fixture finishes; an aborted session leaves containers and volumes with the same project names. | `tests/compose/lifecycle.py:98` | Session start removes projects with the suite's label prefix. |
| C5 | No linux/arm64 run exists. | workflow above | Add an arm64 leg (`ubuntu-24.04-arm`). |

**Decision**: the first implementation task runs the tier ten times on each architecture through
a manual dispatch with a repeat count, before any reliability change. Each failure is classified
and recorded in plan.md, section "Lifecycle failure causes". SC-010 is then measured with the
same dispatch after the changes. These runs are compose lifecycle runs, not tier M measurements.

## R11. Release digest and SBOM (FR-027, SC-011)

**What exists**: `ci-docker-image.yml` builds both platforms, pushes by digest, merges the
manifest list and exposes its digest as the workflow output `digest` (`:44-47,230-300`), signs it
(`:305-346`), attests SPDX and CycloneDX SBOMs with cosign, and uploads them as a workflow
artifact kept for 90 days (`:348-418`). `workflow-publish.yml:178-191` calls it on release. The
GitHub Release is created earlier, by `release-publish.yml:212-216`.

**Gap**: the release body is written before the image exists, so it cannot name the digest, and
the SBOM files live only in a workflow artifact that expires.

**Decision**: add a job `record_image` to `workflow-publish.yml` after `publish_docker_image`,
run only when `publish` is true. It appends a "Container image" section to the GitHub Release
body with `registry.opsmill.io/opsmill/infrahub-sync:<version>@<digest>` and the
`cosign verify-attestation` command, and uploads `infrahub-sync-sbom.spdx.json` and
`infrahub-sync-sbom.cdx.json` as release assets. The job needs `contents: write`;
`trigger-release.yml` grants it to the called workflow (today `contents: read`). The job handles
no credential beyond the workflow's `GITHUB_TOKEN`, and prints none.

**Alternatives considered**: writing the digest into the changelog fragment (the digest does not
exist then); creating the release after the image (reorders the process of ADR 0015 and
`RELEASING.md`).

## R12. Decision records (drafts; `dev/adr/` is not edited during planning)

### Amendment to ADR 0019 (FR-020)

Text to append to `dev/adr/0019-prefect-is-an-internal-component-of-sync.md` under a new heading:

> ## Amendment (2026-10): the Prefect server runs from the Sync image
>
> **Decision.** Every Compose file runs Sync's Prefect server from the Sync image, as its own
> service, started with `python -m infrahub_sync.service.prefect_server`. That module returns
> Prefect's server application unchanged; it adds no route and no bootstrap step.
>
> **Why.**
>
> - The Prefect client in the worker and API and the Prefect server come from one installed
>   package, so their versions cannot differ. Before, the server image was pinned separately.
> - The server stays a separate service. Running it inside the `sync-api` container would give
>   the API container the Prefect database credential and need a process supervisor.
>
> **Consequences.**
>
> - The deployment pulls three images instead of four.
> - A Prefect upgrade is a change to the `service` extra and a new Sync release; no image pin
>   moves elsewhere.
> - A custom Sync image must include the `service` extra, or the Prefect server cannot start.
> - The Prefect port is not published by default. An engineer publishes it on loopback with a
>   second Compose file named on the command line.

The amendment cites no internal source, as FR-020 requires.

### ADR 0023 (draft): operator inputs beside `.env` in the Compose deployment

> # 23. The Compose deployment takes an external object store and custom CA certificates from `.env`
>
> **Status**: Proposed
>
> ## Context
>
> ADR 16 makes the operator deployment one file and a `.env`. Sync could store its files only in
> the MinIO service of that file, the Sync services trusted only the image's certificate
> authorities, and no service mounted a host path.
>
> ## Decision
>
> - An operator may point Sync at an S3-compatible store with `.env` settings for the endpoint,
>   bucket, prefix, region, credential and addressing style, and leave the bundled MinIO out with
>   `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`. Left out, Compose creates no MinIO container, so the
>   store's credential reaches no MinIO process.
> - An operator may name a host directory of PEM files with `INFRAHUB_SYNC_CA_CERTIFICATES`.
>   `sync-bootstrap`, `sync-api` and `sync-worker` mount it read-only. Unset, they mount an empty
>   named volume. At start, each process builds one bundle from the public roots and those files
>   on its scratch root. A file that holds a private key stops the process.
> - An engineer may publish the Prefect port on loopback with a second Compose file named on the
>   command line. The file is not shipped.
>
> ## Consequences
>
> - A deployment with none of these settings runs exactly as before.
> - The Sync services share one read-only mount source. They still share no writable mount.
> - An unreadable or invalid certificate file stops the process at start, naming the file.
> - Moving to an external store does not move earlier files.
>
> ## Alternatives considered
>
> A Compose profile for the bundled store; a per-command scale flag; a `.env` value holding the
> PEM text; a file setting defaulting to `/dev/null`; an auto-loaded override file; a fixed host
> directory created on first start.

## R13. Keeping credentials out of text in this specification (FR-035)

**Decision**: this specification relies on no value masking. Each measure below keeps a
credential out of text by construction:

| Where text is produced | Measure | Source |
| --- | --- | --- |
| Container environments | each credential reaches only the services that read it (R7); with an external store, no MinIO container exists (R6) | contract tests |
| Compose output | a missing setting is reported by name through `:?` (`docker-compose.yml:45,51-52,58`); `up` prints no environment | Compose |
| Documented commands | the documentation uses `docker compose config --quiet` (already at `compose-deployment.mdx:281`, `quickstart-compose.mdx:77`) and `--no-interpolate` when a page shows the rendered file, and says not to share `docker compose config` or `docker inspect` output | FR-035, plan "Documentation" |
| Bootstrap and storage start-up failures | fixed family names and fixed messages, no provider text (`bootstrap.py:66-77`, `storage.py:35-46,198`); the new families `object-store-access-refused` and `object-store-unsupported` follow the same rule | R6 |
| Run failures inside the worker | spec 009's `infrahub_sync/failure_cause.py` builds failure causes from allowlisted fields only; an object store failure inside a run is recorded as `internal_error` with the exception class name and no message (agreed with replan-009) | spec 009 |
| Prefect records | the worker refuses a flow run that carries job variables (`infrahub_sync/service/worker.py:120-127`), so no credential can be stored in Prefect through them; this plan adds no deployment parameter | worker admission |
| Prefect server | its environment holds the Prefect database connection string and the Prefect API credential only; the opt-in file adds a port only | R4, R7 |
| CA setup | logs a count and a path; errors name a file and an `ssl` error class; a private key is refused unread beyond its PEM header | R8 |
| Endpoint URL | a user name or password in the URL is refused in every Sync service, bootstrap included (FR-034) | R6 |

**Existing masking**: `collect_secret_values` and `redact` in `infrahub_sync/execution.py:138-140,261-268`
stay unchanged in the MVP, owned by spec 009 (replan-009). This specification adds nothing to
them and does not depend on them for any credential it passes. The test helper
`tests/compose/redaction.py` protects the diagnostics that the test suite keeps; it is not
product behaviour. This specification does not extend it. Its new checks are leak checks that
search raw output for the generated test values and report the names they find
(`tests/compose/redaction.py:26-30`, `Captured.unredacted`), so they prove absence.

**Alternatives considered**: masking the new credential variables by their names (rejected by
the product owner on 2026-10-15); writing credentials to Docker secrets files (a second
credential file, which ADR 0016 removed).

## R14. Agent context file

The `speckit-plan` workflow updates the SPECKIT block in `CLAUDE.md`. That step is skipped: the
lead updates the block once after the plans for specs 009 and 013 merge.
