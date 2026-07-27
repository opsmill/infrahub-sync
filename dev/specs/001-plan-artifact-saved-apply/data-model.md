# Phase 1 data model: Saved plan artifact

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Date**: 2026-07-26

Entities, their fields, validation rules, and the one state machine this feature touches. The
on-disk encoding of everything here is fixed in
[contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md); this document is the
in-memory model and the rules the types enforce.

All types live in `infrahub_sync/plan/models.py` as Pydantic v2 models, matching the existing
`SyncConfig` style (`infrahub_sync/__init__.py:86-95`).

---

## Constants

| Name | Value | Source |
|---|---|---|
| `PLAN_FORMAT_VERSION` | `2` | FR-027.1; `1` is reserved for the pre-existing row format the reader refuses (FR-019) |
| `SUPPORTED_FORMAT_VERSIONS` | `frozenset({2})` | FR-027's version refusal |
| `ACTIONS` | `("create", "update", "delete")` | FR-002's closed vocabulary (AD009) |
| `CHECKSUM_EXCLUDED_FIELDS` | `("plan_checksum", "run_id", "created_at")` | FR-004; removed, not blanked (AD035) |
| `SC006_MASKED_FIELDS` | `("run_id", "created_at")` | SC-006; deliberately not the same set |

---

## Entity: `DestinationIdentity`

Not a class — a **rule** about a `dict[str, Any]`, enforced by `identity.canonical_identity()`.

| Property | Rule | Requirement |
|---|---|---|
| Representation | An ordered mapping of identity attribute name to value, sorted by attribute name | FR-028.3 |
| Population | Comes from `DiffElement.keys`, which diffsync documents as "as in `DiffSyncModel.get_identifiers()`" (`.venv/…/diffsync/diff.py:180,191`) | — |
| Value domain | Whatever `canonical_value` accepts (see PD-002). For an identity component that is itself a relationship, the value is the peer's DiffSync unique-id string, as the engine holds it | PD-004 |
| Emptiness | An operation for which no identity value can be formed **fails the plan run**, naming the kind and the identity attribute that had no value | AD035; spec Edge Cases |

The same canonical form is what FR-003 hashes, what FR-005 orders relationship-reference lists by, and
what per-object review presents (FR-006) — so the identity an operator reads is the identity the
identifier was derived from.

---

## Entity: `RelationshipReference`

A peer named by kind and identity, never by a destination-assigned id.

| Field | Type | Obligation | Rule |
|---|---|---|---|
| `field` | `str` | required | The owning object's field name, i.e. the `SchemaMappingField.name` whose `reference` is set |
| `peer_kind` | `str` | required | The `SchemaMappingField.reference` value (`infrahub_sync/__init__.py:57`) |
| `cardinality` | `Literal["one", "many"]` | required | Derived from whether the mapped value is a scalar or a list |
| `peers` | `list[DestinationIdentity]` | required | Exactly one element when `cardinality == "one"`; zero or more when `"many"`, ordered canonically by peer identity (AD003, FR-005) |

### Validation

- `peers` must be non-empty for `cardinality == "one"`.
- An **empty** `peers` list under `cardinality == "many"` means the peer set is deliberately empty and
  the replace-set write acts on it; the reference being **absent** means the operation carries no value
  of that kind at all. The two are never interchangeable (FR-028.2).
- A peer identity that cannot be resolved at plan time (the peer is not in the loaded source store)
  fails the plan run naming the owning kind, the field, the peer kind and the unresolvable unique-id —
  unless the pre-existing `--continue-on-error` flag is set, in which case it is warned and the peer is
  dropped, matching the adapter's existing behavior at `infrahub_sync/adapters/infrahub.py:491-493`.

---

## Entity: `PlannedOperation`

One proposed change. This is the record nine later outcomes consume.

