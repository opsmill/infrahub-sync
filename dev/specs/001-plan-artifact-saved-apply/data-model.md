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
| `ACTIONS` | `("create", "update", "delete")` | FR-002's closed vocabulary (AD009). An operation record whose `action` falls outside it is the **genuinely unsupported** operation FR-017 fails the run for, and it is caught at read time by the `Literal` below — before any destination write, which is where FR-017 needs it (AD055) |
| `CHECKSUM_EXCLUDED_FIELDS` | `("plan_checksum", "run_id", "created_at")` | FR-004; removed, not blanked (AD035) |
| `SC006_MASKED_FIELDS` | `("run_id", "created_at")` | SC-006; deliberately not the same set |

---

## Entity: `DestinationIdentity`

Not a class — a **rule** about a `dict[str, Any]`, enforced by `identity.canonical_identity()`.

| Property | Rule | Requirement |
|---|---|---|
| Representation | An ordered mapping of identity attribute name to value, sorted by attribute name | FR-028.3 |
| Population | Comes from `DiffElement.keys`, which diffsync documents as "as in `DiffSyncModel.get_identifiers()`" (`.venv/…/diffsync/diff.py:178`, assigned `:191`) | — |
| Value domain | Whatever `canonical_value` accepts (see PD-002) **or** a nested peer reference. An identity component that is itself a relationship is **never** stored as the peer's DiffSync unique-id string: it is stored as `{"peer_kind": <kind>, "identity": <DestinationIdentity>}`, recursively, so a consumer never has to split a unique-id on `__` to recover the peer's identity (AD043). Ten schema-mapping entries across nine kinds on the qualified path hit this case | AD043, PD-004 |
| Nested peer kind | Established by **probing the store**, never read from the referring field's `SchemaMappingField.reference`. See "Resolving a nested peer kind" below | AD046, AD050 |
| Which store is probed | The **source** store for a `create` or `update` identity; the **destination** store for a derived `delete` identity, whose peers are destination-only by construction (AD049) | AD049 |
| Emptiness | An operation for which no identity value can be formed **fails the plan run**, naming the kind and the identity attribute that had no value | AD035; spec Edge Cases |

The same canonical form is what FR-003 hashes, what FR-005 orders relationship-reference lists by, and
what per-object review presents (FR-006) — so the identity an operator reads is the identity the
identifier was derived from. That holds for a derived delete too: a delete is canonicalised by the same
recursive rule as every other operation, differing only in which store its nested peer kinds are probed
against (AD049).

### Resolving a nested peer kind (AD050)

AD046 forbids taking the peer's kind from the mapping. Its stated mechanism — "the loaded source store
entry knows its own kind" — is **not constructible**: the store is keyed by (model, unique-id) and
every read requires the model up front, both on the base class and in the local implementation
(`.venv/…/diffsync/store/__init__.py:40-52`, `.venv/…/diffsync/store/local.py:30-49`). There is no
kind-free lookup by unique-id, so the kind cannot be discovered from the entry.

The rule is therefore a **bounded probe**:

```python
candidates = {
    field.reference
    for entry in config.schema_mapping if entry.name == owning_kind
    for field in entry.fields if field.name == field_name and field.reference
}
hits = []
for candidate in sorted(candidates):
    try:
        hits.append((candidate, store.get(model=candidate, identifier=peer_unique_id)))
    except ObjectNotFound:
        continue
```

| Hits | Behavior |
|---|---|
| exactly 1 | That candidate is the peer's kind; its `get_identifiers()` gives the peer identity |
| **0** | Fails the plan run, naming the owning kind, the field, the unique-id and the candidates tried — the same derivation-failure path as any other unresolvable peer (FR-030) |
| **> 1** | Fails the plan run, naming the same four things, on that same path (FR-030) |

`store` is the source adapter's store for a `create`/`update` identity and the destination adapter's
store for a derived `delete` identity (AD049). A **single**-candidate set is probed like any other and
a miss still fails: silently returning the sole mapping-declared kind would be exactly the
mapping-derived answer AD046 exists to forbid, arrived at by a different route. On the qualified path
`DcimDevice.location` is the two-candidate case — `{LocationRack, LocationSite}`
(`examples/netbox_to_infrahub/config.yml:239`, `:281`) — and every other reference-bearing identifier
there is a one-candidate case.

---

## Entity: `RelationshipReference`

A peer named by kind and identity, never by a destination-assigned id.

