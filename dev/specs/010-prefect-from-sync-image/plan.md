# Implementation Plan: Compose stacks run the Prefect server from the Sync image

**Branch**: `010-prefect-from-sync-image` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/010-prefect-from-sync-image/spec.md`

## Summary

Every Sync stack (root `docker-compose.yml`, the development stack, and a new pytest fixture)
runs the Prefect server as a `prefect-server` service that uses the Sync image and starts a
factory module with uvicorn, as Infrahub does for its task manager
(`infrahub/docker-compose.yml:283-297`). The root file turns the Prefect UI off and publishes
only the Sync API port; a documented override file opens the UI on the loopback address. Every
stack runs the bundled object store from `pgsty/silo` pinned by tag and digest. The two
development Compose files and `development/preview.env` become one file with `sync` and
`infrahub` profiles, the `dev` namespace gains two modes (`dev.start`, `dev.deps`), and every
invoke task keeps one namespaced name. A new optional extra provides `TestInfrahubSyncDocker`
for end-to-end tests. The research behind each choice is in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.13 in the Sync image (`Dockerfile:8`); the package supports
3.10–3.13, and the Sync service and the Prefect server run on 3.11–3.13 only.

**Primary Dependencies**: Prefect 3.8.6 (`prefect` in the `service` extra, `pyproject.toml:89`),
uvicorn, FastAPI 0.141.1 (pin required, [R1](research.md#r1-run-the-prefect-server-from-the-sync-image-through-a-factory-module)),
Invoke, Docker Compose v2 (2.20 or later, the first release with `include:`), `pgsty/silo`
`RELEASE.2026-10-18T00-00-00Z`. New, in the optional `testcontainers` extra and the `dev`
extra: `infrahub-testcontainers` (1.11.8 at research time, pins `prefect-client==3.8.6`) and
`testcontainers` 4.8.

**Storage**: PostgreSQL 16 (Sync product database and Prefect database), S3 object store (Silo).
No schema change.

**Testing**: pytest. Unit tier without Docker (`uv run invoke tests.tests-unit`); contract tests
over `docker compose config`; the `compose` marker suite and the new fixture for end-to-end
runs; `docker` marker for image tests.

**Target Platform**: Linux containers, linux/amd64 and linux/arm64; macOS and Linux hosts for
engineers.

**Project Type**: Python library and service with a Compose deployment and Invoke development
tasks.

**Performance Goals**: first start of the root file within `--wait-timeout 420`; the operator
documentation gives the measured times (FR-008).

**Constraints**: Sync services run read-only as user 10001 with tmpfs scratch directories
(`docker-compose.yml:31-37`); no new published port in the root file; no Prefect proxy in
`sync-api`; the Prefect version comes from `pyproject.toml` / `uv.lock` only.

**Scale/Scope**: 3 Compose stacks, about 30 invoke tasks, 2 ADRs, about 8 documentation pages,
2 workflows.

No item is left as NEEDS CLARIFICATION; the four planning checks are settled in research
R3–R6.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Against the [project constitution](../../../docs/docs/development/constitution.md), version
1.0.3.

| Principle | Result | Reason |
|-----------|--------|--------|
| I. Read-only and dry-run by default | Pass | No change to `configs`, `runs plan`, `diff`, `sync` or `apply`. |
| II. Sync idempotency and safety | Pass | Bucket creation stays idempotent (`bootstrap.py:79-90`); stale-image detection only rebuilds. |
| III. Adapter symmetry | Not affected | No adapter changes. |
| IV. Type safety and explicit contracts | Pass | New modules are typed; the fixture's public surface is listed in [contracts/testcontainers-api.md](contracts/testcontainers-api.md). |
| V. Test discipline | Pass | Each FR names its test; the unit tier stays Docker-free and disables the `infrahub-testcontainers` pytest plugin (R8). |
| VI. Security, secrets, input boundaries | Pass | Prefect database password held by `prefect-server` and `db-bootstrap` only in the root file; the CLI holds no Prefect credential; the Prefect port is closed by default. |
| VII. Simplicity, new dependencies justified | Pass with note | Two new dependencies, justified by D-15 and D-16, and kept in an optional extra plus `dev`. Fewer files and tasks overall. |
| Quality gates and documentation | Pass | The constitution's "Code quality gates" block names `uv run invoke format` and `uv run invoke lint` (`constitution.md:150-151`). Both names stay (D-7 as revised on 2026-10-22), so the constitution needs no amendment and stays at 1.0.3. |

Gate result before Phase 0: pass. The two new dependencies are listed in
[Complexity Tracking](#complexity-tracking).

Re-check after Phase 1: pass. The design adds one Python module for the Prefect server, one
package for the fixture, and one helper for development settings, each with at least two
callers (stacks or tasks), and introduces no new published port or credential.

## Project Structure

### Documentation (this feature)

```text
dev/specs/010-prefect-from-sync-image/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1
│   ├── compose-services.md
│   ├── invoke-tasks.md
│   └── testcontainers-api.md
├── checklists/requirements.md
└── tasks.md             # Phase 2, created by /speckit-tasks
```

### Source Code (repository root)

```text
docker-compose.yml                        # prefect-server from the Sync image, UI off, Silo
Dockerfile                                # build-inputs label (R5)
pyproject.toml                            # testcontainers extra; package data
infrahub_sync/
├── service/
│   └── prefect_server.py                 # new: create_prefect_app() factory (R1)
└── testcontainers/                       # new: TestInfrahubSyncDocker (R8)
    ├── __init__.py
    ├── helpers.py
    └── docker-compose.yml                # byte-equal copy of the root file
