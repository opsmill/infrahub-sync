# Tasks: Prefect-Managed Remote Infrahub Sync Run (Developer Preview)

**Input**: Design documents from `dev/specs/001-prefect-managed-remote-run/`

**Prerequisites**: `plan.md`, `spec.md` (both required), `research.md`, `data-model.md`,
`quickstart.md`, `contracts/execution-surface.md`, `contracts/prefect-flow.md`,
`contracts/run-result-and-errors.md`. Product authority: brief DB-001 v2
(`LOCAL-DP-001`), carried verbatim in spec.md (DBR-001–DBR-015, DBA-001–DBA-011).

**Tests**: REQUIRED. The spec's acceptance criteria (DBA-001–DBA-011) and Constitution
Principle V name specific automated tests and live verification evidence; test tasks
below are mandatory, not optional.

**Commit discipline (binding, plan Design Outline #4)**: commit 1 = T001 (R-1) alone;
commit 2 = T002 (R-2) alone; feature work only after both. All commits are made by the
root orchestrator; tasks below mark the required commit boundaries.

**Logging convention for new code**: new modules log via `logging.getLogger(__name__)`
under the `infrahub_sync` logger hierarchy — the repository's existing pattern
(`cli.py`, `potenda/`) and load-bearing for the DBR-012 log bridge (research
"Log bridging" decision). No `print()` in package code; no secrets in any log or
exception message (DBR-006, DBA-008).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US5 from spec.md; Setup/Foundational/Polish tasks carry no story label
- Every task cites the DBR/DBA/SC/R/D identifiers it advances (Trace:) and, for
  verification tasks, the evidence it must produce (Evidence:)

---

## Phase 1: Setup (mandated enabling work — R-1, R-2, baseline)

**Purpose**: The brief's ordered pre-implementation readiness items and the inherited
baseline record. No feature work may precede T001/T002.

- [ ] T001 Update the setup and workflow commands from `uv sync` to `uv sync --extra dev` in `AGENTS.md` (lines 18 and 26: the Setup block and the Required Development Workflow block) and in the verbatim mirror `.github/copilot-instructions.md` (lines 18 and 26 — see D007 below); verify with `grep -rn "^uv sync$" AGENTS.md .github/copilot-instructions.md` returning no matches; then run `uv sync --extra dev` and confirm `uv run pytest -q --collect-only` succeeds in this worktree. **Commit boundary: this change is commit 1 of the branch, alone.** Trace: R-1; spec "Mandated Enabling Work"; plan Design Outline #4; D007.
- [ ] T002 Run `uv run infrahub-sync generate --name from-netbox --directory examples/` once and stage the regenerated files under `examples/netbox_to_infrahub/` (expected: four files, ~400 lines of deterministic template drift); verify by re-running the same command and confirming `git status --porcelain examples/` is empty afterward. **Commit boundary: this regeneration is commit 2 of the branch, alone (isolated baseline-hygiene commit).** Trace: R-2; spec "Mandated Enabling Work"; plan Design Outline #4.
- [ ] T003 Record the inherited baseline in `dev/specs/001-prefect-managed-remote-run/run-report.md` (create the file): run `uv run pytest -q` (expect 110 passed, 3 skipped), `uv run invoke lint` (expect exit 0 with pre-existing pylint `import-outside-toplevel` warnings in `infrahub_sync/potenda/__init__.py`), `uv run invoke format` (expect no diffs); note R-5 (`tests/test_potenda_parallel.py` `@pytest.mark.timeout(5)` no-op — inherited, do not fix, do not add `pytest-timeout`). Trace: R-4, R-5, SC-006 (baseline non-regression clause). Evidence: recorded counts and command output in the run report.

**Checkpoint**: Commits 1 and 2 exist; baseline recorded; tree clean.

---

## Phase 2: Foundational (shared execution surface + fingerprint — blocks all stories)

**Purpose**: The narrow typed surface (DBR-007/008/015) and the canonical fingerprint
helper that every user story calls or verifies against. No Prefect import anywhere in
this phase (DBR-010).

- [ ] T004 [P] Create `infrahub_sync/cache/fingerprint.py` with `PLAN_FINGERPRINT_FIELDS = ("action", "resource", "source_id", "attribute", "new_value")` and `compute_plan_fingerprint(run_dir: Path) -> str` implementing the binding algorithm of `contracts/run-result-and-errors.md` §3: project the five fields from `<run_dir>/plan.parquet`, serialize each row as `json.dumps(row, sort_keys=True, separators=(",", ":"))`, sort by `(resource, source_id, action, attribute)` with the full serialized row as final tie-breaker, join with `"\n"`, UTF-8, SHA-256 hexdigest. Fully typed, concise docstring. Trace: DBA-009, SC-007; spec clarification #1; data-model §5.
- [ ] T005 [P] Create `infrahub_sync/execution.py` with the type layer of `contracts/execution-surface.md`: `Operation`/`Status`/`ActionKey` literals, `PotendaFactory` alias, `RunValidationError`, `RunExecutionError`, the frozen+slots seven-field `RunResult` dataclass with `__post_init__` enforcing all data-model §2 invariants (`changed ⇔ status != "no-change" ⇔ sum(summary.values()) > 0`; `planned ⇒ operation == "plan"`; `applied ⇒ operation == "sync"`; `run_id == Path(artifact_path).name`; `set(summary) == {"create","update","delete"}`, raising `ValueError`), and the value-based secret-redaction helper (collect env credential values — at minimum `INFRAHUB_API_TOKEN` — plus values of `token`/`password`/`secret`/`api_key` keys in resolved adapter settings; replace occurrences with `***`). No `prefect` import; module stays importable in a base install. Trace: DBR-015, DBR-006; DBA-008, DBA-010; data-model §§2–3; contracts/run-result-and-errors.md §§1–2.
- [ ] T006 Implement `resolve_sync_instance(sync_name: str, *, directory: str) -> SyncInstance` in `infrahub_sync/execution.py`: exact-string-equality lookup via `infrahub_sync.utils.get_instance(name=sync_name, directory=directory)` semantics (recursive `**/config.yml` discovery); the requested value is never used to construct a filesystem path; no match or unreadable/invalid matched `config.yml` → `RunValidationError` naming the logical name (optionally the offending file), never echoing directory contents, file contents, or credential values. Depends on T005 (same file). Trace: DBR-005; DBA-007; SC-004; data-model §1 validation steps 3–4.
- [ ] T007 Implement `execute_run(sync_instance, *, operation, confirm_writes=False, branch=None, show_progress=None, verbosity=logging.INFO, run_id=None, concurrent_load=True, full_extract=True, allow_rowcount_drop=False, continue_on_error=False, print_diff=True, potenda_factory=None) -> RunResult` in `infrahub_sync/execution.py` per `contracts/execution-surface.md` steps 1–7: (1) validate `operation` membership and the `confirm_writes` gate (`operation="sync"` unconfirmed → `RunValidationError` stating `confirm_writes=true` is required to run `operation=sync`) BEFORE any adapter construction; (2) acquire `infrahub_sync.cache.locks.pipeline_lock(sync_instance.name)` (existing 60 s timeout; `filelock.Timeout` → `RunExecutionError` naming the sync and timeout); (3) build the engine via `potenda_factory` (default `utils.get_potenda_from_instance`); factory `ValueError`/`ImportError` → `RunExecutionError` preserving today's "Failed to initialize the Sync Instance: ..." wording; (4) `plan` = today's `cli.py` diff lifecycle exactly (RunFile `mode="diff"` `running→dry-run`, `force_full_extract`, `load_both_sides`, `diff`, `write_plan`, diff-string log, `summary={"resources": len(top_level)}`, `finished_at`, "Cached run %s at %s"); (5) `sync` = today's serial branch exactly (RunFile `mode="sync"`, `load_both_sides`, `check_rowcount_guardrail`, `diff`, `write_plan`, optional diff print, `sync(diff)` + timing log only when diffs exist else "No difference found. Nothing to sync", `persist_baseline_counts`, `running→applied`, `summary={"resources": ..., "mode": "serial"}`, "Sync run %s at %s"); (6) failures inside 4/5 mark `run.json` `failed` and raise `RunExecutionError` chaining the original; (7) derive `RunResult` (summary counted from the same rows written to `plan.parquet`). All raised messages pass the T005 redaction. Depends on T006. Trace: DBR-004, DBR-007, DBR-009, DBR-015; DBA-006, DBA-010; Constitution I/II; spec clarification #3.
- [ ] T008 Implement `run_remote_request(sync_name, operation="plan", confirm_writes=False, branch=None, *, config_directory) -> RunResult` in `infrahub_sync/execution.py`: `resolve_sync_instance` then `execute_run` with every engine option at its `9edc1bc` CLI default EXCEPT `show_progress=False`; no parameter accepts paths, CLI fragments, credentials, or environment overrides. Depends on T007. Trace: DBR-003, DBR-005, DBR-006; data-model §1 pinned engine options; spec clarification #3.
- [ ] T009 [P] Create `tests/test_plan_fingerprint.py`: unit tests for `compute_plan_fingerprint` — determinism across two identically-planned run dirs, row-order independence, exclusion of timestamps/run-ids/paths (two runs differing only in run_id/dir hash equal), tie-breaker behavior on synthetic duplicate keys, and a fixed-vector digest test; tests may not reimplement the algorithm (assert against the helper, not a copy). Depends on T004. Trace: DBA-009, SC-007; contracts/run-result-and-errors.md §§3–4.
- [ ] T010 Create `tests/test_execution_surface.py` (part 1 — result and redaction): assert `RunResult` exact field set (`len(dataclasses.fields()) == 7`, `slots` rejects extra attributes), immutability (`FrozenInstanceError` on assignment), every data-model §2 invariant (positive and violating cases → `ValueError`), and redaction (seeded secret values in env and adapter settings are replaced by `***` in `RunValidationError`/`RunExecutionError` messages). Depends on T005. Trace: DBA-010, SC-008; DBR-015; contracts/run-result-and-errors.md §§1–2.
- [ ] T011 Extend `tests/test_execution_surface.py` (part 2 — refusals and lifecycles): parametrized SC-004 negative set for `sync_name` (`"nope"` unknown, `"../custom-example"`, an absolute path, `"a/b"` separator, `"--help"` flag-like, `"$(touch ...)"` shell-metacharacter/command-substitution) each refused as `RunValidationError` with filesystem spies proving no read outside the configured directory and subprocess spies (patched `subprocess.Popen`/`os.system`) proving no subprocess start; unconfirmed `operation="sync"` refused BEFORE adapter construction (spy `potenda_factory` never called) with the required message; pipeline-lock contention (held lock, shortened timeout) → `RunExecutionError` naming sync and timeout, bounded not hanging; one execution-failure case (factory raising `ValueError` for an unreachable/misconfigured system) → sanitized `RunExecutionError`; successful `plan` via a fake `potenda_factory` fixture → `status in {"planned","no-change"}`, `run.json` `mode="diff"`/`dry-run`, `plan.parquet` present, `RunResult` fields correct. Depends on T007, T008, T010 (same file). Trace: DBA-006, DBA-007, DBA-010; SC-004, SC-008; DBR-004, DBR-005, DBR-008.

**Checkpoint**: `infrahub_sync/execution.py` and `infrahub_sync/cache/fingerprint.py`
complete and unit-tested; `uv run pytest -q tests/test_execution_surface.py
tests/test_plan_fingerprint.py` green; no `prefect` import anywhere in the package.

---

## Phase 3: User Story 1 — Remote plan through Prefect (Priority: P1) 🎯 MVP

**Goal**: A developer installs the optional extra, starts a default self-hosted Prefect
Server, serves the packaged flow, and a remote caller runs and observes a real read-only
`plan` through Prefect's REST API and UI.

**Independent Test**: Against an empty qualified destination, submit `operation=plan`
for `custom-example` via `POST /api/deployments/{id}/create_flow_run`; a flow-run ID is
returned synchronously, the run reaches COMPLETED, the plan shows five creates, and the
lifecycle plus bridged `infrahub_sync` log lines are visible in Prefect (quickstart
Scenario 1; DBA-002/003/004).

- [ ] T012 [P] [US1] Add the optional extra to `pyproject.toml` under `[project.optional-dependencies]`: `prefect = ["prefect==3.5.0", "importlib-metadata>=4.4", "fastapi>=0.111,<0.121"]` with a comment naming both upstream prefect-3.5.0 packaging defects (missing `importlib_metadata` declaration; loose `fastapi<1.0` bound); base `dependencies` unchanged. Verify `uv sync --extra dev --extra prefect` resolves (redis stays <5.0) and `uv run python -c "import prefect; print(prefect.__version__)"` prints `3.5.0`. Trace: D005, D006; research F1/F2, probes a₁–a₆; DBR-010 (optionality), DBR-014 (base set untouched); contracts/prefect-flow.md §1.
- [ ] T013 [P] [US1] Create `infrahub_sync/orchestration/__init__.py` — empty module docstring only; no `prefect` import at package level (the orchestration package is the ONLY place allowed to import prefect, and only in its leaf modules). Trace: DBR-010; DBA-001; plan Project Structure.
- [ ] T014 [US1] Create `infrahub_sync/orchestration/flow.py` per `contracts/prefect-flow.md` §§2+4: module-level comment explaining the deliberate ABSENCE of `from __future__ import annotations` (research F3 — deferred annotations break prefect 3.5.0 run-time parameter validation); constants `FLOW_NAME = "infrahub-sync"`, `DEPLOYMENT_NAME = "infrahub-sync"`, `CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"`; `RunLoggerBridge(logging.Handler)` forwarding `infrahub_sync`-hierarchy records to the Prefect run logger preserving level and origin logger name; `@flow(name=FLOW_NAME) def infrahub_sync_run(sync_name: str, operation: Literal["plan","sync"] = "plan", confirm_writes: bool = False, branch: str | None = None) -> dict` whose body (in order) gets the run logger, attaches the bridge to `logging.getLogger("infrahub_sync")` inside `try/finally` (removal in `finally`), reads `os.environ[CONFIG_DIR_ENV]` (missing → `RunExecutionError` defensively), calls `run_remote_request(...)` in-process (never a subprocess), logs one summary line (`"run %s finished: status=%s changed=%s summary=%s artifact=%s"`), and returns `dataclasses.asdict(result)`; exceptions propagate so Prefect records FAILED with the sanitized message. Exactly these four parameters — no path/credential/env-override parameter exists. Depends on T008, T012, T013. Trace: DBR-001, DBR-003, DBR-005, DBR-006, DBR-008, DBR-011, DBR-012; DBA-003, DBA-004; SC-001, SC-002; spec clarification #4; research probes b, c₁, c₂, d₁.
- [ ] T015 [US1] Create `infrahub_sync/orchestration/serve.py` runnable as `python -m infrahub_sync.orchestration.serve` per `contracts/prefect-flow.md` §3: at startup read `INFRAHUB_SYNC_CONFIG_DIRECTORY`; if unset, empty, or not an existing directory, emit one error line naming the variable and exit non-zero BEFORE any deployment is served; otherwise call `infrahub_sync_run.serve(name=DEPLOYMENT_NAME)` (locally served deployment; no work pool, no worker; default `enforce_parameter_schema=True`); configurations under the directory are re-resolved per run (no restart needed). Depends on T014. Trace: DBR-002; DBA-002; spec clarification #2; data-model §4.
- [ ] T016 [US1] Create `tests/orchestration/__init__.py` and `tests/orchestration/test_flow.py` with `pytest.importorskip("prefect")` module guard: assert the flow's parameter contract (exactly `sync_name`, `operation` default `"plan"`, `confirm_writes` default `False`, `branch` default `None` — no fifth parameter); assert `RunLoggerBridge` forwards an `infrahub_sync.potenda` child-logger record to a stub run logger preserving level and logger name and is removed after the body (including on exception); assert a successful in-process flow call (patched `run_remote_request`) returns the `asdict` seven-key dict; assert serve-start validation by invoking the `serve.py` validation function with the env var unset/pointing at a file → non-zero exit/error naming `INFRAHUB_SYNC_CONFIG_DIRECTORY`. Depends on T014, T015. Trace: DBR-003, DBR-012; DBA-002 (serve validation), DBA-004; spec clarifications #2/#4.
- [ ] T017 [US1] Create `tests/integration/test_remote_run_live.py` marked `@pytest.mark.integration` (opt-in per `pyproject.toml` marker; requires `INFRAHUB_ADDRESS` + `INFRAHUB_API_TOKEN` in the environment and a live Prefect server + served deployment): drive quickstart Scenario 1 programmatically — resolve the deployment id via `GET /api/deployments/name/infrahub-sync/infrahub-sync`, `POST .../create_flow_run` with `{"parameters": {"sync_name": "custom-example", "operation": "plan"}}`, assert the id is returned synchronously, poll to COMPLETED, assert `POST /api/logs/filter` contains bridged `infrahub_sync` lifecycle lines and the summary line reporting five creates. Depends on T014, T015. Trace: DBA-003, DBA-004; SC-001, SC-002; DBR-011.
- [ ] T018 [US1] LIVE verification (DBA-002): in an environment with the extra installed, start `uv run prefect server start` (default local database + built-in UI) and, with `PREFECT_API_URL`, `INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples"`, `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN` set, start `uv run python -m infrahub_sync.orchestration.serve`; also demonstrate the serve-start failure by unsetting the config-directory variable once. Evidence (append to `dev/specs/001-prefect-managed-remote-run/run-report.md`): reproduction transcript of the documented commands, `GET $PREFECT_API_URL/deployments/name/infrahub-sync/infrahub-sync` showing `status: "READY"` and `enforce_parameter_schema: true`, the failed-start line naming `INFRAHUB_SYNC_CONFIG_DIRECTORY`, and confirmation no external database or worker service was started. If the live environment is unavailable, record the live-environment ceiling per R-3 instead of blocking. Trace: DBA-002; DBR-001, DBR-002; spec clarification #2.
- [ ] T019 [US1] LIVE verification (DBA-003 + DBA-004): verify the lab destination holds zero `InfraDevice` objects (reset a disposable one if not); run quickstart Scenario 1's REST calls; confirm the flow-run ID arrives in the synchronous create response, state reaches COMPLETED, the plan reports exactly five creates / zero updates / zero deletes, the runner-local run directory `.infrahub-sync-cache/custom-example/<run_id>/` contains `run.json` (`status: dry-run`, `mode: diff`) and `plan.parquet` with five creates, and the run lifecycle plus bridged `infrahub_sync` log lines are visible via `POST /api/logs/filter` and in the UI at `http://127.0.0.1:4200`. Evidence (run report): API request/response transcripts, flow-run record, `run.json` contents, plan summary, log excerpt, UI screenshot or state listing. Live-environment ceiling fallback per R-3. Depends on T018. Trace: DBA-003, DBA-004; SC-001, SC-002; DBR-001, DBR-003, DBR-011, DBR-012; R-3.

**Checkpoint**: US1 fully demonstrable — remote plan runs and is observable. MVP.

---

## Phase 4: User Story 2 — Explicitly confirmed destination write (Priority: P2)

**Goal**: A remote caller who reviewed the plan applies it with `operation=sync` +
`confirm_writes=true` through the same surface the CLI uses; the change is observable
at the destination and a follow-up plan converges to no-change.

**Independent Test**: Against a reset empty qualified destination, submit
`operation=sync` with `confirm_writes=true`; the run completes, five `InfraDevice`
objects exist, and a follow-up plan reports no changes (quickstart Scenario 2; DBA-005).

- [ ] T020 [US2] Extend `tests/test_execution_surface.py` with the confirmed-sync lifecycle: successful `operation="sync"`+`confirm_writes=True` via the fake `potenda_factory` → `RunResult(status="applied")` with correct summary, `run.json` `mode="sync"`/`status="applied"`/`summary.mode="serial"`, `persist_baseline_counts` called, timing log only when diffs exist; a second run against the already-synchronized fake state → `status="no-change"`, `changed=False`, all-zero summary, "No difference found. Nothing to sync" logged (idempotent reconciliation). Depends on T007. Trace: DBA-005 (automated analog), DBA-010 (sync-side result schema); SC-003, SC-008; DBR-003, DBR-004; Constitution II.
- [ ] T021 [US2] LIVE verification (DBA-005): verify/reset the destination to zero `InfraDevice` objects; strictly sequentially (each run terminal before the next): (1) `POST .../create_flow_run` with `{"parameters": {"sync_name": "custom-example", "operation": "sync", "confirm_writes": true}}` → COMPLETED; (2) observe exactly five `InfraDevice` objects (`core01..core03`, `edge01`, `edge02`) at the destination via the Infrahub API; (3) follow-up `operation=plan` run → COMPLETED with `status: no-change`, `changed: false`, all-zero summary. Evidence (run report): all three API transcripts, the destination object listing, and the no-change result. No concurrent destination-writing runs (spec edge case 5). Live-environment ceiling fallback per R-3. Depends on T014, T015, T019. Trace: DBA-005; SC-003; DBR-003, DBR-004; Constitution I/II.

**Checkpoint**: Full vertical slice (plan → confirmed apply → converged no-change plan)
demonstrated.

---

## Phase 5: User Story 3 — Safety refusals and secret hygiene (Priority: P3)

**Goal**: Unconfirmed writes and unresolvable/path-like/command-like configuration names
fail clearly before any adapter loads; no credential value ever reaches Prefect-visible
state.

**Independent Test**: `operation=sync` without confirmation fails before either adapter
loads with the destination unchanged; unknown/path-like/command-like `sync_name` values
are refused with no out-of-directory read and no subprocess; seeded canaries appear
nowhere Prefect-visible (quickstart Scenario 3; DBA-006/007/008/010).

- [ ] T022 [US3] Add the canary-redaction scan to `tests/orchestration/test_flow.py`: seed obviously-unique canary values as `INFRAHUB_API_TOKEN` (env) and as `token`/`password` values in a temp configuration's adapter settings; execute one successful patched flow run and one failing run; assert the canary strings appear NOWHERE in flow parameters, the returned result dict, captured/forwarded log records (bridge output), or raised exception messages/state messages. Depends on T014, T016. Trace: DBA-008; SC-005; DBR-006, DBR-012; contracts/run-result-and-errors.md §2.
- [ ] T023 [US3] Add flow-level failure tests to `tests/orchestration/test_flow.py`: in-process flow call with `operation="sync"` unconfirmed → raises `RunValidationError` (Prefect would record FAILED) with the confirm-writes message and a spy `potenda_factory`/adapter never constructed; unknown `sync_name` → `RunValidationError` naming the logical name; one execution fault (unreachable system via patched factory) → `RunExecutionError`, sanitized, and no successful result/dict produced. Depends on T014, T016. Trace: DBA-006, DBA-010 (flow-level halves); SC-004, SC-008; DBR-004, DBR-005, DBR-015.
- [ ] T024 [US3] LIVE verification (DBA-006 + DBA-007 remote form, spec edge case 1): via REST against the served deployment — (a) unconfirmed sync → run FAILED with state message explaining `confirm_writes=true` is required; destination verified unchanged (still the pre-run `InfraDevice` count); (b) `"operation": "apply"` → HTTP 409 with the `'apply' is not one of ['plan', 'sync']` detail and NO flow run created (run inventory unchanged), no `RunResult`, no log lines, no new run directory; (c) each SC-004 negative `sync_name` (`nope`, `../custom-example`, `/etc/passwd`, `a/b`, `--help`, `$(touch /tmp/pwned)`) → run FAILED naming the logical name; `/tmp/pwned` absent afterward. Evidence (run report): request/response transcripts, destination count before/after, run-inventory counts, `ls /tmp/pwned` failure. Live-environment ceiling fallback per R-3. Depends on T018. Trace: DBA-006, DBA-007; SC-004; DBR-004, DBR-005, DBR-008; research probe d₁.

**Checkpoint**: All safety refusals verified at unit, flow, and live levels.

---

## Phase 6: User Story 4 — Base stays Prefect-free; CLI parity (Priority: P4)

**Goal**: The CLI `diff` lifecycle and serial `sync --no-parallel` branch run through the
shared surface with zero user-visible change; base install/import/CLI never touch
Prefect; CLI `diff` and remote `plan` agree exactly.

**Independent Test**: Clean venv without the extra imports the package and runs CLI
sanity with Prefect absent; DBA-009 test population passes unmodified; CLI `diff` vs
remote `plan` on reset fixtures agree on status/changed/summary/fingerprint (quickstart
Scenarios 0 and 4; DBA-001/009).

- [ ] T025 [US4] Refactor `diff_cmd` in `infrahub_sync/cli.py` to delegate to `execution.execute_run`: keep the existing `get_instance` resolution (name OR config_file — CLI-only flexibility) and `adapter_path` merging in the command body; call `execute_run(sync_instance, operation="plan", confirm_writes=False, branch=..., show_progress=..., verbosity=..., run_id=..., concurrent_load=..., full_extract=..., potenda_factory=get_potenda_from_instance)` passing the CLI module-global factory (keeps existing patches on `infrahub_sync.cli.get_potenda_from_instance` effective); map `RunValidationError`/`RunExecutionError` back to `print_error_and_abort` preserving today's exact messages ("Failed to initialize the Sync Instance: ...") and exit behavior; log lines, `run.json` contents, and exit codes byte-identical to today. Depends on T007. Trace: DBR-007, DBR-009; DBA-009; contracts/execution-surface.md "Caller obligations".
- [ ] T026 [US4] Refactor the serial branch of `sync_cmd` in `infrahub_sync/cli.py` (the `else` branch taken by `--no-parallel`, or `--parallel` with explicit `order:`) to delegate to `execute_run(sync_instance, operation="sync", confirm_writes=True, print_diff=<--diff>, allow_rowcount_drop=..., continue_on_error=..., ...)` — the explicit human CLI invocation IS the confirmation; the `--parallel ignored` warning and the parallel `sync_in_tiers` branch remain in `cli.py` untouched (spec Out of Scope). Same error-mapping and behavior-preservation obligations as T025. Depends on T025 (same file). Trace: DBR-007, DBR-009; DBA-009; spec In Scope / Out of Scope.
- [ ] T027 [US4] Run the DBA-009 existing targeted test population UNMODIFIED and prove it: `uv run pytest -q tests/test_cli_full_extract.py tests/test_cli_parallel.py tests/cache/test_cli_sync_cache.py tests/test_logging.py` all green, plus `git diff --stat 9edc1bc -- tests/test_cli_full_extract.py tests/test_cli_parallel.py tests/cache/test_cli_sync_cache.py tests/test_logging.py` empty. Evidence (run report): pytest output and the empty diff. Depends on T025, T026. Trace: DBA-009 (first half); DBR-009; spec informed default (test population).
- [ ] T028 [P] [US4] Create `tests/test_no_prefect_import.py`: after importing `infrahub_sync`, `infrahub_sync.cli`, `infrahub_sync.execution` and invoking CLI sanity in-process (typer runner: `--help`, `list --directory examples/`), assert `not any(m == "prefect" or m.startswith("prefect.") for m in sys.modules)`; also assert `infrahub_sync/execution.py` imports nothing from `infrahub_sync.orchestration` and nothing in the base package imports `infrahub_sync.orchestration` (import-graph rule). Depends on T005 (and passes regardless of the extra being installed). Trace: DBA-001; SC-006; DBR-010; contracts/prefect-flow.md §6.
- [ ] T029 [P] [US4] Create `tests/test_execution_cli_parity.py`: fixture-level paired comparison using the patched-factory fixture pattern from the existing CLI tests — run the CLI `diff` path (typer runner) and `run_remote_request(..., config_directory=...)` against reset copies of the same fixture (fresh run ids, unmodified source), then assert equal `status`, `changed`, per-action `summary`, and `compute_plan_fingerprint(cli_run_dir) == compute_plan_fingerprint(remote_run_dir)` via the shared helper (never a reimplementation). Depends on T004, T008, T025. Trace: DBA-009 (second half, automated); SC-007; DBR-007.
- [ ] T030 [US4] LIVE verification (DBA-001, SC-006 — quickstart Scenario 0): build a clean venv WITHOUT the extra (`uv venv /tmp/base-venv && uv pip install -p /tmp/base-venv/bin/python .`), run the import + `sys.modules` assertion script, `/tmp/base-venv/bin/infrahub-sync --help`, and `/tmp/base-venv/bin/infrahub-sync list --directory examples/`; `prefect` must not be importable in that venv (any import fails loudly). Evidence (run report): transcript showing all commands succeed and the assertion passes. Depends on T025, T026, T028. Trace: DBA-001; SC-006; DBR-010.
- [ ] T031 [US4] LIVE verification (DBA-009 paired comparison, SC-007 — quickstart Scenario 4): on reset copies of the qualified fixture (destination returned to zero `InfraDevice` objects before each compared run, fresh run ids, MockDB unmodified), run `uv run infrahub-sync diff --name custom-example --directory examples/` and one remote `operation=plan` run; compare `status`, `changed`, per-action summary, and `compute_plan_fingerprint` over both run directories. Evidence (run report): both run-dir paths, both `run.json` files, the fingerprint-equality output. Live-environment ceiling fallback per R-3. Depends on T019, T025, T029. Trace: DBA-009; SC-007; DBR-007, DBR-009.

**Checkpoint**: Seam complete with three real callers; base install provably Prefect-free;
CLI ≡ remote parity proven.

---

## Phase 7: User Story 5 — Reproducible example + docs (Priority: P5)

**Goal**: One shipped example lets a developer unfamiliar with the implementation
install, load the schema, configure, serve, remotely invoke, and inspect the preview
without reading package source.

**Independent Test**: A clean-context walkthrough following only
`examples/prefect_remote_run/README.md` reproduces the qualified plan demonstration
(quickstart Scenario 6; DBA-002/011).

- [ ] T032 [P] [US5] Author `examples/prefect_remote_run/schemas/infra_device.yml` — a loadable Infrahub schema YAML defining `InfraDevice` with attributes `name` and `type` (matching the `examples/custom_adapter/config.yml` schema_mapping and the lab schema per R-3; avoid the deprecated `display_labels`/`default_filter` keys the lab warned about); include the load command in the README (T034). This is the R-3 enabling work — no loadable `InfraDevice` schema YAML ships in the repository today. Trace: R-3; DBR-013; DBA-002; spec In Scope (schema authoring).
- [ ] T033 [P] [US5] Author the REST request corpus under `examples/prefect_remote_run/requests/`: documented request bodies (JSON files with the target endpoint noted) for at least (1) create a plan flow run, (2) create a confirmed sync flow run, (3) get flow-run state (`GET /api/flow_runs/{id}`), (4) read run logs (`POST /api/logs/filter` with `flow_run_id` filter) — matching `contracts/prefect-flow.md` §5 exactly; any credential-shaped value is an obviously fake placeholder. These bodies are the corpus DBA-008 scans. Trace: DBR-011, DBR-013; DBA-008 (corpus definition); US5 acceptance scenario 2.
- [ ] T034 [US5] Author `examples/prefect_remote_run/README.md`: explicit machine prerequisites (Python 3.10–3.13, the pinned `prefect==3.5.0` extra, a reachable Infrahub with the `InfraDevice(name, type)` schema); install (`pip install 'infrahub-sync[prefect]'`); schema loading using `schemas/infra_device.yml`; runner configuration (`INFRAHUB_SYNC_CONFIG_DIRECTORY`, credentials via `INFRAHUB_ADDRESS`/`INFRAHUB_API_TOKEN` runner-environment variables only, values shown as obviously fake placeholders); `prefect server start`; `python -m infrahub_sync.orchestration.serve`; remote invocation and inspection walking through the `requests/` corpus; the MANDATORY trusted-development-environment caveat (the default self-hosted server must never be exposed to the public internet); no real credentials anywhere. Depends on T032, T033 (and functionally on Phase 3). Trace: DBR-013; DBA-011; SC-009; US5 acceptance scenarios; spec Constraints (caveat ownership).
- [ ] T035 [US5] Add one reference docs page `docs/docs/reference/prefect-remote-run.mdx` for the optional Prefect preview (plan: "one reference page"): the optional extra and its pins (D005/D006 rationale, one line each), the flow's four parameters and defaults, `INFRAHUB_SYNC_CONFIG_DIRECTORY`, the `RunResult` fields, the confirm-writes gate, the trusted-environment caveat, and a pointer to `examples/prefect_remote_run/`; register the page in `docs/sidebars.ts`; add a short cross-link from `docs/docs/orchestration.mdx` (which already discusses Prefect-as-scheduler); lint with `uv run rumdl check .`. Depends on T034. Trace: DBR-013 (user-facing docs governance — AGENTS.md Documentation); DBR-003, DBR-015 (documented contract); D005, D006.
- [ ] T036 [US5] Example hygiene scan (DBA-008 corpus half + completion condition): scan `examples/prefect_remote_run/` (README, `requests/`, `schemas/`) proving no real credential, no seeded canary value, and that every credential-shaped string is an obviously fake placeholder; preferably as an automated test in `tests/orchestration/test_flow.py` (or a standalone `tests/test_example_hygiene.py`) that greps the example tree for the canary set and non-placeholder token patterns. Depends on T033, T034. Trace: DBA-008; SC-005; DBR-006; spec Completion Conditions ("The example contains no real credentials").
- [ ] T037 [US5] LIVE verification (DBA-011, SC-009): a clean-context walkthrough — an operator or fresh agent session with no exposure to the implementation — follows ONLY `examples/prefect_remote_run/README.md` on a machine meeting its prerequisites and reproduces the qualified plan demonstration (server + served deployment start from the documented commands, remote plan of five creates, state and log inspection), with zero references to package source. Evidence (run report): the walkthrough transcript, an attestation that only the README was consulted, and the reproduced plan summary. Live-environment ceiling fallback per R-3. Depends on T034, T018. Trace: DBA-011, DBA-002; SC-009; DBR-013.

**Checkpoint**: Example reproduces the preview end-to-end for a stranger.

---

## Phase 8: Polish, governance gates, and delivery evidence

**Purpose**: Repository-governance gates (format → lint → ty → tests → CLI sanity →
docs), scope audit, and collated traceability evidence.

- [ ] T038 [P] Scope audit (DBR-014): inspect the final diff (`git diff 9edc1bc --stat` and the new modules) confirming none of backlog B-001–B-007 shipped — no custom HTTP service or Sync-shaped REST resource model, no saved-plan browse/approve/apply-by-run-ID remote workflow, no per-stage Prefect tasks, no work pools/workers/retries/PostgreSQL/Redis-for-prefect/Kubernetes, no schedules/triggers/notifications, no production auth/HA claims, no custom operator UI, no configuration registration/versioning, prefect absent from base `dependencies`, parallel `sync_in_tiers` branch untouched. Record the audit in the run report. Trace: DBR-014; spec Out of Scope; Completion Conditions ("No backlog item implemented").
- [ ] T039 Run the required workflow on the final tree: `uv run invoke format` (no diffs), `uv run invoke lint` (exit 0; no NEW warnings beyond the inherited pylint `import-outside-toplevel` in `infrahub_sync/potenda/__init__.py`), `uv run ty check .` (exit 0, no `[[tool.ty.overrides]]` added, any targeted `# ty: ignore[<rule>]` carries a TODO); new/changed code is Ruff-clean and fully typed with docstrings. Trace: R-4; AGENTS.md Required Development Workflow + policy; spec Constraints; Constitution IV.
- [ ] T040 Run the full suite `uv run pytest -q` in the dev+prefect environment: no regression versus the 110-passed/3-skipped baseline (all inherited tests still pass; new unit tests pass; prefect-dependent tests skip cleanly when the extra is absent — spot-check with a base-only env if practical; live integration tests remain opt-in via `-m integration`). Record final counts in the run report. Depends on all test tasks. Trace: R-4, R-5; SC-006 (baseline clause); Constitution V.
- [ ] T041 CLI sanity on the final tree: `uv run infrahub-sync --help`, `uv run infrahub-sync list --directory examples/` (the new `examples/prefect_remote_run/` must not break discovery — it ships no `config.yml`, only README/schemas/requests), `uv run infrahub-sync generate --name from-netbox --directory examples/` followed by `git status --porcelain` empty (R-2 payoff: sanity runs leave the tree clean). Record transcripts in the run report. Trace: R-2; DBR-009; AGENTS.md CLI sanity; spec Constraints.
- [ ] T042 Collate delivery evidence in `dev/specs/001-prefect-managed-remote-run/run-report.md`: the full DBR-001–015 / DBA-001–011 traceability table below with per-item evidence pointers (test names, transcript sections); the R-4/R-5 inherited-baseline facts reported (not hidden); and — if any of T018/T019/T021/T024/T031/T037 could not run against the live environment — the live-environment ceiling record naming the affected criteria (at most DBA-002–005, DBA-008, DBA-009's paired comparison, DBA-011) with the substitute local evidence, per the spec's informed default. Depends on all prior tasks. Trace: spec Completion Conditions; R-3, R-4, R-5.

---

## Decision record D007 (this phase) — PROVISIONAL (CHECKPOINT)

- **Question**: R-1 mandates changing `uv sync` → `uv sync --extra dev` in `AGENTS.md`,
  committed alone as commit 1. `.github/copilot-instructions.md` carries the same two
  `uv sync` lines (AGENTS.md's Platform-Specific Notes require platform files to include
  the Required Development Workflow block *verbatim*). Does commit 1 include the mirror
  file, or does it drift?
- **Evidence**: `grep -n "uv sync" .github/copilot-instructions.md` → lines 18 and 26,
  identical to the AGENTS.md lines R-1 names; AGENTS.md "Platform-Specific Notes"
  section mandates verbatim inclusion; the brief's R-1 rationale is "so every later
  agent reading repository instructions gets the working command" — which includes
  Copilot agents reading the mirror. No `.cursor/rules/dev-standard.mdc` exists in this
  worktree.
- **Options**: (1) Update only `AGENTS.md` — literal R-1, but leaves the repo violating
  its own verbatim-mirror rule and later Copilot agents with the broken command;
  (2) Update `AGENTS.md` and `.github/copilot-instructions.md` together in commit 1 —
  one logical change, both instruction surfaces consistent; (3) Update the mirror in a
  separate later commit — splits one logical change and leaves a window of drift.
- **Recommendation**: Option 2 (encoded in T001).
- **Rationale**: R-1's purpose (working commands for every instruction-reading agent)
  and AGENTS.md's own verbatim-mirror governance are both satisfied; commit 1 remains a
  single isolated logical change ("fix the documented sync command"), which is the
  plain reading of "committed alone".
- **Confidence**: High.
- **Origin**: governance (repository AGENTS.md Platform-Specific Notes) intersecting a
  brief-mandated task's literal file list; no product scope affected.

---

## Traceability coverage (every DBR/DBA → owning tasks)

| ID | Owning tasks (implementation → verification) |
|---|---|
| DBR-001 | T014 → T017, T018, T019 |
| DBR-002 | T015 → T016, T018 |
| DBR-003 | T008, T014 → T016, T019, T020, T021 |
| DBR-004 | T007 → T011, T020, T023, T024 |
| DBR-005 | T006, T008, T014 → T011, T023, T024 |
| DBR-006 | T005, T008, T014 → T022, T033, T036 |
| DBR-007 | T005–T008, T014, T025, T026 → T029, T031 |
| DBR-008 | T014 (in-process call) → T011 (subprocess spies), T024 |
| DBR-009 | T025, T026 → T027, T031, T041 |
| DBR-010 | T012, T013 → T028, T030 |
| DBR-011 | T014 → T017, T019, T033 |
| DBR-012 | T014 (bridge) → T016, T019, T022 |
| DBR-013 | T032, T033, T034, T035 → T037 |
| DBR-014 | T012 (base deps untouched) → T038 |
| DBR-015 | T005, T007 → T010, T011, T020, T023 |
| DBA-001 | T028, T030 |
| DBA-002 | T015, T032 → T018 (live), T037 |
| DBA-003 | T014 → T017, T019 (live) |
| DBA-004 | T014, T016 → T017, T019 (live) |
| DBA-005 | T020 (automated analog) → T021 (live) |
| DBA-006 | T011 (spies) → T023, T024 (live, destination observation) |
| DBA-007 | T011 (parametrized negatives + spies) → T024 (live) |
| DBA-008 | T022 (canary scan: params/results/logs) + T036 (example request-body corpus) |
| DBA-009 | T027 (population unmodified) + T029 (automated parity) + T031 (live paired comparison) |
| DBA-010 | T010, T011, T020 (result schema; validation + execution faults) + T023 (flow-level) |
| DBA-011 | T034 → T037 (live clean-context walkthrough) |

Decision IDs: D001 (brief authority — header), D005/D006 (T012, T035), D007 (T001).
Spec clarifications: #1 → T004/T009; #2 → T015/T016/T018; #3 → T007/T008; #4 → T014/T016.
Enabling work: R-1 → T001; R-2 → T002, T041; R-3 → T019/T021/T024/T031/T037 fallback +
T032; R-4 → T003, T039, T040; R-5 → T003, T040.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: T001 → T002 → T003 (strict; commits 1 and 2 are bound by the plan).
- **Phase 2 (Foundational)**: after Phase 1. T004 ∥ T005; then T006 → T007 → T008;
  T009 after T004; T010 after T005; T011 after T008 and T010. BLOCKS all user stories.
- **Phase 3 (US1)**: after Phase 2. T012 ∥ T013; T014 after T008+T012+T013; T015 after
  T014; T016/T017 after T015; T018 after T015 (live); T019 after T018 (live).
- **Phase 4 (US2)**: T020 after T007 (can start with Phase 3); T021 after T019 (live,
  strictly sequential runs).
- **Phase 5 (US3)**: T022/T023 after T016; T024 after T018 (live).
- **Phase 6 (US4)**: T025 after T007; T026 after T025; T027 after T026; T028 after T005;
  T029 after T004+T008+T025; T030 after T028+T026; T031 after T019+T029 (live).
- **Phase 7 (US5)**: T032 ∥ T033 anytime after Phase 1; T034 after T032+T033 (content
  finalized only once Phase 3 commands are real); T035 after T034; T036 after T034;
  T037 after T034+T018 (live).
- **Phase 8 (Polish)**: after all desired stories. T038 ∥ early; T039 → T040 → T041 →
  T042 last.

### Live-environment chain (lab Infrahub + Prefect server; R-3)

T018 → T019 → T021 → T024 → T031 → T037 run against the live lab; each records its
evidence in the run report; any unavailability is recorded as the live-environment
ceiling (T042), never silently skipped. The three qualified-demonstration runs
(plan → confirmed sync → follow-up plan) are strictly sequential (spec edge case 5).

### Parallel Opportunities

```text
Phase 2: T004 ∥ T005 (different files); later T009 ∥ T010 ∥ (T028 from US4)
Phase 3: T012 ∥ T013 (pyproject vs new package file)
US5 prep: T032 ∥ T033 can run any time after Phase 1, alongside Phases 2–6
Polish:  T038 can run alongside the T039–T041 gate sequence
```

---

## Implementation Strategy

### MVP first (US1)

1. Phase 1 (commits 1–2 + baseline) → Phase 2 (surface + fingerprint, unit-tested)
2. Phase 3 (extra, flow, serve, tests, live plan demonstration) — **stop and validate**:
   quickstart Scenarios 0-adjacent unit tests plus Scenario 1 live evidence = MVP.

### Incremental delivery

- US2 adds the confirmed write + convergence (Scenario 2 live).
- US3 adds the safety/canary verification set (Scenario 3 live).
- US4 lands the CLI refactor last among code changes so the DBA-009 population and
  parity checks run against the finished surface (Scenarios 0 and 4).
- US5 packages everything as the example + docs (Scenario 6), then Phase 8 gates and
  the collated evidence report close the brief's completion conditions.

### Notes

- Never modify the DBA-009 test population files; T027 proves it with a diff.
- `infrahub_sync/orchestration/flow.py` must NOT use `from __future__ import
  annotations` (research F3); `infrahub_sync/execution.py` may.
- No secrets in code, examples, logs, or evidence transcripts; canary discipline per
  DBA-008. Credentials only via runner environment (`INFRAHUB_ADDRESS`,
  `INFRAHUB_API_TOKEN`), re-derived on the lab host per R-3 if absent.
- No git operations are performed by task execution itself; the root orchestrator owns
  all commits at the marked boundaries.
