# 30. The end-to-end test class inherits Infrahub's, and the Prefect pins move together

**Status**: Accepted
**Date**: 2026-10-22
**Source**: `dev/specs/archive/010-prefect-from-sync-image/research.md` (R4, R8),
`dev/specs/archive/010-prefect-from-sync-image/spec.md` (D-15, D-16, D-17)

## Context

The CI end-to-end tier and the OpsMill demo repositories need a running Infrahub and a running
Sync deployment in one test session. Infrahub publishes `infrahub-testcontainers`, whose
`TestInfrahubDocker` base class starts Infrahub for a test class. That package pins
`prefect-client` exactly. Sync pins `prefect` exactly and runs its Prefect server and worker
from that release ([ADR 0019](0019-prefect-is-an-internal-component-of-sync.md)).

`prefect` and `prefect-client` install files in the same `prefect/` directory: of the 450 files
`prefect-client` 3.8.6 ships, 449 are byte-identical to the same files in `prefect` 3.8.6. One
environment can hold both only when they name the same version.

## Decision

- **A `testcontainers` extra.** `infrahub-sync[testcontainers]` provides
  `infrahub_sync.testcontainers` and the class `TestInfrahubSyncDocker`, which inherits
  `TestInfrahubDocker`. For each test class it starts Infrahub, then the Sync deployment from
  the root `docker-compose.yml` that the package ships
  ([ADR 0016](0016-the-compose-deployment-is-one-tag-pinned-file.md), note of 2026-10-23), and
  connects `sync-worker` to the Infrahub network.
- **Settings.** The Sync image defaults to `registry.opsmill.io/opsmill/infrahub-sync` tagged
  with the installed `infrahub-sync` version; `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION`
  override it. Every other variable the root file reads comes from the test process's
  environment when set, and from a test value otherwise, as `InfrahubDockerCompose` does.
- **Extra Compose files.** The parent class has no hook for them, so the Sync class adds the
  class-scoped fixture `infrahub_sync_compose_files`, a list of paths passed with `-f` after
  the root file, empty by default.
- **One Prefect version.** Sync's `prefect` pin and the `prefect-client` pin of the
  `infrahub-testcontainers` version Sync locks always name the same Prefect version, and move
  together. `tests/test_release_pin.py` checks that `uv.lock` resolves both to one version.
- **Experimental.** The fixture names and settings can change in any release until a demo
  repository adopts the class.

## Consequences

- A Prefect upgrade in Sync waits for an `infrahub-testcontainers` release that pins the same
  `prefect-client`, or the lock fails the release-pin test.
- Removing `prefect-client` alone would delete the shared files from the `prefect`
  installation. uv removes and installs both as a pair when the pins move together.
- `infrahub-testcontainers` registers a pytest plugin that calls `psutil.cpu_freq()` at
  session start, which fails on macOS arm64. The plugin stays enabled, and `tests/conftest.py`
  makes that call return None on failure.
- Connecting `sync-worker` to the Infrahub network is safe only while that network has no
  service named `prefect-server`, `postgres` or `object-store`. On a network that has one, the
  worker can resolve the other stack's service; in a manual check, a worker connected to the
  development stack's network polled that stack's Prefect server and its run ended
  `abandoned`. Infrahub's Compose file has none of these names.
- The class supports Python 3.11 or later, like the `service` extra.

## Alternatives considered

- **A pytest plugin entry point for Sync.** Rejected: `TestInfrahubDocker` is a base class,
  not a plugin, and Sync follows it.
- **Run the fixture from a separate environment without `prefect`.** Rejected: the CI tier and
  the demo repositories install Sync and the fixture together.
