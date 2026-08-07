# Contract: Prefect Flow, Serve Entrypoint, and REST Interaction

Owner: `infrahub_sync/orchestration/` — the ONLY package that imports `prefect`
(DBR-010). Pinned to `prefect==3.8.1` per **D005 option D** (RATIFIED at the checkpoint
gate, Blake Ellis, 2026-07-30); the extra carries **no companion pins** — D006 is
superseded, its two prefect-3.5.0 packaging defects verified fixed at 3.8.1 (see
research.md F1/F2 and "Gate-ratified resolution"). All behaviors below were probe-verified
against 3.5.0 (research.md probe table rows b, c₁, c₂, d₁) and re-verified directly
against 3.8.1 at the gate.

## 1. Dependency declarations (`pyproject.toml`) — D005 option D

```toml
[project]
dependencies = [
    # ...unchanged entries...
    "diffsync>=2.1,<3.0",   # D005: was "diffsync[redis]>=2.1,<3.0" — the [redis] extra caps redis<5.0,
                            #   which is unsatisfiable next to prefect>=3.6 → pydocket → redis>=5
    "redis>=4.3,<9",        # D005: declared DIRECTLY because infrahub_sync/utils.py:11 imports
                            #   diffsync.store.redis.RedisStore unconditionally; permissive floor on
                            #   purpose (NOT redis>=5) so a downstream diffsync[redis] requirement
                            #   still resolves (verified at redis 4.6.0)
]

[project.optional-dependencies]
prefect = [
    "prefect==3.8.1",       # D005 option D: current latest; installable next to the base set above
]
```

diffsync's declared `redis<5.0` cap is stale, not real: its redis store was verified
functionally intact on redis-py 4.6.0, 5.0, 6.4, 7.0 and 8.1.0 (26/26 checks against a
redis 8.4.0 server, including `diff_from`/`diff_to`/`sync_from` between two Redis-backed
adapters; the 4.6.0 control and 8.1.0 results are byte-identical). The API surface the
store uses is `Redis()`, `Redis.from_url()`, `ping/get/set/exists/delete/scan_iter`. The
override is permanent — diffsync has no 3.x release and no open PR raising the cap.

`RedisStore` is opt-in at run time (`utils.py:195-205` defaults to `LocalStore()` and
constructs `RedisStore` only when a configuration sets `store.type == "redis"`; no shipped
example enables it), but the import at `utils.py:11` is unconditional — so *import*
compatibility with the installed redis client is what matters for every user, and that is
what T012a verifies.

Install (preview-accurate form — the preview exists
only on this branch and is not published to PyPI, matching T034's binding
install-source rule): from the repository checkout, `pip install -e '.[prefect]'`
(or `uv pip install -e '.[prefect]'`). The PyPI form
`pip install 'infrahub-sync[prefect]'` is the post-publication shape only and must
not appear in the example README.

## 2. Flow definition (`infrahub_sync/orchestration/flow.py`)

```python
# NO `from __future__ import annotations` in this module: deferred annotations break
# prefect's run-time parameter validation (PydanticUndefinedAnnotation — research F3,
# observed on 3.5.0; the mechanism is version-generic and the omission is kept at 3.8.1,
# where T016's parameter-contract assertions are what confirm it).
# Module docstring notes that this module requires the optional `prefect` extra
# (`pip install -e '.[prefect]'` from the repo checkout) so programmatic importers
# hitting ImportError know the fix (X4).

import dataclasses
import logging
import os
from typing import Literal

from prefect import flow
from prefect.logging import get_run_logger

FLOW_NAME = "infrahub-sync"
# Deployment named "run" (NOT a repeat of the flow name): the lookup path reads
# /api/deployments/name/infrahub-sync/run instead of the stuttering
# .../infrahub-sync/infrahub-sync, and future orchestration briefs get sibling
# deployments as .../infrahub-sync/<verb> for free. Deliberate choice (X12);
# renaming after the preview would break every remote caller's lookup.
DEPLOYMENT_NAME = "run"
CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"


@flow(name=FLOW_NAME)
def infrahub_sync_run(
    sync_name: str,
    operation: Literal["plan", "sync"] = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
) -> dict:
    """Run one Infrahub Sync plan or explicitly confirmed sync via the shared surface.

    EXACTLY these four parameters (DBR-003); no parameter accepts paths, CLI
    fragments, credentials, or environment overrides (DBR-005/006). Returns an
    asdict-SHAPED seven-key dict built explicitly (never dataclasses.asdict —
    see body step 4 / X15); raises RunValidationError / RunExecutionError on
    failure so Prefect records the run FAILED (DBR-015).
    """
```

Contractual body behavior, in order:

1. `get_run_logger()`; attach the log bridge (§4) to `logging.getLogger("infrahub_sync")`
   AND take ownership of that logger's LEVEL: capture
   `logging.getLogger("infrahub_sync").level`, then set it to the run's intended
   level (`logging.INFO`) before the surface call. Attaching a handler alone does
   not defeat `isEnabledFor`; without owning the level, forwarding would silently
   depend on whatever root/Prefect logging configuration is ambient (E4).
