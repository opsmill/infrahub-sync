# Implementation Plan: A NetBox demo of Sync 3.x, ready to try locally and from a Kubernetes deployment

**Branch**: `024-demo-ready-netbox` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/024-demo-ready-netbox/spec.md`

## Summary

One new CLI command, `infrahub-sync demo prepare`, loads the pinned destination schema into an
Infrahub and registers the NetBox package with the NetBox and Infrahub addresses given to it. The
schema copy and the package ship inside the wheel, so the command runs from the published image
with no checkout. A new development-stack preset, `demo`, starts a local Infrahub, the local NetBox
with the `demo` dataset and the Sync services, then runs that same command inside the `sync-api`
container. `dev.demo` is its shortcut and prints the next steps. Pre-release images need no code:
the image workflow and its guide already support them (research R-6). A tutorial page for
colleagues ties it together.

## Technical Context

**Language/Version**: Python 3.11 to 3.13 for the service and the development stack. The CLI module
keeps Python 3.10 compatibility, like the rest of `infrahub_sync/cli.py`.

**Primary Dependencies**: Typer (CLI), `infrahub-sdk` (schema load, already a base dependency),
the Sync HTTP client in `infrahub_sync/client/`, Invoke (development tasks), hatchling (wheel build).

**Storage**: none new. The command writes through the Infrahub API and the Sync API.

**Testing**: pytest. Unit tests for the command with a fake Sync client and a fake Infrahub schema
API; a unit test of the source-tree asset path; a build test that the wheel holds the assets; an image test (`tests/image/`) that the image
holds them; Invoke task unit tests in the style of the existing `tests/tasks/` (or the location the
current task tests use). The end-to-end run is a manual quickstart scenario, because it needs
Docker, Infrahub and NetBox.

**Target Platform**: the Sync image (linux/amd64, linux/arm64) and a developer laptop with Docker.

**Project Type**: CLI and service, single repository.

**Performance Goals**: the local setup reaches the first plan in under 15 minutes including image
pulls (SC-001). The command itself is bounded by the schema convergence wait (120 seconds, as in
the documentation).

**Constraints**: no network fetch for the schema or the package (FR-007); no token in output, logs or
the registered package (FR-010); approval untouched (FR-011); no edit outside this repository.

**Scale/Scope**: one source (NetBox), one schema collection (16 files), one package.

## Constitution Check

Checked against `.specify/memory/constitution.md` (corrected at the start of implementation; the
first draft of this plan missed the file):

- I. Read-only by default: `demo prepare` writes a schema and a configuration, and only as an
  explicit command a person or a deployment step runs. It never plans, syncs or applies, and no
  other command calls it implicitly.
- II. Idempotency: re-running changes nothing (FR-008); a stopped run is completed by running again.
- III. Adapter symmetry: no adapter change.
- IV. Types: new code typed; `ty` clean, no overrides.
- V. Tests: atomic unit tests, plus build and image tests.
- VI. Secrets: tokens from the environment only, never in output (FR-010).
- VII. Simplicity: no new dependency; one module, one preset, one task.

The `AGENTS.md` rules also apply:

- Plan, ask, act, verify, record: the command is read-and-register only; it never plans or applies.
- Idempotency: FR-004 and FR-008 require it, and the Sync API already returns an identical version
  instead of a duplicate (R-3).
- Secrets: tokens come from the environment and never reach output (R-5, FR-010).
- Match existing patterns: the command uses the existing Sync client and the CLI's global
  `--api-url`/token handling; the preset extends the existing `Preset` table.
- Specific exceptions, structured logs: the command raises its own error type and prints through
  the CLI's existing echo helpers.

Post-design re-check: no violation. No ADR is required for a new CLI command group, but one ADR is
added for the decision to ship demo assets inside the wheel (R-1), because it changes what the
published package contains.

## Project Structure

### Documentation (this feature)

```text
dev/specs/024-demo-ready-netbox/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── demo-prepare-cli.md
│   └── dev-demo-task.md
├── checklists/requirements.md
└── tasks.md            # created by speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── cli.py                         # registers the `demo` Typer group
└── demo/
    ├── __init__.py
    ├── assets.py                  # finds the schema copy and the package, in the wheel or the source tree
    ├── package.py                 # URL replacement, moved from tasks/netbox.py:local_package_text
    └── prepare.py                 # schema load, then register or version the configuration

pyproject.toml                     # hatch force-include of the two asset sources into the wheel
Dockerfile                         # COPY of the two asset sources before the wheel build

tasks/
├── dev.py                         # `demo` preset and `dev.demo` task
└── netbox.py                      # `demo-package` reuses infrahub_sync.demo.package

tests/
├── demo/                          # unit tests of assets, package, prepare
├── image/test_demo_assets.py      # the built image holds the assets
├── dev_stack/                     # preset and dev.demo unit tests, next to the existing preset tests
├── test_netbox_tasks.py           # demo-package still writes the same copy after the move
└── test_task_inventory.py, test_task_names_in_docs.py  # updated for the new task

