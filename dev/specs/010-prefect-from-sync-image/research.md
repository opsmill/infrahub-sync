# Research: Compose stacks run the Prefect server from the Sync image

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-10-22

Paths that start with `infrahub/` or `infrahub-helm/` are in the sibling checkouts
`../infrahub` and `../infrahub-helm`, read on 2026-10-22. Other paths are in this repository.
Infrahub is the reference implementation: it already runs its Prefect server from its own image.

## R1. Run the Prefect server from the Sync image through a factory module

**Decision**: add `infrahub_sync/service/prefect_server.py` with one factory,
`create_prefect_app()`, that returns `prefect.server.api.server.create_app()`. Every stack runs
`uvicorn --host 0.0.0.0 --port 4200 --factory infrahub_sync.service.prefect_server:create_prefect_app`
in a `prefect-server` service that uses the Sync image. The health check calls
`http://127.0.0.1:4200/api/health` with `httpx`, in the same form as the `sync-api` health check
(`docker-compose.yml:240-244`), because the Sync image has no `curl` or `wget`.

**Evidence**:

- Infrahub's factory wraps `create_app` (`infrahub/backend/infrahub/prefect_server/app.py:7`,
  `:32-47`) and adds an `/infrahub` router and a start-up lock (`:12-29`). Sync needs neither
  (spec assumption: "Sync's Prefect server module needs none of Infrahub's Prefect bootstrap or
  tuning variables").
- Infrahub runs it with
  `uvicorn --host 0.0.0.0 --port 4200 --factory infrahub.prefect_server.app:create_infrahub_prefect`
  (`infrahub/docker-compose.yml:285`), with only `PREFECT_API_DATABASE_CONNECTION_URL` set
  (`:290-291`) and no published port. The Helm chart does the same (`infrahub-helm/charts/infrahub/values.yaml:421-431`).
- The Sync image installs the `service` extra (`Dockerfile:52-53`, `:67-69`), which pins
  `prefect==3.8.6` for Python 3.11 and later (`pyproject.toml:89`). So the server code and the
  client code in the image are the same Prefect release.
- `prefect.server.api.server.create_app` imports cleanly in an environment with Sync's
  `fastapi==0.141.1` pin (`uv.lock:892-893`). With FastAPI 0.159.1 the same import fails
  (`TypeError: Body() got an unexpected keyword argument 'min_items'` in
  `prefect/server/api/concurrency_limits_v2.py:305`), so the FastAPI pin is load-bearing and
  stays.

**Alternatives considered**:

- `prefect server start` from the Sync image. Rejected: it starts uvicorn as a child process, so
  the container's main process is not the server, and the brief asks for the factory pattern
  Infrahub uses (FR-001).
- Keeping `prefecthq/prefect`. Rejected by D-1 and FR-001.

## R2. Prefect UI: off by default, scratch directory when on

**Decision**: the root file sets `PREFECT_UI_ENABLED=false` and always sets
`PREFECT_SERVER_UI_API_URL` to the loopback address. The documented override snippet sets
`PREFECT_UI_ENABLED=true`, sets `PREFECT_UI_STATIC_DIRECTORY=/tmp/infrahub-sync/prefect-ui`,
and publishes `127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}:4200`. The development stack sets
the same static directory with the UI on.

**Evidence**:

- Prefect 3.8.6 serves the UI only when `PREFECT_UI_ENABLED` is true
  (`prefect/server/api/server.py:721`).
- When the UI is on, Prefect copies the bundled UI files into the static directory and rewrites
  a placeholder in them (`prefect/server/api/server.py:570`, `:589-593`, and the
  `copy_directory` / `replace_placeholder_string_in_files` calls after them). The default static
  directory is inside the installed `prefect` package, which the Sync image's user (`10001`,
  `Dockerfile:106`) cannot write, and the root file runs Sync services with `read_only: true`
  and a tmpfs on `/tmp/infrahub-sync` (`docker-compose.yml:31-37`, `:211-212`). So the copy must
  go to the tmpfs.
- The setting is read as `PREFECT_SERVER_UI_STATIC_DIRECTORY` or `PREFECT_UI_STATIC_DIRECTORY`
  (`prefect/settings/models/server/ui.py:47-53`).