| Field | Type | Obligation | Rule |
|---|---|---|---|
| `operation_id` | `str` | required, always | `"op_" + sha256(canonical_json_bytes([action, kind, identity]))[:16]`; matches `^op_[0-9a-f]{16}$` (FR-003, AD002, PD-001) |
| `action` | `Literal["create","update","delete"]` | required, always | Closed vocabulary; a relationship change is never a fourth action (FR-002, AD009) |
| `kind` | `str` | required, always | The destination kind, from `DiffElement.type` |
| `identity` | `dict[str, Any]` | required, always | Canonical `DestinationIdentity` |
| `tier` | `int` | required, always | Index of the tier set containing the kind, or the kind's index in `top_level` when the configuration declares `order:` (PD-007) |
| `payload` | `dict[str, Any] \| None` | required on `create` and `update`; **omitted** on `delete` | The full mapped source values, authoritative for the fields it carries and silent about every other destination field (FR-028.4). List-valued attributes stay in source order and are never re-sorted (FR-005) |
| `relationships` | `list[RelationshipReference] \| None` | optional — present when the operation carries any, **absent** when it carries none | Ordered by `field` name for determinism |

### Validation

- `operation_id` is recomputed on construction and must equal the derived value — a stored operation
  whose identifier does not match its own triple is a corrupt record, not a valid one.
- `payload` must be `None` when `action == "delete"` and non-`None` otherwise.
- No field may express a grouping of operations into write units (FR-026) — asserted by a test over the
  model's field set, so a future addition trips it.
- Unknown fields on read are **not** tolerated at the operation level (unlike the manifest): the
  operation record's field set is closed, because FR-027's forward-compatibility carve-out is written
  about the manifest, where the schema-fingerprint field lands.

---

## Entity: `SourceSnapshotRecord`

One entry per source-snapshot file the plan was computed against.

| Field | Type | Obligation | Rule |
|---|---|---|---|
| `path` | `str` | required | Run-relative POSIX path, e.g. `A/BuiltinTag.parquet` (`infrahub_sync/cache/parquet_io.py:142`) |
| `digest` | `str` | required | Lowercase sha256 hex over the snapshot's **logical rows** — the table with `_extract_ts` dropped, rows in file order, each encoded with `canonical_json_bytes` and joined by LF (PD-008) |
| `row_count` | `int` | required | The Parquet table's row count, already computed on the load path |

### Validation and matching

- The list is ordered by `path` for determinism.
- "Match" at apply is recomputed equality of all three: an absent recorded path, a disagreeing digest,
  or a disagreeing row count is a refusal (FR-004, FR-010, SC-004).
- Only the **source** side (`A/`) is recorded — FR-004 says "the source snapshot it was planned
  against", and the destination side's snapshot is not what the plan is bound to.

---

## Entity: `PlanManifest`

The artifact's header, and the format contract. Exactly the eight FR-027 fields, plus tolerated
unknowns.

| Field | Type | Obligation | Rule |
|---|---|---|---|
| `format_version` | `int` | required | `2`. A value outside `SUPPORTED_FORMAT_VERSIONS` is refused with a message naming the version found and the versions supported, textually distinct from the v1 message (FR-027, SC-018) |
| `run_id` | `str` | required | The run the plan was produced under. Excluded from the checksum, so it is checked by a separate equality comparison (FR-009, AD012) |
| `created_at` | `str` | required | ISO-8601 UTC. Excluded from the checksum |
| `config_version` | `str` | required | Opaque, non-empty printable ASCII; compared for equality, never parsed (FR-011, AD013) |
| `source_snapshot` | `list[SourceSnapshotRecord]` | required, may be empty | The binding (FR-004, AD008, PD-008) |
| `operations_count` | `int` | required | What keeps an empty plan distinguishable from a torn one (FR-010, FR-022) |
| `delete_operations_computed` | `bool` | required | `false` when the destination side was loaded incrementally (FR-015, AD024). Inside the checksum and **not** masked by SC-006, which is why SC-006 requires both runs to have used the same extraction mode |
| `plan_checksum` | `str` | required | Lowercase sha256 hex over the canonical manifest minus the three excluded fields, concatenated with the raw bytes of `operations.jsonl`, no separator (FR-004, AD001, AD035) |
| *(unknown fields)* | any | tolerated | Preserved verbatim on read and included in the checksummed bytes — a later outcome adds a schema-fingerprint field here (FR-027, AD028). `model_config = ConfigDict(extra="allow")` |

### Validation

