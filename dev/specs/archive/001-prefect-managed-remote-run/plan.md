# Implementation Plan: Prefect-Managed Remote Infrahub Sync Run (Developer Preview)

**Branch**: `001-prefect-managed-remote-run-local-dp-001` | **Date**: 2026-07-30 | **Spec**: `dev/specs/archive/001-prefect-managed-remote-run/spec.md`

**Input**: Feature specification from `dev/specs/archive/001-prefect-managed-remote-run/spec.md`
(authoritative, carrying DBR-001–DBR-015 / DBA-001–DBA-011 verbatim from brief DB-001 v2,
four clarifications, plus D005's dependency remediation — all RATIFIED at the Phase 4
checkpoint gate by Blake Ellis on 2026-07-30).

## Summary

Introduce one narrow, typed Python execution surface (`infrahub_sync/execution.py`) used
by three callers — the CLI `diff` lifecycle, the serial branch of CLI `sync
--no-parallel`, and a new package-owned Prefect flow — so a developer can run and observe
a real Infrahub Sync `plan` (== today's `diff` lifecycle) or explicitly confirmed `sync`
remotely through a default self-hosted Prefect Server's REST API and UI. Prefect stays an
optional extra pinned to **3.8.1** — the extra is exactly `prefect = ["prefect==3.8.1"]`
(D005 option D, ratified 2026-07-30). The brief's 3.7.2 pin, and any prefect ≥ 3.6, is
unsatisfiable while the base requests `diffsync[redis]` (that extra caps `redis<5.0`;
`prefect>=3.6` → `pydocket` → `redis>=5` — see `research.md` F1), so the base declares
`redis` directly with a permissive floor: `diffsync>=2.1,<3.0` + `redis>=4.3,<9`. No
companion pins: D006's `importlib-metadata` and `fastapi` pins existed only to repair
prefect 3.5.0 defects, both verified fixed at 3.8.1, and are withdrawn. The base install,
import, and ordinary CLI never import Prefect. All four load-bearing Prefect behaviors
(co-install, served-deployment REST runs, run-scoped log bridging, parameter rejection
before the flow body) were verified by real probes — against 3.5.0 in Phase 0
(`research.md` probe table) and re-verified directly against 3.8.1 at the gate
(`research.md` "Gate-ratified resolution").

## Technical Context

**Language/Version**: Python 3.10–3.13 (`requires-python >=3.10,<3.14`); repo currently
developed on 3.12.

**Primary Dependencies**: Base — one declaration changes (D005 option D):
`diffsync[redis]>=2.1,<3.0` becomes `diffsync>=2.1,<3.0` plus a directly declared
`redis>=4.3,<9`; `infrahub-sdk[all]`, `netutils`, `pyarrow`, `fsspec`, `filelock`,
`typer` (via sdk), `structlog`, `tqdm` are untouched. `redis` must be declared directly
because `infrahub_sync/utils.py:11` imports `diffsync.store.redis.RedisStore`
unconditionally, so removing the `[redis]` extra without a direct declaration would break
`import infrahub_sync.utils` for every user; the floor is permissive (`>=4.3`, not `>=5`)
so a downstream consumer that still requires `diffsync[redis]` resolves (verified at
redis 4.6.0 — with `redis>=5` that combination is unsatisfiable). New optional extra
`prefect`: exactly `prefect = ["prefect==3.8.1"]` — no companion pins (D006 withdrawn).
Gate-verified resolution of the patched set: prefect 3.8.1, redis 8.1.0, fastapi 0.141.1,
starlette 1.3.1, pydantic 2.13.4, diffsync 2.2.3, infrahub-sdk 1.22.2, pyarrow 21.0.0,
typer 0.27.0, uvicorn 0.52.0, griffe 2.1.0, pydocket 0.23.1.

**Storage**: Existing per-sync cache layout under `.infrahub-sync-cache/<sync_name>/<run_id>/`
(`run.json`, `plan.parquet`, side snapshots) — reused as-is; `artifact_path` in
`RunResult` points at the run directory. Prefect server uses its default local SQLite
under `PREFECT_HOME`.

**Testing**: pytest (`uv sync --extra dev --extra prefect` — what `AGENTS.md:18,26` documents
as of the C1 review remediation, commit `730dc0d`; note R-1 — plain `uv sync` does not install
dev tooling, and without `--extra prefect` the gates go blind: `ty` reports 13 diagnostics
because it cannot see `orchestration/`, and the flow suite silently skips). Prefect-dependent tests are skipped when `prefect` is not importable and/or
marked `integration` when they need a live server; baseline 110 passed / 3 skipped at
`9edc1bc` must not regress (R-4). Gate-verified: under the upgraded dependency set the
inherited suite reports 111 passed / 2 skipped with zero failures — no regression against
the baseline (same 113-test total).

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
| IV | Type Safety & Explicit Contracts | **PASS (with three documented broad-except sites in new code: two under D009, plus one added by the H4 review remediation)** | `execution.py` is fully typed (`Literal` operation/status, frozen `RunResult` with a read-only `summary` mapping, typed factory protocol); contracts in `contracts/` are concrete typed definitions; no `ty` overrides; specific exception classes (`RunValidationError`, `RunExecutionError`). Error-handling design (D009, honest statement — not "declared boundaries only"): `execute_run` preserves the `9edc1bc` CLI failure behavior verbatim, including the CLI's own broad `except Exception:` mark-`run.json`-failed + bare re-raise pattern (`cli.py:156-159`/`285-288` today) — a *preserved existing pattern* documented by a comment at the site, required so lifecycle failures never leave `run.json` at `status="running"`; the two narrow `ValueError` handlers stay where today's CLI has them (factory → prefixed abort via the CLI's wrapper factory; serial-load → unprefixed abort via the CLI-only seam). Sanitize-and-wrap into the typed remote errors happens ONLY in `run_remote_request`, whose boundary translation catches broadly and ALWAYS re-raises typed and sanitized, likewise documented by a comment at the site. **Suppression directives are mechanism-conditioned, not site-uniform** (E16; the rule: a `# noqa: BLE001` is added if and only if ruff reports BLE001 for the code as actually written, verified with `uv run ruff check` during implementation, never speculatively). Probed in this repo with `uv run ruff check --no-cache --select BLE`: a blind `except` + bare `raise` does NOT fire BLE001 (a directive there would make ruff report `RUF100 Unused noqa directive` and exit 1), and neither does `except Exception as exc: raise E(msg) from exc` — but `... from None` and `... from RuntimeError(str(exc))` both DO fire. Therefore: `execute_run` step 6 (blind `except` + bare `raise`) carries **NO** directive; `run_remote_request`'s sanitize-and-wrap **DOES** carry a targeted `# noqa: BLE001` on its `except Exception` line, because contracts/run-result-and-errors.md §2 (E5, binding) permits only the two firing mechanisms there — rebuilt sanitized cause, or `__suppress_context__` with redacted text inlined — while the one clean form (plain `from exc`) is the unredacted-cause leak E5 forbids. `invoke lint` reaches its ratified inherited baseline in exactly that configuration — **exit 30, not exit 0**, with zero new pylint diagnostics attributable to this run (T039; the exit-0 expectation came from the brief's R-4 and was falsified). See contracts/execution-surface.md "BLE001 suppression rule". **Third site, arrived via the H4 review remediation (commit `730dc0d`) and NOT covered by D009** — the count of broad-except sites in new code is therefore **three**, not two: `orchestration/flow.py:88`, the `except Exception` in `RunLoggerBridge.emit` that calls `self.handleError(record)`. Its justification is the stdlib `logging.Handler.emit` contract rather than the error-translation design: `Handler.handle` does not shield `emit`, so anything raised there escapes at the unrelated logging call site — and without the guard a single bad `%`-format call in any `infrahub_sync.*` logger (including a user-written custom adapter) reported a **fully-applied sync as FAILED**, which is the H4 finding. `handleError` is the stdlib remedy for exactly that, and it is what today's plain-CLI `StreamHandler` already does with the same bad call. Unlike the other two, this site swallows rather than re-raises, so under the same mechanism-conditioned E16 rule it carries BOTH a `# noqa: BLE001` and a `# pylint: disable=broad-exception-caught`; both are reported-necessary, and the gate confirms it (no `RUF100`, and pylint adds no new diagnostic — final tree 54 diagnostics, zero new). Full site inventory so a later reader can count: `orchestration/flow.py:88` (logging-handler contract, H4), `execution.py:604` (`execute_run` step 6, preserved CLI pattern, D009, no directive), `execution.py:731` (`run_remote_request` boundary translation, D009, `# noqa: BLE001` only). One caveat: the flow module deliberately omits `from __future__ import annotations` (research F3), with a comment; that trap was re-measured as **version-specific**, not version-generic — see tasks T014. |
| V | Test Discipline | **PASS** | Planned: parametrized negative tests for `sync_name` resolution and `confirm_writes` (SC-004 negative-test set), RunResult schema/invariant tests, fingerprint unit tests, base-install-without-prefect test (SC-006), canary-redaction test (DBA-008), flow tests skip-if-no-prefect, live end-to-end marked `integration`. Existing targeted tests (DBA-009 population) must pass unmodified. |
| VI | Security, Secrets & Input Boundaries | **PASS** | Credentials only from runner env (DBR-006; the infrahub adapter already reads `INFRAHUB_ADDRESS`/`INFRAHUB_API_TOKEN`); `sync_name` is an opaque name matched by exact equality against discovered `config.yml` `name` fields — never used to build a path (same glob/match rule as the CLI lookup, via the D010 tolerant per-file walk); remote parameters carry no paths/CLI fragments/credentials/env overrides; exception messages pass value-based secret redaction; example credentials are obviously fake placeholders. |
| VII | Simplicity & Maintainability | **PASS** | The new abstraction has three real callers (CLI diff, CLI serial sync, flow) — satisfies the two-caller rule. New dependency is brief-mandated and optional, and the extra carries a single pin — `prefect==3.8.1` — with no companion pins to maintain (D005 option D; D006's two repair pins are withdrawn, their prefect-3.5.0 defects verified fixed at 3.8.1). The one base-declaration change (`diffsync[redis]` → `diffsync` + `redis>=4.3,<9`) is the minimum that makes the extra installable at all, keeps the redis client present for `utils.py`'s unconditional `RedisStore` import, and keeps downstream `diffsync[redis]` consumers resolvable. No engine rewrite; parallel branch untouched; generated example files regenerated via `generate` (R-2), never hand-edited. |

**Gate result (pre-Phase-0 and post-Phase-1): PASS — no violations; Complexity Tracking
is empty.** One deviation from the *brief* (not the constitution) is recorded: D005
replaces the unsatisfiable 3.7.2 pin with `prefect==3.8.1` and declares `redis` directly
in the base (option D — RATIFIED at the checkpoint gate by Blake Ellis, 2026-07-30).

**Deviation note — logging (recorded as governance decision D008)**: new modules log
via stdlib `logging.getLogger(__name__)`, not structlog, despite the constitution's
structlog sentence. This matches the entire existing codebase — there is zero structlog
usage anywhere in `infrahub_sync/` today — and is required by the DBR-012 log-bridge
design, which attaches a stdlib `logging.Handler` to the `infrahub_sync` logger
hierarchy to forward records to the Prefect run logger (structlog-emitted records would
bypass that bridge). A constitution PATCH correcting the structlog sentence (and the
stale markdownlint-cli2 → rumdl tooling reference) is queued OUTSIDE this delivery run;
the constitution itself is not edited here.

## Project Structure

### Documentation (this feature)

```text
dev/specs/001-prefect-managed-remote-run/
├── plan.md              # This file
├── research.md          # Phase 0: probe table, F1–F3, D005 record (ratified option D)
│                        #   and D006 record (superseded/withdrawn at the gate)
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

pyproject.toml                  # MODIFIED (D005 option D): base dependencies —
                                #   "diffsync[redis]>=2.1,<3.0" → "diffsync>=2.1,<3.0" + "redis>=4.3,<9";
                                #   [project.optional-dependencies] prefect = ["prefect==3.8.1"]
uv.lock                         # MODIFIED: regenerated by `uv lock`, staged in the same commit (E9)

AGENTS.md                       # MODIFIED (R-1, first commit): uv sync → uv sync --extra dev;
                                #   final state is `uv sync --extra dev --extra prefect`
                                #   (AGENTS.md:18,26) after the C1 review remediation (730dc0d)

.github/workflows/              # MODIFIED by the C1 review remediation (730dc0d), not by any task:
                                #   workflow-tests.yml installs both extras on the whole matrix and
                                #   gains a `base-install-tests` job; workflow-linter.yml installs
                                #   both extras so `ty` can see orchestration/ (see tasks.md
                                #   "Review remediation")

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
   - `resolve_sync_instance(sync_name, *, directory)` — exact-name lookup using the
     same recursive `**/config.yml` glob and `name` match as the CLI lookup, but as a
     tolerant per-file walk (D010) whose name-extraction mechanism is fixed so the
     rules are decidable (E17): `yaml.safe_load` + the top-level `name` key; a file
     whose name is determinable and equal to the request is validated as `SyncConfig`
     (failure → `RunValidationError` naming the logical name and file path only, parse
     detail never chained verbatim); determinable-and-different → skipped silently;
     UNDETERMINABLE (unreadable, YAML error, non-mapping) → skipped with a WARNING
     naming the path only, counted, resolution continues — so a broken neighbor never
     blocks another name and a bad-YAML file can never be the matched one; no match →
     `RunValidationError` naming the logical name and, when applicable, the COUNT of
     unreadable files. The CLI keeps calling
     `utils.get_instance` unchanged. The requested value is never used to build a
     path, so traversal-shaped values fail the same way unknown names do (SC-004).
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
   - Pipeline lock: `cache.locks.pipeline_lock(sync_instance.name, timeout=...)`
     acquired inside `execute_run` (default 60 s; private `_lock_timeout` test seam);
     `filelock.Timeout` propagates unchanged on the CLI path (today's traceback) and
     is wrapped into `RunExecutionError` naming the sync and the timeout only in
     `run_remote_request` (spec edge case 5; D009 wrap locus).
2. **Prefect flow** (`contracts/prefect-flow.md`, DBR-001/002/003/011/012):
   parameters exactly `sync_name: str`, `operation: Literal["plan","sync"] = "plan"`,
   `confirm_writes: bool = False`, `branch: str | None = None`. Body: bridge
   `infrahub_sync` logger → run logger (attach/finally-remove), read
   `INFRAHUB_SYNC_CONFIG_DIRECTORY`, `resolve_sync_instance`, `run_remote_request`,
   log the result summary line, return an asdict-SHAPED seven-key dict built by
   explicit construction (never `dataclasses.asdict` — X15). Serve entrypoint validates
   the env var at startup and exits with an error naming it otherwise (spec
   clarification #2).
3. **RunResult + failure contract** (`contracts/run-result-and-errors.md`, DBR-015):
   frozen dataclass, exactly seven fields, cross-field invariants enforced in
   `__post_init__`; `RunValidationError`/`RunExecutionError` with value-based secret
   redaction applied at raise time; canonical plan fingerprint helper
   (`cache/fingerprint.py`) per spec clarification #1.
4. **Commit order** (binding on the tasks phase): commit 1 = R-1 (AGENTS.md
   `uv sync --extra dev`; later widened to `uv sync --extra dev --extra prefect` by the C1
   review remediation, commit `730dc0d`); commit 2 = R-2 (regenerate
   `examples/netbox_to_infrahub`); feature work only after both.

## Decision-ID map (D001–D013 → artifact locations)

**All of D001–D013 are RATIFIED (checkpoint gate, Blake Ellis, 2026-07-30)** — D005 was
ratified as option D (an override of the recorded recommendation) and D006 is SUPERSEDED /
WITHDRAWN as a consequence; every other decision was accepted as recommended. This table
lets the gate packet and the artifacts be cross-checked ID-by-ID (F5 remediation,
2026-07-30; D012/D013 added in round-2 remediation, 2026-07-30).

| ID | Decision (one line) | Where it lives |
|---|---|---|
| D001 | Canonical plan fingerprint definition (SHA-256 over canonicalized plan rows; null-normalized sort key) | spec §Clarifications #1; contracts/run-result-and-errors.md §3; data-model §5; tasks T004/T009 |
| D002 | Config directory via required `INFRAHUB_SYNC_CONFIG_DIRECTORY`, validated at serve start | spec §Clarifications #2; contracts/prefect-flow.md §3; data-model §4; tasks T015/T016/T018 |
| D003 | Remote runs pin `9edc1bc` engine defaults; pipeline lock owned by the surface, bounded contention failure | spec §Clarifications #3; contracts/execution-surface.md; data-model §1; tasks T007/T008 |
| D004 | Run-scoped log bridge — flow owns the `infrahub_sync` handler AND logger level | spec §Clarifications #4; contracts/prefect-flow.md §4; tasks T014/T016 |
| D005 | **RATIFIED as option D** (gate override of the recorded option B): the extra is exactly `prefect = ["prefect==3.8.1"]`, and the base replaces `diffsync[redis]>=2.1,<3.0` with `diffsync>=2.1,<3.0` + `redis>=4.3,<9` (permissive floor, deliberately not `redis>=5`) | research.md F1 + "Gate-ratified resolution"; spec §Constraints/§Assumptions; this plan §Summary/§Technical Context; contracts/prefect-flow.md §1; tasks T012/T012a/T039; quickstart Setup |
| D006 | **SUPERSEDED / WITHDRAWN at the gate** — the companion pins `importlib-metadata>=4.4` and `fastapi>=0.111,<0.121` existed only to work around prefect 3.5.0 packaging defects; both defects are verified fixed at 3.8.1 (stdlib `importlib_metadata` alias in `prefect/utilities/compat.py`; HTTP 200 on the deployment-name route under fastapi 0.141.1), so the extra carries no companion pins | research.md F2 + D006 record (dated supersession note); contracts/prefect-flow.md §1; tasks T012 |
| D007 | R-1's commit 1 includes the verbatim mirror `.github/copilot-instructions.md` | tasks.md decision record D007; tasks T001 |
| D008 | New modules log via stdlib `logging`, not structlog (constitution PATCH queued outside this run) | this plan, "Deviation note — logging"; tasks header logging convention |
| D009 | Sanitize-and-wrap boundary lives in `run_remote_request` only; `execute_run` preserves `9edc1bc` CLI failure behavior verbatim | contracts/execution-surface.md "Failure semantics"; this plan Constitution row IV; data-model §3; tasks T007/T025/T026/T027 |
| D010 | Tolerant per-file configuration resolution for the remote surface (CLI `get_instance` untouched) | contracts/execution-surface.md `resolve_sync_instance`; data-model §1 step 4; tasks T006/T011 |
| D011 | T035 docs-site reference page is governance-mandated scope beyond the brief's deliverable list | tasks T035; critiques/collation-r1.md |
| D012 | Missing-credential env-var naming (`INFRAHUB_ADDRESS`/`INFRAHUB_API_TOKEN`) lands in `run_remote_request`'s sanitized wrap message; `infrahub_sync/adapters/infrahub.py` is NOT touched, keeping DBR-009 byte-identity absolute (option A) | contracts/execution-surface.md failure-semantics wrap list; contracts/run-result-and-errors.md §2; tasks T008/T011/T025; critiques/collation-r2.md (full record); critiques/fidelity-r2.md F8 |
| D013 | T033a's shipped-example diagnosability change is a recorded gate item grounded in DBA-011 + DBR-012/DBA-004 (not the brief's untriggered fixture-repair allowance); five-device outcome unchanged | tasks T033a; critiques/collation-r2.md (full record); critiques/fidelity-r2.md F9 |

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified.

None — no constitution violations requiring tracked complexity.