| Field | Type | Obligation | Rule |
|---|---|---|---|
| `field` | `str` | required | The owning object's field name, i.e. the `SchemaMappingField.name` whose `reference` is set |
| `peer_kind` | `str` | required | Established by the bounded store probe above, **not** by reading the referring field's `SchemaMappingField.reference`. Deriving it from the mapping is ambiguous on the qualified path, where `DcimDevice` is declared twice with different `location` references (`examples/netbox_to_infrahub/config.yml:212`, `:254`) and a wrong pick fails the whole apply run (AD046, AD050) |
| `cardinality` | `Literal["one", "many"]` | required | Derived from whether the mapped value is a scalar or a list |
| `peers` | `list[DestinationIdentity]` | required | Exactly one element when `cardinality == "one"`; zero or more when `"many"`, ordered canonically by peer identity (AD003, FR-005). Each peer identity may itself contain nested `{peer_kind, identity}` values, recursively (AD043) |

### Validation

- `peers` must be non-empty for `cardinality == "one"`.
- An **empty** `peers` list under `cardinality == "many"` means the peer set is deliberately empty and
  the replace-set write acts on it; the reference being **absent** means the operation carries no value
  of that kind at all. The two are never interchangeable (FR-028.2).
- A peer identity that cannot be resolved at plan time — the peer is in **none** of the candidate
  kinds' stores, or in **more than one** of them — **fails the command**, naming the owning kind, the
  field, the candidate kinds tried and the unresolvable
  unique-id. There is no warn-and-drop escape and no error-tolerance option: `--continue-on-error` is
  declared on the mutating command only (`infrahub_sync/cli.py:190`), so it does not exist on the
  non-mutating command where derivation also runs, and degrading to warn-and-drop there would emit a
  silently incomplete plan — the divergence FR-017 exists to prevent (FR-030, AD047). The adapter's own
  `continue_on_error` handling on the **load** path (`infrahub_sync/adapters/infrahub.py:491-493`) is
  unchanged and is a different path from this one.

---

## Entity: `PlannedOperation`

One proposed change. This is the record nine later outcomes consume.

| Field | Type | Obligation | Rule |
|---|---|---|---|
| `operation_id` | `str` | required, always | `"op_" + sha256(canonical_json_bytes([action, kind, identity]))[:16]`; matches `^op_[0-9a-f]{16}$` (FR-003, AD002, PD-001) |
| `action` | `Literal["create","update","delete"]` | required, always | Closed vocabulary; a relationship change is never a fourth action (FR-002, AD009) |
| `kind` | `str` | required, always | The destination kind, from `DiffElement.type` |
| `identity` | `dict[str, Any]` | required, always | Canonical `DestinationIdentity` — on a `delete` too, with its nested peer kinds probed against the destination store (AD049) |
| `tier` | `int` | required, always | Index of the tier set containing the kind, or the kind's index in `top_level` when the configuration declares `order:` (PD-007) |
| `payload` | `dict[str, Any] \| None` | required on `create` and `update`; **omitted** on `delete` | `element.keys` ∪ `element.source_attrs`, minus any key carried as a `RelationshipReference` — i.e. the **identity components are inside the payload**. `source_attrs` alone is not enough: it comes from `get_attrs()`, whose contract is "does not include the fields in `_identifiers`" (`.venv/…/diffsync/__init__.py:340-347`, called at `.venv/…/diffsync/helpers.py:223`), and the generator strips identifiers out of `_attributes` (`infrahub_sync/generator/__init__.py:95`). A payload without them cannot form the destination's HFID, the upsert is unkeyed, and every re-apply duplicates (AD042). Authoritative for the fields it carries and silent about every other destination field (FR-028.4). List-valued attributes stay in source order and are never re-sorted (FR-005) |
| `relationships` | `list[RelationshipReference] \| None` | optional — present when the operation carries any, **absent** when it carries none | Ordered by `field` name for determinism |

### Validation

- `operation_id` is recomputed on construction and must equal the derived value — a stored operation
  whose identifier does not match its own triple is a corrupt record, not a valid one.
- `payload` must be `None` when `action == "delete"` and non-`None` otherwise.
- On a `create` or `update`, every key of `identity` must appear either in `payload` or as the `field`
  of a `RelationshipReference` — never in neither. This is the model-level guard on AD042: without it,
  a payload built from `source_attrs` alone validates cleanly and produces unkeyed writes at apply.
- No field may express a grouping of operations into write units (FR-026) — asserted by a test over the
  model's field set, so a future addition trips it.
- Unknown fields on read are **not** tolerated at the operation level (unlike the manifest): the
  operation record's field set is closed, because FR-027's forward-compatibility carve-out is written
  about the manifest, where the schema-fingerprint field lands.
