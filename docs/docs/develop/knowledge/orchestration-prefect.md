---
title: "Prefect orchestration"
---

## Prefect orchestration

Prefect executes Sync work through two integrations. Registered runs use the Sync API and
its service worker; the direct integration runs a read-only plan from a local configuration
without the Sync API.

| Integration | Input | Where to inspect the result |
| --- | --- | --- |
| Registered service | An API-created product run, bound to a registered configuration version | Sync API run, plan and results resources, with links to Prefect executions |
| Direct Prefect | A read-only plan for a configuration name resolved from the serving process's directory | Prefect flow-run logs and the summary line |

For the supported deployment and the lifecycle of a registered run, start with
[Sync architecture](sync-architecture.md). The direct integration is a separate optional
Python entrypoint, not the Compose operator workflow.

### Registered service integration

`infrahub_sync/service/` provides the Sync API and its Prefect integration, installed by
the `service` extra. The API submits work to `infrahub-sync-service/run`; a process worker
executes the installed `infrahub_sync.service.flow.service_sync_run` function.

The service uses the vendored OpsMill Prefect Extras package for deployment catalogue
validation, deployment convergence and submission idempotency. The API retains product
records; Prefect records live execution state and logs. See
[durable product records](../../reference/durable-product-records.mdx) for the distinction.

```python
@flow(name="infrahub-sync-service")
def service_sync_run(
    run_id: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    config_id: str | None = None,
    registry_version: int | None = None,
    package_checksum: str | None = None,
    branch: str | None = None,
    expected_checksum: str | None = None,
    confirm_writes: bool = False,
) -> dict[str, Any]: ...
```

Registered execution supplies all three configuration fields: `config_id`,
`registry_version` and `package_checksum`. The worker checks them against the product run
and registered package. A partial binding is refused; the all-unset form is retained for
the legacy path, and managed writes require a registered binding.