- The UI reports `auth="BASIC"` when `PREFECT_SERVER_API_AUTH_STRING` is set
  (`prefect/server/api/server.py`, `ui_settings`), so the UI asks for the Prefect API credential
  that the root file already sets (`docker-compose.yml:173`). This matches User Story 4.
- Infrahub sets `PREFECT_UI_ENABLED` only in its test compose files and notes that the UI needs
  write permission ("run container as root to enable UI",
  `infrahub/python_testcontainers/infrahub_testcontainers/docker-compose.test.yml:111`). The
  scratch directory removes that need.

**Alternatives considered**: running `prefect-server` as root, or with a writable root
filesystem, as Infrahub's test files and enterprise chart do
(`infrahub-helm/charts/infrahub-enterprise/values.yaml:80-89`). Rejected: the root file runs
every Sync service read-only as user 10001.

## R3. Silo as the bundled object store (planning check 1)

**Decision**: pin `pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7`
in every Compose file. Use the standard image, not the `-distroless` variant. Keep the
`object-store-init` repair service, now on the Silo image, and keep running the server as
`65532:65532`. Use `["CMD", "silo", "healthcheck", "ready"]` as the health check. Keep bucket
creation in `sync-bootstrap`, which already uses the S3 API.

**Evidence** (checked 2026-10-22 with Docker 29.8.2; runtime tests on arm64):

