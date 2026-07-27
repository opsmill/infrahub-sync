# Contract: the in-process plan reader and the pre-apply verifier

**Requirement**: FR-029 — "Reading a stored plan MUST have exactly one supported entry point."
Everything else in `infrahub_sync/plan/` is internal to this outcome; only the names below are a
supported surface, and nothing broader is designed here (AD029).

## The single entry point

```python
from infrahub_sync.plan import read_saved_plan

def read_saved_plan(
    *,
    sync_name: str,
    run_id: str,
    config: SyncConfig | None = None,
) -> SavedPlan
```

- Locates the run as `cache_root_for(sync_name) / run_id` (`infrahub_sync/cache/paths.py:26-59`),
  reusing `_require_safe_segment`'s traversal guard (`:11-23`) for both arguments.
- Reads `<run_dir>/plan/`. Constructs **no adapter**, extracts nothing, takes **no** pipeline lock,
  creates or modifies nothing in the run directory, and never mutates run state (FR-008, AD021,
  AD031).
- `config` is optional and used for one thing only: validating that a `kind` filter names a kind the
  configuration declares (FR-006). Review is otherwise configuration-independent once the run is
  located.
- Returns **data**, never rendered text, so a caller consumes it without parsing output and SC-010's
  canary scan can scan the returned value as data (FR-029).

## `SavedPlan`

```python
class SavedPlan:
    manifest: PlanManifest
    checksum_ok: bool
    verification_notes: list[str]

    def summary(self) -> PlanSummary: ...
    def operations(self, *, kind: str | None = None) -> list[PlannedOperation]: ...

class PlanSummary:
    by_action: dict[str, int]           # {"create": 21, "update": 12, "delete": 4}
    by_kind: dict[str, int]             # {"BuiltinTag": 3, "LocationSite": 6, ...}
    total: int
    delete_operations_computed: bool    # from the manifest — AD056
    deletes_not_executed: int           # by_action.get("delete", 0) — AD055, AD056
```

| Behavior | Rule | Requirement |
|---|---|---|
| A plan that would fail apply verification | **Rendered anyway.** `checksum_ok` is `False` and `verification_notes` says why; review never refuses to show and never mutates state | AD031 |
| A plan with zero operations | `summary()` returns `total == 0` and the renderer states the plan contains no operations, rather than producing empty output | FR-022 |
| `kind` the configuration **declares** but the plan holds no operation for | returns **`[]`**. It does **not** raise: the never-empty rule is a presentation obligation and belongs to the renderer, not to an interface FR-029 requires callers to consume as data | FR-006, FR-029, AD058 |
| `kind` naming a kind the configuration does **not** declare | raises `UnknownPlanKindError` naming the kind, the kinds the plan holds, and the next action | FR-006, AD036, AD058, AD059 |
| Delete-computation disclosure | `summary()` always carries `delete_operations_computed`, read up from the manifest, so a plan missing its whole delete class is distinguishable from one that has no deletes | FR-006, FR-015, AD056 |
| Non-zero delete count | `summary()` carries `deletes_not_executed`; the renderer must annotate it inline in **both** depths, stating no delete will be executed against the destination by this release | FR-006, FR-017, AD055, AD056 |
| Per-object detail field set | at minimum `operation_id`, `action`, `kind`, `identity` — the review-side source SC-005 compares against the apply result | AD020 |
| Reading after the producing process exited | supported; the reader touches only the filesystem | FR-007 |

The command-line review mode is a **thin renderer** over this object and re-implements no reading,
filtering or summarizing, so both of SC-009's reachability paths exercise one code path (FR-029). Two
obligations sit **on the renderer** rather than here, because they are about an operator's reading and not
about data: turning an empty `operations(kind=…)` result into FR-006's error, and annotating
`deletes_not_executed`.

## Error taxonomy

All in `infrahub_sync/plan/errors.py`, all deriving from `PlanArtifactError(Exception)`. Specific
exceptions only — no broad `except Exception:` anywhere on these paths (Constitution IV).

**Every entry names the operator's next action (AD059).** AD036 attached that obligation to *refusals*
only, so `VerificationFailure` carried it and nothing else did — nine failures named a cause and stopped.
`PlanArtifactError` therefore declares `next_action: str` on the base class, so a subclass cannot be added
without one, and the table's last column is a contract rather than a suggestion. Where the raising site
already holds an enumeration the message **lists it**: echoing the operator's own input back while
withholding a list the command already has is a failure that reads as an answer.

