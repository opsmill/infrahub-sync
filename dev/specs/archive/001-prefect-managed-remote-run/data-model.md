# Data Model: Prefect-Managed Remote Infrahub Sync Run

Entities owned or consumed by this feature. Typed definitions live in `contracts/`;
this file records fields, invariants, relationships, and state transitions.

## 1. Execution request (shared contract, owned by this feature)

The minimal input accepted by the shared execution surface and the Prefect flow. The
flow parameters correspond one-to-one; no additional remote parameter exists.

| Field | Type | Default | Semantics |
|---|---|---|---|
| `sync_name` | `str` | required | Opaque logical configuration name. Compared by exact string equality against the `name` field of each `config.yml` discovered recursively under the configured directory (same glob and match rule as CLI `--name`/`--directory`, but via `resolve_sync_instance`'s tolerant per-file walk — D010, validation step 4; the CLI itself keeps `utils.get_instance` unchanged). Never used to construct a filesystem path. |
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
   not found (never echoing directory contents). When the walk skipped N > 0 files
   whose name was undeterminable (step 4), the same message additionally states that
   N file(s) could not be read — the count only — so a typo is distinguishable from a
   broken configuration.
4. Resolved-configuration readability/validity — `resolve_sync_instance` performs a
   tolerant per-file walk (D010; same `**/config.yml` glob and exact-name match as the
   CLI lookup, but per-file error handling instead of `get_all_sync`'s eager
   validate-everything pass). **Name extraction (E17)**: each discovered file is read
   and parsed with `yaml.safe_load`; the file's name is the top-level `name` key of the
   loaded mapping (`data.get("name")`), and is **UNDETERMINABLE** when the read raises
   `OSError`, the parse raises `yaml.YAMLError`, or the loaded object is not a mapping.
   The three resulting states are decidable and disjoint:
   - name determinable **and equal** to the request → validate as `SyncConfig`; on
     failure → `RunValidationError` naming the logical name and the file path ONLY,
     the parse detail never chained verbatim (pydantic's `input_value` echo can leak
     file contents, including inline secrets the redactor never collected);
   - name determinable **and different** (including a mapping with no/non-string
     `name`, which can never equal the request) → skipped silently (DEBUG at most);
   - name **UNDETERMINABLE** → skipped with a WARNING naming the file path ONLY,
     counted, and resolution continues — one broken neighbor never blocks other
     names, and a bad-YAML file can never be "the matched one".

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
| `summary` | `Mapping[Literal["create", "update", "delete"], int]` | Flat per-action counts over the run's plan rows; all three keys always present (zero-filled). Exposed read-only: `__post_init__` wraps the value in `types.MappingProxyType` via `object.__setattr__` so it cannot be mutated after validation (E14; DBA-010's immutability assertion covers it). |
| `artifact_path` | `str` | Absolute runner-local run directory (`.infrahub-sync-cache/<sync_name>/<run_id>/`), containing at least `run.json` and `plan.parquet`. |

**Cross-field invariants** (enforced in `__post_init__`, unit-tested):

- `changed is True` ⇔ `status != "no-change"` ⇔ `sum(summary.values()) > 0`.
- `status == "planned"` only with `operation == "plan"`.
- `status == "applied"` only with `operation == "sync"`.
- `run_id == Path(artifact_path).name`.
- `set(summary.keys()) == {"create", "update", "delete"}`.

