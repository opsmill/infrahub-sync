# Research: Fewer images in the Compose deployment

All file references are to commit `d9e83cb2` unless stated otherwise. Probes run during
planning used the local image `infrahub-sync:dev` (Prefect 3.8.6, uvicorn 0.52.0) on
Docker 29.4.0, linux/arm64. Probe files lived in the session scratchpad and are not part of
the repository.

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
  `docker-compose.yml:201,258-262`), so no compose file depends on the `uvicorn` console script.
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
  pattern FR-003 names, and it writes a PID file under `PREFECT_HOME` for background mode only.
- Infrahub's Prefect tuning variables (`../infrahub/development/Dockerfile:128-132`, for example
  `PREFECT_SERVER_EVENTS_MAXIMUM_RELATED_RESOURCES=500`). Not added: no Sync source shows a need
  (spec Assumptions).

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
  by UID 10001 in the image (`Dockerfile:91-96`), so the copy works in all three stacks,
  including the development and preview stacks, which do not run read-only.
- A copy error is caught, logged and skipped, and the server then serves no UI
  (`prefect/server/api/server.py:741-743`). The image test therefore checks that the UI answers,
  not only that the API does.
- The setting is read only by the server application. The API, worker and bootstrap processes
  never call `create_ui_app`.

**Memory note**: tmpfs pages count against the container's memory. The UI copy adds about
57 MB to `prefect-server` and is made again at each start (spec edge case "Restart on tmpfs").

**Alternatives considered**: setting the variable only in each compose file (three places to keep
in step, and the image test would not cover it); turning the UI off
(`PREFECT_SERVER_UI_ENABLED=false`), rejected because FR-008 needs the UI when the opt-in is on.

## R3. Health check and start timing

**Decision**: keep the current health check of `prefect-server` unchanged: `python -c` with
`urllib.request.urlopen('http://localhost:4200/api/health', timeout=5)`, interval 5 s, timeout
10 s, 30 retries, start period 20 s (`docker-compose.yml:178-187`).

**Rationale**: `python` is the image interpreter. The probe in R1 reached healthy in 8 seconds,
UI copy included, inside the 20-second start period (FR-006).

## R4. Published ports and the Prefect opt-in (FR-007, FR-008)

**Decision**:

- The root `docker-compose.yml` removes `ports` from `prefect-server`. `sync-api` is the only
  service with `ports`.
- `PREFECT_SERVER_UI_API_URL` stays in the shipped file, fixed to loopback:
  `http://127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}/api`. It no longer follows
  `INFRAHUB_SYNC_BIND_ADDRESS`.
- The opt-in is a second Compose file that the OpsMill engineer writes on the deployment host
  from the snippet in `docs/docs/compose-deployment.mdx`, named `prefect-ui.yml`, and passes
  explicitly with `-f`:

  ```yaml
  services:
    prefect-server:
      ports:
        - "127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}:4200"
  ```

  `docker compose -f docker-compose.yml -f prefect-ui.yml up -d --wait` publishes the port.
  `docker compose up -d --wait` without the second file recreates `prefect-server` without it.

**How the opt-in fits ADR 0016**: ADR 0016 makes the operator deployment one self-contained file
with no wrapper, no mounted scripts and no secret files
(`dev/adr/0016-the-compose-deployment-is-one-tag-pinned-file.md`, "Decision"). The deployment
stays that one file. `prefect-ui.yml` is a support-time addition that is not shipped, not
downloaded, and not needed to run Sync. It is named on the command line, so it applies only to
the command that names it. ADR 0016 rejected an auto-loaded `docker-compose.override.yml`
because Compose merges it silently into every command ("Alternatives Considered"); an explicit
`-f` file has no such effect, and forgetting it closes the port rather than leaving it open.

**Alternatives considered**:

- A port line driven by a variable, for example `"${INFRAHUB_SYNC_PREFECT_PUBLISH:-127.0.0.1::4200}"`.
  Rejected: Compose has no empty port entry, so every default publishes something (an empty host
  port publishes an ephemeral one).
