# Implementation Plan: Prefect-Managed Remote Infrahub Sync Run (Developer Preview)

**Branch**: `001-prefect-managed-remote-run-local-dp-001` | **Date**: 2026-07-30 | **Spec**: `dev/specs/001-prefect-managed-remote-run/spec.md`

**Input**: Feature specification from `dev/specs/001-prefect-managed-remote-run/spec.md`
(authoritative, carrying DBR-001–DBR-015 / DBA-001–DBA-011 verbatim from brief DB-001 v2,
four PROVISIONAL clarifications, plus D005's version remediation).

## Summary

Introduce one narrow, typed Python execution surface (`infrahub_sync/execution.py`) used
by three callers — the CLI `diff` lifecycle, the serial branch of CLI `sync
--no-parallel`, and a new package-owned Prefect flow — so a developer can run and observe
a real Infrahub Sync `plan` (== today's `diff` lifecycle) or explicitly confirmed `sync`
remotely through a default self-hosted Prefect Server's REST API and UI. Prefect stays an
optional extra pinned to **3.5.0** (D005; the brief's 3.7.2 is unsatisfiable next to
`diffsync[redis]` — see `research.md` F1) with two companion pins repairing prefect
3.5.0's own packaging defects (D006). The base install, import, and ordinary CLI never
import Prefect. All four load-bearing Prefect behaviors (co-install, served-deployment
REST runs, run-scoped log bridging, parameter rejection before the flow body) were
verified by real probes against 3.5.0 (`research.md` probe table).

## Technical Context

**Language/Version**: Python 3.10–3.13 (`requires-python >=3.10,<3.14`); repo currently
developed on 3.12.

**Primary Dependencies**: Base (unchanged): `infrahub-sdk[all]`, `diffsync[redis]`
(redis <5.0), `pyarrow`, `filelock`, `typer` (via sdk), `structlog`, `tqdm`. New
optional extra `prefect`: `prefect==3.5.0`, `importlib-metadata>=4.4`,
`fastapi>=0.111,<0.121` (D005 + D006, probe-verified).

**Storage**: Existing per-sync cache layout under `.infrahub-sync-cache/<sync_name>/<run_id>/`
(`run.json`, `plan.parquet`, side snapshots) — reused as-is; `artifact_path` in
`RunResult` points at the run directory. Prefect server uses its default local SQLite
under `PREFECT_HOME`.

**Testing**: pytest (`uv sync --extra dev`; note R-1 — plain `uv sync` does not install
dev tooling). Prefect-dependent tests are skipped when `prefect` is not importable and/or
marked `integration` when they need a live server; baseline 110 passed / 3 skipped at
`9edc1bc` must not regress (R-4).

**Target Platform**: Local developer machines (macOS/Linux) running the CLI; a trusted
development host running the Prefect server + served deployment (never public internet —
README caveat mandated by spec Constraints).

**Project Type**: Single Python package with a Typer CLI + optional orchestration
integration; examples under `examples/`.

**Performance Goals**: None new — preview scope. The qualified demonstration is a
5-record fixture; remote runs pin today's CLI defaults (full extract, concurrent side
load) with progress display disabled.

**Constraints**: Base install/import/CLI must not import Prefect (DBA-001, SC-006);
flow calls the surface in-process, never a subprocess (DBR-008); pipeline lock owned by
the surface with the existing 60 s acquisition timeout → `RunExecutionError` on
contention; `INFRAHUB_SYNC_CONFIG_DIRECTORY` read at serve start; engine options pinned
to CLI defaults at `9edc1bc`; parallel sync branch untouched; flow module must not use
`from __future__ import annotations` (research F3).

**Scale/Scope**: ~5 new/changed source modules, 1 example directory, 1 docs page,
pyproject extra, tests. No schema/DB migrations. Out-of-scope list (B-001–B-007) is
hard; none of it appears in this design.

## Constitution Check

*GATE: evaluated against Constitution v1.0.0 before Phase 0 research; re-evaluated after
Phase 1 design (both evaluations below reflect the final design).*

| # | Principle | Verdict | Evidence in this design |
|---|---|---|---|
| I | Read-Only / Dry-Run by Default | **PASS** | Remote `operation` defaults to `plan` (non-mutating, == `diff` lifecycle). `sync` requires the explicit `confirm_writes=true` gate enforced inside the surface **before adapter construction** (`RunValidationError` otherwise); the CLI's own mutating command supplies confirmation by explicit human invocation, exactly as today. No new implicit mutation. |
| II | Sync Idempotency & Safety | **PASS** | The surface reuses the existing `Potenda` load → guardrail → diff → write_plan → sync pipeline unchanged; the qualified demonstration's third leg (post-sync plan reports no changes) is an explicit acceptance scenario (US2/SC-003). Failures write `run.json` `status=failed` exactly as today; lock contention is a bounded `RunExecutionError`, not a hang. |
| III | Adapter Symmetry & Pattern Consistency | **PASS (N/A-new-adapter)** | No new adapter. All operations flow through `potenda`; the flow calls the same surface as the CLI. The example consumes the existing `examples/custom_adapter` fixture. |
| IV | Type Safety & Explicit Contracts | **PASS** | `execution.py` is fully typed (`Literal` operation/status, frozen `RunResult`, typed factory protocol); contracts in `contracts/` are concrete typed definitions; no `ty` overrides; specific exception classes (`RunValidationError`, `RunExecutionError`) — no broad `except Exception:` in new code (the surface catches the engine's *declared* `ValueError`/`filelock.Timeout`/`ImportError` boundaries and re-raises typed). One caveat: the flow module deliberately omits `from __future__ import annotations` (research F3) with a comment. |
| V | Test Discipline | **PASS** | Planned: parametrized negative tests for `sync_name` resolution and `confirm_writes` (SC-004 negative-test set), RunResult schema/invariant tests, fingerprint unit tests, base-install-without-prefect test (SC-006), canary-redaction test (DBA-008), flow tests skip-if-no-prefect, live end-to-end marked `integration`. Existing targeted tests (DBA-009 population) must pass unmodified. |
| VI | Security, Secrets & Input Boundaries | **PASS** | Credentials only from runner env (DBR-006; the infrahub adapter already reads `INFRAHUB_ADDRESS`/`INFRAHUB_API_TOKEN`); `sync_name` is an opaque name matched by exact equality against discovered `config.yml` `name` fields — never used to build a path (reuses `utils.get_instance` semantics); remote parameters carry no paths/CLI fragments/credentials/env overrides; exception messages pass value-based secret redaction; example credentials are obviously fake placeholders. |
| VII | Simplicity & Maintainability | **PASS** | The new abstraction has three real callers (CLI diff, CLI serial sync, flow) — satisfies the two-caller rule. New dependency is brief-mandated and optional; companion pins are defect repairs, each justified by a recorded probe (D006). No engine rewrite; parallel branch untouched; generated example files regenerated via `generate` (R-2), never hand-edited. |

**Gate result (pre-Phase-0 and post-Phase-1): PASS — no violations; Complexity Tracking
is empty.** One deviation from the *brief* (not the constitution) is recorded: D005
replaces the unsatisfiable 3.7.2 pin (BLOCKING checkpoint decision, root-issued).

## Project Structure

### Documentation (this feature)

```text
dev/specs/001-prefect-managed-remote-run/
├── plan.md              # This file
├── research.md          # Phase 0: probe table, F1–F3, D005/D006 records
├── data-model.md        # Phase 1: entities, invariants, state transitions
├── quickstart.md        # Phase 1: runnable validation scenarios
├── contracts/
│   ├── execution-surface.md      # Typed Python contract of the shared surface
│   ├── prefect-flow.md           # Flow parameters, deployment, REST interaction contract
│   └── run-result-and-errors.md  # RunResult + failure contract + fingerprint definition
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created by this phase)
```

### Source Code (repository root)

```text
infrahub_sync/
├── cli.py                      # MODIFIED: diff + serial sync branch delegate to execution.execute_run();
│                               #   parallel branch, apply, generate, list untouched
├── execution.py                # NEW: shared typed execution surface (no Prefect import)
│                               #   RunResult, RunValidationError, RunExecutionError,
│                               #   resolve_sync_instance(), execute_run(), run_remote_request()
├── orchestration/              # NEW package: the ONLY place that imports prefect
│   ├── __init__.py             #   (empty; no prefect import at package level)
│   ├── flow.py                 #   @flow infrahub-sync run flow (no future-annotations import)
│   └── serve.py                #   python -m infrahub_sync.orchestration.serve entrypoint;
│                               #   validates INFRAHUB_SYNC_CONFIG_DIRECTORY at startup
├── cache/
│   └── fingerprint.py          # NEW: compute_plan_fingerprint(run_dir) — canonical SHA-256
└── utils.py                    # UNCHANGED (get_instance / get_potenda_from_instance reused)

pyproject.toml                  # MODIFIED: [project.optional-dependencies] prefect = [...] (D005+D006)

AGENTS.md                       # MODIFIED (R-1, first commit): uv sync → uv sync --extra dev

examples/
├── netbox_to_infrahub/         # REGENERATED (R-2, second commit): baseline hygiene
└── prefect_remote_run/         # NEW: example README + InfraDevice schema + REST request examples
    ├── README.md               #   install/serve/invoke/inspect walkthrough + trusted-env caveat
    ├── schemas/infra_device.yml#   loadable InfraDevice(name, type) schema (R-3 enabling work)
    └── requests/               #   documented REST bodies (create run, get state, get logs)

docs/docs/                      # MODIFIED: one reference page for the optional Prefect preview

tests/
├── test_execution_surface.py   # NEW: validation refusals (parametrized), RunResult schema,
│                               #   invariants, redaction, lock contention
├── test_execution_cli_parity.py# NEW: CLI diff vs execute_run parity incl. fingerprint (fixture-level)
├── test_plan_fingerprint.py    # NEW: canonical fingerprint unit tests
├── test_no_prefect_import.py   # NEW: SC-006 — base import/CLI leaves no prefect module loaded
├── orchestration/
│   └── test_flow.py            # NEW: flow tests (skipped when prefect absent); canary scan
└── integration/
    └── test_remote_run_live.py # NEW: opt-in (-m integration) served-deployment E2E
```

**Structure Decision**: Single-package layout retained. The seam is one new module
(`execution.py`) plus one isolated orchestration package; Prefect imports are confined
to `infrahub_sync/orchestration/` so the DBA-001 boundary is auditable by a single
import-graph rule. Examples remain pure consumption material (constitution III/VII;
spec constraint "the integration is packaged capability").

## Design Outline (what Phase 2 tasks will implement)

Full typed contracts live in `contracts/`; entity semantics in `data-model.md`. Key
structural decisions, each traceable to a requirement:

1. **Execution surface** (`contracts/execution-surface.md`, DBR-007/008/015):
   - `resolve_sync_instance(sync_name, *, directory)` — exact-name lookup via
     `utils.get_instance(name=..., directory=...)` (identical semantics to CLI
     `--name`/`--directory`); no match → `RunValidationError`. The requested value is
     never used to build a path, so traversal-shaped values fail the same way unknown
     names do (SC-004).
   - `execute_run(...)` — owns validation order (operation → confirm_writes →
     [caller-resolved instance]) **before** the pipeline lock and adapter construction;
     then reproduces today's CLI lifecycles exactly: `diff` lifecycle for `plan`
     (RunFile mode `diff`, status `running→dry-run`), serial sync lifecycle for `sync`
     (mode `sync`, status `running→applied`, guardrail, optional diff print, baseline
     persistence). Engine options are keyword parameters defaulting to the `9edc1bc`
     CLI defaults; the CLI passes its flags through; `run_remote_request()` never
     overrides them except `show_progress=False`.
   - `potenda_factory` injection: the CLI passes its module-global
     `get_potenda_from_instance` so the existing test patches on
     `infrahub_sync.cli.get_potenda_from_instance` keep working unmodified (DBA-009).
   - Pipeline lock: `cache.locks.pipeline_lock(sync_instance.name)` acquired inside
     `execute_run`; `filelock.Timeout` → `RunExecutionError` naming the sync and the
     60 s timeout (spec edge case 5).
2. **Prefect flow** (`contracts/prefect-flow.md`, DBR-001/002/003/011/012):
   parameters exactly `sync_name: str`, `operation: Literal["plan","sync"] = "plan"`,
   `confirm_writes: bool = False`, `branch: str | None = None`. Body: bridge
   `infrahub_sync` logger → run logger (attach/finally-remove), read
   `INFRAHUB_SYNC_CONFIG_DIRECTORY`, `resolve_sync_instance`, `run_remote_request`,
   log the result summary line, return `asdict(RunResult)`. Serve entrypoint validates
   the env var at startup and exits with an error naming it otherwise (spec
   clarification #2).
3. **RunResult + failure contract** (`contracts/run-result-and-errors.md`, DBR-015):
   frozen dataclass, exactly seven fields, cross-field invariants enforced in
   `__post_init__`; `RunValidationError`/`RunExecutionError` with value-based secret
   redaction applied at raise time; canonical plan fingerprint helper
   (`cache/fingerprint.py`) per spec clarification #1.
4. **Commit order** (binding on the tasks phase): commit 1 = R-1 (AGENTS.md
   `uv sync --extra dev`); commit 2 = R-2 (regenerate `examples/netbox_to_infrahub`);
   feature work only after both.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified.

*(empty — no constitution violations)*
