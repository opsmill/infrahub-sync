# Contract: Shared Execution Surface (`infrahub_sync/execution.py`)

The narrow typed Python surface required by DBR-007/DBR-008/DBR-015, used by exactly
three callers: CLI `diff`, the serial branch of CLI `sync --no-parallel`, and the
Prefect flow. This module imports **no Prefect symbol** (DBR-010) and stays importable
in a base install.

Types below are the contract. Signatures are binding; bodies shown only where behavior
is contractual. Failure semantics follow **D009** (sanitize-and-wrap only in
`run_remote_request`; the CLI path preserves `9edc1bc` failure behavior verbatim) and
configuration resolution follows **D010** (tolerant per-file walk) — both RATIFIED
(checkpoint gate, Blake Ellis, 2026-07-30), records in `critiques/collation-r1.md`.

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

# Factory call shape identical to utils.get_potenda_from_instance — injected so the
# CLI can pass its own thin wrapper over the module-global (keeps existing patches on
# `infrahub_sync.cli.get_potenda_from_instance` effective, DBA-009 — see
# "Failure semantics" / D009 below). A Protocol rather than `Callable[..., Potenda]`,
# which erases the parameter names: the pinned seven-keyword call shape is part of
# the type, so a rename in the real factory is a type error rather than a runtime
# TypeError inside the remote boundary.
class PotendaFactory(Protocol):
    def __call__(
        self,
        *,
        sync_instance: SyncInstance,
        branch: str | None = ...,
        show_progress: bool | None = ...,
        verbosity: int = ...,
        run_id: str | None = ...,
        continue_on_error: bool = ...,
        concurrent_load: bool = ...,
    ) -> Potenda: ...


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

    Name extraction (binding, E17 — the rules below are decidable only because
    this mechanism is fixed): for each discovered ``config.yml``, read the file
    and parse it with ``yaml.safe_load``. The file's name is

    - **determinable** when the read and the parse both succeed AND the loaded
      object is a mapping: the name is the value of the top-level ``name`` key
      (``data.get("name")``). A mapping with no ``name`` key, or a ``name`` that
      is not a string, yields a value that can never equal the requested string,
      so such a file is *determinable-and-different*;
    - **UNDETERMINABLE** when the read raises ``OSError`` or ``UnicodeDecodeError``
      (a non-UTF-8 file: ``read_text(encoding="utf-8")`` raises it, and because it
      is a ``ValueError`` neither of the other two clauses catches it), the parse
      raises ``yaml.YAMLError``, or the loaded object is not a mapping.

    Per-file behavior (binding — total and disjoint over the three states above;
    exactly one rule applies to every discovered file):

    1. **Determinable AND equal to the request** — this is *the matched file*.
       Validate its parsed data as ``SyncConfig`` and return the resulting
       ``SyncInstance``, constructed with
       ``directory=str(<the matched config.yml>.parent)`` (binding — V3), matching
       ``utils.get_all_sync`` (``utils.py:129``). It must be the matched file's own
       parent directory, NOT the configured root passed in as ``directory``:
       ``utils.import_adapter`` resolves the generated adapter at
       ``<sync_instance.directory>/<adapter.name>/sync_adapter.py``
       (``utils.py:74-77``), and the qualified demonstration depends on
       ``examples/custom_adapter/mockdb/sync_adapter.py``. Passing the root instead
       would make that path miss for any nested configuration and silently fall
       through to the plugin loader. If validation fails, raise RunValidationError naming the
       logical name and the file path ONLY; the original parse detail is never
       chained verbatim (pydantic's ``input_value=...`` echo can carry that
       file's contents, including inline secrets the redactor never collected).
    2. **Determinable AND different from the request** — skipped silently (a
       DEBUG line at most); this is the ordinary case for every other
       configuration in the directory.
    3. **UNDETERMINABLE** (unreadable file, YAML error, non-mapping document) —
       skipped with a WARNING naming the file path ONLY (never contents, never
       the exception's rendered detail), counted, and resolution CONTINUES. An
       unreadable neighbor can therefore never block resolution of another name,
       and a bad-YAML file can never be "the matched one" — it has no
       determinable name to match with.

    Terminal error when no file matched (binding): raise the ordinary unknown-name
    RunValidationError naming the logical name. When the walk skipped N > 0
    UNDETERMINABLE files, that message must additionally state that N file(s) in
    the directory could not be read (the COUNT only — never names, paths beyond
    the WARNING lines, or contents), so the operator can tell a typo from a broken
    configuration.

    The CLI keeps calling `utils.get_instance` directly (caller-obligations
    table); CLI resolution behavior is unchanged.

    Raises:
        RunValidationError: no configuration with that logical name exists under
            `directory` (message names the logical name and, when applicable, the
            count of unreadable files), or the matched config.yml failed
            `SyncConfig` validation (message names the logical name and the file
            path, never file contents or credential values).
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
    _lock_already_held: bool = False,  # CLI-only (T026) — see "Already-locked caller"
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
       only in run_remote_request. **Already-locked caller (binding)**: when
       `_lock_already_held=True`, `execute_run` does NOT acquire the lock and runs
       steps 3–7 directly in the caller's already-held lock scope. This seam exists
       for exactly one caller — CLI `sync_cmd`'s serial branch, which must keep
       today's outer `with pipeline_lock(...)` because the parallel branch it shares
       a command body with still needs it (see "Already-locked caller (CLI serial
       sync)" below). A second same-process `FileLock` on the same path does not
       re-enter: it blocks for the full timeout and then raises `filelock.Timeout`
       (probed — E21), so without this seam the CLI serial path would self-deadlock.
       `run_remote_request` never sets it, so the remote path always acquires the
       lock itself and spec edge case 5 is unaffected.
    3. Inside the lock, build the engine via `potenda_factory` (adapter construction —
       the first point credentials are read) with NO surrounding catch of its own:
       factory failures (`ValueError`, `ImportError`, ...) propagate with their
       original types. The CLI preserves today's prefixed abort by passing a wrapper
       factory (see "Failure semantics"); run_remote_request wraps for remote callers.

       **Pinned call shape (binding — E23)**: `execute_run` calls the factory with
       ALL SEVEN keyword arguments of `utils.get_potenda_from_instance`, always
       explicitly, for BOTH operations:

       ```python
       ptd = potenda_factory(
           sync_instance=sync_instance,
           branch=branch,
           show_progress=show_progress,
           verbosity=verbosity,
           run_id=run_id,
           continue_on_error=continue_on_error,
           concurrent_load=concurrent_load,
       )
       ```

       Today's two commands pass DIFFERENT subsets (`diff` omits
       `continue_on_error`, `cli.py:131-138`; `sync` omits `run_id`,
       `cli.py:229-236`), and the omitted values default to exactly what the surface
       now passes explicitly (`continue_on_error=False`, `run_id=None`) — so
       `utils.get_potenda_from_instance` behaves identically and the DBA-009
       population (which asserts nothing about factory kwargs) is unaffected. A
       custom or fake factory does see the difference, hence the pin. The CLI's
       wrapper factory adapts by forwarding `**kwargs` unchanged, so both CLI
       commands and the remote path produce the same call.

       Immediately after the factory returns, and BEFORE any RunFile is built:
       set `ptd.force_full_extract = full_extract`, then reproduce today's guard
       verbatim (`cli.py:143-145` / `241-243`, E22) —
       `if ptd.run_dir is None: raise RuntimeError("get_potenda_from_instance did
       not allocate a run_dir")`. It is load-bearing twice: the message is part of
       today's observable behavior for a misbehaving factory, and it narrows
       `Potenda.run_dir: Path | None` (`potenda/__init__.py:51`) so
       `ptd.run_dir / "run.json"` type-checks under T039's ty gate.
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
       except is the preserved existing pattern, documented as such by a comment
       at the site; it is not new looseness (D009; plan Constitution Check
       row IV). Suppression directive at THIS site: **none** — see the
       "BLE001 suppression rule" below; BLE001 does not fire on a blind `except`
       whose handler re-raises the caught exception, and a directive here would
       make ruff report `RUF100 Unused noqa directive` and exit 1, failing
       T039's lint gate. run.json can therefore never be
       left at `status="running"` by a lifecycle failure.
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
    # Private test seams (E18/X17) — mirroring execute_run's. NOT part of the remote
    # contract: the Prefect flow NEVER sets them (it calls this function with request
    # parameters only), no remote caller can reach them, and they carry no meaning in
    # the deployment's parameter schema. They exist so tests that must drive the REAL
    # sanitize-and-wrap boundary can inject a fake engine or shorten the lock wait
    # instead of improvising a monkeypatch target (T011/T022/T023/T029).
    _potenda_factory: PotendaFactory | None = None,  # forwarded as execute_run's potenda_factory
    _lock_timeout: float = 60.0,  # forwarded as execute_run's _lock_timeout
) -> RunResult:
    """Composition used by the Prefect flow (and any programmatic remote-shaped caller).

    resolve_sync_instance(sync_name, directory=config_directory) then execute_run(...)
    with EVERY engine option left at its 9edc1bc CLI default EXCEPT
    `show_progress=False` (progress display disabled on remote runs) and except the
    two private seams above, which are forwarded verbatim to `execute_run`
    (`potenda_factory=_potenda_factory`, `_lock_timeout=_lock_timeout`) and default
    to exactly the production values. `_serial_load_error` is never set (CLI-only).
    No PUBLIC parameter of this function accepts paths, CLI fragments, credentials,
    or environment overrides, and the private seams are sanctioned test seams only —
    never set by the flow.

    THE sanitize-and-wrap boundary (D009): this function — and only this function —
    converts failures into the typed remote contract:

    - RunValidationError (from resolve_sync_instance or execute_run step 1)
      propagates unchanged (already sanitized at raise). The `resolve_sync_instance`
      call itself runs INSIDE the boundary's `try`, so anything it raises other
      than `RunValidationError` is wrapped and sanitized here rather than escaping
      raw (binding — otherwise the tolerant per-file walk's whole purpose is
      bypassed by the one exception it did not anticipate).
    - `filelock.Timeout` → RunExecutionError naming the sync name and the timeout.
    - Factory `ValueError` → RunExecutionError whose message preserves today's CLI
      wording ("Failed to initialize the Sync Instance: ..."). **Stage
      discrimination (binding)**: the wording applies to FACTORY-stage `ValueError`s
      only. `potenda` wraps every LOAD-stage failure into `ValueError` too
      (`potenda/__init__.py:234-250`), and `RunResult.__post_init__` raises it for an
      invariant violation, so this function passes its own wrapper factory
      (mirroring `cli._cli_potenda_factory`) that marks factory-stage failures; a
      load-stage `ValueError` or an invariant violation falls through to the
      stage-naming clause below instead of being reported as a credential problem.
      **Missing-credential
      case (D012 option A, binding)**: when the wrapped cause is an adapter
      missing-credential refusal, THIS wrap message additionally names the
      runner-environment variables the operator must set — for the infrahub
      adapter `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN`, by NAME only, never a
      value, attributed to the FAILING adapter via the
      `Error initializing <Name>Adapter:` prefix and omitted entirely for an
      adapter with no known variables. The naming lives here, at the remote boundary, precisely so the
      adapter modules stay untouched: `infrahub_sync/adapters/infrahub.py` is NOT
      modified by this delivery, and DBR-009's CLI byte-identity therefore holds
      absolutely (the adapter's own message still flows unchanged through the
      CLI's prefixed abort).
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
    typed, never swallowed, and documented as such by a comment at the site
    (D009; plan Constitution Check row IV). Suppression directive at THIS site:
    **a targeted `# noqa: BLE001` on the `except Exception` line IS required** —
    see the "BLE001 suppression rule" below; both chain mechanisms
    run-result-and-errors.md §2 (E5) permits here fire BLE001, and the one
    BLE001-clean form (plain `from exc`) is the leak E5 forbids.
    """