- An auto-loaded `docker-compose.override.yml`. Rejected for the reason ADR 0016 gives.
- A forwarding container (for example `socat`) started by hand. Rejected: a fifth image to pull
  and pin, against SC-001.
- A Compose profile with a second Prefect service that publishes the port. Rejected: a second
  server against the same database runs the background services twice.

## R5. Object store image (FR-012 to FR-016, FR-019)

**Decision**: `pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46`
in every service that uses the object store image: `object-store-init` and `object-store`
(root), `object-store` (development), `sync-minio` and `sync-minio-bootstrap` (preview). The
compose files keep `user: "65532:65532"` on the long-running server, as today
(`docker-compose.yml:125`), and add it in the development and preview files.

**Candidates checked on 2026-10-15** (`docker buildx imagetools inspect`, Docker Hub tag API):

| Image | Anonymous pull | Version tag | Last update | Result |
| --- | --- | --- | --- | --- |
| `quay.io/minio/minio` | no (401 Unauthorized) | yes | not checked | fails "pulled without authentication" |
| `minio/minio` (Docker Hub) | no ("pull access denied") | not checked | not checked | fails |
| `bitnami/minio` | no (`latest` not found) | not checked | not checked | fails |
| `cgr.dev/chainguard/minio` (current) | yes | only `latest` and `latest-dev` on the free tier | current | fails "names one release" |
| `coollabsio/minio` | yes | `RELEASE.2025-10-15T17-29-55Z` | 2025-10-27 | fails "update in the last 90 days" |
| `pgsty/minio` | yes | `RELEASE.2026-08-04T00-00-00Z` | 2026-08-04 | frozen: the project was renamed to Silo on 2026-08-06 and publishes no newer `pgsty/minio` tag |
| `rustfs/rustfs` | yes | `1.0.1` | 2026-10-03 | not MinIO; not checked for `/minio/health/live` or for reading a MinIO volume |
| `pgsty/silo` | yes | `RELEASE.2026-09-16T00-00-00Z`, `RELEASE.2026-10-15T00-00-00Z` | 2026-10-15 | selected |

**Evidence for each FR-013 condition** (`pgsty/silo:RELEASE.2026-09-16T00-00-00Z`):

| Condition | Evidence |
| --- | --- |
| Pulled without authentication | `docker buildx imagetools inspect` and `docker pull` succeed with no login; Docker Hub tag API lists the tag publicly. |
| linux/amd64 and linux/arm64 | Index `sha256:635197cb…` lists `linux/amd64` and `linux/arm64` (plus two attestation entries). |
| Version tag naming one release | `RELEASE.2026-09-16T00-00-00Z`; the image label `org.opencontainers.image.version` holds the same value; `silo --version` prints `RELEASE.2026-09-16T00-00-00Z (commit-id=2a4d5140…)`. |
| Publisher states it publishes security fixes, update in last 90 days | `SECURITY.md` of `github.com/pgsty/silo`: "Security fixes are tracked on the active development branch and summarized in the security advisory ledger"; README: "every fix ships with a public advisory" and "a release every one to two months, at most a quarter apart". Releases `RELEASE.2026-09-16T00-00-00Z` (2026-09-16) and `RELEASE.2026-10-15T00-00-00Z` (2026-10-15). |
| MinIO S3 API and `/minio/health/live` | README: "Silo preserves S3 and storage-format compatibility, including existing `MINIO_*` variables, … `/minio/*` routes, and `.minio.sys` data". Probe: `curl http://localhost:9000/minio/health/live` returned 200; `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` and `MINIO_BROWSER=off` were honoured. |
| Runs as a non-root user | The image configuration has no `User`, so it defaults to root. The entrypoint supports an arbitrary UID (`ensure_writable_home`). Probe: started with `user: 65532:65532` and `--read-only --tmpfs /tmp`, `id` printed `uid=65532`. The compose files set that user, as they do today for the Chainguard build. |

