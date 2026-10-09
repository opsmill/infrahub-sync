---
title: "The shared execution surface"
---

## The shared execution surface

`infrahub_sync/execution.py` runs individual plan, verification and apply operations for
the Sync service and direct Python callers. The CLI submits registered runs through the
Sync HTTP API; the worker calls this module to execute them.

For tracing a registered write, start with the service stages and their
[configuration write guard](apply-guard.md). For a direct Python or Prefect plan, start
with `execute_run` or `run_remote_request`. The direct Prefect integration resolves a local
configuration and only plans; it does not perform the registered service workflow.

The module imports no Prefect symbols or orchestration modules and remains importable
in a base install. [ADR 9][adr-imports] explains the optional-package boundary.
The behavior below follows the [released execution source][execution-source].

### Callers and entry points {#the-three-callers}

| Caller | Entry point and responsibility |
| --- | --- |
| CLI `diff`, `sync`, `apply` | Call `SyncClient` methods over HTTP; inspect API run and plan resources. They do not call `execute_run`. |
| Sync service worker | Call `execute_run` for each stage, passing `base_directory` for that stage's private scratch directory. A service `sync` composes plan, verify and apply. |
| Direct Python caller | Pass a resolved `SyncInstance` to `execute_run`; the operation selects the lifecycle and return type. |
| Direct Prefect flow | Call `run_remote_request`, which resolves a configuration name and calls `execute_run` in-process for a plan. |

The [CLI][cli-source] constructs requests through `SyncClient`.
The [service flow][flow-source] retrieves retained plans from object storage into private
scratch directories; each stage removes its scratch when it ends. The CLI reads results
through the API rather than opening those directories.

`base_directory` takes precedence over `INFRAHUB_SYNC_CACHE_DIR` and the working directory.
Without it, direct execution uses the environment's cache setting or defaults to
`<cwd>/.infrahub-sync-cache/<sync_name>/`. A runner-local path is not a durable service
artifact URL; see [durable product records](../../reference/durable-product-records.mdx)
for retained artifacts and results.

### Operations and return types

The `Operation` type includes `"plan"`, `"verify"`, `"apply"` and `"sync"`, but the core
refuses `"sync"` at runtime and provides no overload for it. Registered `sync` is a
service composition of the other operations under one configuration guard.

| `execute_run` operation | Inputs and behavior | Return type |
| --- | --- | --- |
| `plan` | Load both adapters, compare data and save proposed operations. Refuse to overwrite a committed plan under an existing `run_id`. | `RunResult`; `SavedPlan` when the service sets `_return_saved_plan=True`. |
| `verify` | Require `run_id` and read an existing saved plan without constructing adapters. | `SavedPlan`. |
| `apply` | Require `run_id`, `confirm_writes=True`, `ownership` and `record_applied`. Construct the destination and execute saved operations. | `RunResult`. |
| `sync` | Raise `RunValidationError` before constructing adapters or run state. | No result. |

Verification normally returns review data, including checksum status and verification
notes. The service sets `_require_verified=True` to run the full saved-plan verifier and
raise `PlanVerificationError` if checks fail. A returned `SavedPlan` from an ordinary
review call is therefore not proof that the artifact passed every apply check.

For apply, `ownership` implements `WriteOwnership`: it proves the caller's right to write
before each dispatched destination operation and after the final operation.
`record_applied` receives the completed `ApplyRecord` before saving the applied run state.
The service uses that callback to retain write evidence if later cleanup or persistence
fails.

### Direct name resolution and Prefect execution

`run_remote_request(sync_name, operation, confirm_writes, branch, config_directory=…)`
resolves an exact logical name from `config.yml` files beneath `config_directory`.
The Python caller supplies that directory; the packaged Prefect flow reads it from
`INFRAHUB_SYNC_CONFIG_DIRECTORY`. The four flow parameters contain no configuration path,
credentials, CLI fragments or environment overrides.

