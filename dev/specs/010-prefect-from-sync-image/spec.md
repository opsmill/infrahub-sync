# Feature Specification: Compose stacks run the Prefect server from the Sync image

**Feature Branch**: `010-prefect-from-sync-image`

**Created**: 2026-10-22

**Status**: Draft

**Input**: Idea brief "Compose stacks run Prefect from the Sync image (spec A)", product-owner
decisions D-1 to D-17 of 2026-10-21.

## Summary

Every Sync Compose stack runs its Prefect server from the public `prefecthq/prefect` image, so the
Prefect server version can differ from the Prefect client inside the Sync image, and customer teams
pull four images. The root deployment also publishes the Prefect port and serves the Prefect UI by
default. The bundled object store runs from a Chainguard build of upstream MinIO, whose code is
archived.

This feature runs the Prefect server from the Sync image as its own service in all three stacks:
the root deployment file, the development stack and a new pytest fixture for end-to-end tests. The
root deployment publishes only the Sync API port and turns the Prefect UI off; OpsMill engineers
open the UI on the loopback address through a documented override file. The bundled object store
moves to `pgsty/silo`, a maintained MinIO fork. Engineers get one development Compose file and one
`dev` task namespace with two modes, and every invoke task has one name.

This feature is the first of two. A second feature, built on this one, covers the remaining
3.0 blockers listed under Jira card INFP-692: an external S3 store, credentials for every adapter,
a custom certificate authority, lifecycle reliability, and the release digest and SBOM.

## Clarifications

### Session 2026-10-22

Answers from the product owner.

- Q: Which task names run the full format pass and the full lint pass once the top-level `format`
  and `lint` tasks are removed? → A (revised 2026-10-22): the top-level `format` and `lint`
  tasks stay as the full passes (documentation and Python). `linter.format`, `linter.lint`,
  `docs.format` and `docs.lint` stay as subsets. `tests-all` becomes `tests.all` and
  `check-310` becomes `tests.check-310`. Every other top-level task is removed. Reason for the
  revision: the other OpsMill repositories expose top-level `format` and `lint`. Infrahub
  registers `format_all` and `lint_all` at the top level (`infrahub/tasks/__init__.py:53-54`).
  `uv run --no-sync invoke --list`, run on 2026-10-22, lists top-level `format` and `lint` in
  `infrahub/python_sdk` ("Run RUFF to format all Python files", "Run all linters"),
  `infrahub-backup` ("Format Python (ruff) and Markdown (rumdl) files", "Run all linters (ruff,
  mypy, yamllint, rumdl)") and `styrmin` ("Format all code (Python and Markdown)", "Run all
  linters (Python, Markdown, and YAML)").
  The first answer, that `linter.format` and `linter.lint` would take over the documentation
  legs and that no top-level task would remain, is withdrawn.
- Q: Does running the AGPLv3 Silo image unmodified in a separate container need any action from
  OpsMill? → A: The operator documentation names the Silo image, its AGPLv3 licence and a link to
  its source. No notice file ships. This is recorded as the product owner's decision.
- Q: Which stacks does the rule "only `prefect-server` holds the Prefect database password"
  cover? → A: The root file, and so the test fixture that ships it. `db-bootstrap` is an allowed
  exception because it creates the Prefect role. The development stack keeps one local password.
- Q: Which Sync image does `TestInfrahubSyncDocker` use by default? → A:
  `registry.opsmill.io/opsmill/infrahub-sync` tagged with the installed `infrahub-sync` package
  version. The root file's image variables (`INFRAHUB_SYNC_DOCKER_IMAGE`, `VERSION`) override
  it, and CI sets them to its own image.
- Q: What happens to `development/preview.env`? → A: It is removed. Its values go directly into
  the development Compose file, and the `dev.*` tasks and the benchmark runner read them from
  there or from the Compose defaults.

### Session 2026-10-22 (analysis follow-up)

Changes the product owner approved after the cross-artifact analysis.