**Reading the existing volume (FR-019, SC-006)**: probe on one named volume: the current
Chainguard build (`minio RELEASE.2026-09-22T19-25-18Z`) as UID 65532 created bucket
`infrahub-sync` and wrote `infrahub-sync/runs/x.json`. Silo then started on the same volume as
UID 65532 with a read-only root, served the health endpoint, returned the object unchanged, and
created a second bucket. No reset is needed. The release notes still name the image change. The
compose lifecycle tier repeats this check on both architectures (plan, "Tests").

**Tools in the image**: `/bin/sh`, `stat`, `chown`, `curl`, `mc` and `mcli` are present; `wget`
is not. Consequences:

- The `object-store` health checks change from `wget` to `curl -fsS -o /dev/null http://localhost:9000/minio/health/live`.
- `object-store-init` keeps its `sh -ec` command unchanged (`docker-compose.yml:102-118`): the
  volume owner stays 65532, so the repair is a no-op on volumes written by the current build.
- `sync-minio-bootstrap` keeps its `mc alias set` and `mc mb --ignore-existing` call
  (`development/docker-compose.preview.yml:46-55`) with `MC_CONFIG_DIR=/tmp/.mc`, which the image
  already sets (FR-015).
- The server binary is `silo`; the entrypoint maps the argument `server` to `silo server`, so
  `command: ["server", "/data"]` stays.

**Licence**: AGPL-3.0, the same licence as the MinIO server the compose files run today.

**Alternatives considered**: `RELEASE.2026-10-15T00-00-00Z` (released on the day of selection;
the plan prefers the release that has been public for a month, and Dependabot or a later change
can move the pin); the `-distroless` variant (no shell and no `curl`, so `object-store-init` and
the health check would need other tools); `pgsty/minio` (frozen identity); replacing MinIO with
another product (out of scope, AD-3).

**Risk**: Docker Hub limits anonymous pulls per IP address. Today two of four images come from
Docker Hub (`postgres`, `prefecthq/prefect`); after the change two of three do (`postgres`,
`pgsty/silo`). The count does not grow.

## R6. Worker credential variables (FR-023, FR-025)

**Decision**: the root and development compose files list every credential identifier of the
bundled adapters in `sync-worker.environment`, each as `"${NAME:-}"`. The list follows spec 012
FR-005 and FR-006 (`<adapter>-<setting>`, upper case, prefix `INFRAHUB_SYNC_CREDENTIAL_`, with
the Infrahub token named `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`), applied to each
adapter's `credential_setting_paths` in `infrahub_sync/configuration/capabilities.py:510-582`.
The full list is in [contracts/environment-variables.md](contracts/environment-variables.md)
(17 variables).

Which services receive them:

- `sync-worker`: all 17.
- `sync-api`: only `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`, as today
  (`docker-compose.yml:234`). The API reads the destination schema for the validation route
  (`infrahub_sync/configuration/capabilities.py:186-211`), and spec 009 FR-032 runs its
  destination schema check there.
- No other service. `prefect-server` and `db-bootstrap` stay the only holders of the Prefect
  database password (FR-002).

**How this fits ADR 0016**: the variables are optional settings of the one file, read from the
`.env` beside it, like the three tokens the file passes today. No secret file, no wrapper.

**Masking**: `INFRAHUB_SYNC_CREDENTIAL_` contains `CREDENTIAL`, one of the name substrings that
`collect_secret_values` treats as secret (`infrahub_sync/execution.py:138-140,261-268`), so the
values of all 17 variables are already collected. Spec 009 FR-027 and FR-028 change how values
are collected; they do not depend on these names.

**Alternatives considered**:

- `env_file: .env` on `sync-worker`. Rejected: it passes every `.env` value, including the
  PostgreSQL administrator password and the Prefect database password, to the worker (FR-002).
- A second, worker-only env file (`env_file: {path: worker.env, required: false}`). Rejected:
  FR-023 names `.env`, and a second credential file is what ADR 0016 removed.
- Passing a credential only for adapters a package uses. Not possible in Compose: the
  environment is fixed when the container is created.

A contract test derives the expected list from the capability registry, so adding an adapter
credential without adding its variable fails the test.