The worker reads endpoint settings from the package and resolves credential references
in its environment. Each stage uses private
temporary files and exchanges plans through object storage; it reads no shared cache
location. See the [registered-run lifecycle](sync-architecture.md#one-registered-run) for
planning, verification, approved writes and result persistence.

### Direct integration {#the-flow}

`infrahub_sync/orchestration/` provides the optional direct integration, installed by the
`prefect` extra. It calls `run_remote_request` in
[the shared execution module](execution-surface.md) in-process.
The flow retains four parameters:

```python
@flow(name="infrahub-sync")
def infrahub_sync_run(
    sync_name: str,
    operation: Literal["plan", "sync"] = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
) -> dict[str, Any]: ...
```

Only `operation="plan"` executes here. Although `"sync"` remains in the parameter schema,
`run_remote_request` refuses it before resolving a configuration, even when
`confirm_writes=True`. Submit writes through the Sync API.

None of the four parameters accepts a path, a CLI fragment, a credential, or an
environment override. Other runtime settings come from the serving process's
configuration and environment.

The flow name is `infrahub-sync` and the deployment name is `run`, so the deployment lookup
path is `/api/deployments/name/infrahub-sync/run`.

The flow attaches the log bridge, reads `INFRAHUB_SYNC_CONFIG_DIRECTORY`, calls
`run_remote_request`, logs the summary and returns a dictionary. Exceptions propagate to
Prefect with sanitized messages.

#### The summary line is the supported result surface

```python
SUMMARY_LINE_FORMAT = "run %s finished: status=%s changed=%s summary=create:%d,update:%d,delete:%d artifact=%s"
```

This line is how a remote caller reads a run's outcome, and its format is contractual —
never a Python dictionary `repr`. It contains five `RunResult` fields: `run_id` (the leading
substitution), `status`, `changed`, the three summary counts, and `artifact_path`.
`sync_name` and `operation` deliberately do not appear. Changing the format is a breaking
change for consumers.

Result retrieval through Prefect's own result persistence is not part of the contract.

#### The return dictionary {#the-return-value-is-built-by-hand-not-with-asdict}

The flow builds a seven-key dictionary from the `RunResult` fields:

```python
out = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
out["summary"] = dict(result.summary)
```

`dataclasses.asdict(result)` cannot be used. It deep-copies field values, and `RunResult`
wraps `summary` in a `MappingProxyType`, which is not deep-copyable —
`TypeError: cannot pickle 'mappingproxy' object`. Using it here would fail a successful run while
constructing the return value.

### The log bridge

`RunLoggerBridge` forwards records from the `infrahub_sync` logger hierarchy to the Prefect
run logger, preserving each record's level and originating logger name and redacting secrets.
The direct flow sets the source logger to `INFO` and disables propagation while the bridge
is attached. In `finally`, it removes the bridge and restores both the previous level and
propagation setting.

Those settings are process-global. `_REMOTE_LOGGER_OWNERSHIP_LOCK` serializes the bridge
scope within a process, so concurrent flow calls cannot attach competing bridges or restore
each other's logger settings. It does not serialize runs in separate processes.
The service flow uses the same lock and bridge when a Prefect run context exists.
In offline executor tests, it catches `MissingContextError` and uses its module logger
without attaching a bridge.

#### The serve entrypoint

Run as `python -m infrahub_sync.orchestration.serve`. It:

1. checks for a missing `prefect` import, reports the extra and install command, and exits
   non-zero; unrelated import failures propagate;
2. reads `INFRAHUB_SYNC_CONFIG_DIRECTORY` and, if it is unset, empty, or not an existing
   directory, emits one error line **naming the variable** and exits non-zero before any
   deployment is served;
3. calls `infrahub_sync_run.serve(name=DEPLOYMENT_NAME)` — a locally served deployment, no
   work pool, no separate worker;
4. serves until interrupted.

Startup errors are written to standard error through `sys.stderr.write`.

The directory path is fixed at serve start; its *contents* are re-resolved on every run, so
configurations added, edited or removed take effect on the next run without re-serving.

The serve process must be started from the repository root for the shipped example to work:
its `config.yml` uses repository-root-relative paths resolved against the serving process's
working directory, and the cache root defaults to `Path.cwd()/.infrahub-sync-cache`. Started
elsewhere, the example degrades to a silently empty plan or an adapter import failure.

#### Remote interaction

Direct-integration callers use Prefect's API. The paths below are relative to the server
origin; `$PREFECT_API_URL` normally already includes `/api`.

| Step | Request |
|---|---|
| Find the deployment | `GET /api/deployments/name/infrahub-sync/run` |
| Create a run | `POST /api/deployments/{id}/create_flow_run` with `{"parameters": {…}}` |
| Observe state | `GET /api/flow_runs/{id}` |
| Read logs and the summary line | `POST /api/logs/filter` with `{"logs": {"flow_run_id": {"any_": ["{id}"]}}}` |

Run creation returns the flow-run identifier **synchronously**, in state `SCHEDULED`.

A served deployment defaults to `enforce_parameter_schema=True`, so the `operation`
annotation becomes an enum in the deployment's parameter schema and an invalid value is
refused at run *creation*: `POST … /create_flow_run` with `"operation": "apply"` returns
**HTTP 409** and **no flow run object is created at all**. Input validation for that
parameter therefore never reaches the flow body.

#### Prefect-specific traps

- **Keep concrete parameter annotations.** Deferred annotations caused
  `PydanticUndefinedAnnotation: name 'Literal' is not defined` before the flow body ran
  on Prefect 3.5.0. The module omits `from __future__ import annotations`, and a test
  checks that the operation annotation resolves to `Literal["plan", "sync"]`.
  The tests also check refusal of an invalid operation during parameter validation on
  Prefect 3.8.1.
- **`PREFECT_LOCAL_STORAGE_PATH` does not follow `PREFECT_HOME`.** Redirecting
  `PREFECT_HOME` isolates the database but not persisted run results. Test isolation — and
  any operator who wants one directory — needs both variables set.
- **`dataclasses.asdict()` cannot copy a `MappingProxyType` field** — construct the return
  dictionary explicitly, as shown above.
- **Pinning the version is not optional here.** The extra pins `prefect==3.8.1` exactly,
  because the base dependency set and Prefect's transitive `redis` requirement interact —
  see [ADR 8](https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/dev/adr/0008-declare-redis-directly-instead-of-the-diffsync-extra.md).

### Optional imports

The `orchestration` and `service` packages contain Sync's Prefect imports; a base install
loads neither integration. The `service` extra also installs the dependencies used by
`opsmill_prefect_extras/`. See
[ADR 9](https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/dev/adr/0009-optional-integrations-live-in-their-own-package.md)
for the optional-package boundary.

### See also

- [The shared execution surface](execution-surface.md) — what the flow actually calls.
- [Quality gates](quality-gates.md) — the two CI test legs this integration adds.
- [Direct-flow example](https://github.com/opsmill/infrahub-sync/tree/f98a845986d1f03503d321ce5561b65a3946bf74/examples/prefect_remote_run) — the schema and sample requests.
- [Direct Prefect reference](../../reference/prefect-remote-run.mdx) — configure and call the direct integration.