- An `action` outside `ACTIONS` is rejected at construction and surfaces as
  `UnsupportedOperationActionError` naming the operation identifier, the action found, the actions
  recognized and the operator's next action. This is the **only** class FR-017 fails the run for, and it
  is caught while the artifact is being read — before any destination write, which is where FR-017 needs
  it. A recorded **delete** is a valid action and never reaches this path: it is a designed limitation
  handled by the apply loop, not a malformed record (FR-017, AD055, AD059).

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
| `operations(kind=None)` | `list[PlannedOperation]` | Per-object detail; `kind` narrows to one destination kind. A `kind` the configuration **declares** but the plan holds no operation for returns `[]`; only a `kind` the configuration does **not** declare raises `UnknownPlanKindError` (FR-006, FR-029, AD036, AD058) |

### `operations(kind=…)`: empty for a declared kind, raise for an undeclared one (AD058)

The never-empty rule is FR-006's, and it is a **presentation** obligation discharged by the renderer, not
by this interface. FR-029 requires a caller to consume the reader as data without parsing rendered
output; raising on an empty result forces every programmatic caller to catch an exception to learn a
count, which is the presentation rule leaking into the data API.

| `kind` | Declared by the configuration? | Plan holds operations for it? | Behavior |
|---|---|---|---|
| `LocationSite` | yes | yes | returns those operations |
| `BuiltinTag` | yes | no | returns `[]` — a legitimate answer |
| `NotAKind` | no | — | raises `UnknownPlanKindError` naming the kind, the kinds the plan holds, and the next action (AD059) |

The renderer turns the middle row into FR-006's error, listing the kinds the plan does hold, because at
that point an operator is the audience.

### `PlanSummary` (AD056)

| Field | Type | Rule |
|---|---|---|
| `by_action` | `dict[str, int]` | e.g. `{"create": 21, "update": 12, "delete": 4}` |
| `by_kind` | `dict[str, int]` | e.g. `{"BuiltinTag": 3, "LocationSite": 6}` |
| `total` | `int` | Sum of `by_action` |
| `delete_operations_computed` | `bool` | Carried up from `manifest.delete_operations_computed` (FR-015). **Required**: without it a plan missing its whole delete class renders identically to a plan that has no deletes, and FR-015's "explicit and reviewable" claim is carried by nothing (AD056) |
| `deletes_not_executed` | `int` | `by_action.get("delete", 0)`. Non-zero means the renderer must annotate, inline in summary and in detail, that no delete will be executed against the destination by this release (FR-006, FR-017, AD055, AD056) |

Both fields are **derived on read** from the manifest and the operation set; neither is a new artifact
field, so the format and `plan_checksum` are unaffected.

---

## Entity: `VerificationFailure`

One per failed pre-apply check. The apply refuses when the list is non-empty.

| Field | Type | Rule |
|---|---|---|
| `check` | `Literal["format_version","run_binding","plan_checksum","source_snapshot","config_version","torn_operations","write_surface"]` | Named so the operator knows which one failed (FR-009) |
| `run_id` | `str` | The run identifier refused — a refusal naming only the check leaves an operator applying several runs unable to tell which one was refused (AD036) |
| `expected` | `str \| None` | Present only where neither value is secret (FR-009, FR-018) |
| `found` | `str \| None` | Same |
| `next_action` | `str` | The operator's remedy, e.g. "re-run `diff` to rebuild the plan". **Required, never empty** |

### The next-action obligation covers the whole taxonomy (AD059)

AD036 attached `next_action` to *refusals*, so `VerificationFailure` carries it and the nine other
failures did not. Every error in
[contracts/plan-reader-api.md](./contracts/plan-reader-api.md)'s taxonomy now carries one, and where the
raising site already holds an enumeration the message lists it rather than echoing the operator's input
back. `PlanArtifactError` therefore declares `next_action: str` on the base class, so a subclass cannot
be added without one.

| Failure | Enumeration the message must list |
|---|---|
| `UnknownPlanKindError` | the destination kinds the plan actually holds |
| Unknown run identifier | the run identifiers that exist under `cache_root_for(sync_name)` |
| `PlanFormatVersionError` | `SUPPORTED_FORMAT_VERSIONS` |
| `UnsupportedOperationActionError` | `ACTIONS` |

The remaining five — torn artifact, unreadable path, derivation failure, peer zero-match, peer
multi-match — have no enumeration in hand and carry a next action only.

---

## Run state

No new state is introduced. The existing vocabulary in `RunFile.status`
(`infrahub_sync/cache/sidecars.py:71`) is reused unchanged, because
`previous_successful_run_dir` consumes it through
`_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})` (`infrahub_sync/cache/incremental.py:24`) and
adding a member would be a compatibility change this outcome does not authorize (AD010).