## R7. Custom CA certificates (FR-024, FR-025)

**Decision**:

- New optional `.env` setting `INFRAHUB_SYNC_CA_CERTIFICATES`: a host directory that holds one or
  more PEM files (`*.pem`, `*.crt`). The value must be an absolute path or start with `./`.
- `sync-worker` and `sync-api` mount it read-only at `/etc/infrahub-sync/ca`:
  `"${INFRAHUB_SYNC_CA_CERTIFICATES:-sync-ca-certificates}:/etc/infrahub-sync/ca:ro"`. When the
  setting is unset, the source is the empty named volume `sync-ca-certificates`, declared in the
  file's `volumes` block.
- Both services get `INFRAHUB_SYNC_CA_DIRECTORY: "/etc/infrahub-sync/ca"`.
- New module `infrahub_sync/service/trust_store.py`. At process start, `worker.main()` and
  `serve.main()` call it before any client exists. When the directory holds at least one PEM
  file, it:
    1. checks that each file loads with `ssl.SSLContext.load_verify_locations`, and stops the
       process with an error that names the file otherwise;
    2. writes `certifi.where()` content followed by the operator's files to
       `$TMPDIR/infrahub-sync-ca-bundle.pem` (`TMPDIR=/tmp/infrahub-sync`, tmpfs);
    3. sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `INFRAHUB_TLS_CA_FILE` in `os.environ` to
       that file, unless the variable is already set;
    4. logs the number of certificates and the bundle path with `structlog`.

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
      `pin_infrahub_sdk_authority` pins only the address, credentials and `tls_insecure`, and
      leaves transport settings to the worker (`infrahub_sync/configuration/credentials.py:54-66`).
- Each variable replaces the default store rather than adding to it, so the bundle starts from
  `certifi`, the store `requests` and `httpx` use by default. FR-024 asks for the operator's CA in
  addition to the image's default trust store.
- Flow runs inherit the worker's environment: the process worker builds the child environment
  from `os.environ` (`prefect/workers/process.py:95`, `prefect/runner/_starter_engine.py:115`).
- `sync-api` is included because it opens the destination schema read itself (R6). Coordination
  with spec 009 recorded in plan.md.
- The planning probe confirmed both mount forms in a read-only container: unset gave an empty
  directory; `INFRAHUB_SYNC_CA_CERTIFICATES=./ca` gave the host file.
- `docs/docs/custom-certificates.mdx` ("Current limit of the Compose deployment") documents the
  gap this closes.

**How this fits ADR 0016**: the mount is optional, read-only, and holds public certificates, not
secrets and not scripts. Without the setting, the file needs nothing beside it but `.env`. ADR
0023 (draft in R11) records the change to the "no host path" property.

**Change to existing properties**: `tests/compose/test_compose_contract.py` holds that the API
and the worker share no mount source (`:131`), that a Sync service mounts nothing (`:145`), that
no service mounts a host path (`:210`) and that only PostgreSQL and the object store keep a
named volume (`:171`). The compose file header states the first two
(`docker-compose.yml:19-25`). The plan narrows them to writable mounts: the CA mount is the one
read-only exception, on `sync-api` and `sync-worker` only, and the empty placeholder volume is
never written.

**Alternatives considered**:

- A file path setting bound to a file target, defaulting to `/dev/null`. Rejected: Docker Desktop
  resolves bind sources through its file sharing, which does not share `/dev`.
- A default host directory such as `./ca-certificates`. Rejected: Compose creates a missing bind
  source on the host as root, a side effect for every operator who sets nothing.
- The PEM content in a `.env` variable passed through a `configs.content` block. Rejected: a
  multi-line value in `.env` is hard to write correctly.
- Pointing `SSL_CERT_FILE` at the operator's file directly. Rejected: it replaces the public
  roots, so connections to sources with public certificates would fail.
- Installing the CA into the image's system store at start. Rejected: the root filesystem is
  read-only and the process does not run as root.