- `operations_count` must equal the number of lines in `operations.jsonl`; a disagreement is torn
  (FR-010).
- `config_version` must match `^[\x20-\x7e]+$` (non-empty printable ASCII, AD035).
- The three checksum-excluded fields are **removed** from the mapping before canonicalization, not
  blanked (AD035).

---

## Entity: `SavedPlan` (in-memory review result)

What FR-029's single reader entry point returns. Data, never rendered text.

| Member | Type | Notes |
|---|---|---|
| `manifest` | `PlanManifest` | As read, including tolerated unknown fields |
| `checksum_ok` | `bool` | Review verifies the checksum and reports it prominently, but renders regardless (AD031) |
| `verification_notes` | `list[str]` | Human-readable notes about any check that did not pass; review never mutates run state |
| `summary()` | `PlanSummary` | Counts per action and counts per kind (FR-006). A zero-operation plan produces a summary that *states* the plan contains no operations rather than empty output (FR-022) |
| `operations(kind=None)` | `list[PlannedOperation]` | Per-object detail; `kind` narrows to one destination kind. A `kind` matching no operation, or naming a kind the configuration does not declare, raises `UnknownPlanKindError` naming the kind (FR-006, AD036) |

`PlanSummary` carries `by_action: dict[str, int]`, `by_kind: dict[str, int]`, and `total: int`.

---

## Entity: `VerificationFailure`

One per failed pre-apply check. The apply refuses when the list is non-empty.

| Field | Type | Rule |
|---|---|---|
| `check` | `Literal["format_version","run_binding","plan_checksum","source_snapshot","config_version","torn_operations","write_surface"]` | Named so the operator knows which one failed (FR-009) |
| `run_id` | `str` | The run identifier refused — a refusal naming only the check leaves an operator applying several runs unable to tell which one was refused (AD036) |
| `expected` | `str \| None` | Present only where neither value is secret (FR-009, FR-018) |
| `found` | `str \| None` | Same |
| `next_action` | `str` | The operator's remedy, e.g. "re-run `diff` to rebuild the plan" |

---

## Run state

No new state is introduced. The existing vocabulary in `RunFile.status`
(`infrahub_sync/cache/sidecars.py:71`) is reused unchanged, because
`previous_successful_run_dir` consumes it through
`_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})` (`infrahub_sync/cache/incremental.py:24`) and
adding a member would be a compatibility change this outcome does not authorize (AD010).

```text
pending ─▶ running ─▶ applied      (apply succeeded)
                   ├▶ dry-run      (diff / plan-only run)
                   └▶ failed       (refused apply, unsupported operation,
                                    destination rejection, or any raised error)
```

Transitions this feature adds or corrects:

| Event | State recorded | Requirement |
|---|---|---|
| Pre-apply verification fails | `failed`, with an **empty** applied-operation set rather than no field at all | FR-009, AD010, AD036 |
| A plan containing a delete finishes applying its non-deletes | `failed`, message naming the unsupported operation's identifier and action | FR-017, SC-007 |
| The destination rejects an operation, or transport fails | `failed`, message naming the failing operation identifier and the underlying error; already-written operations stay written | AD027, FR-025 |
| The pre-existing schema-subhash mismatch abort | `failed` — today it aborts leaving `running` on disk permanently (`infrahub_sync/cli.py:322-323`, `:336-340`) | AD010 |
| A run already at `applied` is applied again | Permitted; verification runs unconditionally regardless of operation count | AD033 |
| Any review operation | **No transition.** Review never mutates run state | AD031 |

## Relationships between entities

```text
Run (run.json, existing)
 └── PlanArtifact  (<run_dir>/plan/)
      ├── PlanManifest              1     ── binds ──▶ Run.run_id
      │    ├── SourceSnapshotRecord 0..n  ── binds ──▶ <run_dir>/A/<resource>.parquet
      │    └── plan_checksum              ── covers ──▶ manifest ⧺ operations bytes
      └── PlannedOperation          0..n  (ordered by tier, then operation_id)
           └── RelationshipReference 0..n ── names ──▶ (peer_kind, DestinationIdentity)
                                                          │
                                            resolved at apply by PeerResolver
                                            ──▶ destination node id
```
