# 29. The development stack is one Compose file with profiles, run by one `dev` namespace

**Status**: Accepted
**Date**: 2026-10-22
**Source**: `dev/specs/archive/008-prefect-from-sync-image/research.md` (R5, R6, R7),
`dev/specs/archive/008-prefect-from-sync-image/spec.md` (D-6, D-8, D-9, D-10, D-12)

## Context

Engineers had two local stacks: a development Compose file and a separate preview stack with
its own Compose file, settings file and task namespace. The preview tasks started the Sync API
and worker as background host processes with PID files. The benchmark runner read the preview
settings file. The Prefect server in both stacks came from the public Prefect image, not from
the Sync image.

Engineers need two ways of working: everything in containers, or the dependencies in containers
with the Sync API and worker on the host, for a debugger.

## Decision

- **One Compose file.** `development/docker-compose.yml` holds the core services (PostgreSQL,
  the object store and `prefect-server`, which runs the Sync image built from the checkout)
  with no profile. `sync-bootstrap`, `sync-api` and `sync-worker` have the `sync` profile. The
  file includes `development/docker-compose.infrahub.yml` with Compose `include:`, and every
  Infrahub service has the `infrahub` profile.
- **One task namespace with two modes.** `dev.start` starts the core services and both
  profiles. `dev.deps` starts the core services and the `infrahub` profile, prints the
  addresses and returns; `dev.api` and `dev.worker` then run the host processes in the
  foreground. No task writes a PID file or starts a background process.
- **An Infrahub that already runs.** `--infrahub-network` or
  `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` drops the `infrahub` profile and connects the
  `sync-worker` container to that network. The Infrahub address then comes from
  `INFRAHUB_SYNC_DEV_INFRAHUB_URL`, default `http://infrahub-server:8000`.
- **The Compose file is the one source of settings.** Every setting is a `${NAME:-default}`
  interpolation in the file, except the approval setting, which is `${REQUIRE_APPROVAL-false}`:
  its default applies only when the variable is not set, so an empty value is passed to the
  services as it is. The tasks read the resolved values (project name, published ports,
  bind address, Infrahub admin token) from `docker compose config --format json`, through
  `dev_settings()` in `tasks/dev.py`. The benchmark runner reads its destination's Infrahub
  port and admin token from the same resolved output, through `resolved_compose()`.
- **The image rebuilds only when its build inputs change.** The image carries the label
  `org.opsmill.infrahub-sync.build-inputs`, the SHA-256 of `Dockerfile`, `pyproject.toml` and
  `uv.lock`. `dev.start` and `dev.deps` rebuild when the image or the label is missing or the
  value differs. The checkout's `infrahub_sync/` and `opsmill_prefect_extras/` are mounted
  read-only into the Sync containers, so a code change needs `dev.restart`, not a rebuild.

## Consequences

- The preview stack, its settings file and its task namespace are removed. The nightly
  workflow calls the `dev` and `netbox` tasks.
- A shell variable overrides any setting, and the tasks and the stack always read the same
  value, because both go through Compose interpolation.
- `docker compose config --profiles` lists both profiles, so a test can check them.
- The included Infrahub file must keep the `infrahub` profile on every service. Refreshing it
  from upstream means restoring the profile and the image pins (`development/README.md`).
- The development stack and the root deployment both publish the Sync API on host port 8001,
  so running both at once needs one port moved.
- In `dev.deps` mode, the host processes call the Prefect server on `127.0.0.1`. The rule that
  only Sync-image services call the Prefect API covers the root file and the test fixture, not
  this mode.

## Alternatives considered

- **Pass the Infrahub file with a second `-f` from the tasks.** Rejected: every command line
  needs two files, and `docker compose config --profiles` cannot list the `infrahub` profile.
- **Keep the defaults as Python constants in the tasks.** Rejected: two sources for one value.
- **Parse the Compose YAML directly.** Rejected: it misses interpolation and shell overrides.
- **`docker compose up --build` on every start.** Rejected: every source edit changes the
  copied project layer, so every start would rebuild.
- **Compare file modification times with the image creation time.** Rejected: a `git checkout`
  changes modification times without changing content.