**Names and masking**: neither `INFRAHUB_SYNC_CA_CERTIFICATES` nor `INFRAHUB_SYNC_CA_DIRECTORY`
matches the secret name shapes of `infrahub_sync/execution.py:138-140`, so the paths are not
masked in messages. Agreed with spec 009.

## R8. Development and preview stacks (FR-001, FR-009, FR-011)

**Decision**:

- `development/docker-compose.dev.yml`: `prefect-server` uses `*sync-image` and `*sync-build`
  (`infrahub-sync:dev`), runs `python -m infrahub_sync.service.prefect_server`, and keeps
  `127.0.0.1:4230:4200` and its environment. `sync-worker` receives the 17 credential variables
  and the CA mount, so the development stack can test FR-023 and FR-024.
- `development/docker-compose.preview.yml`: `sync-prefect` uses image `infrahub-sync:preview` with
  `build: {context: .., dockerfile: Dockerfile}` and the same command. The project directory is
  `development/` because `docker-compose.infrahub.yml` is the first file (`tasks/preview.py:44-47`),
  so `..` is the repository root. It keeps `127.0.0.1:${PREVIEW_PREFECT_PORT:-4210}:4200`.
- `tasks/preview.py` `up` adds `--build` to its `up` call (`tasks/preview.py:524`), so the server
  always matches the checkout (US4 independent test).
- `PREVIEW_PREFECT_IMAGE_TAG` and `PREVIEW_PREFECT_IMAGE_DIGEST` are removed from
  `development/preview.env:43-47`, from `tasks/preview.py:116-117,139-140,155-158`, from
  `tests/compose/test_compose_contract.py` (`test_preview_env_tags_match_compose_pin_defaults`)
  and from `tests/preview/test_preview_configuration.py`.

**Note**: the preview `sync-prefect` keeps Prefect's SQLite database under `PREFECT_HOME`, as the
stock image did. The nightly integration job logged `sqlite3.OperationalError: database is
locked` from a Prefect telemetry task (GitHub Actions runs 36651980135, 36595139169,
36570554009). Whether that came from `sync-prefect` is not known; this specification does not
change it.

## R9. Compose lifecycle tier reliability (FR-026, SC-005, SC-010)

**What the sources say**:

- `infrahub-sync-process/product/path-to-3.0.md:235`: "Engineering cannot rely on the Compose
  lifecycle qualification as a regression gate while it fails at random." It names no failure.
- The tier runs only in the `compose-suite` job of `.github/workflows/workflow-nightly-e2e.yml:189-260`,
  on `ubuntu-22.04` (linux/amd64) only. `gh run list --workflow workflow-nightly-e2e.yml` shows
  seven runs, the last on 2026-10-02; none of the five runs checked lists a `compose-suite` job.
  The four failed runs failed in the integration job
  (`tests/integration/test_destination_schema_live_read.py:69`), not in the compose tier.
- No recorded failure of the compose tier was found in the repository, in CI history or in the
  archived specifications.

**Candidate causes found by reading the tier** (to be confirmed or ruled out by the measurement
task in the plan):

| # | Candidate | Where | Planned change |
| --- | --- | --- | --- |
| C1 | Fixed host ports shared by test modules (`API_PORT=8061`, `PREFECT_PORT=4261`; `test_lifecycle.py:55` uses 4231; `test_configuration_free_startup.py:46` uses 4251). A project left over from an interrupted run holds the port, and the next `up` fails on publication. | `tests/compose/lifecycle.py:28-33` | Prefect ports removed with FR-007; API port chosen free per deployment. |
| C2 | `up --quiet-pull` pulls from three registries in every test deployment; a registry error or the Docker Hub anonymous limit fails `up`. | `tests/compose/conftest.py:215` | Pull once per session with retries before the first `up`; `prefecthq/prefect` and `cgr.dev` removed. |
| C3 | One readiness bound (420 s) covers a cold Infrahub fixture start and the deployment start on a shared runner. | `tests/compose/lifecycle.py:41`, `conftest.py:316-322` | Record start times per service in the diagnostics; set the bound from measured times. |
| C4 | Teardown removes a project only when its fixture finishes; an aborted session leaves containers and volumes with the same project names. | `tests/compose/lifecycle.py:98` (`infrahub-sync-{instance}`) | Session start removes projects with the suite's label prefix. |
| C5 | No linux/arm64 run exists, so failures specific to arm64 are not known. | workflow above | Add an arm64 leg (`ubuntu-24.04-arm`). |