| Exception | Raised when | Message must name | Next action |
|---|---|---|---|
| `PlanFormatV1Error` | The `plan/` directory is absent entirely | the run identifier and the expected artifact path (FR-019) | re-plan: re-run `diff` for this sync to produce a current-format artifact |
| `PlanArtifactTornError` | `plan/` present without a complete manifest; manifest present with `operations.jsonl` absent; line count ≠ `operations_count`; a recorded snapshot absent or disagreeing | the run identifier, which part is torn, and the expected value versus the found one (FR-010) | re-run `diff` to rebuild the artifact; the partial one cannot be repaired |
| `PlanFormatVersionError` | `format_version` not in `SUPPORTED_FORMAT_VERSIONS` | the version **found** and the versions **supported** — text deliberately distinct from `PlanFormatV1Error`, because the remedies differ (FR-027, SC-018) | **lists `SUPPORTED_FORMAT_VERSIONS`**; the artifact was written by a different version of the tool, so re-plan with this one or apply with the one that wrote it |
| `PlanArtifactUnreadableError` | Permission denied or I/O failure on the run directory or any artifact file | the path that could not be read. Never presented as absent, v1, or zero-operation (AD036) | check permissions and ownership on the named path, then retry |
| `UnknownPlanKindError` | A `kind` names a kind the configuration does not declare. **Not** raised for a declared kind the plan has no operation for — that returns `[]` (AD058) | the kind (FR-006, AD036) | **lists the destination kinds the plan holds**, so the operator picks from what exists rather than guessing again |
| Unknown run identifier | The run identifier does not exist under the cache root, or its run holds no plan artifact | the run identifier and the expected artifact path (FR-008, AD021) | **lists the run identifiers that exist for that sync** — the command has already located the cache root, so the enumeration is in hand |
| `UnsupportedOperationActionError` | An operation record's `action` is outside `ACTIONS`. Refused while reading, before any destination write (FR-017, AD055) | the operation identifier and the action **found** | **lists `ACTIONS`**; the artifact was produced by a tool this one does not understand, so re-plan with this version |
| `DuplicateOperationIdError` | Two operations share an identifier at write time | both operations' kind, action and identity (FR-021) | the plan is pathological: two operations address the same object with the same action, so correct the schema mapping that produced them |
| `UnserializablePayloadValueError` | `canonical_value` meets a type outside its table | the kind, the field and the Python type (PD-002) | narrow the field's mapping or add the type to the canonical-value table; the artifact cannot be written deterministically without it |
| `PeerNotFoundError` | A peer identity matches no destination object (FR-014, SC-016) | peer kind, peer identity, referring operation identifier | create the peer at the destination, or re-plan so the peer is created by the same plan |
| `PeerAmbiguousError` | A peer identity matches more than one destination object (FR-014, SC-016) | peer kind, peer identity, match count | the destination kind's identity is not unique for these values: de-duplicate at the destination or narrow the mapping's identifiers |
| `PlanVerificationError` | Any pre-apply check fails | every failed check, per `VerificationFailure` below | per-failure, carried on each `VerificationFailure` |

A plan-derivation failure on the non-mutating path raises whichever of the above applies — most often
`UnknownPlanKindError`'s peer-probe siblings, `UnserializablePayloadValueError`, or
`DuplicateOperationIdError` — and FR-030 requires the command to fail with that message intact rather than
degrading it to a warning (AD047, AD059).

## The pre-apply verifier

```python
def verify_plan(
    *,
    run_dir: Path,
    run_id: str,
    config_version: str,
    write_surface_missing_on: str | None = None,
) -> list[VerificationFailure]
```

Returns an **empty list** when the plan is safe to apply. A non-empty list means refuse — before any
destination write (FR-009). It performs no writes itself and does not record run state; the caller
does that.

**`write_surface_missing_on` takes the adapter's name, not a boolean (AD058).** The earlier signature
took `write_surface_available: bool` while the check it drives promises a message that *names the
adapter* — which a boolean cannot supply, so the message was unwritable from the arguments the function
received. The parameter is therefore the name of the destination adapter that lacks the planned-write
surface, or `None` when the surface is present. One parameter carries both facts, the message names what
FR-023 requires it to name, and the caller — which holds the adapter — is the only place that can supply
it. The function still constructs nothing and touches no adapter.