docs/docs/
├── tutorials/try-the-netbox-demo.mdx   # new page for colleagues
├── development-stack.mdx               # "Run a first sync" points to dev.demo
├── tutorials/netbox-demo-to-infrahub.mdx  # points to the new page
└── reference/ (generated CLI docs)     # regenerated by docs.generate
examples/netbox_to_infrahub/README.md   # points to the new page
dev/adr/00NN-demo-assets-ship-in-the-wheel.md
changelog/+demo-prepare.added.md
```

**Structure Decision**: a new `infrahub_sync/demo/` package keeps the command out of the 2,200-line
`cli.py`; `cli.py` only registers the group. The asset sources stay where they are
(`examples/netbox_to_infrahub/package.yml`, `tests/data/nightly_schema/traditional-infrastructure-sot/`),
and the wheel build copies them, so there is one copy of each in the repository.

## Design

### `infrahub-sync demo prepare` (FR-006 to FR-011)

Contract: [contracts/demo-prepare-cli.md](contracts/demo-prepare-cli.md).

1. Resolve the assets (R-1): the schema directory and the package text.
2. Build the package text for the given `--netbox-url` and `--infrahub-url` with the URL replacement
   moved from `tasks/netbox.py` (R-2). The replacement refuses a package whose shape changed.
3. Load the schema into the Infrahub at `--infrahub-address` (defaults to `--infrahub-url`; they
   differ when the command runs somewhere other than the worker) with `InfrahubClientSync`, the
   token from `INFRAHUB_API_TOKEN`, waiting for convergence up to `--schema-timeout` seconds
   (default 120) (R-4). Report `loaded` or `unchanged`.
   On failure, stop with exit code 1 and the cause; register nothing (FR-009).
4. Register or version the configuration through the Sync client (R-3): look up configurations by
   the package name; none → `register_config`; one or more → `create_config_version` on the newest.
   A 200 answer means unchanged; a 201 answer means a new version. Use a deterministic idempotency
   key per operation and target (critique E2): `demo-prepare-register-<sha12>` for a registration,
   `demo-prepare-version-<config_id>-<sha12>` for a version, where `<sha12>` is the first 12 hex
   characters of the package text's SHA-256. A retried call replays; a key is never reused for a
   different route or body.
5. Print a summary: schema state, configuration ID, version, whether anything changed. Never print
   a token. With `--json`, print the same as one JSON object for scripts and a later Styrmin action.

### The `demo` preset and `dev.demo` (FR-001 to FR-005)

Contract: [contracts/dev-demo-task.md](contracts/dev-demo-task.md).

- Add `Preset("demo", ..., local_infrahub=True, netbox=True)` with no smoke seed. The `preview`
  preset seeds the smoke schema into the same Infrahub; the demo Infrahub holds only the destination
  schema (R-7).
- `dev.demo` = `dev.start --preset demo`, then ensure NetBox holds the `demo` dataset (seed it only
  when NetBox is new, as `preview` does; otherwise print the dataset found when it can tell, and
  always the `netbox.seed --dataset demo` command that reloads it), then run
  `infrahub-sync demo prepare` **on the host** (`uv run`, source tree) with the worker addresses
  written into the package (`http://netbox:8080`, `http://infrahub-server:8000`),
  `--infrahub-address` set to the stack's published Infrahub address, and the Sync API's loopback
  address. The Infrahub admin token and the Sync development token are passed in the process
  environment, never as arguments.
- Why the host and not the `sync-api` container (critique E1): the development stack bind-mounts
  `../infrahub_sync` over the installed package, which hides the assets the wheel bundles, and it
  does not mount `examples/` or `tests/data/`. The image path is proven instead by an image test
  (the built image holds the assets) and by quickstart scenario 3 (`docker run` of the built image,
  no source mount). Both paths run the same command (FR-005).
- The worker's NetBox and Infrahub credentials already default to the local tokens through
  `_stack_env` (FR-002); the `demo` preset sets `netbox_credential` like `preview`.
- Print the web interface address (the Sync API address, which serves the interface at its root),
  the token to enter, the configuration ID and the CLI commands for a plan on a new branch (FR-003).

### Pre-release images (FR-012, FR-013)

No code (R-6). The tutorial page links to the "Publishing an image" guide, which already documents
a dispatch from any ref under a pre-release tag and states that a dispatch never moves `latest`.

### Documentation (FR-014, FR-015)

- New `docs/docs/tutorials/try-the-netbox-demo.mdx`: who it is for, `dev.demo`, the first plan and
  apply from the web interface and the CLI, the note that approval is off, `demo prepare` for a
  Kubernetes deployment, that the Styrmin drivers are not available yet, and where to send feedback
  (R-8).
- "Run a first sync", the NetBox tutorial and the example README each get one line pointing to it.
- Regenerate the CLI reference with `uv run invoke docs.generate`.

## Complexity Tracking

No gate violation to justify.
