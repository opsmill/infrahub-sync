# Contract: Shared Execution Surface (`infrahub_sync/execution.py`)

The narrow typed Python surface required by DBR-007/DBR-008/DBR-015, used by exactly
three callers: CLI `diff`, the serial branch of CLI `sync --no-parallel`, and the
Prefect flow. This module imports **no Prefect symbol** (DBR-010) and stays importable
in a base install.

Types below are the contract. Signatures are binding; bodies shown only where behavior
is contractual.

```python
"""Shared typed execution surface for the diff/plan and serial-sync lifecycles."""

from __future__ import annotations  # allowed here — this module defines no Prefect flow

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance
    from infrahub_sync.potenda import Potenda

Operation = Literal["plan", "sync"]
Status = Literal["planned", "applied", "no-change"]
ActionKey = Literal["create", "update", "delete"]

# Factory signature identical to utils.get_potenda_from_instance — injected so the
# CLI can pass its own module-global (keeps existing patches on
# `infrahub_sync.cli.get_potenda_from_instance` effective, DBA-009).
PotendaFactory = Callable[..., "Potenda"]


class RunValidationError(Exception):
    """Input-boundary refusal (see contracts/run-result-and-errors.md)."""


class RunExecutionError(Exception):
    """Adapter or engine failure (see contracts/run-result-and-errors.md)."""


@dataclass(frozen=True, slots=True)
class RunResult:
    """Immutable success result — exactly these seven fields (DBR-015)."""

    sync_name: str
    operation: Operation
    run_id: str
    status: Status
    changed: bool
    summary: dict[ActionKey, int]
    artifact_path: str

    def __post_init__(self) -> None:
        """Enforce the cross-field invariants from data-model.md §2; raise ValueError."""


def resolve_sync_instance(sync_name: str, *, directory: str) -> "SyncInstance":
    """Resolve `sync_name` by exact string equality within `directory`.

    Semantics identical to `utils.get_instance(name=sync_name, directory=directory)`
    (recursive `**/config.yml` discovery, exact `name` match). The requested value is
    NEVER used to construct a filesystem path; traversal-shaped or command-like values
    therefore fail exactly like unknown names.

    Raises:
        RunValidationError: no configuration with that logical name exists under
            `directory`, or the matched config.yml is unreadable/invalid (message names
            the logical name — and may name the offending file — but never file
            contents or credential values).
    """


def execute_run(
    sync_instance: "SyncInstance",
    *,
    operation: Operation,
    confirm_writes: bool = False,
    branch: str | None = None,
    # Engine options — defaults are the CLI option defaults at commit 9edc1bc.
    show_progress: bool | None = None,
    verbosity: int = logging.INFO,
    run_id: str | None = None,
    concurrent_load: bool = True,
    full_extract: bool = True,
    allow_rowcount_drop: bool = False,
    continue_on_error: bool = False,
    print_diff: bool = True,
    potenda_factory: PotendaFactory | None = None,  # None → utils.get_potenda_from_instance
) -> RunResult:
    """Run one plan (== diff lifecycle) or serial sync against a resolved instance.

    Contractual behavior, in order:

    1. Validate `operation` membership (defensive; Prefect already enforces it for
       remote runs) and the `confirm_writes` gate: `operation="sync"` with
       `confirm_writes` False raises RunValidationError BEFORE any adapter object is
       constructed or connection attempted. The message states that
       `confirm_writes=true` is required to run `operation=sync`.
    2. Acquire the per-configuration pipeline lock
       (`cache.locks.pipeline_lock(sync_instance.name)`, existing 60 s timeout).
       `filelock.Timeout` → RunExecutionError naming the sync name and timeout.
    3. Inside the lock, build the engine via `potenda_factory` (adapter construction —
       the first point credentials are read). Factory `ValueError`/`ImportError` →
       RunExecutionError (message text preserves today's CLI wording:
       "Failed to initialize the Sync Instance: ..." for ValueError).
    4. `operation="plan"`: reproduce the CLI diff lifecycle byte-for-byte in behavior —
       RunFile(mode="diff", status "running"), force_full_extract, load_both_sides,
       diff, write_plan, log the diff string (same logger semantics as today),
       run.json → "dry-run" with `summary={"resources": len(top_level)}`, finished_at,
       "Cached run %s at %s" log line.
    5. `operation="sync"`: reproduce the CLI serial sync lifecycle — RunFile
       (mode="sync"), load_both_sides, check_rowcount_guardrail, diff, write_plan,
       optional diff print (`print_diff`), `sync(diff)` + timing log only when the diff
       has changes, else "No difference found. Nothing to sync",
       persist_baseline_counts, run.json → "applied" with
       `summary={"resources": ..., "mode": "serial"}`, "Sync run %s at %s" log line.
    6. Any failure inside 4/5 marks run.json "failed" (as today) and raises
       RunExecutionError chaining the original exception.
    7. On success, return RunResult derived per data-model.md §2 (summary counted from
       the same rows written to plan.parquet).

    All raised messages pass secret redaction (run-result-and-errors.md §3).
    """


def run_remote_request(
    sync_name: str,
    operation: Operation = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
    *,
    config_directory: str,
) -> RunResult:
    """Composition used by the Prefect flow (and any programmatic remote-shaped caller).

    resolve_sync_instance(sync_name, directory=config_directory) then execute_run(...)
    with EVERY engine option left at its 9edc1bc CLI default EXCEPT
    `show_progress=False` (progress display disabled on remote runs). No parameter of
    this function accepts paths, CLI fragments, credentials, or environment overrides.
    """
```