**Decision**: the first implementation task runs the tier ten times on each architecture through
a manual dispatch with a repeat count, before any reliability change. Each failure is classified
and recorded in plan.md, section "Lifecycle failure causes", which the implementation updates.
SC-010 is then measured with the same dispatch after the changes. These runs are compose
lifecycle runs, not tier M measurements.

## R10. Release digest and SBOM (FR-027, SC-011)

**What exists**: `ci-docker-image.yml` builds both platforms, pushes by digest, merges the
manifest list and exposes its digest as the workflow output `digest` (`:44-47,230-300`), signs it
(`:305-346`), attests SPDX and CycloneDX SBOMs to the registry with cosign, and uploads them as a
workflow artifact kept for 90 days (`:348-418`). `workflow-publish.yml:178-191` calls it on
release. The GitHub Release is created earlier, by `release-publish.yml:212-216`, with the
changelog section as its body; `trigger-release.yml` runs on `release: published`.

**Gap**: the release body is written before the image exists, so it cannot name the digest, and
the SBOM files live only in a workflow artifact that expires.

**Decision**: add a job `record_image` to `workflow-publish.yml` after `publish_docker_image`,
run only when `publish` is true. It:

1. appends a "Container image" section to the GitHub Release body with
   `registry.opsmill.io/opsmill/infrahub-sync:<version>@<digest>` and the `cosign
   verify-attestation` command for the SBOM (`gh release view` then `gh release edit --notes-file`);
2. uploads `infrahub-sync-sbom.spdx.json` and `infrahub-sync-sbom.cdx.json` from the `sbom-<version>`
   artifact as release assets (`gh release upload`).

The job needs `contents: write`; `trigger-release.yml` grants it to the called workflow
(today `contents: read`). The registry host comes from `vars.HARBOR_HOST`, the same variable the
push uses.

**Alternatives considered**: writing the digest into the changelog fragment (the digest does not
exist when the fragment is written); creating the release after the image (reorders the release
process that ADR 0015 and `RELEASING.md` describe).

## R11. Decision records (drafts; `dev/adr/` is not edited during planning)

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

The amendment cites no internal source (no ticket, chat or internal document), as FR-020
requires.

### ADR 0023 (draft): operator inputs beside `.env` in the Compose deployment

> # 23. The Compose worker takes custom CA certificates from an optional read-only directory
>
> **Status**: Proposed
>
> ## Context
>
> ADR 16 makes the operator deployment one file and a `.env`. The worker and the API trusted only
> the image's certificate authorities, and `.env` cannot carry a certificate in a usable form.
> The deployment also held that no service mounts a host path.
>
> ## Decision
>
> - An operator may name a host directory of PEM files with `INFRAHUB_SYNC_CA_CERTIFICATES`.
>   `sync-api` and `sync-worker` mount it read-only. Unset, they mount an empty named volume.
> - At start, each process builds one bundle from the public roots and those files on its scratch
>   root, and points `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `INFRAHUB_TLS_CA_FILE` at it.
> - An engineer may publish the Prefect port on loopback with a second Compose file named on the
>   command line. The file is not shipped.
>
> ## Consequences
>
> - A deployment with no custom CA runs exactly as before.
> - The API and the worker share one read-only mount source. They still share no writable mount.
> - An unreadable or invalid certificate file stops the process at start, naming the file.
>
> ## Alternatives considered
>
> A `.env` value holding the PEM text; a file setting defaulting to `/dev/null`; an auto-loaded
> override file; a fixed host directory created on first start.

## R12. Agent context file

The `speckit-plan` workflow updates the SPECKIT block in `CLAUDE.md`. That step is skipped: the
lead updates the block once after the plans for specs 009 and 013 merge.