- **Source and licence**: [github.com/pgsty/silo](https://github.com/pgsty/silo), a fork of
  `minio/minio`, not archived; `pgsty/minio` redirects there. The
  [LICENSE](https://github.com/pgsty/silo/blob/master/LICENSE) is AGPLv3; the image label is
  `org.opencontainers.image.licenses=AGPL-3.0-or-later`.
- **Release cadence**: 12 GitHub releases from 2025-12-15 to 2026-10-18, with gaps from 2 days to
  2 months (about one a month). Dated Docker Hub tags since the repository was created on
  2026-08-06: five, the first on 2026-08-06 and the last on 2026-10-18
  ([Docker Hub tags](https://hub.docker.com/r/pgsty/silo/tags)). The latest,
  `RELEASE.2026-10-18T00-00-00Z`, was pushed on 2026-10-18. Whether releases before 2026-08-06
  were published under another repository is not known.
- **Platforms and anonymous pull**: the index digest above lists linux/amd64 and linux/arm64.
  `docker pull --platform` for both succeeded with an empty Docker configuration, so no login is
  needed. The amd64 image was pulled and inspected but not run.
- **User**: the standard image sets no user and runs as root. A new named volume is created as
  `0:0`. Run as `65532:65532` on that volume, the server exits with
  `Unable to initialize backend: file access denied`. After
  `chown -R 65532:65532 /data` in the same image, it starts as `65532` and
  `/minio/health/live` returns 200. So the `object-store-init` repair service is still needed,
  in the root file and now in the development stack too.
- **Tools inside**: the standard image has `/bin/sh`, bash, `curl`, `mc`, `chown` and `stat`; it
  has no `wget`, `busybox` or `nc`. The current health check uses `wget`
  (`docker-compose.yml:138-148`, `development/docker-compose.dev.yml:84-100`), so it must change
  (FR-013). The current repair command uses only `sh`, `stat` and `chown`
  (`docker-compose.yml:102-118`), which the Silo image has.
- **Health check**: `silo healthcheck ready` exits 0 on a running server and reports
  `connection refused` otherwise. The `-distroless` variant uses the same command as its built-in
  health check, so the check does not depend on the variant.
- **Bucket creation without `mc`**: `sync-bootstrap` already creates the bucket with boto3
  (`infrahub_sync/service/bootstrap.py:79-90`) and treats an existing bucket as success.
  A test with `aws s3api create-bucket` against Silo succeeded, and a second call returned
  `BucketAlreadyOwnedByYou`, which is one of the codes the bootstrap treats as present. FR-012
  is therefore already met in the root file; the development stack uses the same bootstrap.
- **Entrypoint**: the binary is `silo`; the entrypoint still accepts `server` as the first
  argument (`command: ["server", "/data"]` keeps working). The web console listens on a random
  port unless `--console-address` is set; the stacks keep `MINIO_BROWSER=off`.
- **linux/amd64 run (task T001, 2026-10-22)**: the amd64 image ran on an arm64 host (Apple
  M5 Pro, macOS 26.6.2, OrbStack 2.2.4, Docker 29.8.2) through OrbStack's amd64 emulation,
  which uses Rosetta, not QEMU. `uname -m` in the container printed `x86_64`, and the server
  log printed `Version: RELEASE.2026-10-18T00-00-00Z (go1.27.2 linux/amd64)`. Every step
  passed:

  ```bash
  IMG=pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7
  docker pull --platform linux/amd64 "$IMG"
  docker volume create s010-t001-data
  docker network create s010-t001-net
  # Repair step: the new volume is 0:0 before and 65532:65532 after; exit 0.
  docker run --rm --platform linux/amd64 --entrypoint sh -v s010-t001-data:/data "$IMG" \
    -c 'stat -c "%u:%g" /data; chown -R 65532:65532 /data; stat -c "%u:%g" /data'
  # Server as 65532:65532 with the console off; dummy credentials.
  docker run -d --name s010-t001-silo --platform linux/amd64 --network s010-t001-net \
    --user 65532:65532 -e MINIO_BROWSER=off \
    -e MINIO_ROOT_USER=s010dummyuser -e MINIO_ROOT_PASSWORD=s010dummypassword \
    -v s010-t001-data:/data "$IMG" server /data
  # Prints "ready: ok (200, 5ms)" and exits 0 (second attempt, about 2 seconds after start).
  docker exec s010-t001-silo silo healthcheck ready
  # Run twice: the first call returns {"Location": "/s010-check"} and exits 0; the second
  # returns "An error occurred (BucketAlreadyOwnedByYou) when calling the CreateBucket
  # operation" and exits 254.
  docker run --rm --network s010-t001-net \
    -e AWS_ACCESS_KEY_ID=s010dummyuser -e AWS_SECRET_ACCESS_KEY=s010dummypassword \
    -e AWS_DEFAULT_REGION=us-east-1 amazon/aws-cli:2.22.0 \
    --endpoint-url http://s010-t001-silo:9000 s3api create-bucket --bucket s010-check
  ```

  The run used emulation, so it shows that the amd64 binary works; it does not show behaviour
  on a native amd64 kernel. On a GitHub `ubuntu-latest` runner the same commands run natively
  without `--platform`. Whether they have been run there is not known.

**Alternatives considered**: the `-distroless` variant (no shell, so the repair service and its
`stat` test cannot run in it); a second image only for the repair (adds a fourth image, against
SC-001).

## R4. `prefect` and `prefect-client` in one environment (planning check 2)

**Decision**: add `infrahub-testcontainers` (and so `prefect-client`) to the new
`testcontainers` extra and to the `dev` extra. One environment can hold `prefect==3.8.6` and
`prefect-client==3.8.6`. A contract test reads both versions from `uv.lock` (FR-024).

**Evidence**:

- `infrahub-testcontainers` pins `prefect-client==3.8.6`
  (`infrahub/python_testcontainers/pyproject.toml:27`) and `testcontainers>=4.8,<4.9` (`:22`).
  Infrahub itself never installs both: its root lock has `prefect` 3.8.6 and no
  `prefect-client` (`infrahub/uv.lock:2922-2923`), and the testcontainers lock has
  `prefect-client` and no `prefect` (`infrahub/python_testcontainers/uv.lock:803-804`).
- A scratch project with `prefect==3.8.6`, `infrahub-testcontainers` and `fastapi==0.141.1`
  locked with uv 0.12.9 on Python 3.13 to `infrahub-testcontainers` 1.11.8, `prefect` 3.8.6,
  `prefect-client` 3.8.6 and `testcontainers` 4.8.2, and installed without a warning.
- Both distributions install files under `prefect/`. Of the 450 files `prefect-client` ships,
  449 are byte-identical to the same files in `prefect`. The one that differs is
  `prefect/_build_info.py` (build date and commit only); after installation it holds
  `prefect-client`'s copy and `__version__ = "3.8.6"`. `prefect.__version__` is 3.8.6 and
  `prefect.server.api.server.create_app` imports.
- Risk: uninstalling `prefect-client` alone would delete the 450 shared files from the `prefect`
  installation. uv removes and installs both as a pair when the pins move together, which D-17
  requires.

**Alternatives considered**: running the fixture from a separate environment without `prefect`.
Rejected: the CI end-to-end tier and demo repositories install Sync and the fixture together.

## R5. How `dev.start` and `dev.deps` notice that the image is stale (planning check 3, FR-018)

**Decision**: the development image carries a label,
`org.opsmill.infrahub-sync.build-inputs`, whose value is the SHA-256 of the bytes of
`Dockerfile`, `pyproject.toml` and `uv.lock`, in that order. `dev.build` computes the value and
passes it as a build argument that the Dockerfile writes into the label. `dev.start` and
`dev.deps` compute the same value, read the label with
`docker image inspect --format '{{ index .Config.Labels "org.opsmill.infrahub-sync.build-inputs" }}' infrahub-sync:dev`,
and run the build when the image is missing, the label is missing, or the values differ. Code
changes do not touch these files, and the checkout is mounted over the installed code (FR-019),
so code changes alone never rebuild.

**Evidence**:

- Infrahub does not detect stale images. `dev.build` runs `docker compose build` on request
  (`infrahub/tasks/container_ops.py:26-55`, `infrahub/tasks/dev.py:41-60`), `start_services`
  runs `up -d` without `--build` (`infrahub/tasks/container_ops.py:99-104`), and the developer
  rebuilds by hand. Its Dockerfile copies `uv.lock` and `pyproject.toml` before the source to use
  the layer cache (`infrahub/development/Dockerfile:99-102`).
- Sync's `tasks/dev.py:60-81` builds and starts the same way, with no staleness check; the image
  has OCI labels from build arguments (`Dockerfile:76-85`) but none for the build inputs.
- The Sync image already copies `pyproject.toml` and `uv.lock` to `/opt/infrahub-sync/`
  (`Dockerfile:99`), so a container could be started to compare them, but a label read is one
  `docker image inspect` and needs no container.

**Alternatives considered**:

- `docker compose up --build` on every start. Rejected: every start then rebuilds the project
  layer, because `COPY` of the source changes with each edit, which FR-018 says code changes
  must not cause.
- Comparing file modification times with the image creation time. Rejected: a `git checkout`
  changes modification times without changing content.

## R6. How `tasks/bench.py` reads the development settings (planning check 4)

**Decision**: the development Compose file holds every setting that `development/preview.env`
holds today, as `${NAME:-default}` interpolations so a shell variable still overrides it. A
single helper in `tasks/dev.py`, `dev_settings()`, runs
`docker compose -f development/docker-compose.yml --profile sync --profile infrahub config --format json`
and returns the resolved values the tasks need: the project name, the published host ports of
`sync-api`, `prefect-server` and `infrahub-server`, their bind address, and the
`INFRAHUB_INITIAL_ADMIN_TOKEN` of `infrahub-server`. `tasks/bench.py` calls this helper in place
of `preview.load_preview_env()` and `preview.preview_urls()`, and calls `dev._compose` in place of
`preview._compose`.

**Evidence**:

- `tasks/bench.py:97-102` builds its settings from `preview.load_preview_env()` and overrides
  `VERSION`, `INFRAHUB_DOCKER_IMAGE`, `INFRAHUB_DOCKER_IMAGE_DIGEST` and `COMPOSE_PROJECT_NAME`.
  It then reads the Infrahub URL through `preview.preview_urls(...)["infrahub"]`, which uses
  `PREVIEW_INFRAHUB_PORT` and `PREVIEW_INFRAHUB_BIND_ADDRESS` (`:103`), the
  `INFRAHUB_INITIAL_ADMIN_TOKEN` (`:104`) and `COMPOSE_PROJECT_NAME` (`:171`, `:236`). It also
  calls `preview._compose` (`:126`) and uses `preview.DEV_DIR / "docker-compose.infrahub.yml"`
  (`:136`, `:244`).
- `tasks/bench.py:192`, `:216`, `:220` and `:290` pass `ROOT / "compose.yaml"` as the Sync
  compose file, and `tests/test_benchmark_runner.py:904` asserts it. The repository root has no
  `compose.yaml`, only `docker-compose.yml`. The plan replaces this path with the development
  Compose file; whether the benchmark runner works today is not known.
- `docker compose config --format json` resolves interpolation, profiles and defaults the same
  way `up` does, so the tasks and the stack cannot read different values.
- No GitHub workflow runs the benchmark runner (no file under `.github/` mentions `bench`).
- With `--infrahub-network` (or `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`), the included Infrahub
  file is not started, so its ports do not describe the running Infrahub. In that case
  `dev_settings()` resolves without the `infrahub` profile and takes the Infrahub URL from
  `INFRAHUB_SYNC_DEV_INFRAHUB_URL`, default `http://infrahub-server:8000` (clarified
  2026-10-22).

**Alternatives considered**: duplicating the defaults as Python constants (two sources for one
value); parsing the YAML file directly (misses interpolation and shell overrides).

## R7. One development Compose file with an `infrahub` profile

**Decision**: rename `development/docker-compose.dev.yml` to `development/docker-compose.yml`
and merge the preview services into it. Core services have no profile; `sync-bootstrap`,
`sync-api` and `sync-worker` have `profiles: ["sync"]`. The file includes
`development/docker-compose.infrahub.yml` with Compose `include:`, and every service in that
file gets `profiles: ["infrahub"]`, so D-9 (Infrahub starts from that file) and D-12 (one file
with profiles) both hold. `dev.start` activates `sync` and `infrahub`; `dev.deps` activates
`infrahub` only; `--infrahub-network` or `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` drops the
`infrahub` profile and attaches the Sync services to that network, as
`tasks/dev.py:37-48` does today.

**Evidence**: `development/docker-compose.infrahub.yml:209-329` defines the Infrahub services
(`message-queue`, `cache`, `database`, `task-manager`, `task-manager-db`, `infrahub-server`,
`task-worker`) and has no profiles. The current development file has no profiles and no
checkout mounts (`development/docker-compose.dev.yml:32-206`).

**Alternatives considered**: passing `-f development/docker-compose.infrahub.yml` from the tasks
(two files on every command line, and `docker compose config --profiles` cannot list the
`infrahub` profile, which FR-015's test reads).

## R8. `TestInfrahubSyncDocker`

**Decision**: `infrahub_sync/testcontainers/` holds `TestInfrahubSyncDocker(TestInfrahubDocker)`
and the packaged root file at `infrahub_sync/testcontainers/docker-compose.yml`. A class-scoped
fixture starts the packaged file after Infrahub, with `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION`
taken from the environment or defaulting to `registry.opsmill.io/opsmill/infrahub-sync` and the
installed `infrahub-sync` version. It attaches `sync-worker` to the Infrahub project's network
so the worker reaches `infrahub-server`. A byte-equality test compares the packaged file with
the root `docker-compose.yml`. Because the parent class has no hook for extra compose files, the
Sync class adds one: the class-scoped fixture `infrahub_sync_compose_files`, a list of paths
(default empty) passed with `-f` after the packaged file. Each variable the root file reads is
taken from `os.environ` when set and from a test default otherwise, as `InfrahubDockerCompose`
does (clarified 2026-10-22; spec 013 uses both).

**Evidence**:

- `TestInfrahubDocker` is at `infrahub/python_testcontainers/infrahub_testcontainers/helpers.py:21`.
  Its fixtures are class-scoped: `infrahub_version` (`:22-24`, `INFRAHUB_TESTING_IMAGE_VER` or the
  package version), `tmp_directory`, `default_branch`, `infrahub_compose` (`:85-98`),
  `infrahub_app` (`:100-121`), `infrahub_port` (`:123-125`).
- `InfrahubDockerCompose` copies a packaged compose file into a temporary directory and writes
  an `.env` file in which each value is `os.environ.get(key, default)`
  (`infrahub/python_testcontainers/infrahub_testcontainers/container.py:144-160`, `:162-197`).
  The project name is `infrahub-test-<uuid8>` (`:140-142`). The compose file declares no
  custom network, so the network is `<project>_default`.
- A subclass can override the `infrahub_compose` fixture; there is no hook for extra compose
  files (`container.py:311-321`).
- The Infrahub package lists its compose files in hatch `include` lists
  (`infrahub/python_testcontainers/pyproject.toml:93-107`). Sync builds with hatchling and
  packs `infrahub_sync` and `opsmill_prefect_extras` (`pyproject.toml:158-166`); a file inside
  `infrahub_sync/` ships by default. Keeping the root file as the single source and copying it
  at build time with `[tool.hatch.build.targets.wheel.force-include]` is the other option; the
  byte-equality test covers either.
- `infrahub-testcontainers` registers a pytest plugin through `pytest11`
  (`infrahub/python_testcontainers/pyproject.toml:34-35`). Its session start calls
  `psutil.cpu_freq()`, which raises on macOS arm64. By product-owner decision of 2026-10-22 the
  plugin stays enabled, and `tests/conftest.py` wraps `psutil.cpu_freq` so it returns None on
  failure, as the OpsMill demo repositories do. Sync's unit tier already excludes the `integration`, `preview`, `docker` and
  `compose` markers (`tasks/tests.py:22`).

**Alternatives considered**: a pytest plugin entry point for Sync. Rejected: `TestInfrahubDocker`
is a base class, not a plugin, and Sync follows it.

## R9. Task names

**Decision**: as clarified and revised on 2026-10-22: the top-level `format` and `lint` stay as
the full passes and are the only top-level tasks; `linter.format`, `linter.lint`,
`docs.format` and `docs.lint` stay as subsets; `tests.all` and `tests.check-310` replace
`tests-all` and `check-310`; the other top-level tasks in `tasks/__init__.py` (`docusaurus`,
`generate-doc`, `tests-unit`, `tests-integration`, `build`, `start`, `destroy`) are removed;
`preview` is removed; `netbox.up` and `netbox.down` become `netbox.start` and `netbox.destroy`.

**Evidence**: the top-level `lint` and `format` call `docs.lint` / `docs.format` and then
`linter.lint_all` / `linter.format_all` (`tasks/__init__.py:40-50`); `linter.lint` and
`linter.format` exist at `tasks/linter.py:36-57`. Infrahub registers the same two tasks at the
top level (`infrahub/tasks/__init__.py:53-54`). `uv run --no-sync invoke --list`, run on
2026-10-22, lists top-level `format` and `lint` in `infrahub/python_sdk` ("Run RUFF to format
all Python files", "Run all linters"), `infrahub-backup` ("Format Python (ruff) and Markdown
(rumdl) files", "Run all linters (ruff, mypy, yamllint, rumdl)") and `styrmin` ("Format all code
(Python and Markdown)", "Run all linters (Python, Markdown, and YAML)"). Because `format` and `lint` keep their names,
`AGENTS.md:37-38`, `docs/docs/development/constitution.md:150-151`,
`docs/docs/development/knowledge/quality-gates.md` and
`docs/docs/development/guides/adding-an-adapter.md:395-396` stay valid and the constitution
needs no amendment. Users of removed names:
`docs/docs/development/guidelines/testing-tiers.md:38`, `:539` (`check-310`);
`.github/workflows/workflow-linter.yml:184` (`invoke docusaurus`); the nightly workflow and
`.github/scripts/nightly_e2e.py` (`preview.up`, `preview.seed`, `preview.down`, `netbox.down`,
lines 112-258).

## R10. Nightly workflow and the CI end-to-end tier

**Decision**: the nightly job keeps its current structure and calls the new tasks
(`dev.start`, `dev.seed`, `dev.destroy`, `netbox.start`, `netbox.destroy`). The end-to-end
compose suite moves to `TestInfrahubSyncDocker` (D-15).

**Evidence**: `.github/workflows/workflow-nightly-e2e.yml:24-25` runs
`.github/scripts/nightly_e2e.py`, which calls `invoke preview.up` (`:112`, `:115`),
`preview.seed` (`:217`, `:225`), `preview.down --volumes` (`:223`, `:240`, `:257`) and checks
digests with `docker compose config --images` against the preview and NetBox files (`:70-87`).
The `compose-suite` job (`:189-250`) builds `infrahub-sync:compose-test` and runs
`pytest -m compose tests/compose`.

## R11. First-start measurement procedure (FR-008)

**Decision**: measure the first start of the root `docker-compose.yml` under two conditions, on
two hosts, with the procedure below. This section records the baseline, with Prefect from
`prefecthq/prefect`. Task T024 adds the times for this feature with the same procedure.

**Procedure**: run it in an empty directory that holds only `docker-compose.yml` and an `.env`
written as in `docs/docs/compose-deployment.mdx` (the generator script there). Use a project
name that no other stack on the host uses. Clear an exported `VERSION`, which overrides `.env`.

1. Warm start, every image already pulled, no volumes:

   ```bash
   docker compose -p s010-measure pull
   docker compose -p s010-measure down -v
   time docker compose -p s010-measure up -d --wait --wait-timeout 420
   docker compose -p s010-measure down -v
   ```

2. Cold start, no image and no volume:

   ```bash
   docker compose -p s010-measure down -v
   docker compose -p s010-measure config --images | sort -u | xargs docker image rm
   time docker compose -p s010-measure up -d --wait --wait-timeout 420
   docker compose -p s010-measure down -v
   ```

   `down -v` removes only the volumes of this project. A global `docker volume prune` would
   also remove the volumes of other stacks on the host, so the procedure does not use it.
   Before `docker image rm`, check with `docker ps -a --filter ancestor=<image ID>` that no
   other container uses the image; when one does, keep the image and record the run as
   partly warm.

3. Record the `real` time of `time`, the date, the host (CPU, operating system, Docker Engine
   and Compose versions) and every image that was not pulled during the cold run.

Hosts: an amd64 GitHub `ubuntu-latest` runner and an arm64 laptop.

**Baseline** (2026-10-22, root file at commit `0fc900c6`):

| Host | Warm start | Cold start |
| --- | --- | --- |
| arm64 laptop: Apple M5 Pro, 18 cores, 48 GiB, macOS 26.6.2, OrbStack 2.2.4, Docker Engine 29.8.2, Compose 5.5.0 | 20.5 s | 42.2 s, with the Sync image already present (see below) |
| amd64 GitHub `ubuntu-latest` runner | to be measured in CI with the commands above | to be measured in CI with the commands above |

Notes on the laptop runs:

- The Sync image the file names, `registry.opsmill.io/opsmill/infrahub-sync:3.0.0a5`, was not
  in the registry (`docker pull` returned `not found`). The runs used an image built from the
  checkout instead (`docker build -t s010-baseline-sync:local .`, about 19 seconds with a warm
  build cache, 989 MB), set through `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION`. A local image
  cannot be pulled again, so the cold run kept it: the cold time includes pulling the other
  three images only.
- Images pulled during the cold run, with their unpacked size:
  - `postgres:16-alpine`: 411 MB
  - `cgr.dev/chainguard/minio:latest-dev`: 288 MB
  - `prefecthq/prefect:3.8.6-python3.13`: 1238 MB
- Both runs ended with `postgres`, `object-store`, `prefect-server`, `sync-api` and
  `sync-worker` healthy and `db-bootstrap`, `object-store-init` and `sync-bootstrap` exited
  with code 0. The published ports were moved to 18010 and 14210 so that the run could not
  collide with other stacks on the host.
- Each condition ran once. How much the times vary between runs is not known.

**This feature** (T024, 2026-10-22, root file at commit `c60828dc`: `prefect-server` from the
Sync image, the object store from
`pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc41…`), with the same procedure and project
name `s010-measure`:

| Host | Warm start | Cold start |
| --- | --- | --- |
| arm64 laptop: Apple M5 Pro, macOS 26.6.2, OrbStack, Docker Engine 29.8.2, Compose 5.5.0 | 20.6 s | 28.8 s, with the Sync image already present (see below) |
| amd64 GitHub `ubuntu-latest` runner | to be measured in CI with the commands above | to be measured in CI with the commands above |

Notes on the laptop runs:

- As for the baseline, the Sync image was built from the checkout
  (`docker build -t infrahub-sync:s010-measure .`, 1.01 GB) and set through
  `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync` and `VERSION=s010-measure`. The cold run kept it.
  The file now names three images, and the Sync image is one of them, so the cold run pulled
  two images only:
  - `postgres:16-alpine` (pinned by digest): 412 MB
  - `pgsty/silo:RELEASE.2026-10-18T00-00-00Z` (pinned by digest): 206 MB
- No other container used either image, so both were removed before the cold run.
- Both runs ended with `postgres`, `object-store`, `prefect-server` and `sync-api` healthy,
  `sync-worker` running, and `db-bootstrap`, `object-store-init` and `sync-bootstrap` exited
  with code 0. The Sync API port was moved to 18010; the file publishes no other port.
- The cold start is 13.4 s shorter than the baseline's because the Prefect image (1238 MB) is
  no longer pulled. On a host that also pulls the Sync image, the cold start includes pulling
  about 1 GB for that image; that time was not measured here.
- Each condition ran once. How much the times vary between runs is not known.