```text
pending ─▶ running ─▶ applied      (apply succeeded — including an apply that
                   │                skipped recorded deletes, which is a designed
                   │                limitation and not a fault, AD055)
                   ├▶ dry-run      (diff / plan-only run)
                   └▶ failed       (refused apply, an operation whose action is
                                    not recognized, destination rejection, or any
                                    raised error)
```

Transitions this feature adds or corrects:

| Event | State recorded | Requirement |
|---|---|---|
| Pre-apply verification fails | `failed`, with an **empty** applied-operation set rather than no field at all | FR-009, AD010, AD036 |
| A plan containing a delete finishes applying its non-deletes | **`applied`**, with a non-zero `skipped_delete_count` and the skipped identifiers recorded, and an operator-visible warning naming the count. **Not `failed`** — an operation this release does not execute by design is a limitation, not a fault (AD055) | FR-016, FR-017, SC-007 |
| An operation carries an action outside `ACTIONS` | `failed`, refused before any destination write, naming the operation identifier, the action found, the actions recognized and the next action. This is the class that *is* genuinely unsupported | FR-017, AD055, AD059 |
| The destination rejects an operation, or transport fails | `failed`, message naming the failing operation identifier, the underlying error and the next action; already-written operations stay written | AD027, FR-025, AD059 |
| A run already at `applied` is applied again | Permitted; verification runs unconditionally regardless of operation count | AD033 |
| Any review operation | **No transition.** Review never mutates run state | AD031 |

**Not a transition this feature touches (AD063).** AD010 also folded in a repair of the pre-existing
schema-subhash abort, on the reading that it leaves `running` on disk permanently. That path is
**unreachable**: the block imports `infrahub_sync.utils._resolve_infrahub_schema`, which is defined
nowhere in the package (`infrahub_sync/cli.py:330`, called `:332`; the comment at `:325` says a later
outcome will provide it), so the import raises `ImportError` and the `except ImportError: pass` at
`:341-342` swallows the whole block — the abort at `:336-340` cannot execute. The repair is **dropped**
and the record corrected; AD010's run-state rule stands for the new refusal paths, which is what DBA-004
measures. Making the check live is unrelated scope.

**One consequence of the `applied` state, recorded rather than discovered (AD055).**
`previous_successful_run_dir` treats `applied` as successful
(`_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})`, `infrahub_sync/cache/incremental.py:24`), so an
apply that skipped deletes counts as a successful prior run for a later warm start. That is correct — the
apply succeeded at everything this release executes — and introducing a distinct state to say otherwise
would be exactly the compatibility change AD010 declines.

## The apply record on the run (AD062, AD055)

`RunFile.KEYS` is a closed tuple — `("status", "mode", "summary", "finished_at")`
(`infrahub_sync/cache/sidecars.py:76`) — and `summary` is already `dict[str, Any]` (`:73`). The apply
record therefore lives inside `summary` under named keys, so no persisted schema other code reads is
extended and the `cache/` layer stays unchanged as plan.md declares.

| Summary key | Type | Rule |
|---|---|---|
| `summary["applied_operations"]` | `list[str]` | FR-020's **ordered** applied-operation identifiers, in the order reported. FR-025's last-applied pointer is its final element, not a separate field. Recorded as `[]` — present and empty — on a refusal, never absent (FR-009, AD036, AD062) |
| `summary["skipped_delete_count"]` | `int` | FR-017's count of delete operations the apply did not execute. `0` on a delete-free plan. Non-zero drives the operator-visible warning, which names this number (AD055) |
| `summary["skipped_delete_operations"]` | `list[str]` | The skipped deletes' operation identifiers, in stored order. Together with `applied_operations` this makes the reviewed set minus the applied set a **recorded value** rather than an inference — which is what DBR-016 protects and what distinguishes this from a silent skip (AD055) |

**Invariant**, asserted at apply and by SC-007: `len(applied_operations) + skipped_delete_count` equals
the plan's `operations_count` whenever the apply completes without a rejection, and
`set(applied_operations) | set(skipped_delete_operations)` equals the plan's full identifier set. A
partial apply (AD027) breaks the first equality by construction and records `failed`, which is how the two
cases stay distinguishable.

**Successor note (DB-005).** The outcome that replaces the run record with durable storage behind
provider interfaces should promote `skipped_delete_count` from a summary key to a first-class
run-record field. It is a summary key here only because this outcome declares the run-directory layer
unchanged.

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
