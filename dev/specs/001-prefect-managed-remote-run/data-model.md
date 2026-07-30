# Data Model: Prefect-Managed Remote Infrahub Sync Run

Entities owned or consumed by this feature. Typed definitions live in `contracts/`;
this file records fields, invariants, relationships, and state transitions.

## 1. Execution request (shared contract, owned by this feature)

The minimal input accepted by the shared execution surface and the Prefect flow. The
flow parameters correspond one-to-one; no additional remote parameter exists.

| Field | Type | Default | Semantics |
|---|---|---|---|
| `sync_name` | `str` | required | Opaque logical configuration name. Compared by exact string equality against the `name` field of each `config.yml` discovered recursively under the configured directory (same lookup as CLI `--name`/`--directory`, `infrahub_sync/utils.py::get_instance`/`get_all_sync`). Never used to construct a filesystem path. |
| `operation` | `Literal["plan", "sync"]` | `"plan"` | `plan` maps to the existing `diff` lifecycle; `sync` maps to the existing serial (`--no-parallel`) sync lifecycle. |
| `confirm_writes` | `bool` | `False` | Explicit write gate. Required `True` only for `operation="sync"`; with `plan` it has no effect and the run stays read-only. |
| `branch` | `str \| None` | `None` | Forwarded exactly as the CLI `--branch` option is today (adapter branch resolution: adapter settings `branch` → this value → `"main"` for infrahub-named adapters, per `utils.get_potenda_from_instance`). Only ever passed to the Infrahub API as a branch name; a nonexistent branch surfaces as `RunExecutionError` from the adapter/engine phase. |