**Derivation (single-source, binding — E8/E7)**: `summary`, `changed`, and `status`
ALL derive from the one in-memory materialized plan-row list
(`Potenda._diff_to_rows(diff)` or an equivalent shared function over the same diff
object) — the same rows the engine passes to `write_plan` — counting rows whose
`action` is `create`/`update`/`delete`. The result is NEVER derived by re-reading
`plan.parquet` (the DBA-009 test population's fakes never write one). Nested-child
caveat: `_diff_to_rows` walks only the diff root's direct children while
`Diff.has_diffs()` recurses; the rows are the feature's result fidelity boundary —
`has_diffs()` keeps gating `sync` execution as today, but the result fields come from
the rows, so a nested-children-only diff reports `no-change`/`changed=False` even when
a sync executed. A synthetic nested-diff unit test pins this behavior (tasks T029).

## 3. Failure contract

Two exception classes, both defined in `infrahub_sync/execution.py`, both carrying a
specific human-readable cause with configured secret values redacted (`***`).

| Class | Owns | Qualifying causes |
|---|---|---|
| `RunValidationError` | Every input-boundary refusal | unconfirmed `sync`; unknown `sync_name`; path-like or command-like `sync_name` values (refused by the no-match rule — never interpreted); unreadable or invalid resolved configuration |
| `RunExecutionError` | Adapter/engine failure | missing runner-environment credentials (adapter init `ValueError` — e.g. "Both url and token must be specified!"); unreachable source/destination systems; nonexistent Infrahub branch; pipeline-lock contention (`filelock.Timeout` after the existing 60 s acquisition timeout); adapter import failure (`ImportError` from `get_potenda_from_instance`) |

Semantics:

- Wrap locus (D009): the surface's own validation raises `RunValidationError`
  directly; every other conversion into these classes happens ONLY in
  `run_remote_request` — `execute_run` preserves the `9edc1bc` CLI failure pattern
  and re-raises original exception types (contracts/execution-surface.md
  "Failure semantics").
- "Specific" = the message names the failing input or stage and the underlying cause.
- "Sanitized" = the message contains no configured secret values; redaction is
  value-based over: the values of runner-environment credentials
  (`INFRAHUB_API_TOKEN`, plus every environment variable whose name matches
  `*_TOKEN`/`*_PASSWORD`/`*_SECRET`/`*_API_KEY` — E10) and secret-valued keys
  (`token`, `password`, `secret`, `api_key`) found in the resolved configuration's
  adapter settings. Redaction covers the WHOLE cause chain at the wrap point (E5):
  a traceback rendering of the raised error must contain no unredacted original
  message (contracts/run-result-and-errors.md §2). The same redaction obligation
  applies to forwarded log records (DBA-008 canary scan).
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
  run (`resolve_sync_instance` re-walks `**/config.yml` per run via the D010 tolerant
  per-file walk — NOT `get_all_sync`, which §1 step 4 forbids on this path), so
  add/edit/remove takes effect next run without re-serving.

## 5. Canonical plan fingerprint (new derived value)

`compute_plan_fingerprint(run_dir: Path) -> str` in `infrahub_sync/cache/fingerprint.py`
— the single shared helper both sides of the DBA-009/SC-007 comparison use.

Definition (spec clarification #1): SHA-256 hex digest over the run's plan rows —
fields `action`, `resource`, `source_id`, `attribute`, `new_value` from
`<run_dir>/plan.parquet` — rows sorted by `(resource, source_id, action, attribute)`
with the row's full serialized form as final tie-breaker (sort-key fields normalize
`None` to `""` so the sort stays total under null-bearing future row formats — E12;
serialization unchanged); each row serialized as
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
| Deployment | `GET /api/deployments/name/infrahub-sync/run` (deployment name `run` — X12, contracts/prefect-flow.md §2) | Deployment id for run creation; `enforce_parameter_schema=true` (server-side enum guard on `operation`, probe a₆/d₁) |
| Flow run | `POST /api/deployments/{id}/create_flow_run` (returns id synchronously, state SCHEDULED) → `GET /api/flow_runs/{id}` | SC-001 run identifier + lifecycle observation |
| Run logs | `POST /api/logs/filter` with `flow_run_id` filter | DBA-004/SC-002 — bridged `infrahub_sync` lifecycle lines (probe c₁) |

## State transitions

```text
Remote request ──(Prefect param schema: bad operation)──▶ HTTP 409, no flow run   [probe d₁]
      │
      ▼ flow run SCHEDULED → RUNNING (flow body)
validate confirm_writes ──fail──▶ RunValidationError ─▶ flow FAILED (no adapters built)
      │
resolve sync_name ──no match / matched-but-invalid config──▶ RunValidationError ─▶ flow FAILED
      │              (unrelated broken neighbor: WARNING + skip, resolution continues — D010)
acquire pipeline lock ──60 s timeout──▶ filelock.Timeout ──wrapped by run_remote_request──▶
      │                                 RunExecutionError ─▶ flow FAILED   (D009 wrap locus)
build Potenda (adapters init) ──ValueError/ImportError propagate──▶ wrapped by
      │                          run_remote_request ─▶ RunExecutionError ─▶ flow FAILED
      │                          (run.json may be "failed")
      ▼
plan lifecycle: load → diff → write_plan → run.json dry-run ─▶ RunResult(planned|no-change)
sync lifecycle: load → guardrail → diff → write_plan → [sync if diffs] → baseline →
                run.json applied ─▶ RunResult(applied|no-change)
      │
      ▼ flow COMPLETED, returns the asdict-SHAPED seven-key dict (explicit
        construction, never dataclasses.asdict — X15)
```