- Q: Does "nothing outside the Sync image calls the Prefect API" (D-2, FR-004) cover the
  development host processes? → A: No. The rule covers the root file and the test fixture. In
  the development stack, `dev.api` and `dev.worker` run on the host and call the Prefect server
  on `127.0.0.1:4230`. `dev.smoke` checks the worker through the Sync API's `/status` route, not
  through the Prefect API.
- Q: How does a test add a Compose file to the Sync project in `TestInfrahubSyncDocker`? → A:
  Through the class-scoped fixture `infrahub_sync_compose_files`, which returns a list of paths
  and is empty by default. Spec 013 uses this name.
- Q: Where do the fixture's settings come from? → A: Each variable the root file reads is taken
  from `os.environ` when it is set, and from a test default otherwise, as `InfrahubDockerCompose`
  does.
- Q: How does the development stack find an Infrahub instance given by `--infrahub-network`? →
  A: Its URL comes from `INFRAHUB_SYNC_DEV_INFRAHUB_URL` (default `http://infrahub-server:8000`
  inside the network); the ports of the included Infrahub file are not used in that case.
- Q: Are the task renames delivered with the stack changes? → A: No. The task renames and
  deletions go in a separate pull request from the development stack and mode changes.

## Decisions carried from the idea brief

These are final product-owner decisions. This specification does not reopen them.

- **D-1**: `prefect-server` is its own service that runs the Sync image. `sync-api` does not
  proxy the Prefect API. OpsMill engineers can opt in to publishing the Prefect port on the
  loopback address.
- **D-2** (scope clarified 2026-10-22): in the root file and the test fixture, nothing outside
  the Sync image calls the Prefect API. The CLI holds no Prefect credential. In the development
  stack, the host processes `dev.api` and `dev.worker` may call it.
- **D-3**: [ADR 0019](../../adr/0019-prefect-is-an-internal-component-of-sync.md) is amended:
  later, the Prefect API will be reachable only through the Sync API, either as one process or
  through a proxy (not decided yet). Keeping the Prefect database password out of `sync-api` is
  an extra layer of protection, not a principle.