```

## BLE001 suppression rule (binding — E16; mechanism-conditioned, not site-uniform)

**The invariant that decides it**: a `# noqa: BLE001` is added **if and only if ruff
reports BLE001 for the code as actually written**. Verify with `uv run ruff check`
during implementation; never add — or omit — the directive speculatively. The two
broad-except sites in `execution.py` therefore resolve DIFFERENTLY, because their
handlers differ, not because one site is privileged.

Probe facts (this repo, `uv run ruff check --no-cache --select BLE`; `select = ["ALL"]`
with no BLE ignore for `infrahub_sync/**`):

| Handler as written | BLE001 |
|---|---|
| `except Exception: <mark run.json failed>; raise` (bare re-raise) | does NOT fire (adding `# noqa: BLE001` → `RUF100 Unused noqa directive`, ruff exit 1) |
| `except Exception as exc: raise E(msg) from exc` | does NOT fire |
| `except Exception as exc: raise E(msg) from None` | **FIRES** |
| `except Exception as exc: raise E(msg) from RuntimeError(str(exc))` | **FIRES** |

Consequences, binding at each site:

- **`execute_run` step 6** (blind `except` + bare `raise`): **NO suppression
  directive.** BLE001 does not fire on a handler that re-raises the caught
  exception, and a directive would trip RUF100 and fail T039's lint gate.