development/
├── docker-compose.yml                    # new name; merges dev and preview (R7)
├── docker-compose.infrahub.yml           # services gain profiles: ["infrahub"]
├── docker-compose.dev.yml                # removed
├── docker-compose.preview.yml            # removed
└── preview.env                           # removed
tasks/
├── __init__.py                           # top-level format and lint only; dev namespace added
├── dev.py                                # build/deps/start/restart/stop/destroy/status/logs/seed/smoke/api/worker
├── tests.py                              # tests.all, tests.check-310, plugin disabled in unit tier
├── netbox.py                             # start/destroy
├── bench.py                              # reads dev_settings() (R6)
└── preview.py                            # removed
.github/
├── workflows/workflow-nightly-e2e.yml    # new task names; compose suite uses the fixture
├── workflows/workflow-linter.yml         # docs.docusaurus
└── scripts/nightly_e2e.py                # new task names
docs/docs/
├── compose-deployment.mdx                # override snippet, first-start times, Silo licence
├── development-stack.mdx                 # both modes
├── development/guides/preview-stack.md   # removed or folded into development-stack.mdx
└── development/guidelines/testing-tiers.md
dev/adr/
├── 0016-the-compose-deployment-is-one-tag-pinned-file.md   # note: override snippet, package data
└── 0019-prefect-is-an-internal-component-of-sync.md        # amendment (D-3)
AGENTS.md                                 # new task names
changelog/<id>.changed.md                 # release note incl. D-14 down -v
tests/
├── compose/                              # contract tests (FR-001..FR-007, FR-011, FR-013)
├── dev_stack/                            # profiles, mounts, task inventory, staleness
├── testcontainers/                       # byte-equality, image selection
└── test_release_pin.py                   # prefect / prefect-client version match (FR-024)
```

**Structure Decision**: one Python package with Invoke tasks and Compose files, as today. New
code goes next to the existing service modules (`infrahub_sync/service/serve.py`,
`worker.py`); the fixture gets its own subpackage so the optional extra does not leak imports
into the base install.

## Phase 0 and Phase 1 outputs

- [research.md](research.md): R1–R10, including the four planning checks.
- [data-model.md](data-model.md): Compose services, profiles, settings and the image label.
- [contracts/compose-services.md](contracts/compose-services.md): what `docker compose config`
  must show for each stack.
- [contracts/invoke-tasks.md](contracts/invoke-tasks.md): the task inventory and behaviour.
- [contracts/testcontainers-api.md](contracts/testcontainers-api.md): the public Python
  surface of `infrahub_sync.testcontainers`.
- [quickstart.md](quickstart.md): how to check each user story by hand.

## Order of work

1. Prefect server module and root file (`prefect-server` from the Sync image, UI off, override
   snippet), with contract tests. Delivers User Story 1 without the object store change.
2. Silo in every Compose file, health checks and the repair service. Completes User Story 1.
3. Development stack merge, profiles, checkout mounts, `dev.*` tasks, stale-image label,
   `dev_settings()`, `bench.py`. Delivers User Stories 2 and 3.
4. Task renames (top-level `format` and `lint` stay), `AGENTS.md`, documentation, workflows.
5. `infrahub_sync.testcontainers`, the extra, the version-match test, the CI end-to-end tier.
   Delivers User Story 5.
6. ADR 0019 amendment, ADR 0016 note, release note with the `down -v` instruction, measured
   first-start times. Delivers User Story 4's documentation and FR-008 to FR-010.

## Still open after planning

- First-start times for FR-008 are measured during implementation; they are not known yet.
- The Silo images were run on arm64 only; amd64 was pulled and inspected but not run.
- Whether the benchmark runner works today is not known: it points at a `compose.yaml` that
  does not exist (R6). The plan points it at the new development file either way.
- Record that D-5 reverses the 2026-10-15 product decision, and update Jira card INFP-692.
  This is a product task outside the repository.

## Complexity Tracking

| Item | Why needed | Simpler alternative rejected because |
|------|------------|--------------------------------------|
| Two new dependencies (`infrahub-testcontainers`, `testcontainers`) | D-15 and D-16 require the fixture to inherit `TestInfrahubDocker`. | Writing an independent fixture would duplicate Infrahub's start-up logic and drift from it. |
