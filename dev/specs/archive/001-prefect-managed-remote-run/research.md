# Research: Prefect-Managed Remote Infrahub Sync Run (Developer Preview)

**Status**: COMPLETE. Phase 0 initially STOPPED on a failed falsifying probe (the brief's
`prefect==3.7.2` pin is unsatisfiable); the root orchestrator issued **D005** (pin
`prefect==3.5.0`, provisional pending the gate), the spec was remediated, and the
remaining probes were re-run and passed against 3.5.0. All probe processes were started
and stopped inside the session scratchpad; port 4200 was verified free before and after;
the lab Infrahub at `localhost:8000` was never contacted.

> **Gate outcome (2026-07-30, Blake Ellis)** — D005 was **ratified as option D**, an
> override of the option B recorded below: the extra pins `prefect==3.8.1` and the BASE
> dependency set changes (`diffsync[redis]>=2.1,<3.0` → `diffsync>=2.1,<3.0` +
> `redis>=4.3,<9`). **D006 is SUPERSEDED / WITHDRAWN** — no companion pins. Everything
> below the F1/F2/F3 headings and in the probe table is the **historical Phase 0 record
> against 3.5.0** and is deliberately preserved unchanged (append-only convention, E20);
> the binding values now live in "Gate-ratified resolution (D005 option D)" below, plus
> `spec.md` §Constraints/§Assumptions, `plan.md` §Technical Context,
> `contracts/prefect-flow.md` §1, and tasks T012/T012a.

**Date**: 2026-07-30
**Feature**: `dev/specs/001-prefect-managed-remote-run` (branch `001-prefect-managed-remote-run-local-dp-001`)
**Probe environment**: `uv 0.7.6`, Python 3.12.2 (macOS/darwin), fresh venv under the
session scratchpad (`scratchpad/probe/venv`), `PREFECT_HOME` inside the scratchpad,
Prefect server on `127.0.0.1:4200` only.

## Finding F1 (BLOCKING, resolved by D005): Prefect 3.7.2 cannot install alongside the base dependency set

The brief and original spec fixed Prefect **3.7.2** as the optional extra's pinned
version. That constraint is **factually unsatisfiable** against `infrahub-sync`'s base
dependencies. Chain (verified from primary PyPI metadata, resolver-independent):

- `infrahub-sync` base (`pyproject.toml`) → `diffsync[redis]>=2.1,<3.0`
- every available `diffsync` 2.x `redis` extra → `redis>=4.3,<5.0` (2.1.0 and 2.2.3
  verified; 2.2.0–2.2.2 are yanked)
- `prefect==3.7.2` → `pydocket>=0.19.0` (unconditional, not extra-gated)
- every `pydocket` release `0.13.0b2`–`0.23.1` → `redis>=5`

`redis<5.0` and `redis>=5` cannot coexist, so `pip install infrahub-sync[prefect]`
pinned to 3.7.2 fails for every user. Additional scoping: `pydocket` entered prefect at
**3.6.0**, so every prefect ≥ 3.6.0 is incompatible; the newest pydocket-free final
release is **3.5.0**. The base package cannot drop redis: `infrahub_sync/utils.py:11`
imports `diffsync.store.redis.RedisStore` unconditionally.

**Resolution as recorded in Phase 0 — D005 repair path B (provisional)**: the optional
extra pins `prefect==3.5.0`. Path A (restructuring base deps to keep 3.7.2 by allowing
redis ≥5) was rejected: it ships a redis major bump to every existing user inside a
preview. Origin: brief-gap/instance against the brief's Dependencies section (its "Prefect
3.7.2 — Available" satisfaction evidence was falsified at the install boundary; VAL-6 had
supplied prefect via a `uv run --with` overlay, unpinned, so it never exercised
extra-vs-base resolution).