- **D-4**: Two features, in order: this one, then the INFP-692 blockers on top of it.
- **D-5** (2026-10-21): every Compose file runs the bundled object store from
  [`pgsty/silo`](https://hub.docker.com/r/pgsty/silo), pinned by dated release tag and digest,
  instead of `cgr.dev/chainguard/minio:latest-dev`. The image is not mirrored into
  `registry.opsmill.io`.
  According to its [Docker Hub page](https://hub.docker.com/r/pgsty/silo) and the
  [Pigsty MinIO FAQ](https://pigsty.io/docs/minio/faq/), Silo keeps MinIO's S3 API, environment
  variables and on-disk format, ships amd64 and arm64 images, and is licensed under AGPLv3.
  Upstream MinIO Community Edition stopped publishing images on 2025-10-23 and its repository
  is archived ([lobehub issue 9845](https://github.com/lobehub/lobehub/issues/9845)).
- **D-6**: one `dev` task namespace with two modes: `dev.deps` (dependencies in containers, Sync
  API and worker on the host) and `dev.start` (everything in containers). The `preview` task
  namespace is removed.
- **D-7** (revised 2026-10-22): every task has exactly one name, except that `format` and
  `lint` stay at the top level as the full passes. The other top-level duplicates are removed:
  `docusaurus`, `generate-doc` and `tests-*` (`tests-all` becomes `tests.all`, `check-310`
  becomes `tests.check-310`). `netbox.up` and `netbox.down` become `netbox.start` and
  `netbox.destroy`. `AGENTS.md` and CI move to the new names.
- **D-8**: in `dev.deps` mode, the Prefect server runs from the Sync image built from the
  checkout.
- **D-9**: both modes start Infrahub from `development/docker-compose.infrahub.yml` by default.
  `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` or `--infrahub-network` attaches to an Infrahub instance
  that is already running.
- **D-10**: `dev.deps` starts containers only and prints their URLs. `dev.api` and `dev.worker`
  run the host processes in the foreground. PID files and background process handling are
  removed.
- **D-11**: on the operator deployment, a documented `docker-compose.override.yml` snippet opens
  the Prefect UI on `127.0.0.1`. The shipped root file always sets `PREFECT_SERVER_UI_API_URL`.
- **D-12**: one development Compose file with profiles: core services (PostgreSQL, the object
  store, `prefect-server` on `127.0.0.1:4230`), a `sync` profile (`sync-bootstrap`, `sync-api`,
  `sync-worker`) and an `infrahub` profile. `docker-compose.preview.yml` is removed.
- **D-13**: the operator deployment turns the Prefect UI off by default
  (`PREFECT_UI_ENABLED=false`). The override snippet turns it on, points its static files at a
  scratch directory and publishes the port. The development stack keeps the UI on.
- **D-14**: there is no supported in-place update from `3.0.0a5`. The release notes tell
  operators to run `docker compose down -v` first. The state lost is confirmed: the Prefect work
  pool, deployments and run history; saved plans and run files; the `infrahub_sync` database.
- **D-15**: the CI end-to-end tier starts Infrahub with `infrahub-testcontainers` and Sync with a
  matching fixture. The `dev.*` tasks stay for engineers.
- **D-16**: a new `infrahub-sync[testcontainers]` extra provides the module
  `infrahub_sync.testcontainers` and the class `TestInfrahubSyncDocker`, which inherits
  `TestInfrahubDocker`. The root `docker-compose.yml` ships as package data. The fixture stays
  experimental until a demo repository adopts it.
- **D-17**: Sync's `prefect` pin and the `prefect-client` pin of the `infrahub-testcontainers`
  version Sync depends on always name the same Prefect version.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Customer team deploys Sync from the root Compose file (Priority: P1)

A customer team writes a `.env` file, runs `docker compose up -d --wait`, then registers a
configuration, plans and applies with the CLI. The Prefect server runs the same Sync image and
version as the Sync API and worker, so the Prefect server cannot differ from the Prefect client.
The team pulls three images instead of four. The default deployment publishes only the Sync API
port and serves no Prefect UI.

**Why this priority**: the root file is the supported customer deployment
([ADR 0016](../../adr/0016-the-compose-deployment-is-one-tag-pinned-file.md)). A version
mismatch between Prefect server and client, or an exposed Prefect port, affects every customer.

**Independent Test**: start the root file with a complete `.env` on a host with no Sync volumes,
wait for every service to report healthy, then register, plan and apply a configuration with the
CLI.

**Acceptance Scenarios**:

1. **Given** a complete `.env`, **When** the team runs `docker compose up -d --wait`, **Then**
   every service reports healthy, `prefect-server` runs
   `${INFRAHUB_SYNC_DOCKER_IMAGE}:${VERSION}` like `sync-api` and `sync-worker`, and no
   container runs `prefecthq/prefect`.
2. **Given** the default root deployment, **When** the team lists the published host ports,
   **Then** `sync-api` is the only service that publishes a host port.
3. **Given** the default root deployment, **When** the team inspects the resolved environment of
   each service, **Then** only `prefect-server`, and `db-bootstrap` which creates the Prefect
   role, hold the Prefect database password, and the `cli` service holds no Prefect credential.
4. **Given** the default root deployment, **When** the team starts it from an empty volume,
   **Then** the bundled object store runs `pgsty/silo` pinned by release tag and digest, reaches
   healthy, and `sync-bootstrap` creates the bucket.
5. **Given** a deployment of `3.0.0a5`, **When** the team reads the release notes, **Then** the
   notes tell them to run `docker compose down -v` before updating and list the state that this
   removes.

---

### User Story 2 - Engineer runs the whole development stack in containers (Priority: P1)

An OpsMill engineer runs `uv run invoke dev.start` from a checkout. Infrahub, PostgreSQL, the
object store, `prefect-server` and the Sync services run in containers built from the checkout.
After a code change, `dev.restart` recreates the Sync containers so they run the changed code
without an image rebuild.

**Why this priority**: engineers verify every change against this stack. It replaces three
development Compose files and two task namespaces.

**Independent Test**: from a fresh checkout, follow
[`development-stack.mdx`](../../../docs/docs/development-stack.mdx) to run `dev.start`, then
`dev.smoke`.

**Acceptance Scenarios**:

1. **Given** a checkout, **When** the engineer runs `uv run invoke dev.start`, **Then** Infrahub,
   PostgreSQL, the object store, `prefect-server` and the Sync services run in containers built
   from the checkout, and `dev.smoke` passes.
2. **Given** a running stack, **When** the engineer changes a module under `infrahub_sync/` and
   runs `dev.restart`, **Then** the Sync services run the changed code.
3. **Given** an `infrahub-sync:dev` image built from an older lock file or `Dockerfile`, **When**
   the engineer runs `dev.start`, **Then** the task rebuilds the image before starting the stack.
4. **Given** an Infrahub instance already running on a Docker network, **When** the engineer runs
   `dev.start --infrahub-network <network>` or sets `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`,
   **Then** the stack attaches to that Infrahub instead of starting its own.

---

### User Story 3 - Engineer runs the Sync API and worker on the host (Priority: P1)

An OpsMill engineer runs `dev.deps` to start only the dependencies in containers, then runs
`dev.api` and `dev.worker` in two terminals, so they can use a debugger and see the process output
directly.

**Why this priority**: this mode replaces the `preview` stack and its background process
handling, which engineers use for debugging today.

**Independent Test**: from a fresh checkout, run `dev.deps`, then `dev.api` and `dev.worker` in two
terminals, then `dev.smoke`.

**Acceptance Scenarios**:

1. **Given** a checkout, **When** the engineer runs `dev.deps`, **Then** the task starts the
   containers, including `prefect-server` from the Sync image built from the checkout, prints
   their URLs and returns.
2. **Given** `dev.deps` has run, **When** the engineer runs `dev.api` and `dev.worker` in two
   terminals, **Then** both host processes reach the Prefect server on `127.0.0.1:4230`, run in
   the foreground, and `dev.smoke` passes.
3. **Given** `dev.deps` has run but `dev.api` or `dev.worker` is not running, **When** the
   engineer runs `dev.smoke`, **Then** the task fails and names the missing process.

---

### User Story 4 - OpsMill engineer opens the Prefect UI on a customer deployment (Priority: P2)

During a support session, an OpsMill engineer adds the documented override snippet to a customer
deployment to view the Prefect UI on the loopback address, and removes it afterwards.

**Why this priority**: needed for support, but used only on request. The default deployment must
stay closed.

**Independent Test**: start the root file with the override snippet, open the UI on the loopback
address, then remove the snippet and confirm the port is closed.

**Acceptance Scenarios**:

1. **Given** the root file, **When** the engineer adds the documented override snippet and runs
   `docker compose up -d`, **Then** the Prefect UI answers on
   `127.0.0.1:${INFRAHUB_SYNC_PREFECT_PORT:-4200}` and asks for the Prefect API credential.
2. **Given** the snippet is in place, **When** the engineer removes it and runs
   `docker compose up -d`, **Then** the port is closed and the UI is off.
3. **Given** a customer deployment that already has a `docker-compose.override.yml`, **When** the
   engineer follows the operator documentation, **Then** the documentation explains how to merge
   the snippet into the existing file.

---

### User Story 5 - CI and demo repositories run end-to-end tests against Infrahub and Sync (Priority: P2)

A demo repository maintainer, or the Sync CI end-to-end tier, writes a pytest class that inherits
`TestInfrahubSyncDocker` and tests a Sync configuration package against a running Infrahub and
Sync, as they already do for Infrahub alone with `TestInfrahubDocker`.

**Why this priority**: lets demo repositories test their packages, and replaces the development
tasks in CI. It depends on User Story 1, because the fixture runs the root file.

**Independent Test**: run a pytest class inheriting `TestInfrahubSyncDocker` with the CI-built
image, and complete a plan and an apply.

**Acceptance Scenarios**:

1. **Given** a test class inheriting `TestInfrahubSyncDocker`, **When** pytest runs it, **Then**
   Infrahub starts from `infrahub-testcontainers`, Sync starts from the packaged root Compose file
   and the CI-built image set through `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION`, the Sync worker
   reaches `infrahub-server`, and a plan and an apply
   complete.
2. **Given** the unit test tier, **When** it runs, **Then** it needs no Docker and the
   `testcontainers` pytest plugin does not load.

---

### Edge Cases

- **Read-only image**: the Prefect UI copies its static files at start. The copy goes to a scratch
  directory, and only when the UI is on.
- **Stale development image**: an `infrahub-sync:dev` image built before the Prefect server
  module existed is rebuilt by `dev.start` and `dev.deps` (FR-018).
- **Existing override file**: a customer who already has a `docker-compose.override.yml` follows
  the documentation to merge the snippet.
- **`INFRAHUB_SYNC_PREFECT_PORT` already set**: without the override snippet the variable has no
  effect. The release notes say so.
- **Custom image without the `service` extra**: `prefect-server` fails its health check, and its
  log shows the start error.
- **`dev.smoke` in `dev.deps` mode without `dev.api` or `dev.worker`**: the task fails and names
  the missing process.
- **Update from `3.0.0a5`**: not supported in place. The operator runs `docker compose down -v`
  first and loses the state listed in D-14.

## Requirements *(mandatory)*

### Functional Requirements

#### Prefect server from the Sync image

- **FR-001**: The root Compose file, the development stack and the test fixture MUST each run the
  Prefect server as its own service, `prefect-server`, from the Sync image, started by uvicorn
  through a Prefect server module in the Sync package. No Compose file and no fixture MUST
  reference `prefecthq/prefect`. Test: a contract test finds zero `prefecthq/prefect` references.
- **FR-002**: In each stack, `prefect-server` MUST run the same image reference as `sync-api` and
  `sync-worker`. Test: `docker compose config` comparison per stack.
- **FR-003**: In the root file, and so in the test fixture that ships it, `prefect-server` MUST be
  the only service that holds the Prefect database password, with one allowed exception:
  `db-bootstrap` (PostgreSQL image), because it creates the Prefect role. The `cli` service MUST
  hold no Prefect credential. The development stack is not covered and keeps one local password
  for all its services. Test: a contract test over the resolved environment of each service in
  the root file.
- **FR-004**: `sync-api` MUST NOT proxy the Prefect API. In the root file and the test fixture,
  nothing outside the Sync image MUST call the Prefect API. The development host processes
  `dev.api` and `dev.worker` are allowed to. Test: a contract test finds no `PREFECT_API_URL` or
  Prefect credential outside the Sync-image services of the root file.
- **FR-005**: Changing the `prefect` pin in `pyproject.toml` and `uv.lock` MUST change the
  Prefect server version in every stack, with no other file edited. Test: a contract test finds
  no Prefect version string and no Prefect image in any Compose file or in the `Dockerfile`.

#### Root deployment

- **FR-006**: By default, the root file MUST NOT publish the Prefect port, MUST set
  `PREFECT_UI_ENABLED=false`, and MUST always set `PREFECT_SERVER_UI_API_URL` to the loopback
  address. Test: `docker compose config` assertions.
- **FR-006a**: The root file and the development stack MUST publish the Sync API on host port
  8001 by default; inside the container it listens on 8000. Product-owner decision 2026-10-23: the Sync API defaults to host port 8001; 8000 is for Infrahub. Test: `docker compose
  config` assertions.
- **FR-007**: The operator documentation MUST include an override snippet that turns the Prefect
  UI on, points its static files at a scratch directory, and publishes the port on `127.0.0.1`
  only. It MUST explain how to merge the snippet into an existing `docker-compose.override.yml`.
  Test: a Compose test of the root file plus the snippet.
- **FR-008**: The operator documentation MUST give the measured first-start times and the command
  `docker compose up -d --wait --wait-timeout 420`, because `--wait-timeout` has no default. An
  earlier measurement recorded about 20 seconds with the images already present and 220.5 seconds
  on a cold CI runner; the times for this feature are not known until measured. Test: a
  documentation test for the command.
- **FR-009**: The release notes MUST say that no in-place update from `3.0.0a5` is supported,
  that operators run `docker compose down -v` first, which state this removes (D-14), and that
  `INFRAHUB_SYNC_PREFECT_PORT` has no effect without the override snippet.
- **FR-010**: [ADR 0019](../../adr/0019-prefect-is-an-internal-component-of-sync.md) MUST be
  amended as stated in D-3, and
  [ADR 0016](../../adr/0016-the-compose-deployment-is-one-tag-pinned-file.md) MUST gain a note
  on the override snippet and on the root file shipped as package data.

#### Bundled object store

- **FR-011**: Every Compose file MUST run the bundled object store from `pgsty/silo`, pinned by a
  dated release tag and its digest. No file MUST reference `cgr.dev/chainguard/minio` or
  `minio/minio`. Test: a digest contract test.
- **FR-012**: `sync-bootstrap` MUST create the bucket through the S3 API, without the `mc` client
  inside the object store image. Test: a bootstrap test against an empty Silo instance.
- **FR-013**: Every object store health check, and the repair of the volume owner, MUST use only
  what the Silo image contains, because the Chainguard `-dev` build supplied `wget` and `mc`.
  Test: a Compose start from an empty volume reaches healthy.
- **FR-014**: The operator documentation MUST name the Silo image, its AGPLv3 licence and a link
  to its source. No third-party notice file ships with the release. The product owner decided on
  2026-10-22 that running Silo unmodified in a separate container needs no further action. Test:
  a documentation test for the image name, the licence and the link.

#### Development stack and invoke tasks

- **FR-015**: The development stack MUST be one Compose file with core services (PostgreSQL, the
  object store, `prefect-server` on `127.0.0.1:4230`), a `sync` profile (`sync-bootstrap`,
  `sync-api`, `sync-worker`) and an `infrahub` profile, with the Prefect UI on.
  `docker-compose.preview.yml` and `development/preview.env` MUST be removed. The settings that
  `development/preview.env` holds today MUST be written directly in the development Compose file,
  and the `dev.*` tasks MUST read them from that file or from its defaults. Test: file inventory
  and `docker compose config --profiles`.
- **FR-016**: The task inventory MUST be:
  - top level: `format` (`docs.format`, then `linter.format`) and `lint` (`docs.lint`, then
    `linter.lint`), the full passes;
  - `dev`: `dev.build`, `dev.deps`, `dev.start`, `dev.restart`, `dev.stop`, `dev.destroy`,
    `dev.status`, `dev.logs`, `dev.seed`, `dev.smoke`, `dev.api` and `dev.worker`;
  - `netbox`: `netbox.start` and `netbox.destroy` in place of `netbox.up` and `netbox.down`;
  - `tests`: `tests.all` (unit and integration tiers) and `tests.check-310`, next to the
    existing `tests.tests-unit` and `tests.tests-integration`;
  - `docs`, `linter`: unchanged, including the subsets `docs.format`, `docs.lint`,
    `linter.format` and `linter.lint`.

  Test: task inventory.
- **FR-017**: Every invoke task MUST have exactly one name, and `format` and `lint` MUST be the
  only top-level tasks. The top-level `docusaurus`, `generate-doc`, `tests-unit`,
  `tests-integration`, `tests-all`, `check-310`, `build`, `start` and `destroy` tasks and the
  `preview` namespace MUST be removed; their replacements are in FR-016. Test: a task inventory
  test finds no duplicate names and no other top-level task.
- **FR-018**: `dev.start` and `dev.deps` MUST rebuild the Sync image when the dependencies or the
  `Dockerfile` changed since the image was built. Code changes alone MUST NOT need a rebuild.
  Test: run against an image built from an older lock file.
- **FR-019**: The development stack MUST mount the checkout's `infrahub_sync/` and
  `opsmill_prefect_extras/` directories read-only over the installed copies in its Sync services,
  and `dev.restart` MUST recreate the Sync containers so they run the changed code. Test: change a
  module, run `dev.restart`, observe the change.
- **FR-020**: `dev.start` and `dev.deps` MUST start Infrahub from
  `development/docker-compose.infrahub.yml` by default. `--infrahub-network` or
  `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` MUST attach the stack to an Infrahub instance that is
  already running; the tasks then take its URL from `INFRAHUB_SYNC_DEV_INFRAHUB_URL` (default
  `http://infrahub-server:8000`), not from the included Infrahub file. Test: command-line tests.
- **FR-021**: `dev.deps` MUST start containers only and print their URLs. `dev.api` and
  `dev.worker` MUST run the host processes in the foreground. The tasks MUST NOT write PID files
  or manage background processes.
- **FR-022**: `dev.smoke` MUST fail and name the missing process when it runs in `dev.deps` mode
  without `dev.api` or `dev.worker`. It checks both through the Sync API: no answer from `/status`
  names `dev.api`; a `/status` answer whose worker is not available names `dev.worker`.

#### End-to-end test fixture

- **FR-023**: The optional extra `infrahub-sync[testcontainers]` MUST provide the module
  `infrahub_sync.testcontainers` with the class `TestInfrahubSyncDocker`, which inherits
  `TestInfrahubDocker`. The root `docker-compose.yml` MUST ship as package data, byte-equal to the
  repository file. The module MUST be documented as experimental until a demo repository adopts
  it. By default the class MUST run `registry.opsmill.io/opsmill/infrahub-sync` tagged with the
  installed `infrahub-sync` package version; `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION`, the root
  file's image variables, MUST override it, and CI MUST set them to its own image. Each other
  variable the root file reads MUST be taken from `os.environ` when set and from a test default
  otherwise. A class-scoped fixture `infrahub_sync_compose_files` (a list of paths, empty by
  default) MUST add Compose files to the Sync project. Test: a byte-equality test; a test of the
  default and overridden image reference; a test that an extra file and an `os.environ` value
  reach the Sync project; the CI end-to-end tier uses the class.
- **FR-024**: Sync's `prefect` pin and the `prefect-client` pin of the `infrahub-testcontainers`
  version Sync depends on MUST name the same Prefect version. Test: reads both versions from
  `uv.lock`.
- **FR-025**: The unit test tier MUST NOT need Docker. The `infrahub-testcontainers` pytest plugin
  stays enabled in `tests.tests-unit`, and its session start MUST NOT fail on macOS arm64
  (product-owner decision of 2026-10-22, which replaces the earlier rule that the plugin does not
  load).

#### CI and documentation

- **FR-026**: The CI end-to-end tier, the nightly workflow and the benchmark runner MUST use the
  new task names or the fixture. `AGENTS.md` and the documentation MUST name only the current
  tasks. `invoke format` and `invoke lint` in `AGENTS.md`, `docs/docs/development/constitution.md`,
  `docs/docs/development/knowledge/quality-gates.md` and
  `docs/docs/development/guides/adding-an-adapter.md` stay valid; the removed names (for example
  `invoke docusaurus` in `.github/workflows/workflow-linter.yml`, `check-310` in
  `docs/docs/development/guidelines/testing-tiers.md`, and every `preview.*` task) MUST be
  replaced. The benchmark runner (`tasks/bench.py`) reads `development/preview.env` today; it MUST
  read the same settings from the development Compose file or its defaults (planning settled how,
  in [research.md R6](research.md#r6-how-tasksbenchpy-reads-the-development-settings-planning-check-4)).
  Test: a search over `.github/`, `docs/` and `AGENTS.md` finds no removed task name.
- **FR-027**: [`development-stack.mdx`](../../../docs/docs/development-stack.mdx) MUST describe both
  modes well enough for an engineer with a fresh checkout to reach a passing `dev.smoke` without
  other documents.

### Key Entities

- **Root Compose file** (existing, [ADR 0016](../../adr/0016-the-compose-deployment-is-one-tag-pinned-file.md)):
  the customer deployment. Changes: `prefect-server` runs the Sync image, the Prefect UI is off,
  the Prefect port is not published, the object store runs Silo. Also ships as package data.
- **Development stack** (existing, merged): one Compose file that replaces
  `development/docker-compose.dev.yml`, `development/docker-compose.preview.yml` and
  `development/preview.env`. It holds the development settings directly and uses one local
  database password for all its services.
- **Sync image** (existing): now also runs `prefect-server`.
- **Prefect server module** (new): the module in the Sync package that builds the Prefect server
  application for uvicorn: `create_prefect_app()` in `infrahub_sync/service/prefect_server.py`,
  as decided in planning.
- **`TestInfrahubSyncDocker`** (new): the pytest base class for end-to-end tests of Infrahub plus
  Sync. It runs the published Sync image of the installed package version unless the root file's
  image variables name another image, and takes extra Sync Compose files from
  `infrahub_sync_compose_files`.
- **Override snippet** (new, documentation): the `docker-compose.override.yml` content that opens
  the Prefect UI on the loopback address.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A customer team deploying the root file pulls three distinct images, down from four.
- **SC-002**: The Prefect server and the Sync worker report the same Prefect version in 100% of
  end-to-end runs.
- **SC-003**: The default root deployment publishes one host port.
- **SC-004**: Zero `prefecthq/prefect` references remain across the repository's Compose files
  and the test fixture.
- **SC-005**: The number of development Compose files for Sync goes from three to one, and the
  invoke task list has zero duplicate names.
- **SC-006**: An engineer with a fresh checkout reaches a passing `dev.smoke` in either mode by
  following `development-stack.mdx` alone.
- **SC-007**: Changing the Prefect pin in `pyproject.toml` changes the Prefect server version in
  every stack with no other file edited.

## Constitution Alignment

Against the [project constitution](../../../docs/docs/development/constitution.md):

- **VII. Simplicity and maintainability**: supported. Fewer Compose files, fewer tasks, no
  background process handling.
- **VI. Security, secrets, and input boundaries**: supported. In the root file, the Prefect
  database password is in one Sync service (plus `db-bootstrap`, which creates the role), the CLI
  holds no Prefect credential, and the Prefect port is closed by default.
- **V. Test discipline**: the end-to-end tier stays opt-in by marker; the unit tier stays free of
  Docker (FR-025).
- **I. Read-only and dry-run by default**: not affected.

## Governance Gates Crossed

`AGENTS.md` names no gate list; this list is the generic one from the brief.

- New dependency: `infrahub-testcontainers` and `testcontainers`, as an optional extra and a
  development dependency.
- CI/CD change: task names, the nightly workflow, the end-to-end tier.
- Python API change: new public module `infrahub_sync.testcontainers`, experimental. No HTTP API
  or CLI change.
- Architecture records: ADR 0019 amendment and ADR 0016 note (FR-010).
- No database or schema change. No authentication change.

## Assumptions

- The Sync image and the current Prefect server image both use Prefect 3.8.6 on Python 3.13, so
  the Prefect database needs no migration.
- Sync's Prefect server module needs none of Infrahub's Prefect bootstrap or tuning variables.
- Customer teams accept the state loss of D-14 on updates between alpha releases (confirmed by the
  product owner).

## Planning Checks

These facts are not known yet and are checked during planning:

- Whether uv installs `prefect` and `prefect-client` of the same version together without a file
  conflict. The Infrahub repository pins `prefect==3.8.6` next to its own
  `infrahub-testcontainers` (`infrahub/pyproject.toml`, line 31), whose lock may avoid
  `prefect-client`.
- For Silo: the latest release date and the release frequency; whether both platforms pull
  without authentication; the user and UID the image runs as (the root file runs the object store
  as `65532:65532`); which tools the image contains for health checks.
- Product: update Jira card INFP-692, which still lists "temporary MinIO build" as a blocker.

## Out of Scope

- A Prefect proxy in `sync-api`, or merging `sync-api` and `prefect-server` into one process.
- `demo.*` tasks.
- A supported in-place update from `3.0.0a5`.
- Mirroring the object store image into `registry.opsmill.io`.
- Replacing the bundled object store with a product that is not a MinIO fork.
- The INFP-692 blockers covered by the second feature: an external S3 store, credentials for
  every adapter, a custom certificate authority, lifecycle reliability, and the release digest
  and SBOM.