Only `operation="plan"` executes through this direct integration. `"sync"` is refused
before configuration resolution, even with `confirm_writes=True`. Other engine options
use `execute_run` defaults, except `show_progress=False`.
See [Prefect orchestration](orchestration-prefect.md#the-flow) for the flow parameters,
logging and serve entrypoint.

### `RunResult`

Plan and apply normally return a frozen, slotted data class with seven fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `sync_name` | `str` | Resolved logical configuration name. |
| `operation` | `Operation` | The core operation that produced the result: `plan` or `apply`. |
| `run_id` | `str` | Run directory name. |
| `status` | `Status` | `planned`, `applied` or `no-change`, derived from operation counts. |
| `changed` | `bool` | Whether the operation-count total is greater than zero. |
| `summary` | `Mapping[ActionKey, int]` | Counts for `create`, `update` and `delete`, including zero counts. |
| `artifact_path` | `str` | Absolute runner-local run directory; its final path segment equals `run_id`. |

`RunResult.__post_init__` validates these relationships and raises `ValueError` when they
do not hold:

- `changed`, `status != "no-change"` and `sum(summary.values()) > 0` must agree.
- `status="planned"` requires `operation="plan"`.
- `status="applied"` permits `operation="apply"` or `"sync"` in the data class, although
  `execute_run` refuses a sync request.
- `artifact_path` must be absolute and end in `run_id`.
- `summary` must contain exactly `create`, `update` and `delete`.

The data class copies `summary` into a `MappingProxyType` to prevent mutation of its counts.
Freezing the data class alone would only prevent rebinding the field. To serialize the
result, copy its fields and convert `summary` to a plain dictionary; `dataclasses.asdict`
cannot deep-copy this mapping. The
[direct flow's return conversion](orchestration-prefect.md#the-return-value-is-built-by-hand-not-with-asdict)
shows the implementation.

For a plan, counts come from the saved operations returned by the plan writer. Injected
test engines whose writer returns no counts fall back to their in-memory plan rows.
For apply, counts come from the exact artifact consumed by the apply engine.

**These counts describe planned operations, not confirmed destination writes.** Apply
skips deletes but includes them in `summary`. A delete-only apply can therefore return
`status="applied"` and `changed=True` while dispatching no destination operations.
The `ApplyRecord` separately records applied operation IDs and skipped delete IDs.
See [planned writes and apply](planned-write-and-apply.md) for those records.

### Failure model

`execute_run` raises `RunValidationError` for invalid operation inputs and
`RunConcurrencyError` when its local pipeline lock remains unavailable after the bounded
wait. Adapter, plan and engine failures otherwise retain their lifecycle exception types.
A raised exception means the call returned no success result; it does not mean the
destination was unchanged.

The core attempts to save `run.json` with `status="failed"` when a started lifecycle fails.
If both persistence attempts fail, the core logs a warning and preserves the original
failure. Input refusals can happen before any run file exists.

`run_remote_request` is the direct integration's sanitize-and-wrap boundary:

- `RunValidationError` reports a refused request or unresolved/invalid configuration.
- `RunExecutionError` wraps other `Exception` failures, including lock contention,
  adapter initialization and import failures, with redacted messages and causes.

The registered service has its own failure boundary in `service_sync_run`. It records
available write evidence and sanitizes the exception before raising to Prefect.
Neither wrapper rolls back completed destination writes. Follow the
[uncertain-write procedure](../../compose-deployment.mdx#when-the-outcome-of-a-write-is-uncertain)
when a service run reports an ambiguous outcome. See
[secret redaction](../guidelines/secret-redaction.md) for the shared redaction functions.

### The lock and the already-locked caller

Plan and apply acquire a per-configuration filesystem pipeline lock unless the caller
sets `_lock_already_held=True`. The default wait is 60 seconds. On contention, the core
reports the latest local run file still marked running, if available, as diagnostic
context; that file can be stale and does not prove lock ownership.

Both the lock and that lookup use `base_directory`. They coordinate callers sharing one
cache root. In the service's private stage directory they cannot exclude another worker,
so registered writes acquire the [PostgreSQL configuration guard](apply-guard.md).
The service sets `_lock_already_held=True` on its apply calls while holding that guard.
The direct remote wrapper never sets it. Verification takes no pipeline lock.

Planning calls `PotendaFactory` with eight explicit keyword arguments: `sync_instance`,
`branch`, `show_progress`, `verbosity`, `run_id`, `continue_on_error`, `concurrent_load` and
`base_directory`. The protocol preserves those parameter names for type checking of
injected factories. Apply uses `PlanApplier.open_existing` to construct only the
destination adapter; it does not call the planning factory or reload the source.

### Plan fingerprint

`cache.fingerprint.compute_plan_fingerprint(run_dir)` hashes five fields — `action`,
`resource`, `source_id`, `attribute` and `new_value` — from every row of the legacy
`plan.parquet` file for comparisons. It excludes timestamps, run identifiers and
paths; [ADR 7][adr-fingerprint] records that comparison algorithm.

The registered apply approval instead uses the saved artifact's `plan_checksum`.
Do not substitute the legacy fingerprint for the checksum returned by `runs plan`.
See [the saved plan artifact](plan-artifact.md) for the manifest and checksum rules.

### See also

- [Sync architecture](sync-architecture.md) — registered run submission, execution and persistence.
- [The configuration write guard](apply-guard.md) — acquisition, ownership checks and release.
- [Prefect orchestration](orchestration-prefect.md) — service and direct-flow integrations.
- [Testing](../guidelines/testing.md) — execution contracts and test isolation.

[execution-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/execution.py
[cli-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/cli.py#L519-L579
[flow-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/flow.py
[adr-imports]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/dev/adr/0009-optional-integrations-live-in-their-own-package.md
[adr-fingerprint]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/dev/adr/0007-canonical-plan-fingerprint-as-equivalence-oracle.md