2. Inside `try/finally` (bridge removal AND level restoration — restore the captured
   prior level — in the same `finally`):
   read `os.environ[CONFIG_DIR_ENV]` (inherited from the serving process — the value
   was validated at serve start; a missing var here still raises `RunExecutionError`
   defensively), then
   `result = run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=...)`.
3. Log one summary line with the FIXED key=value format
   (`"run %s finished: status=%s changed=%s summary=create:%d,update:%d,delete:%d artifact=%s"`)
   so DBA-003's plan summary is remotely observable in the run log. This line is the
   **supported remote observation surface** for the run result (X7): its format is
   contractual (never a Python dict repr) and remote callers may parse it. Every
   substitution is pinned — **the leading `%s` is `result.run_id`** (X18), followed by
   `status`, `changed`, the three `summary` counts, and `artifact_path` — FIVE
   distinct `RunResult` fields (`run_id`, `status`, `changed`, `summary`,
   `artifact_path`); `sync_name` and `operation` do NOT appear on the line. Any
   format change is a breaking
   change for consumers of this preview; a future API brief supersedes it via the
   owned contract's extend-not-fork rule (F11) rather than by silently reformatting.
4. Return an **asdict-shaped dict** built by EXPLICIT seven-key construction
   (binding — X15):

   ```python
   # NOT dataclasses.asdict(result): asdict() deep-copies field values, and the
   # E14 `summary` mappingproxy is not deep-copyable — root-probed, it raises
   # TypeError: cannot pickle 'mappingproxy' object, so the flow's success path
   # would fail at return time. Do not "simplify" this back to asdict().
   out = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
   out["summary"] = dict(result.summary)
   return out
   ```

   The shape is a shallow seven-key dict with `summary` as a plain `dict` — exactly
   what T016 asserts, so T016 needs no change.

The flow calls the execution surface **in-process**; it never spawns the CLI
(DBR-008). Exceptions propagate: Prefect marks the flow run FAILED and stores the
(sanitized) exception message as the state message.

## 3. Serve entrypoint (`infrahub_sync/orchestration/serve.py`)

Run as: `python -m infrahub_sync.orchestration.serve`

Contract:

1. At startup, guard the prefect import: if importing `prefect` (via
   `orchestration.flow`) raises `ImportError`, emit exactly one error line naming
   the optional extra and the install command (e.g.
   `prefect is not installed - install the optional integration: pip install -e '.[prefect]'`)
   and exit non-zero — never a bare traceback (X4).