### Check order and semantics

Cheapest and most structural first, so an operator is told the artifact is the wrong artifact before
being told its contents disagree (FR-009).

| # | Check | Fails when | Notes |
|---|---|---|---|
| 1 | `format_version` | not in `SUPPORTED_FORMAT_VERSIONS`, or the manifest cannot be parsed | **Gate.** When it fails, checks 2–5 are not evaluated and the refusal says so — an artifact whose revision is not understood cannot have its remaining fields meaningfully interpreted (PD-006) |
| 2 | `run_binding` | `manifest.run_id != run_id` | A separate equality comparison, not a checksum input, because `run_id` is deliberately excluded from `plan_checksum` for SC-006 — which is exactly what would otherwise let a copied `plan/` directory verify clean (AD012, SC-015) |
| 3 | `torn_operations` + `plan_checksum` | operations file absent, or line count ≠ `operations_count` → **torn**; otherwise recomputed checksum ≠ `plan_checksum` → **mismatch** | Torn is reported instead of mismatch, because a checksum cannot be computed over bytes that are not there (FR-010) |
| 4 | `source_snapshot` | any recorded path absent, or the recomputed **logical-row** digest (AD037 — the table with `_extract_ts` dropped, not the file's raw bytes) or row count disagrees | Absent, truncated and mismatched all land here — the three words SC-004 enumerates |
| 5 | `config_version` | `manifest.config_version != config_version` | Equality only. The value is **never parsed** (FR-011, SC-013). The caller supplies the comparison value: recomputed by the default rule on the CLI path, or verbatim from an in-process caller (AD013) |
| — | `write_surface` | `write_surface_missing_on` is not `None` | Evaluated in the same pre-write gate rather than surfacing as a later per-operation failure (FR-023). The failure names **that adapter** — which is why the parameter is the adapter's name and not a boolean (AD058) — and tells the operator to use `sync`, the shape the engine already has at `infrahub_sync/potenda/__init__.py:354-360` |

Checks 2–5 are **all** evaluated and **every** failure is named, so one apply attempt tells the
operator everything that is wrong (AD036).

### `VerificationFailure`

```python
class VerificationFailure:
    check: str          # one of the names in the table above
    run_id: str         # the run refused — a refusal naming only the check is not actionable
    expected: str | None
    found: str | None
    next_action: str
```

`expected` and `found` are populated only where neither value is secret (FR-009, FR-018). For the
checksum and digest checks they are the two hex values, which are not secret. For `config_version`
they are the two opaque values, which are digests by default and, when caller-supplied, are the
caller's own opaque string — never a credential, because credentials live in `settings` and never
enter the manifest (AD018).

### What a refusal records

| Obligation | Rule |
|---|---|
| Run state | `failed` in the existing vocabulary; never left at `running`, and never `applied` (AD010) |
| Applied-operation set | recorded as **empty** under `summary["applied_operations"]`, not absent, so a refusal and an apply that wrote nothing are not distinguishable only by a missing field (AD036, AD062) |
| Skipped-delete record | `summary["skipped_delete_count"]` is `0` and `summary["skipped_delete_operations"]` is `[]` on a refusal — nothing was skipped because nothing was attempted (AD055, AD062) |
| Destination writes | **zero**, asserted in every SC-004 / SC-011 / SC-015 / SC-018 case |
| Finality | not terminal — the same run may be applied again once the cause is corrected (AD033) |
| Not extended to the pre-existing schema-subhash abort | That path is unreachable — it imports a resolver the package does not define (`infrahub_sync/cli.py:330`) and the `except ImportError: pass` at `:341-342` swallows the block, so the abort at `:336-340` cannot execute. Its repair is dropped and the record corrected; AD010's rule stands for the **new** refusal paths, which is what DBA-004 measures (AD063) |

Verification runs unconditionally on every apply attempt whatever the operation count, so an empty plan
with a broken checksum is still refused (AD033). Verification must complete before any destination
**write**; constructing an adapter or opening a destination connection beforehand is permitted, which
is what the code already does (AD034).
