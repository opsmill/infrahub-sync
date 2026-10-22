# Contract: invoke tasks

The complete list of task names after this feature, and what each development task does. A
task inventory test (`uv run invoke --list` parsed, or the `Collection` walked) checks that every
name below exists, that no top-level task exists, and that no name appears twice (FR-016,
FR-017, SC-005).

## Removed names and their replacements

| Removed | Replacement |
|---------|-------------|
| `format` | `linter.format` (now runs `docs.format` first) |
| `lint` | `linter.lint` (now runs `docs.lint` first) |
| `docusaurus` | `docs.docusaurus` |
| `generate-doc` | `docs.generate` |
| `tests-unit` | `tests.tests-unit` |
| `tests-integration` | `tests.tests-integration` |
| `tests-all` | `tests.all` |
| `check-310` | `tests.check-310` |
| `build`, `start`, `destroy` (top-level today, from `tasks/dev.py`, added in `tasks/__init__.py`) | `dev.build`, `dev.start`, `dev.destroy` |
| `preview.up`, `preview.seed`, `preview.smoke`, `preview.status`, `preview.logs`, `preview.down` | `dev.deps` or `dev.start`, `dev.seed`, `dev.smoke`, `dev.status`, `dev.logs`, `dev.stop` / `dev.destroy` |
| `netbox.up`, `netbox.down` | `netbox.start`, `netbox.destroy` |

Names not listed here (`linter.lint-*`, `linter.format-ruff`, `docs.rumdl`,
`docs.format-rumdl`, `tests.tests-product-store-postgresql`, `release.*`, `bench.*`, other
`netbox.*`) keep their current names.

## `dev` namespace

| Task | Behaviour | Requirement |
|------|-----------|-------------|
| `dev.build [--no-cache]` | Builds `infrahub-sync:dev` with the build-inputs label | FR-018 |
| `dev.start [--infrahub-network NAME]` | Rebuilds when the label differs; starts core, `sync` and `infrahub` (or attaches to `NAME`); waits for health | FR-018, FR-020 |
| `dev.deps [--infrahub-network NAME]` | Rebuilds when the label differs; starts core and `infrahub` only; prints the URLs; returns | FR-018, FR-021 |
| `dev.api` | Runs the Sync API on the host in the foreground against the `dev.deps` containers | FR-021 |
| `dev.worker` | Runs the Sync worker on the host in the foreground against the `dev.deps` containers | FR-021 |
| `dev.restart` | Recreates the Sync containers so they run the mounted code | FR-019 |
| `dev.stop` | Stops the containers and keeps the volumes | — |
| `dev.destroy` | Removes the containers and volumes; keeps the image | — |
| `dev.status` | Shows the containers, their health and the URLs | — |
| `dev.logs [--name SERVICE] [--lines N]` | Shows a service's logs | — |
| `dev.seed` | Loads the smoke dataset into Infrahub | — |
| `dev.smoke` | Seeds, then runs the smoke suite; in `dev.deps` mode, fails and names `dev.api` or `dev.worker` when one does not answer | FR-022 |

`INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` has the same effect as `--infrahub-network`. No task writes
a PID file or starts a background process (FR-021).

## Callers that must move to the new names (FR-026)

- `AGENTS.md`: required workflow, policy, Invoke tasks list.
- `docs/docs/development/constitution.md` (PATCH amendment),
  `docs/docs/development/knowledge/quality-gates.md`,
  `docs/docs/development/guides/adding-an-adapter.md`,
  `docs/docs/development/guidelines/testing-tiers.md`, `docs/docs/development-stack.mdx`.
- `.github/workflows/workflow-linter.yml` (`invoke docusaurus`),
  `.github/workflows/workflow-nightly-e2e.yml` and `.github/scripts/nightly_e2e.py`.
- `tasks/bench.py` (through `dev_settings()`).

A search test over `.github/`, `docs/` and `AGENTS.md` finds none of the removed names.