2. Read `INFRAHUB_SYNC_CONFIG_DIRECTORY`. If unset, empty, or not an existing
   directory: emit one error line **naming the variable** and exit with a non-zero
   status **before any deployment is served** (spec clarification #2).
3. Error-line mechanism (binding — the repo's AST no-print test covers every new
   module): `logging` (the lastResort handler writes ERROR to stderr without
   configuration) or `sys.stderr.write` — **never `print()`** (E13).
4. Call `infrahub_sync_run.serve(name=DEPLOYMENT_NAME)` — a locally served deployment;
   no work pool, no separate worker, default `enforce_parameter_schema=True`
   (probe a₆ verified the served deployment reports `enforce_parameter_schema: true`
   and an `enum: ["plan", "sync"]` schema for `operation`).
5. The process serves until interrupted; configurations under the directory are
   re-resolved per run (no restart needed for content changes).

Prerequisites (documented in the example README, not enforced by code):

- A Prefect server started with `prefect server start` and `PREFECT_API_URL`
  pointing at it.
- **Working directory (binding for the qualified example)**: the serve process must
  be started from the repository root. The qualified fixture's `config.yml` uses
  repo-root-relative `./` paths (adapter spec and `db_path`), resolved against the
  serving process's CWD (`plugin_loader.py:235-238`), and the cache root defaults
  to `Path.cwd()/.infrahub-sync-cache` — started elsewhere, the example degrades to
  a silently empty plan or an adapter import failure (X1/E11). The example README
  states this both as a prerequisite and next to the serve command.

## 4. Log bridge (run-scoped, DBR-012 / spec clarification #4)

```python
class RunLoggerBridge(logging.Handler):
    """Forward `infrahub_sync` hierarchy records into the Prefect run logger."""

    def __init__(self, run_logger) -> None:   # run_logger: prefect LoggerAdapter
        super().__init__(level=logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        # re-log preserving level and origin logger name:
        #   run_logger.log(record.levelno, "%s | %s", record.name, record.getMessage())
        ...
```

- Attached to `logging.getLogger("infrahub_sync")` immediately before the
  execution-surface call, removed in `finally`.
- **The flow owns the source logger's LEVEL, not just the handler** (E4): a handler
  never defeats `Logger.isEnabledFor`, and the `infrahub_sync` hierarchy is
  level-`NOTSET` by default (the CLI makes INFO effective via `_setup_logging`,
  which the flow never calls). The flow therefore captures
  `logging.getLogger("infrahub_sync").level`, sets it to `logging.INFO` before the
  surface call, and restores the captured level in the same `finally` that removes
  the handler. Only with both does forwarding become independent of operator-set
  Prefect logging environment variables and ambient root-logger configuration
  (spec clarification #4 / D004; verified by T016's root-at-WARNING case).
- **Process-isolation assumption (stated, not assumed — E21)**: attaching the handler
  and setting the level mutate PROCESS-GLOBAL logging state, which is safe only
  because each flow run occupies its own process — the preview's default served
  behavior (`flow.serve(...)` runs each flow run in a subprocess). Concurrent
  same-process flow runs would race on the `infrahub_sync` logger: two runs of
  DIFFERENT configurations are not excluded by the per-configuration pipeline lock, so
  they would cross-attach bridges (one run's records, including adapter detail,
  forwarded into the other run's Prefect log) and one run's `finally` would restore the
  level under the other. In-process concurrent execution is OUT OF SCOPE for this
  preview; if a later brief runs flows in-process, the bridge must key on the current
  run and the level mutation must be reference-counted. Worth one confirmation with the
  existing probe rig (submit two concurrent runs of two configurations, confirm
  distinct PIDs); probe c₁ was a single run.
- Probe c₁ evidence: a child-logger record (`probe_pkg.potenda`) forwarded this way is
  returned by `POST /api/logs/filter` for the flow run, name preserved in the message.
- Records are forwarded at the run's effective level (INFO and above by default —
  SC-002's denominator). Secret redaction applies to forwarded text (the bridge passes
  messages produced by code already bound by the no-secrets logging rules; the DBA-008
  canary scan is the verification).

## 5. Remote REST interaction (consumed contract, DBR-011)

All endpoints are Prefect's own API under `$PREFECT_API_URL` (default
`http://127.0.0.1:4200/api`). These exact interactions are what the example README's
request corpus documents (and what DBA-008 scans).

| Step | Request | Verified response shape |
|---|---|---|
| Find deployment | `GET /api/deployments/name/infrahub-sync/run` | `200`, body has `id`, `status: "READY"`, `enforce_parameter_schema: true`, `parameter_openapi_schema.properties.operation.enum == ["plan","sync"]` |
| Create run | `POST /api/deployments/{id}/create_flow_run` body `{"parameters": {"sync_name": "custom-example", "operation": "plan"}}` | `201/200` with flow-run `id` **synchronously** (state `SCHEDULED`) — SC-001's "identifier in the synchronous response" |
| Observe state | `GET /api/flow_runs/{id}` | `state.type` progresses to `COMPLETED` (probe b: ~7 s) or `FAILED`; `state.message` carries the sanitized failure cause |
| Read logs | `POST /api/logs/filter` body `{"logs": {"flow_run_id": {"any_": ["{id}"]}}}` | Array of records incl. bridged `infrahub_sync` lifecycle lines (probe c₁/c₂) |
| Read the result | same `POST /api/logs/filter` response — locate the flow's summary line | The §2-step-3 summary line with its FIXED key=value format (`... summary=create:N,update:N,delete:N ...`) is the supported remote carrier of the RunResult fields (X7; leading `%s` = `run_id`, X18) — its stability is scoped to THIS PREVIEW, and a future API brief supersedes it via the owned contract's extend-not-fork rule (F11); result retrieval via Prefect result persistence is NOT part of this preview's contract |
| Invalid `operation` | same create-run call with `"operation": "apply"` | **`409`**, body `{"detail": "Error creating flow run: Validation failed for field 'operation'. Failure reason: 'apply' is not one of ['plan', 'sync']"}` — **no flow run object is created** (probe d₁; satisfies spec edge case 1 in its strongest form: no RunResult, no log lines, no run directory) |

## 6. Import boundary (DBR-010 / DBA-001 / SC-006)

- `import infrahub_sync`, `import infrahub_sync.cli`, `import infrahub_sync.execution`,
  and every CLI sanity command load **zero** `prefect*` modules — asserted by a
  **subprocess-isolated probe** (E3/X3): the test builds a small script that imports
  the package, runs the CLI sanity in-process, and asserts over ITS OWN
  `sys.modules`, then executes it in a fresh interpreter via
  `subprocess.run([sys.executable, "-c", script])`, asserting the exit code and
  output. An in-process `sys.modules` assertion is unsound in the dev+prefect
  full-suite environment: pytest collection imports `tests/orchestration/test_flow.py`
  (whose module-level `importorskip` loads prefect) before the probe runs, and under
  `--dist loadscope` the pollution is per-worker arbitrary. Quickstart Scenario 0
  (clean venv without the extra) remains the authoritative SC-006 evidence.
- The static import-graph check stays in-process (collection-safe):
  `infrahub_sync/execution.py` imports nothing from `infrahub_sync.orchestration` or
  `prefect`, and nothing in the base package imports `infrahub_sync.orchestration`.
- Installing the extra changes nothing about plain CLI invocations: only
  `infrahub_sync.orchestration.flow` / `.serve` import prefect, and nothing in the base
  package imports `infrahub_sync.orchestration`.