**Validation order (all before any adapter object is constructed or connection
attempted — the single gate the spec's informed default defines):**

1. `operation` membership — enforced by Prefect parameter typing for remote runs
   (probe d₁: HTTP 409 at run creation, no flow run created) and by the surface's own
   `Literal` check for programmatic callers (`RunValidationError`).
2. `confirm_writes` gate — surface-level; `operation="sync"` with `confirm_writes`
   false/absent → `RunValidationError` whose message states that `confirm_writes=true`
   is required to run `operation=sync`.
3. `sync_name` resolution — surface-level; no exact match under the configured
   directory → `RunValidationError` naming the logical name and the fact that it was
   not found (never echoing directory contents).
4. Resolved-configuration readability/validity — pydantic `SyncConfig` parse errors on
   the matched `config.yml` → `RunValidationError` naming the configuration by logical
   name (and optionally the offending file), never printing file contents or credential
   values.

**Engine options pinned for remote runs** (CLI defaults at `9edc1bc`): full extract
(`full_extract=True`), concurrent side load (`concurrent_load=True`), rowcount
guardrail enforced with no drop allowance (`allow_rowcount_drop=False`),
continue-on-error off, no cache run-id reuse (`run_id=None` → fresh id), adapter paths
only from the resolved configuration (no `adapter_path` injection), progress display
disabled (`show_progress=False` — no progress-bar rendering in remote output or
forwarded logs).

## 2. RunResult (immutable success result)

Frozen dataclass, **exactly** these seven fields, no others; field values cannot be
reassigned after construction (both properties asserted by DBA-010 tests).

| Field | Type | Meaning / derivation |
|---|---|---|
| `sync_name` | `str` | Resolved logical configuration name (`SyncInstance.name`). |
| `operation` | `Literal["plan", "sync"]` | Requested operation. |
| `run_id` | `str` | Infrahub Sync cache run identifier allocated for the run (today `YYYYMMDDTHHMM-<8 hex>`, `cache/paths.py::generate_run_id`). Equals the final path segment of `artifact_path`. |
| `status` | `Literal["planned", "applied", "no-change"]` | `planned` = plan with changes; `applied` = sync that wrote; `no-change` = either operation with an empty plan. |
| `changed` | `bool` | Whether the plan contained destination changes. |
| `summary` | `dict[Literal["create", "update", "delete"], int]` | Flat per-action counts over the run's plan rows; all three keys always present (zero-filled). |
| `artifact_path` | `str` | Absolute runner-local run directory (`.infrahub-sync-cache/<sync_name>/<run_id>/`), containing at least `run.json` and `plan.parquet`. |

**Cross-field invariants** (enforced in `__post_init__`, unit-tested):

- `changed is True` ⇔ `status != "no-change"` ⇔ `sum(summary.values()) > 0`.
- `status == "planned"` only with `operation == "plan"`.
- `status == "applied"` only with `operation == "sync"`.
- `run_id == Path(artifact_path).name`.
- `set(summary.keys()) == {"create", "update", "delete"}`.

**Derivation**: `summary` is counted from the same diff-materialized plan rows the
engine writes to `plan.parquet` (`Potenda._diff_to_rows`), counting rows whose `action`
is `create`/`update`/`delete`.

## 3. Failure contract

Two exception classes, both defined in `infrahub_sync/execution.py`, both carrying a
specific human-readable cause with configured secret values redacted (`***`).

| Class | Owns | Qualifying causes |
|---|---|---|
| `RunValidationError` | Every input-boundary refusal | unconfirmed `sync`; unknown `sync_name`; path-like or command-like `sync_name` values (refused by the no-match rule — never interpreted); unreadable or invalid resolved configuration |
| `RunExecutionError` | Adapter/engine failure | missing runner-environment credentials (adapter init `ValueError` — e.g. "Both url and token must be specified!"); unreachable source/destination systems; nonexistent Infrahub branch; pipeline-lock contention (`filelock.Timeout` after the existing 60 s acquisition timeout); adapter import failure (`ImportError` from `get_potenda_from_instance`) |

Semantics:

- "Specific" = the message names the failing input or stage and the underlying cause.
- "Sanitized" = the message contains no configured secret values; redaction is
  value-based over: the values of runner-environment credentials (`INFRAHUB_API_TOKEN`)
  and secret-valued keys (`token`, `password`, `secret`, `api_key`) found in the
  resolved configuration's adapter settings. The same redaction obligation applies to
  forwarded log records (DBA-008 canary scan).
- Either class ⇒ no `RunResult` is returned, `run.json` (if already created) is left
  with `status="failed"` exactly as today's CLI does, and the Prefect flow ends FAILED.

## 4. Sync configuration (existing entity, consumed)

An existing Infrahub Sync project directory: `config.yml` (pydantic `SyncConfig`) plus
generated adapter/model modules. Installed manually on the runner. Selected by logical
name from **one** server-configured directory:

- Directory source: required env var `INFRAHUB_SYNC_CONFIG_DIRECTORY`, read by the
  serving process at startup; missing or non-directory value → serve process exits with
  an error naming the variable before any deployment is served.
- The directory path is fixed at serve start; its **contents** are re-resolved on every
  run (`get_all_sync` re-globs `**/config.yml`), so add/edit/remove takes effect next
  run without re-serving.

## 5. Canonical plan fingerprint (new derived value)

`compute_plan_fingerprint(run_dir: Path) -> str` in `infrahub_sync/cache/fingerprint.py`
— the single shared helper both sides of the DBA-009/SC-007 comparison use.

Definition (spec clarification #1): SHA-256 hex digest over the run's plan rows —
fields `action`, `resource`, `source_id`, `attribute`, `new_value` from
`<run_dir>/plan.parquet` — rows sorted by `(resource, source_id, action, attribute)`
with the row's full serialized form as final tie-breaker; each row serialized as
compact sorted-key JSON (`json.dumps(row, sort_keys=True, separators=(",", ":"))`);
rows joined by `"\n"`; UTF-8 encoded. Timestamps, run identifiers, and filesystem paths
are excluded by construction (they are not among the five fields).

## 6. Run artifacts (existing entities, unchanged shape)

| Artifact | Producer | Notes |
|---|---|---|
| `run.json` (`cache/sidecars.py::RunFile`) | surface (same writes the CLI makes today) | `plan` lifecycle: `mode="diff"`, `running → dry-run` (or `failed`); `sync` lifecycle: `mode="sync"`, `running → applied` (or `failed`); `summary`/`finished_at` as today |
| `plan.parquet` (`cache/parquet_io.py::PLAN_SCHEMA`) | engine (`Potenda.write_plan`) | Target of DBA-003's plan-artifact evidence and of the fingerprint |
| Side snapshots `A/*.parquet`, `B/*.parquet`, `cursors.json`, baseline sidecars | engine | Unchanged behavior |

## 7. Prefect-side records (external, consumed via REST)

| Record | How obtained | Used for |
|---|---|---|
| Deployment | `GET /api/deployments/name/infrahub-sync/infrahub-sync` | Deployment id for run creation; `enforce_parameter_schema=true` (server-side enum guard on `operation`, probe a₆/d₁) |
| Flow run | `POST /api/deployments/{id}/create_flow_run` (returns id synchronously, state SCHEDULED) → `GET /api/flow_runs/{id}` | SC-001 run identifier + lifecycle observation |
| Run logs | `POST /api/logs/filter` with `flow_run_id` filter | DBA-004/SC-002 — bridged `infrahub_sync` lifecycle lines (probe c₁) |

## State transitions

```text
Remote request ──(Prefect param schema: bad operation)──▶ HTTP 409, no flow run   [probe d₁]
      │
      ▼ flow run SCHEDULED → RUNNING (flow body)
validate confirm_writes ──fail──▶ RunValidationError ─▶ flow FAILED (no adapters built)
      │
resolve sync_name ──no match / invalid config──▶ RunValidationError ─▶ flow FAILED
      │
acquire pipeline lock ──60 s timeout──▶ RunExecutionError ─▶ flow FAILED
      │
build Potenda (adapters init) ──ValueError/ImportError──▶ RunExecutionError ─▶ flow FAILED
      │                                                    (run.json may be "failed")
      ▼
plan lifecycle: load → diff → write_plan → run.json dry-run ─▶ RunResult(planned|no-change)
sync lifecycle: load → guardrail → diff → write_plan → [sync if diffs] → baseline →
                run.json applied ─▶ RunResult(applied|no-change)
      │
      ▼ flow COMPLETED, returns asdict(RunResult)
```
