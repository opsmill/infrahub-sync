# Contract: Shared Execution Surface (`infrahub_sync/execution.py`)

The narrow typed Python surface required by DBR-007/DBR-008/DBR-015, used by exactly
three callers: CLI `diff`, the serial branch of CLI `sync --no-parallel`, and the
Prefect flow. This module imports **no Prefect symbol** (DBR-010) and stays importable
in a base install.

Types below are the contract. Signatures are binding; bodies shown only where behavior
is contractual. Failure semantics follow **D009** (sanitize-and-wrap only in
`run_remote_request`; the CLI path preserves `9edc1bc` failure behavior verbatim) and
configuration resolution follows **D010** (tolerant per-file walk) — both PROVISIONAL
(CHECKPOINT), see `critiques/collation-r1.md`.

```python
"""Shared typed execution surface for the diff/plan and serial-sync lifecycles."""

from __future__ import annotations  # allowed here — this module defines no Prefect flow

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, NoReturn, TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance
    from infrahub_sync.potenda import Potenda

Operation = Literal["plan", "sync"]
Status = Literal["planned", "applied", "no-change"]
ActionKey = Literal["create", "update", "delete"]

# Factory signature identical to utils.get_potenda_from_instance — injected so the
# CLI can pass its own thin wrapper over the module-global (keeps existing patches on
# `infrahub_sync.cli.get_potenda_from_instance` effective, DBA-009 — see
# "Failure semantics" / D009 below).
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
    summary: Mapping[ActionKey, int]  # read-only after validation — see __post_init__
    artifact_path: str

    def __post_init__(self) -> None:
        """Enforce the cross-field invariants from data-model.md §2; raise ValueError.

        Also wraps `summary` in `types.MappingProxyType` via `object.__setattr__`
        so the mapping cannot be mutated after validation — `frozen=True` alone
        prevents only field rebinding, and DBA-010's immutability assertion must
        stay honest (`result.summary["create"] += 1` must raise TypeError).
        """


def resolve_sync_instance(sync_name: str, *, directory: str) -> "SyncInstance":
    """Resolve `sync_name` by exact string equality within `directory` (D010).

    Performs its OWN tolerant per-file walk: the same recursive `**/config.yml`
    discovery glob and the same exact-`name` match the CLI lookup uses, but each
    discovered file is read and validated individually (per-file yaml/pydantic/OS
    error handling) instead of `utils.get_all_sync`'s eager validate-everything
    pass — one broken neighbor must not block resolution of every other name, and
    its parse error must not leak that file's contents. The requested value is
    NEVER used to construct a filesystem path; traversal-shaped or command-like
    values therefore fail exactly like unknown names.

    Per-file behavior (binding):

    - A file that fails to read or parse and whose raw ``name:`` does NOT match
      the request is skipped with a WARNING log naming the offending file (path
      only, never contents), and resolution continues.
    - A file whose raw ``name:`` matches the request but whose content is invalid
      — or a file that is unreadable where the name may live — raises
      RunValidationError naming the logical name and the file path ONLY. The
      original parse detail is never chained verbatim (pydantic's
      ``input_value=...`` echo can carry that file's contents, including inline
      secrets the redactor never collected).

    The CLI keeps calling `utils.get_instance` directly (caller-obligations
    table); CLI resolution behavior is unchanged.

    Raises:
        RunValidationError: no configuration with that logical name exists under
            `directory`, or the matched config.yml is unreadable/invalid (message
            names the logical name and may name the offending file path, but never
            file contents or credential values).
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
    # Private seams — not part of the remote contract; run_remote_request never sets them.
    _lock_timeout: float = 60.0,  # sanctioned test seam for lock-contention tests (T011)
    _serial_load_error: "Callable[[ValueError], NoReturn] | None" = None,  # CLI-only (D009)
) -> RunResult:
    """Run one plan (== diff lifecycle) or serial sync against a resolved instance.

    Contractual behavior, in order. Failure semantics per D009 (see "Failure
    semantics" below): `execute_run` raises ORIGINAL exception types and never
    wraps into RunExecutionError; its only surface-typed raise is step 1's
    RunValidationError.

    1. Validate `operation` membership (defensive; Prefect already enforces it for
       remote runs) and the `confirm_writes` gate: `operation="sync"` with
       `confirm_writes` False raises RunValidationError BEFORE any adapter object is
       constructed or connection attempted. The message states that
       `confirm_writes=true` is required to run `operation=sync`. (These refusals
       are surface-owned and unreachable from the CLI callers, which always pass a
       valid operation and confirm by explicit invocation — no CLI behavior change.)
    2. Acquire the per-configuration pipeline lock
       (`cache.locks.pipeline_lock(sync_instance.name, timeout=_lock_timeout)`;
       default 60 s, exactly today's). `filelock.Timeout` propagates UNCHANGED —
       today's CLI lets it traceback; the remote wrap into RunExecutionError happens
       only in run_remote_request.
    3. Inside the lock, build the engine via `potenda_factory` (adapter construction —
       the first point credentials are read) with NO surrounding catch of its own:
       factory failures (`ValueError`, `ImportError`, ...) propagate with their
       original types. The CLI preserves today's prefixed abort by passing a wrapper
       factory (see "Failure semantics"); run_remote_request wraps for remote callers.
    4. `operation="plan"`: reproduce the CLI diff lifecycle byte-for-byte in behavior —
       RunFile(mode="diff", status "running"), force_full_extract, load_both_sides,
       diff, write_plan, log the diff string (same logger semantics as today),
       run.json → "dry-run" with `summary={"resources": len(top_level)}`, finished_at,
       "Cached run %s at %s" log line.
    5. `operation="sync"`: reproduce the CLI serial sync lifecycle — RunFile
       (mode="sync"), load_both_sides with today's inner narrow catch preserved
       (`except ValueError` around the load: mark run.json "failed", save, then
       invoke `_serial_load_error(exc)` when the CLI provided it — the unprefixed
       abort fires at the site, exactly `cli.py:263-268` today — or re-raise the
       original ValueError unchanged when it did not), then check_rowcount_guardrail,
       diff, write_plan, optional diff print (`print_diff`), `sync(diff)` + timing
       log only when the diff has changes, else "No difference found. Nothing to
       sync", persist_baseline_counts, run.json → "applied" with
       `summary={"resources": ..., "mode": "serial"}`, "Sync run %s at %s" log line.
    6. Any other failure inside 4/5 is handled by the PRESERVED `9edc1bc` CLI
       pattern, verbatim: `except Exception: run_file.status = "failed";
       run_file.save(); raise` — a broad mark-and-rethrow with a bare re-raise of
       the ORIGINAL exception (today's `cli.py:156-159` / `285-288`). This broad
       except is the preserved existing pattern, documented as such at the site
       with a targeted `# noqa: BLE001`; it is not new looseness (D009; plan
       Constitution Check row IV). run.json can therefore never be left at
       `status="running"` by a lifecycle failure.
    7. On success, return RunResult derived per data-model.md §2 — single-source
       derivation (binding): `summary`, `changed`, and `status` ALL derive from the
       one in-memory materialized plan-row list (`ptd._diff_to_rows(diff)` or an
       equivalent shared function over the same diff object) — the same rows passed
       to `write_plan` — and NEVER by re-reading `plan.parquet` (the DBA-009 fixture
       population's fakes never write one). Nested-child caveat (binding):
       `_diff_to_rows` walks only the diff root's direct children while
       `Diff.has_diffs()` is recursive; the materialized rows are the preview's
       result fidelity boundary — `has_diffs()` continues to gate `sync` execution
       exactly as today, but the result fields come from the rows, so a
       nested-children-only diff yields `status="no-change"` / `changed=False` /
       all-zero summary even when a sync executed. A synthetic nested-diff unit
       test pins this behavior (tasks T029).

    No sanitize-and-wrap happens in this function (D009).
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
    `show_progress=False` (progress display disabled on remote runs); the private
    seams are never set. No parameter of this function accepts paths, CLI fragments,
    credentials, or environment overrides.

    THE sanitize-and-wrap boundary (D009): this function — and only this function —
    converts failures into the typed remote contract:

    - RunValidationError (from resolve_sync_instance or execute_run step 1)
      propagates unchanged (already sanitized at raise).
    - `filelock.Timeout` → RunExecutionError naming the sync name and the timeout.
    - Factory `ValueError` → RunExecutionError whose message preserves today's CLI
      wording ("Failed to initialize the Sync Instance: ...").
    - Factory/adapter `ImportError` → RunExecutionError naming the adapter import
      failure.
    - Every other exception escaping execute_run → RunExecutionError with a
      sanitized message naming the stage and cause.

    Every wrapped message passes value-based secret redaction, and redaction covers
    the WHOLE cause chain at this wrap point (run-result-and-errors.md §2): the
    cause is rebuilt as a sanitized copy — or suppressed via `__suppress_context__`
    with its redacted text inlined — so that NO traceback rendering of the raised
    error contains an unredacted original message. The broad catch this boundary
    requires is a translation, not suppression: caught broadly, ALWAYS re-raised
    typed, never swallowed, with a targeted `# noqa: BLE001` (D009; plan
    Constitution Check row IV).
    """
```

## Caller obligations

| Caller | How it calls | Preserved behavior |
|---|---|---|
| `cli.py::diff_cmd` | resolves instance via its existing `get_instance` path (name **or** config_file — CLI-only flexibility), applies `adapter_path` merging, then `execute_run(instance, operation="plan", confirm_writes=False, branch=..., show_progress=..., verbosity=..., run_id=..., concurrent_load=..., full_extract=..., potenda_factory=<CLI wrapper factory — see Failure semantics>)` | Exit codes, log lines, run.json contents identical; `--full-extract`, `--show-progress`, `--run-id`, `--concurrent-load` pass through; factory `ValueError` → today's prefixed abort at the site; every lifecycle failure → today's uncaught original-type traceback |
| `cli.py::sync_cmd` (serial branch only: `--no-parallel`, or `--parallel` with explicit `order:`) | same resolution, then `execute_run(instance, operation="sync", confirm_writes=True, print_diff=<--diff>, allow_rowcount_drop=..., continue_on_error=..., potenda_factory=<CLI wrapper factory>, _serial_load_error=<unprefixed abort — see Failure semantics>, ...)` — the explicit human CLI invocation IS the confirmation | `--parallel ignored` warning and the parallel `sync_in_tiers` branch stay in cli.py untouched; factory → prefixed abort; serial-load `ValueError` → unprefixed abort; all other lifecycle failures → original-type traceback |
| `orchestration/flow.py` | `run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=os.environ["INFRAHUB_SYNC_CONFIG_DIRECTORY"])` | Engine defaults pinned; `show_progress=False`; failures surface ONLY as sanitized `RunValidationError` / `RunExecutionError` |

### Failure semantics (binding — D009; DBR-009: exit codes and output identical to the current CLI at `9edc1bc`)

This section replaces the earlier `__cause__`-keyed CLI error mapping, which could
not discriminate factory `ValueError` from load-phase `ValueError` (both load paths
wrap any failure into `ValueError`, `potenda/__init__.py:230-246`) and would have
rewritten today's tracebacks and wording. The design is identity by construction,
not reconstruction:

- **`execute_run` raises original exception types and never wraps.** Its only
  surface-typed raise is the step-1 `RunValidationError`, unreachable from the CLI
  callers. Sanitize-and-wrap into `RunValidationError`/`RunExecutionError` lives
  ONLY in `run_remote_request`.
- **Factory site (prefixed abort — both CLI commands)**: the CLI keeps today's
  narrow handler at the construction site, in `cli.py`, by passing a thin wrapper
  as `potenda_factory`:

  ```python
  def _cli_potenda_factory(**kwargs: Any) -> Potenda:
      try:
          # module global resolved at call time — DBA-009 patches on
          # infrahub_sync.cli.get_potenda_from_instance stay effective
          return get_potenda_from_instance(**kwargs)
      except ValueError as exc:
          print_error_and_abort(f"Failed to initialize the Sync Instance: {exc}")
  ```

  The resulting `typer.Exit` propagates out of `execute_run` untouched (no run.json
  exists yet at factory time — the RunFile is created only after the engine
  allocates `run_dir`, and step 6's broad except has not been entered), so wording,
  exit code, and control flow are byte-identical to `cli.py:139-140` / `237-238`.
  A factory `ImportError` is caught nowhere on the CLI path and surfaces as today's
  uncaught traceback.
- **Serial-load site (unprefixed abort — sync command only)**: `execute_run`'s
  step-5 narrow `except ValueError` around `load_both_sides` marks run.json failed,
  saves, and invokes the CLI-supplied `_serial_load_error(exc)`
  (`lambda exc: print_error_and_abort(str(exc))`) — the unprefixed abort fires at
  the site exactly as `cli.py:263-268` today; the `typer.Exit` then passes through
  the preserved outer broad except (re-mark failed, bare re-raise), reproducing
  today's `cli.py:285-288` control flow. Remote callers leave `_serial_load_error`
  unset and the original `ValueError` re-raises (after run.json is marked failed)
  for `run_remote_request` to wrap.
- **Everything else** (guardrail, diff, write_plan, sync, persist — arbitrary
  exception types, including `filelock.Timeout` at acquisition): mark run.json
  failed where a RunFile exists and bare re-raise the original — an uncaught
  traceback of the ORIGINAL type at the CLI, exactly today's behavior; wrapped and
  sanitized only in `run_remote_request`.
- **CLI mapping tests per stage** (tasks T025/T026/T027): one test per stage
  asserting today's exact wording and traceback types — factory `ValueError` →
  prefixed abort wording + exit code; serial-load `ValueError` (sync) → unprefixed
  abort; a diff-path lifecycle failure (including a load `ValueError`) → uncaught
  traceback of the ORIGINAL type with run.json `failed`; factory `ImportError` →
  uncaught `ImportError` traceback; lock contention → uncaught `filelock.Timeout`
  traceback.

## Compatibility constraints (binding)

- The DBA-009 test population (`tests/test_cli_full_extract.py`,
  `tests/test_cli_parallel.py`, `tests/cache/test_cli_sync_cache.py` serial cases,
  `tests/test_logging.py`) passes **unmodified**. This forces: the CLI keeps
  `get_potenda_from_instance` imported as a module global in `infrahub_sync.cli` and
  resolves it at call time inside the wrapper factory it passes to the surface; the
  surface sets `ptd.force_full_extract`; run.json is written to `ptd.run_dir`;
  `write_plan`/`persist_baseline_counts` are called on the engine object; RunResult
  fields derive from the in-memory row list, never from re-reading `plan.parquet`
  (the population's fakes never write one — execute_run step 7).
- `infrahub_sync/execution.py` imports nothing from `infrahub_sync.orchestration` and
  nothing from `prefect` (asserted by the SC-006 test).
- The parallel sync branch (`sync_in_tiers`) is NOT moved behind this surface
  (spec Out of Scope).
