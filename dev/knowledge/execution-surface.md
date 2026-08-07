# The shared execution surface

> Part of: `dev/knowledge/` | Related: [Sync architecture](sync-architecture.md), [Prefect orchestration](orchestration-prefect.md)

<!-- Extracted from dev/specs/archive/001-prefect-managed-remote-run on 2026-07-31 -->

`infrahub_sync/execution.py` is the typed Python entry point to a single sync run. It exists
because the plan and serial-sync lifecycles need more than one caller: the CLI drives them
from a terminal, and the packaged Prefect flow drives them in a served process. Rather than
letting the flow shell out to the CLI or duplicate the lifecycle, both go through the same
functions and get the same result object.

The module is deliberately import-light. It imports no Prefect symbol and nothing from
`infrahub_sync.orchestration`, so it stays importable in a base install — see
[ADR 5](../adr/0005-optional-integrations-live-in-their-own-package.md). It is its own module
rather than an addition to `utils.py`, which is already broad and would blur the seam.

## The three callers

| Caller | Entry point |
|---|---|
| CLI `diff` | `execute_run(instance, operation="plan", …)` |
| CLI `sync --no-parallel` (serial branch only) | `execute_run(instance, operation="sync", confirm_writes=True, …)` |
| `orchestration/flow.py` | `run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=…)` |

The parallel sync branch (`sync_in_tiers`) is not behind this surface; it stays in `cli.py`.

`run_remote_request` is the remote-shaped composition: it resolves a logical name against a
directory, then calls `execute_run` with every engine option at its CLI default except
`show_progress=False`. No public parameter of it accepts paths, CLI fragments, credentials,
or environment overrides.

## `RunResult`

A successful run returns a frozen, slotted dataclass with exactly seven fields:

```python
@dataclass(frozen=True, slots=True)
class RunResult:
    sync_name: str        # resolved logical configuration name
    operation: Operation  # "plan" | "sync"
    run_id: str           # cache run id; equals Path(artifact_path).name
    status: Status        # "planned" | "applied" | "no-change"
    changed: bool
    summary: Mapping[ActionKey, int]  # create/update/delete, always all three, zero-filled
    artifact_path: str    # absolute runner-local run directory
```

`__post_init__` does two things. It wraps `summary` in a `types.MappingProxyType` via
`object.__setattr__`, because `frozen=True` prevents rebinding a field but not mutating the
dict behind it. And it validates cross-field invariants, raising `ValueError` — an invariant
violation is a bug in the surface, not a run failure:

- `changed` ⇔ `status != "no-change"` ⇔ `sum(summary.values()) > 0`
- `status == "planned"` implies `operation == "plan"`; `status == "applied"` implies
  `operation == "sync"`
- `artifact_path` is absolute, and `run_id == Path(artifact_path).name`
- `set(summary) == {"create", "update", "delete"}`

`artifact_path` is required absolute because it crosses a process boundary: a remote caller
cannot recover the serving process's working directory. `cache.paths.cache_root_for`
absolutizes a relative cache directory at the single derivation point, using `absolute()`
rather than `resolve()` so the final path segment the `run_id` invariant compares against
survives.

`summary`, `changed` and `status` derive from the authoritative saved operations that the
real engine just derived in memory and handed to the artifact writer, never by re-reading
`plan.parquet`. Behavioral test engines with the legacy no-return `write_plan` shape fall
back to their in-memory materialized plan rows, which keeps the execution seam injectable
without weakening production delete reporting.

One test-seam fidelity boundary is worth knowing: a legacy behavioral engine that returns
no saved-operation counts falls back to the row materializer, which walks only the diff
root's direct children, while `Diff.has_diffs()` is recursive. Such a fake can therefore
execute a sync for nested-only changes but report `status="no-change"`; a unit test pins that
fallback behavior. The real saved-plan engine instead refuses unwalked nested elements
during plan derivation, before it can return a misleading result.

## Failure model

Two exception types make up the remote contract:

- `RunValidationError` — every input-boundary refusal: `operation="sync"` without
  `confirm_writes`, a `sync_name` that matches no installed configuration, or a matched
  configuration that is unreadable or invalid.
- `RunExecutionError` — an adapter or engine failure after validation passed: missing
  runner-environment credentials, an unreachable system, a nonexistent Infrahub branch,
  pipeline-lock contention, or an adapter import failure.

Both are raised in one place only. `execute_run` re-raises original exception types;
`run_remote_request` is the sole sanitize-and-wrap boundary. That split is what keeps CLI
failure behaviour identical, and it is the subject of
[ADR 1](../adr/0001-translate-run-failures-only-at-the-remote-boundary.md). Message
sanitization rules are in
[Secret redaction](../guidelines/secret-redaction.md).

A raise means no `RunResult` exists for that run. Any `run.json` already created is left at
`status="failed"`.

## The lock and the already-locked caller

`execute_run` acquires the per-configuration pipeline lock with the same 60-second timeout
the CLI has always used, and lets `filelock.Timeout` propagate unchanged.

The CLI serial-sync path is the exception. `sync_cmd` must construct the engine inside its
own outer `with pipeline_lock(...)`, because the parallel/serial branch predicate reads
`ptd.tiers` and is only knowable once the engine exists — and constructing the engine twice
would allocate a second run directory and re-emit the tier log lines. A second same-process
`FileLock` on the same path does not re-enter: it blocks for the full timeout and then
raises. So the CLI serial branch passes `_lock_already_held=True` and a factory closure that
returns the already-constructed engine, and `execute_run` runs the lifecycle inside the
caller's lock. No other caller sets it; `run_remote_request` never does.

`execute_run` calls the factory with all seven keyword arguments of
`utils.get_potenda_from_instance`, always explicitly, for both operations. The two CLI
commands historically passed different subsets whose omitted values defaulted to exactly
what the surface now passes, so the real factory behaves identically — but a fake factory in
a test does see the difference, which is why the call shape is a `Protocol` rather than a
bare `Callable`: a rename in the factory becomes a type error instead of a runtime
`TypeError` inside the remote boundary.

## Plan fingerprint

`infrahub_sync/cache/fingerprint.py::compute_plan_fingerprint(run_dir)` returns a SHA-256
digest over the canonicalized plan rows, excluding timestamps, run identifiers and paths by
construction. It is how "the remote path produced the same plan as the CLI" is tested. The
algorithm and its compatibility rules are in
[ADR 3](../adr/0003-canonical-plan-fingerprint-as-equivalence-oracle.md).

## See also

- [Prefect orchestration](orchestration-prefect.md) — the packaged remote caller.
- [Secret redaction](../guidelines/secret-redaction.md) — the rules the wrap boundary applies.
- [Testing](../guidelines/testing.md) — how contract-bearing behaviour here is tested.
