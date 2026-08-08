# Prefect orchestration

> Part of: `dev/knowledge/` | Related: [The shared execution surface](execution-surface.md)

<!-- Extracted from dev/specs/archive/001-prefect-managed-remote-run on 2026-07-31 -->

`infrahub_sync/orchestration/` is a packaged Prefect integration: a flow that runs one plan
or one confirmed sync, and a serve entrypoint that exposes it as a locally served
deployment. It is the only package in the repository that imports `prefect`, it is installed
by the optional `prefect` extra, and nothing in the base package imports it — see
[ADR 9](../adr/0009-optional-integrations-live-in-their-own-package.md).

The flow calls [the shared execution surface](execution-surface.md) in-process. It never
spawns the CLI.

## The flow

```python
@flow(name="infrahub-sync")
def infrahub_sync_run(
    sync_name: str,
    operation: Literal["plan", "sync"] = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
) -> dict: ...
```

Exactly those four parameters. None of them accepts a path, a CLI fragment, a credential, or
an environment override. Everything else the run needs comes from the serving process's own
environment.

`FLOW_NAME` is `infrahub-sync` and `DEPLOYMENT_NAME` is `run`, so the deployment lookup path
is `/api/deployments/name/infrahub-sync/run` rather than a stuttering repeat of the flow
name, and later orchestration work gets sibling deployments as `.../infrahub-sync/<verb>`.

The body, in order: attach the log bridge and take ownership of the source logger's level,
read the configuration directory from the environment, call `run_remote_request`, log one
summary line, return a dict. Exceptions propagate — Prefect marks the run FAILED and stores
the sanitized message as the state message.

### The summary line is the supported result surface

```python
SUMMARY_LINE_FORMAT = "run %s finished: status=%s changed=%s summary=create:%d,update:%d,delete:%d artifact=%s"
```

This line is how a remote caller reads a run's outcome, and its format is contractual —
never a Python dict repr. It carries five `RunResult` fields: `run_id` (the leading
substitution), `status`, `changed`, the three summary counts, and `artifact_path`.
`sync_name` and `operation` deliberately do not appear. Changing the format is a breaking
change for consumers.

Result retrieval through Prefect's own result persistence is not part of the contract.

### The return value is built by hand, not with `asdict`

The flow returns an `asdict`-*shaped* seven-key dict built explicitly:

```python
out = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
out["summary"] = dict(result.summary)
```

`dataclasses.asdict(result)` cannot be used. It deep-copies field values, and `RunResult`
wraps `summary` in a `MappingProxyType`, which is not deep-copyable —
`TypeError: cannot pickle 'mappingproxy' object`. Every successful run would fail at return
time. The reason is recorded at the construction site so nobody simplifies it back.

## The log bridge

`RunLoggerBridge` is a `logging.Handler` attached to `logging.getLogger("infrahub_sync")`
immediately before the surface call and removed in a `finally`. Each record is re-logged
through Prefect's run logger preserving the level and the originating logger name, so
lifecycle lines from anywhere in the `infrahub_sync` hierarchy show up in the flow-run log
and are retrievable through the API.

**The flow owns the logger's level, not just its handler.** A handler never defeats
`Logger.isEnabledFor`, and the `infrahub_sync` hierarchy is level-`NOTSET` outside the CLI —
the CLI makes INFO effective in `_setup_logging`, which the flow never calls. So the flow
captures the logger's current level, sets `INFO` before the call, and restores the captured
level in the same `finally` that removes the handler. With both, forwarding does not depend
on ambient root-logger configuration or on operator-set Prefect logging environment
variables.

**This mutates process-global logging state**, which is only safe because each flow run gets
its own process — the default behaviour of `flow.serve(...)`. Two concurrent in-process runs
of *different* configurations are not excluded by the per-configuration pipeline lock, so
they would cross-attach bridges (one run's records, adapter detail included, forwarded into
the other run's log) and one run's `finally` would restore the level under the other. If
flows are ever run in-process, the bridge must key on the current run and the level mutation
must be reference-counted.

## The serve entrypoint

Run as `python -m infrahub_sync.orchestration.serve`. It:

1. guards the `prefect` import and, on `ImportError`, emits one error line naming the extra
   and the install command, then exits non-zero;
2. reads `INFRAHUB_SYNC_CONFIG_DIRECTORY` and, if it is unset, empty, or not an existing
   directory, emits one error line **naming the variable** and exits non-zero before any
   deployment is served;
3. calls `infrahub_sync_run.serve(name=DEPLOYMENT_NAME)` — a locally served deployment, no
   work pool, no separate worker;
4. serves until interrupted.

Error lines go through `logging` or `sys.stderr.write`, never `print()`: the repository has
an AST-level test that forbids `print` in package modules.

The directory path is fixed at serve start; its *contents* are re-resolved on every run, so
configurations added, edited or removed take effect on the next run without re-serving.

The serve process must be started from the repository root for the shipped example to work:
its `config.yml` uses repo-root-relative paths resolved against the serving process's
working directory, and the cache root defaults to `Path.cwd()/.infrahub-sync-cache`. Started
elsewhere, the example degrades to a silently empty plan or an adapter import failure.

## Remote interaction

Everything a caller does goes through Prefect's own API under `$PREFECT_API_URL`.

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

## Prefect-specific traps

Recorded because each cost real measurement time.

- **A flow module must not use `from __future__ import annotations`** — at least not
  reliably. With deferred annotations, `Flow.validate_parameters` →
  `ValidatedFunction.model_rebuild` fails with
  `PydanticUndefinedAnnotation: name 'Literal' is not defined` and the run ends FAILED
  before the body executes. Observed on Prefect 3.5.0; re-measured on 3.8.1, where the
  refusal still works. The failure is version-specific, so the module keeps the omission
  (against the repository's convention, with a comment saying why) and a test pins that the
  annotation resolves.
- **`PREFECT_LOCAL_STORAGE_PATH` does not follow `PREFECT_HOME`.** Redirecting
  `PREFECT_HOME` isolates the database but not persisted run results. Test isolation — and
  any operator who wants one directory — needs both variables set.
- **`dataclasses.asdict()` cannot copy a `MappingProxyType` field**, as above.
- **Pinning the version is not optional here.** The extra pins `prefect==3.8.1` exactly,
  because the base dependency set and Prefect's transitive `redis` requirement interact —
  see [ADR 8](../adr/0008-declare-redis-directly-instead-of-the-diffsync-extra.md).

## See also

- [The shared execution surface](execution-surface.md) — what the flow actually calls.
- [Quality gates](quality-gates.md) — the two CI test legs this integration adds.
- `examples/prefect_remote_run/` — the runnable example and its request corpus.
- `docs/docs/reference/prefect-remote-run.mdx` — the user-facing reference page.