- **`run_remote_request`'s sanitize-and-wrap** (blind `except` + typed re-raise
  with a sanitized/suppressed cause): **a targeted `# noqa: BLE001` on the
  `except Exception` line IS required.** `contracts/run-result-and-errors.md` §2
  (E5, binding) permits exactly two chain mechanisms at this wrap — the cause
  rebuilt as a sanitized copy, or `__suppress_context__` with the redacted cause
  text inlined into the wrapper message — and BOTH fire BLE001 (rows 3 and 4
  above). The only BLE001-clean form, plain `from exc`, is precisely what E5
  forbids: it renders the unredacted original message in a traceback. The
  directive is therefore unavoidable here, and it carries a comment naming E5 as
  the reason the clean form is unavailable.

T039's gate is satisfiable exactly in that configuration: `uv run invoke lint`
exits 0 **with** the `run_remote_request` directive present and **without** one at
the step-6 site.

## Caller obligations

| Caller | How it calls | Preserved behavior |
|---|---|---|
| `cli.py::diff_cmd` | resolves instance via its existing `get_instance` path (name **or** config_file — CLI-only flexibility), applies `adapter_path` merging, then `execute_run(instance, operation="plan", confirm_writes=False, branch=..., show_progress=..., verbosity=..., run_id=..., concurrent_load=..., full_extract=..., potenda_factory=<CLI wrapper factory — see Failure semantics>)` | Exit codes, log lines, run.json contents identical; `--full-extract`, `--show-progress`, `--run-id`, `--concurrent-load` pass through; factory `ValueError` → today's prefixed abort at the site; every lifecycle failure → today's uncaught original-type traceback |
| `cli.py::sync_cmd` (serial branch only: `--no-parallel`, or `--parallel` with explicit `order:`) | same resolution, then — inside today's outer `with pipeline_lock(...)`, with the engine already constructed in the command body (see "Already-locked caller (CLI serial sync)") — `execute_run(instance, operation="sync", confirm_writes=True, print_diff=<--diff>, allow_rowcount_drop=..., continue_on_error=..., potenda_factory=<closure returning the already-constructed engine>, _serial_load_error=<unprefixed abort — see Failure semantics>, _lock_already_held=True, ...)` — the explicit human CLI invocation IS the confirmation | `--parallel ignored` warning and the parallel `sync_in_tiers` branch stay in cli.py untouched; factory → prefixed abort; serial-load `ValueError` → unprefixed abort; all other lifecycle failures → original-type traceback |
| `orchestration/flow.py` | `run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=os.environ["INFRAHUB_SYNC_CONFIG_DIRECTORY"])` — request parameters plus the config directory ONLY; the `_potenda_factory` / `_lock_timeout` private seams are never set by the flow | Engine defaults pinned; `show_progress=False`; failures surface ONLY as sanitized `RunValidationError` / `RunExecutionError` |

