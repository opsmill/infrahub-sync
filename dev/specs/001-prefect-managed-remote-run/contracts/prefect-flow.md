# Contract: Prefect Flow, Serve Entrypoint, and REST Interaction

Owner: `infrahub_sync/orchestration/` — the ONLY package that imports `prefect`
(DBR-010). Pinned to `prefect==3.5.0` per **D005**, with companion pins
`importlib-metadata>=4.4` and `fastapi>=0.111,<0.121` per **D006** (see research.md
F1/F2). All behaviors below were probe-verified against 3.5.0 (research.md probe table
rows b, c₁, c₂, d₁).

## 1. Optional extra (`pyproject.toml`)

```toml
[project.optional-dependencies]
prefect = [
    "prefect==3.5.0",            # D005: newest Prefect 3 resolvable next to diffsync[redis] (redis<5)
    "importlib-metadata>=4.4",   # D006: prefect 3.5.0 imports it (workers/base.py) but no longer declares it
    "fastapi>=0.111,<0.121",     # D006: prefect 3.5.0's fastapi<1.0 bound admits router-incompatible releases
]
```

Base `dependencies` are unchanged. Install: `pip install 'infrahub-sync[prefect]'`
(or `uv pip install 'infrahub-sync[prefect]'`).

## 2. Flow definition (`infrahub_sync/orchestration/flow.py`)

```python
# NO `from __future__ import annotations` in this module: deferred annotations break
# prefect 3.5.0 run-time parameter validation (PydanticUndefinedAnnotation — research F3).

import dataclasses
import logging
import os
from typing import Literal

from prefect import flow
from prefect.logging import get_run_logger

FLOW_NAME = "infrahub-sync"
DEPLOYMENT_NAME = "infrahub-sync"
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
    fragments, credentials, or environment overrides (DBR-005/006). Returns
    dataclasses.asdict(RunResult) on success; raises RunValidationError /
    RunExecutionError on failure so Prefect records the run FAILED (DBR-015).
    """
```

Contractual body behavior, in order:

1. `get_run_logger()`; attach the log bridge (§4) to `logging.getLogger("infrahub_sync")`.
2. Inside `try/finally` (bridge removal in `finally`):
   read `os.environ[CONFIG_DIR_ENV]` (inherited from the serving process — the value
   was validated at serve start; a missing var here still raises `RunExecutionError`
   defensively), then
   `result = run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=...)`.
3. Log one summary line
   (`"run %s finished: status=%s changed=%s summary=%s artifact=%s"`) so DBA-003's
   plan summary is remotely observable in the run log.
4. Return `dataclasses.asdict(result)`.

The flow calls the execution surface **in-process**; it never spawns the CLI
(DBR-008). Exceptions propagate: Prefect marks the flow run FAILED and stores the
(sanitized) exception message as the state message.

## 3. Serve entrypoint (`infrahub_sync/orchestration/serve.py`)

Run as: `python -m infrahub_sync.orchestration.serve`

Contract:

1. At startup, read `INFRAHUB_SYNC_CONFIG_DIRECTORY`. If unset, empty, or not an
   existing directory: log/print one error line **naming the variable** and exit with a
   non-zero status **before any deployment is served** (spec clarification #2).
2. Call `infrahub_sync_run.serve(name=DEPLOYMENT_NAME)` — a locally served deployment;
   no work pool, no separate worker, default `enforce_parameter_schema=True`
   (probe a₆ verified the served deployment reports `enforce_parameter_schema: true`
   and an `enum: ["plan", "sync"]` schema for `operation`).
3. The process serves until interrupted; configurations under the directory are
   re-resolved per run (no restart needed for content changes).

Prerequisite (documented in the example README, not enforced by code): a Prefect
server started with `prefect server start` and `PREFECT_API_URL` pointing at it.

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
  execution-surface call, removed in `finally` — forwarding never depends on
  operator-set Prefect logging environment variables.
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
| Find deployment | `GET /api/deployments/name/infrahub-sync/infrahub-sync` | `200`, body has `id`, `status: "READY"`, `enforce_parameter_schema: true`, `parameter_openapi_schema.properties.operation.enum == ["plan","sync"]` |
| Create run | `POST /api/deployments/{id}/create_flow_run` body `{"parameters": {"sync_name": "custom-example", "operation": "plan"}}` | `201/200` with flow-run `id` **synchronously** (state `SCHEDULED`) — SC-001's "identifier in the synchronous response" |
| Observe state | `GET /api/flow_runs/{id}` | `state.type` progresses to `COMPLETED` (probe b: ~7 s) or `FAILED`; `state.message` carries the sanitized failure cause |
| Read logs | `POST /api/logs/filter` body `{"logs": {"flow_run_id": {"any_": ["{id}"]}}}` | Array of records incl. bridged `infrahub_sync` lifecycle lines (probe c₁/c₂) |
| Invalid `operation` | same create-run call with `"operation": "apply"` | **`409`**, body `{"detail": "Error creating flow run: Validation failed for field 'operation'. Failure reason: 'apply' is not one of ['plan', 'sync']"}` — **no flow run object is created** (probe d₁; satisfies spec edge case 1 in its strongest form: no RunResult, no log lines, no run directory) |

## 6. Import boundary (DBR-010 / DBA-001 / SC-006)

- `import infrahub_sync`, `import infrahub_sync.cli`, `import infrahub_sync.execution`,
  and every CLI sanity command load **zero** `prefect*` modules — asserted by a test
  that runs the commands and checks `sys.modules`, executed in an environment where
  prefect is absent (so any import fails loudly).
- Installing the extra changes nothing about plain CLI invocations: only
  `infrahub_sync.orchestration.flow` / `.serve` import prefect, and nothing in the base
  package imports `infrahub_sync.orchestration`.
