# Extraction Record

**Extracted on**: 2026-07-31
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0001-translate-run-failures-only-at-the-remote-boundary.md` (from `critiques/collation-r1.md` D009, `contracts/execution-surface.md`)
- `dev/adr/0002-resolve-remote-configurations-file-by-file.md` (from `critiques/collation-r1.md` D010, `contracts/execution-surface.md`)
- `dev/adr/0003-canonical-plan-fingerprint-as-equivalence-oracle.md` (from `spec.md` D001, `contracts/run-result-and-errors.md`)
- `dev/adr/0004-declare-redis-directly-instead-of-the-diffsync-extra.md` (from `research.md` D005 / F1 / F2 / "Gate-ratified resolution")
- `dev/adr/0005-optional-integrations-live-in-their-own-package.md` (from `research.md` non-probe decisions, `contracts/prefect-flow.md` §6)

## Knowledge Created

- `dev/knowledge/execution-surface.md` (new) — the typed execution surface: callers,
  `RunResult`, failure model, lock seam, fingerprint helper.
- `dev/knowledge/orchestration-prefect.md` (new) — the packaged flow, serve entrypoint, log
  bridge, remote API surface, and Prefect-specific traps.
- `dev/knowledge/quality-gates.md` (new) — `invoke lint` / `invoke format` composition and
  short-circuit behaviour, the inherited pylint baseline and its environment sensitivity,
  measurement rules, and the `generate` churn caveat.

## Knowledge Updated

- `dev/knowledge/README.md` (index — two new sections)

## Guidelines Created

- `dev/guidelines/secret-redaction.md` (new) — where to sanitize, what to collect, why
  over-collection fails, the length floor, and whole-cause-chain coverage.
- `dev/guidelines/testing.md` (new) — mutation kill as acceptance criterion, asserting a
  negative in a subprocess, external-state isolation, private test seams, remediation review.

## Guidelines Updated

- `dev/guidelines/README.md` (index — new "Repository-wide" section)

## Other Updated

- `dev/adr/README.md` (index — new "Current" section; the directory previously held only the
  naming convention)
- `AGENTS.md` — corrected the documented `invoke lint` order and short-circuit behaviour,
  replaced "some warnings are expected" with a pointer to the recorded baseline, and added a
  "Beyond Adapters" index for the non-adapter `dev/` pages.

## Not Extracted

- `plan.md`, `tasks.md`, `quickstart.md`, `run-report.md`, `opsmill-implement-report.md`,
  `sessions/`, `checklists/`, `critiques/` (except the D009/D010 records) — execution
  artifacts.
- The prefect 3.5.0 packaging saga (D006's `importlib-metadata` and `fastapi` companion pins)
  — superseded upstream at 3.8.1; kept only as the rejected alternative in ADR 4.
- Retrospective findings R5, R6, R7, R11, R12, R13 (process half), R15 — properties of the
  delivery harness, dispositioned to issues in the planning repository, not repository
  documentation. R13's engineering lesson is extracted into the redaction and testing
  guidelines; its process disposition is not.
- Run-internal identifiers (requirement, task, finding and mutation IDs) — stripped from all
  extracted text.

## Archive

Spec directory moved to `dev/specs/archive/001-prefect-managed-remote-run/` as a historical
record.