### Already-locked caller (CLI serial sync) — binding structure

`sync_cmd` today has ONE `with pipeline_lock(sync_instance.name):` block whose prologue
(factory call, `force_full_extract`, `run_dir is None` guard, `RunFile(...).save()`) runs
BEFORE the `if parallel and ptd.tiers:` branch (`cli.py:227-245`). The branch predicate
reads `ptd.tiers`, so it is knowable only once the engine exists; `diff_cmd`'s pattern
(delete the outer lock, let `execute_run` construct the engine) is therefore not
available here, and constructing the engine on both sides of the branch is forbidden —
`utils.get_potenda_from_instance` allocates a fresh `run_dir`/`run_id` whenever
`run_id is None` (which `sync_cmd` always passes), and `SyncInstance.compute_order_and_tiers`
re-emits its INFO `tier %d (%d): %s` lines, both of which would break byte-identity.

The pinned structure (binding):

- The engine is constructed **EXACTLY ONCE**, in the command body, inside today's outer
  `with pipeline_lock(...)` — so `ptd.tiers` stays readable for the branch predicate and
  the parallel branch keeps the lock it still needs.
- Construction keeps today's narrow handler at the site: the command body calls the CLI
  wrapper factory (prefixed abort on `ValueError`, `cli.py:237-238`), so factory failure
  behavior is unchanged for both branches.