**Resolution as RATIFIED at the gate (2026-07-30, Blake Ellis) — D005 option D, which
SUPERSEDES path B above**: the extra pins `prefect==3.8.1` (current latest) and the base
declares redis directly with a **permissive** floor — `diffsync>=2.1,<3.0` +
`redis>=4.3,<9`. This is not path A: path A was read as "allow redis ≥5", which forces a
major bump on existing users; `>=4.3,<9` forces nothing (an existing environment on redis
4.6 stays valid) while permitting the redis 8.x that `pydocket` requires. The conflict
chain above is unchanged and is exactly what makes the base change necessary; because
diffsync has no 3.x release and no open PR raising its `<5.0` extra cap, this override is
permanent rather than a wait-for-upstream stopgap. Spec Constraints and Assumptions carry
the ratified D005 text. Full evidence: "Gate-ratified resolution (D005 option D)" below.

## Finding F2: prefect 3.5.0 needs two companion pins in the extra to work at all (→ D006)

Two packaging defects in prefect 3.5.0 itself were found by running it, and both are
repaired by companion constraints in the optional extra (no base-dependency change):

1. **Missing `importlib_metadata` dependency.** prefect 3.5.0 (requires-python >=3.10)
   dropped its `importlib-metadata>=4.4; python_version < "3.10"` declaration (present
   through 3.4.x) but `prefect/workers/base.py:26` still does
   `from importlib_metadata import ...` unconditionally. Observed: `prefect server start`
   crashes with `ModuleNotFoundError: No module named 'importlib_metadata'`. Installing
   `importlib-metadata>=4.4` fixes CLI import (verified).
2. **fastapi upper bound too loose.** prefect 3.5.0 declares `fastapi<1.0.0,>=0.111.0`;
   a fresh resolve today picks fastapi 0.141.1 / starlette 1.3.1, under which the running
   server returns **500** on (at least) `GET /api/deployments/name/{flow}/{deployment}`
   with `AttributeError: 'PrefectRouter' object has no attribute 'routes'`. Pinning
   fastapi to **0.120.4** (the release current on prefect 3.5.0's upload date,
   2025-10-31; brings starlette 0.49.3) fixes it (verified end-to-end). The exact break
   point between 0.120.4 and 0.141.1 was not bisected; the extra therefore uses the
   evidence-backed ceiling `fastapi>=0.111,<0.121`.

See decision record **D006** below.

> **SUPERSEDED (2026-07-30, gate)**: both defects above are properties of prefect **3.5.0**
> only, and both are verified fixed at the ratified `prefect==3.8.1` — the
> `importlib_metadata` import now resolves through a stdlib alias in
> `prefect/utilities/compat.py`, and the deployment-name route returns HTTP 200 under the
> freshly-resolved fastapi 0.141.1 / starlette 1.3.1. F2 therefore no longer describes any
> shipped constraint; it is kept as the record of why 3.5.0 alone was not viable. The extra
> is exactly `prefect = ["prefect==3.8.1"]`.

## Finding F3: the packaged flow module must not use `from __future__ import annotations`

With deferred (stringified) annotations in the flow's defining module, prefect 3.5.0's
run-time parameter validation (`Flow.validate_parameters` →
`ValidatedFunction.model_rebuild`) fails with
`PydanticUndefinedAnnotation: name 'Literal' is not defined`, and the flow run ends
FAILED before the body runs (observed on run `4d1ce354`). Removing the future import
fixes it. Design consequence: `infrahub_sync/orchestration/flow.py` uses real (non-
deferred) annotations; this is repo-atypical and gets an explanatory comment. Native
`str | None` syntax is still fine (Python floor is 3.10).

## Probe table

Per the falsifying-probe protocol: assumption, library+version, exact command, observed
result, affected decision/task. Commands ran in `scratchpad/probe/` with
`PREFECT_HOME=scratchpad/prefect-home`, `PREFECT_API_URL=http://127.0.0.1:4200/api`.