## Caller obligations

| Caller | How it calls | Preserved behavior |
|---|---|---|
| `cli.py::diff_cmd` | resolves instance via its existing `get_instance` path (name **or** config_file — CLI-only flexibility), applies `adapter_path` merging, then `execute_run(instance, operation="plan", confirm_writes=False, branch=..., show_progress=..., verbosity=..., run_id=..., concurrent_load=..., full_extract=..., potenda_factory=get_potenda_from_instance)` (the module-global) | Exit codes, log lines, run.json contents identical; `--full-extract`, `--show-progress`, `--run-id`, `--concurrent-load` pass through |
| `cli.py::sync_cmd` (serial branch only: `--no-parallel`, or `--parallel` with explicit `order:`) | same resolution, then `execute_run(instance, operation="sync", confirm_writes=True, print_diff=<--diff>, allow_rowcount_drop=..., continue_on_error=..., ...)` — the explicit human CLI invocation IS the confirmation | `--parallel ignored` warning and the parallel `sync_in_tiers` branch stay in cli.py untouched |
| `orchestration/flow.py` | `run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=os.environ["INFRAHUB_SYNC_CONFIG_DIRECTORY"])` | Engine defaults pinned; `show_progress=False` |

### CLI error mapping (binding — DBR-009: exit codes and output identical to the current CLI at `9edc1bc`)

The CLI commands map surface errors back to today's behavior **by origin**
(`__cause__`), not by wrapper class alone:

- `RunValidationError`, and any surface error wrapping the factory `ValueError`
  (`__cause__` is `ValueError`, execute_run step 3) → `print_error_and_abort` with
  today's exact wording (`"Failed to initialize the Sync Instance: <exc>"` for the
  factory case), preserving today's exit code.
- A surface error wrapping a factory `ImportError` (`__cause__` is `ImportError`,
  `RunExecutionError`-typed per step 3) → the CLI re-raises the original
  `ImportError` unchanged. The CLI at `9edc1bc` catches only `ValueError` around
  factory construction, so a missing optional dependency surfaces as an uncaught
  traceback today; that traceback behavior (and its exit code) must be preserved.

## Compatibility constraints (binding)

- The DBA-009 test population (`tests/test_cli_full_extract.py`,
  `tests/test_cli_parallel.py`, `tests/cache/test_cli_sync_cache.py` serial cases,
  `tests/test_logging.py`) passes **unmodified**. This forces: the CLI keeps
  `get_potenda_from_instance` imported as a module global in `infrahub_sync.cli` and
  passes it into the surface; the surface sets `ptd.force_full_extract`; run.json is
  written to `ptd.run_dir`; `write_plan`/`persist_baseline_counts` are called on the
  engine object.
- `infrahub_sync/execution.py` imports nothing from `infrahub_sync.orchestration` and
  nothing from `prefect` (asserted by the SC-006 test).
- The parallel sync branch (`sync_in_tiers`) is NOT moved behind this surface
  (spec Out of Scope).