- The serial branch then calls `execute_run` with
  `potenda_factory=<closure that ignores its keyword arguments and RETURNS THE
  ALREADY-CONSTRUCTED ENGINE>`. `execute_run` therefore performs no second construction
  and allocates no second `run_dir`/`run_id`; step 3's pinned seven-kwarg call shape
  (E23) is still made, the closure just discards it.
- Because that engine already exists inside the caller's lock, `execute_run`'s own step-2
  acquisition would self-deadlock in-process (a second same-process `FileLock` on the
  same path blocks the full timeout, then raises `filelock.Timeout` — probed, E21). The
  sanctioned avoidance is the CLI-only private seam `_lock_already_held=True`: the CLI
  serial path keeps today's outer lock and `execute_run` skips acquisition for this
  already-locked caller. No other caller sets it (`run_remote_request` never does).
- The prologue statements after construction (`ptd.force_full_extract = full_extract`,
  the `run_dir is None` guard, `RunFile(...).save()`) stay in the command body exactly as
  today, because the parallel branch needs them; `execute_run` repeating them on the same
  engine is idempotent (same attribute value, same guard, same `run.json` content at the
  same path).
- The `--parallel ignored` warning and the parallel `sync_in_tiers` branch remain in
  `cli.py` untouched (spec Out of Scope).

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

  `print_error_and_abort` raises `typer.Abort` (`cli.py:72-74`) — click's `Abort`,
  rendered as `Aborted.` with **exit code 1**, NOT `typer.Exit` (whose default
  code is 0, which would hide failures from CI — E19). The resulting `typer.Abort`
  propagates out of `execute_run` untouched (no run.json
  exists yet at factory time — the RunFile is created only after the engine
  allocates `run_dir`, and step 6's broad except has not been entered), so wording,
  exit code, and control flow are byte-identical to `cli.py:139-140` / `237-238`.
  A factory `ImportError` is caught nowhere on the CLI path and surfaces as today's
  uncaught traceback.
- **Serial-load site (unprefixed abort — sync command only)**: `execute_run`'s
  step-5 narrow `except ValueError` around `load_both_sides` marks run.json failed,
  saves, and invokes the CLI-supplied `_serial_load_error(exc)`
  (`lambda exc: print_error_and_abort(str(exc))`) — the unprefixed abort fires at
  the site exactly as `cli.py:263-268` today; the `typer.Abort` (a `RuntimeError`
  subclass, so the broad except does catch it — exit code 1, `Aborted.`) then passes through
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
  prefixed abort wording + **exit code 1 and the `Aborted.` output** (`typer.Abort`,
  not `typer.Exit` — E19); serial-load `ValueError` (sync) → unprefixed
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