| # | Assumption | Version(s) | Exact command / test | Observed result | Affected decision |
|---|---|---|---|---|---|
| a₁ | `prefect==3.7.2` installs alongside the base dependency set (original mandate) | prefect 3.7.2, infrahub-sync 2.0.1, uv 0.7.6, py3.12 | `uv pip install "prefect==3.7.2" <repo>` | **FAILED** — no solution: redis<5.0 (diffsync[redis]) vs redis>=5 (pydocket) | F1 → STOP → **D005** |
| a₂ | Conflict is metadata-real, not resolver-specific | PyPI JSON API | `curl pypi.org/pypi/{prefect/3.7.2,pydocket/0.13.0b2,pydocket/0.19.0,pydocket/0.23.1,diffsync/2.2.3}/json` | prefect→`pydocket>=0.19.0` unconditional; all pydocket→`redis>=5`; diffsync[redis]→`redis<5.0,>=4.3`; pydocket enters prefect at 3.6.0 | F1 evidence; scopes D005 to 3.5.0 as newest resolvable |
| a₃ | `prefect==3.5.0` (D005) installs alongside the base dependency set | prefect 3.5.0, redis 4.6.0, diffsync 2.2.3 | `uv pip install "prefect==3.5.0" <repo>` then import `prefect`, `redis`, `diffsync`, `infrahub_sync` | **PASS** — resolves and imports; versions as listed | D005 confirmed viable |
| a₄ | prefect 3.5.0 CLI starts out of the box | prefect 3.5.0 | `prefect server start --host 127.0.0.1 --port 4200` | **FAILED** — `ModuleNotFoundError: importlib_metadata` (declared through 3.4.x, dropped in 3.5.0, still imported in `workers/base.py:26`) | F2 → **D006** companion pin `importlib-metadata>=4.4` (fix verified) |
| a₅ | prefect 3.5.0 server works with freshly-resolved fastapi | fastapi 0.141.1 / starlette 1.3.1 | serve flow → `GET /api/deployments/name/probe-infrahub-sync/probe-deployment` | **FAILED** — HTTP 500, `AttributeError: 'PrefectRouter' object has no attribute 'routes'` | F2 → **D006** companion pin `fastapi>=0.111,<0.121` |
| a₆ | fastapi 0.120.4 (contemporary with 3.5.0) repairs the server | fastapi 0.120.4 / starlette 0.49.3 | `uv pip install "fastapi==0.120.4"`; restart server + serve | **PASS** — deployment READY, `enforce_parameter_schema: true`, `operation` schema `{"enum": ["plan","sync"], "default": "plan", "type": "string"}` | D006 ceiling evidence |
| b | A served deployment can be created and run via REST | prefect 3.5.0 + D006 pins | `POST /api/deployments/{id}/create_flow_run` body `{"parameters": {"sync_name": "custom-example", "operation": "plan"}}`; poll `GET /api/flow_runs/{id}` | **PASS** — run id returned synchronously (state SCHEDULED), reached COMPLETED in ~7 s (run `8b1133bd`) | DBR-001/002/011, DBA-002/003, SC-001 mechanism verified |
| c₁ | Run-scoped logging-handler bridging lands package-logger lines in the Prefect run log | prefect 3.5.0 | Flow attaches a `logging.Handler` to logger `probe_pkg` forwarding each record to `get_run_logger()`; child logger `probe_pkg.potenda` emits INFO; handler removed in `finally`; `POST /api/logs/filter` `{"logs": {"flow_run_id": {"any_": ["<id>"]}}}` | **PASS** — server-side log contains `probe_pkg.potenda \| BRIDGE-CANARY lifecycle log line for custom-example` (7 records total) | DBR-012, SC-002 mechanism verified (spec clarification #4) |
| c₂ | Flow-body log lines also visible via REST | prefect 3.5.0 | same logs/filter call | **PASS** — `FLOW-BODY-EXECUTED operation=plan sync_name=custom-example` present | DBA-004 |
| d₁ | Bad `operation` values are rejected before the flow body runs | prefect 3.5.0 | `POST /api/deployments/{id}/create_flow_run` body `{"parameters": {"sync_name": "custom-example", "operation": "apply"}}` | **PASS (strong form)** — HTTP **409**, body `{"detail": "Error creating flow run: Validation failed for field 'operation'. Failure reason: 'apply' is not one of ['plan', 'sync']"}`; **no flow run created at all** (server run inventory unchanged at 2) | Spec edge case 1; DBR-003; `enforce_parameter_schema` defaults true for served deployments |
| d₂ | (Corollary of F3) run-time parameter validation precedes the flow body | prefect 3.5.0 | first `create_flow_run` before the F3 fix | Run `4d1ce354` FAILED with `Validation of flow parameters failed ... PydanticUndefinedAnnotation`; no body log lines emitted | F3; demonstrates validation-precedes-body for run-time validation too |

**Shutdown evidence**: after probes, serve process and server killed
(`pkill -f probe_flow.py`; `kill <listener pids>`); `lsof -iTCP:4200 -sTCP:LISTEN`
empty; `pgrep -fl "prefect|probe_flow"` empty.

## Decision records

### D005 (root orchestrator — recorded here for traceability)

- **Decision**: The optional extra pins `prefect==3.5.0` instead of the brief's 3.7.2.
- **Status**: **RATIFIED — but OVERRIDDEN to option D** (checkpoint gate, Blake Ellis,
  2026-07-30). The recorded decision and options below are left exactly as written; the
  gate did not pick any of A/B/C.
- **Origin**: brief-gap/instance (brief Dependencies section; its "Prefect 3.7.2 —
  Available" evidence falsified by probe a₁/a₂; repair level: version substitution
  within the same major, no base-dependency change).
- **Evidence**: probe a₁/a₂ (conflict), a₃ (3.5.0 resolves), root's independent
  re-verification.
- **Options**: A — keep 3.7.2, restructure base deps to allow redis≥5 (rejected: redis
  major bump for every existing user inside a preview); B — pin 3.5.0 (chosen); C —
  return brief for intake (not needed given B).
- **Gate note (2026-07-30, Blake Ellis)**: the human **overrode this record to option D**,
  a fourth option produced by gate research and not present in the list above: pin
  `prefect==3.8.1` (current latest) in the extra AND declare redis directly in the base
  with a **permissive** floor — `diffsync>=2.1,<3.0` + `redis>=4.3,<9`, deliberately NOT
  `redis>=5`. Option D differs from the rejected option A precisely in that floor: it
  forces no upgrade on any existing installation and keeps a downstream `diffsync[redis]`
  requirement resolvable (verified at redis 4.6.0; `redis>=5` makes it unsatisfiable),
  while still admitting the redis 8.x `pydocket` needs. **D006 is superseded as a
  consequence** — its companion pins repaired 3.5.0-only defects. Accepted risk: 3.8.1 was
  released the same day, so it has zero field exposure; the human accepted this, mitigated
  by direct behavioral probes rather than elapsed time. Evidence: "Gate-ratified resolution
  (D005 option D)" below.

### D006 — Optional-extra composition: companion pins repairing prefect 3.5.0 packaging defects — SUPERSEDED / WITHDRAWN (gate, Blake Ellis, 2026-07-30)

- **Question**: What exactly does the `prefect` optional extra declare, given that
  `prefect==3.5.0` alone is broken out of the box (probes a₄, a₅)?
- **Evidence**: probe a₄ (missing `importlib_metadata` → server CLI crash; fix
  verified), a₅/a₆ (fastapi 0.141/starlette 1.3 → HTTP 500 on deployment routes;
  fastapi 0.120.4/starlette 0.49.3 verified working end-to-end through probes b–d).
- **Options**:
  1. Extra = `prefect==3.5.0` only — **rejected**: DBA-002's "documented commands"
     fail immediately on a fresh install (crash at `prefect server start`, 500s at
     serve time).
  2. Extra = `prefect==3.5.0` + `importlib-metadata>=4.4` + `fastapi>=0.111,<0.121`
     — **chosen**: minimal, evidence-backed, entirely inside the optional extra (base
     install untouched; DBA-001 unaffected).
  3. Bisect fastapi to the true break point and use a wider ceiling — rejected for the
     preview: cost without benefit; `<0.121` is the verified ceiling and can be widened
     later with evidence.
- **Recommendation / Decision**: option 2. The extra reads:
  `prefect = ["prefect==3.5.0", "importlib-metadata>=4.4", "fastapi>=0.111,<0.121"]`
  with a comment naming both upstream defects.
- **Rationale**: DBA-002 requires the documented commands to work from a clean
  `pip install infrahub-sync[prefect]`; both pins repair defects in the pinned prefect
  version itself, not in this package; neither touches the base dependency set.
- **Confidence**: High for correctness of the repairs (each verified by a before/after
  probe); medium for the fastapi ceiling's tightness (deliberately conservative).
- **Origin**: inherent (third-party packaging defects discovered by mandated probes);
  consequence of D005.
- **Status / gate note (2026-07-30, Blake Ellis)**: **SUPERSEDED / WITHDRAWN.** The
  question, evidence, options and decision above are left as written; they were correct for
  `prefect==3.5.0`. With D005 ratified as option D (`prefect==3.8.1`), both defects are
  verified fixed upstream (`importlib_metadata` resolves via the stdlib alias in
  `prefect/utilities/compat.py`; the deployment-name route returns HTTP 200 under fastapi
  0.141.1 / starlette 1.3.1), so **both companion pins are removed** and the extra is
  exactly `prefect = ["prefect==3.8.1"]`. Nothing in this record is implementable any more.

## Gate-ratified resolution (D005 option D) — 2026-07-30

Root-verified at the checkpoint gate against the real patched `pyproject.toml`. This
section, not F1/F2 or the 3.5.0 probe table, is the binding evidence for the shipped
dependency declarations.

**Declarations.** Base: `"diffsync[redis]>=2.1,<3.0"` → `"diffsync>=2.1,<3.0"` +
`"redis>=4.3,<9"`. Extra: `prefect = ["prefect==3.8.1"]` (single pin).

**Resolved set** (real patched pyproject): prefect 3.8.1, redis 8.1.0, fastapi 0.141.1,
starlette 1.3.1, pydantic 2.13.4, diffsync 2.2.3, infrahub-sdk 1.22.2, pyarrow 21.0.0,
typer 0.27.0, uvicorn 0.52.0, griffe 2.1.0, pydocket 0.23.1.

| Question | Verified result |
|---|---|
| Why must the base change at all? | `diffsync[redis]` → `redis>=4.3,<5.0`; `prefect>=3.6` → `pydocket>=0.19.0` → `redis>=5`. Unsatisfiable together. diffsync has no 3.x and no open PR raising the cap → the override is permanent, not a stopgap. |
| Is diffsync's `<5.0` cap real? | No — stale. Its redis store works on redis-py 4.6.0, 5.0, 6.4, 7.0, 8.1.0: **26/26 functional checks** against a redis 8.4.0 server, including `diff_from`/`diff_to`/`sync_from` between two Redis-backed adapters; the 4.6.0 control and 8.1.0 results are byte-identical. Store's API surface: `Redis()`, `from_url()`, `ping/get/set/exists/delete/scan_iter`. |
| Why `>=4.3` and not `>=5`? | With `redis>=4.3,<9` a downstream consumer that requires `diffsync[redis]` still resolves (redis 4.6.0); with `redis>=5` that combination is unsatisfiable. The permissive floor is the point of option D. |
| Why declare redis directly? | `infrahub_sync/utils.py:11` imports `diffsync.store.redis.RedisStore` unconditionally, so the client must exist in every install. Run-time use is opt-in: `utils.py:195-205` defaults to `LocalStore()` and builds `RedisStore` only when a config sets `store.type == "redis"`; no shipped example enables it. Import compatibility is therefore what matters for all users — and the repo suite has **zero** redis coverage, which is why T012a exists. |
| Do prefect 3.8.1's load-bearing behaviors hold? | Yes. Served-deployment REST run creation → **COMPLETED**. Run-scoped stdlib-logging bridging retrievable via `POST /api/logs/filter` with **levels preserved**. Invalid `operation` → **HTTP 409**, no flow run created. |
| Are D006's two defects gone? | Yes. `importlib_metadata` resolves through the stdlib alias in `prefect/utilities/compat.py`; the deployment-name route returns **HTTP 200** under fastapi 0.141.1. Both companion pins removed. |
| Test-suite impact | **111 passed / 2 skipped** under the upgraded set — zero delta from baseline (same 113-test total, zero failures). |
| `ty` impact | Installing prefect pulls `prometheus-client`, resolving the optional import that `infrahub_sync/adapters/prometheus.py:10`'s `# ty: ignore[unresolved-import]` suppresses → one EXPECTED `unused-ignore-comment` warning in the prefect-synced environment, while the comment stays NECESSARY without prefect. The adapter file is not edited (out of scope); T039 records the warning and names the cause. |
| Residual risk (human-accepted) | prefect 3.8.1 was released the same day → zero field exposure. Mitigated by the direct behavioral probes above rather than by elapsed time. |

## Non-probe research decisions

| Topic | Decision | Rationale | Alternatives considered |
|---|---|---|---|
| Execution-surface module | New `infrahub_sync/execution.py`: `RunResult`, `RunValidationError`, `RunExecutionError`, `resolve_sync_instance()`, `execute_run()`, `run_remote_request()` | One import-light module, no Prefect import, callable by CLI/flow/tests; matches "narrow typed surface" (DBR-007) | Splitting types/engine into two modules (more surface, no benefit); putting it in `utils.py` (already broad, would blur the seam) |
| Test-compat seam | `execute_run(..., potenda_factory=...)`; the CLI passes its module-global `get_potenda_from_instance` | The DBA-009 test population patches `infrahub_sync.cli.get_potenda_from_instance` (see `tests/test_cli_parallel.py`, `tests/test_cli_full_extract.py`, `tests/cache/test_cli_sync_cache.py`); passing the CLI's own global keeps those patches effective **unmodified** | Having the surface import the factory directly (breaks the existing patches → violates DBA-009's "pass unmodified") |
| Prefect integration package | New `infrahub_sync/orchestration/` (`flow.py`, `serve.py`); serve via `python -m infrahub_sync.orchestration.serve` | Imports prefect only when explicitly used (DBR-010); `python -m` avoids installing a console script into Prefect-free base installs; name avoids shadowing confusion with a `prefect` subpackage | Console-script entry point (present in base installs, crashes without the extra — muddies DBA-001's story); `infrahub_sync/prefect/` (name-shadowing confusion) |
| Log bridging | Handler attached to `logging.getLogger("infrahub_sync")` in the flow body before `run_remote_request`, forwarding each record to `get_run_logger()` (level INFO), removed in `finally` | Probe c₁ verified server-side visibility incl. child loggers; matches spec clarification #4 (no operator env vars needed) | Setting `PREFECT_LOGGING_EXTRA_LOGGERS` (operator-dependent, explicitly excluded by the clarification) |
| Fingerprint helper | `infrahub_sync/cache/fingerprint.py`: `compute_plan_fingerprint(run_dir: Path) -> str` per the spec's canonical definition | plan.parquet I/O lives in `infrahub_sync/cache/`; single shared helper (spec clarification #1) | In `execution.py` (would drag pyarrow into the seam module needlessly) |
| Config-directory env var | `serve.py` validates `INFRAHUB_SYNC_CONFIG_DIRECTORY` (present + is a directory) at startup, exits non-zero naming the variable otherwise; the flow body re-reads the inherited env var per run and re-resolves contents | Matches spec clarification #2 (fail at serve start; contents re-resolved per run) | Baking the path into deployment parameters (remotely overridable — violates DBR-005/DBR-006) |
| Flow return value | The flow returns `dataclasses.asdict(RunResult)` | JSON-friendly for Prefect state/result surfaces; the typed `RunResult` contract is asserted at the execution surface (DBA-010) | Returning the frozen dataclass itself (works in-process but serializes less predictably) |
| Secret redaction | `execution.py` collects candidate secret values (env `INFRAHUB_API_TOKEN`; values of `token`/`password`/`secret`/`api_key` keys in resolved adapter settings) and replaces occurrences in outgoing exception messages with `***` | Implements the failure contract's "sanitized" obligation mechanically; verifiable by DBA-008 canary scan | Regex-based heuristics (false negatives on unknown formats; value-based replacement is exact for the seeded canary) |

### Round-2 notes on the rows above (2026-07-30; append-only — the original probe records are left as written, E20)

These three rows record the mechanisms as first designed. Where a later finding
sharpened a mechanism, the **binding** version now lives in the contracts; the row
above is kept for the rationale and the alternatives it rejected, not as the
implementation instruction.

- **Log bridging** — superseded in mechanism by **E4** (inside D004): attaching the
  handler is not sufficient, because a handler never defeats `Logger.isEnabledFor`
  and the `infrahub_sync` hierarchy is level-`NOTSET` outside the CLI. The flow now
  owns the source logger's **LEVEL as well as the handler**: capture
  `logging.getLogger("infrahub_sync").level`, set `logging.INFO` before the surface
  call, restore the captured level in the same `finally` that removes the handler.
  Binding text: `contracts/prefect-flow.md` §2 steps 1–2 and §4; tasks T014/T016.
  The rejected alternative (`PREFECT_LOGGING_EXTRA_LOGGERS`) is unchanged.
- **Secret redaction** — superseded in scope by **E10** and **E5**. E10: the
  candidate set is collected by env-variable **NAME pattern** — at minimum
  `INFRAHUB_API_TOKEN`, plus the value of every variable whose name matches
  `*_TOKEN`/`*_PASSWORD`/`*_SECRET`/`*_API_KEY` (DBR-006 routes adapter credentials
  such as `NETBOX_TOKEN` through the runner environment, outside the resolved
  settings) — in addition to the secret-valued settings keys the row names. E5:
  redaction covers the **WHOLE cause chain** at the wrap point, not just the wrapper
  message, because a traceback renders every `__cause__`/`__context__` message.
  Binding text: `contracts/run-result-and-errors.md` §2; tasks T005/T010/T022.
- **Flow return value** — superseded in mechanism by **X15**: `dataclasses.asdict`
  deep-copies field values and the E14 `summary` mappingproxy is not deep-copyable
  (`TypeError: cannot pickle 'mappingproxy' object`, root-probed). The flow returns
  an **asdict-SHAPED** seven-key dict built by explicit construction instead. Binding
  text: `contracts/prefect-flow.md` §2 step 4; `contracts/run-result-and-errors.md`
  §1 point 5; tasks T014. The row's rationale (JSON-friendliness) and rejected
  alternative (returning the frozen dataclass) are unaffected.

## Version facts (installed and verified in the Phase 0 probe venv — HISTORICAL)

> **Superseded (2026-07-30, gate)**: this table records the 3.5.0-era probe venv. The
> shipped versions are in "Gate-ratified resolution (D005 option D)" above — prefect 3.8.1,
> redis 8.1.0, fastapi 0.141.1, no `importlib-metadata` or `fastapi` pin at all.

| Package | Version | Why it matters |
|---|---|---|
| prefect | 3.5.0 | D005 pin; newest prefect 3 resolvable next to `diffsync[redis]` |
| importlib-metadata | latest (>=4.4) | D006 repair for prefect 3.5.0's missing declaration |
| fastapi / starlette | 0.120.4 / 0.49.3 | D006 repair for prefect 3.5.0's loose `<1.0` bound |
| redis | 4.6.0 | Proof the base `diffsync[redis]` constraint is honored unchanged |
| diffsync | 2.2.3 | Base dependency, unchanged |
| infrahub-sync | 2.0.1 (local) | Installed from the working tree |
