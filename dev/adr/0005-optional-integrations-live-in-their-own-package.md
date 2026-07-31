# 5. Optional integrations live in their own package and are proven absent in CI

**Status**: Accepted
**Date**: 2026-07-31
**Source**: `dev/specs/archive/001-prefect-managed-remote-run/research.md`
(non-probe research decisions), `contracts/prefect-flow.md` §6

## Context

The Prefect integration had to be genuinely optional: installing `infrahub-sync` without the
extra must leave the package importable and every CLI command working, with no `prefect`
module loaded anywhere. "Optional" claimed in a docstring is not the same as optional in
fact, and the failure mode is silent — a stray top-level import in a shared module makes the
base install depend on the extra without anyone noticing until a user reports it.

## Decision

The integration is a package of its own, `infrahub_sync/orchestration/`, and it is the only
place in the repository that imports `prefect`. Nothing in the base package imports it.
The shared execution surface it calls (`infrahub_sync/execution.py`) imports no Prefect
symbol and stays importable in a base install.

It is entered with `python -m infrahub_sync.orchestration.serve` rather than a console
script, so a base install does not advertise an entry point that crashes when invoked. The
entrypoint guards its own import: an `ImportError` produces one error line naming the extra
and the install command, then a non-zero exit — never a bare traceback.

The claim is enforced by three mechanisms rather than asserted:

- **A CI leg that proves the absence.** The test workflow runs twice: once with
  `--extra dev --extra prefect`, and once with `--extra dev` alone. The base leg first
  asserts `python -c "import prefect"` *fails*, and fails the job if it succeeds, before
  running the suite. Without that step the leg would silently become a second full-extra run
  the day the lockfile changes.
- **A subprocess-isolated import probe.** The import-absence test writes a small script that
  imports the package, exercises the CLI in-process, and asserts over *its own*
  `sys.modules`, then runs it in a fresh interpreter and checks the exit code and output. An
  in-process assertion cannot work: pytest collection imports the flow test module, whose
  module-level `importorskip` loads Prefect, before the probe runs — and under distributed
  test runs that pollution is per-worker arbitrary.
- **A static import-graph check**, which is collection-safe and therefore stays in-process.

## Consequences

Both installation shapes are exercised on every run, so the optional boundary cannot rot
quietly, and the same three mechanisms are reusable for the next optional integration.

The costs are explicit: CI runs an extra job, the flow suite is skipped in the base leg by
design, and any future module that wants to import the integration has to be added to the
static check's expectations deliberately. One asymmetry is worth knowing about — installing
the extra pulls transitive packages that resolve imports the base install cannot resolve, so
a `# ty: ignore[unresolved-import]` that is necessary without the extra can report as
unused with it. The type gate therefore runs in the full environment.

## Alternatives Considered

**A console-script entry point** — present in every base install and crashing without the
extra, which muddies exactly the story the base-install CI leg exists to tell.

**Naming the package `infrahub_sync/prefect/`** — shadowing confusion with the third-party
module, for no gain.

**Asserting the import boundary in-process only** — unsound in the full-extra environment,
where collection has already imported Prefect before the assertion runs.
