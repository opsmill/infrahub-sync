# The saved plan artifact

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

> Part of: `dev/knowledge/` | Related: [Planned writes and apply](planned-write-and-apply.md), [Incremental sync and cache](incremental-and-cache.md), [ADR 0001](../adr/0001-saved-plan-artifact-format.md)

A run records what it intends to change as a **plan artifact** under its cache run directory. The
artifact is what `infrahub-sync diff --from-plan` renders for review and what an apply executes, and it
exists so that the set of changes an operator reads is the set that gets written. The format is owned
by `infrahub_sync/plan/` and is versioned: `format_version` is `2`, and `1` is reserved for the
pre-existing `plan.parquet` row format, which is still written and never read by this path.

For *why* the format is shaped this way, see [ADR 0001](../adr/0001-saved-plan-artifact-format.md).

## What is on disk

```text
<cache_root>/<sync-name>/<run_id>/
├── A/<resource>.parquet      # source snapshot — the binding target
├── B/<resource>.parquet      # destination snapshot — not bound
├── plan.parquet              # v1 row format; still written, never read here
├── run.json, cursors.json, schema-sub-hash.txt
└── plan/                     # the artifact
    ├── operations.jsonl      # written FIRST
    └── manifest.json         # written LAST — its presence is the commit point
```

Both files are written tmp-then-`replace`, the same way the other sidecars are. The write order is
load-bearing, and it is what makes the failure verdicts disjoint without any heuristic:

| Observation | Verdict |
|---|---|
| `plan/` absent entirely | A pre-existing v1 plan — re-plan |
| `plan/` present, `manifest.json` absent or unparseable | **Torn** |
| `manifest.json` present, `operations.jsonl` absent | **Torn** |
| `manifest.json` present, line count ≠ `operations_count` | **Torn** |
| `manifest.json` present, `format_version` unrecognized | Unrecognized version — worded distinctly from the v1 message |
| Any path exists but cannot be read | Unreadable, naming the path |

Unreadable is deliberately not flattened into a verification failure: it is a different condition with
a different remedy, so `verify_plan` raises `PlanArtifactUnreadableError` for it rather than returning
it as a failure entry.

## Canonical encoding

Both files use one encoding: UTF-8 without BOM,
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, LF endings only, one
operation object per line with every line terminated. An empty plan is a zero-byte `operations.jsonl`.

Values pass through `canonical_value` before encoding: `str | int | float | bool | None` pass through,
`datetime`/`date` become ISO-8601 strings, `Decimal` becomes its `str`, `list`/`tuple` recurse **in
source order**, `dict` recurses key-sorted, and anything else raises `UnserializablePayloadValueError`
naming the kind, the field and the Python type. There is no `default=` fallback.

Canonical *ordering* applies to the operations sequence and to relationship peer lists only. A
payload's list-valued attributes keep source order and are never re-sorted, because sorting them would
make the applied value differ from the reviewed source value.

## An operation

```json
{"action":"create","identity":{"name":"prod"},"kind":"BuiltinTag","operation_id":"op_3531c0d83d698fd1","payload":{"description":"Production","name":"prod"},"tier":0}
```

| Key | Type | Obligation |
|---|---|---|
| `operation_id` | `string`, `^op_[0-9a-f]{16}$` | always |
| `action` | `"create" \| "update" \| "delete"` | always |
| `kind` | `string` | always |
| `identity` | `object`, key-sorted | always |
| `tier` | `integer ≥ 0` | always |
| `payload` | `object` | required on create/update, **omitted** on delete |
| `relationships` | `array` | present **iff** the operation carries any |

Operations are stored ordered by `(tier, operation_id)` and are executed in exactly that order — no
re-sorting and no recomputation at apply time. When the configuration supplies an explicit `order:`,
the engine computes no tiers, and an operation's tier is its kind's index in the flat `top_level`
order — which under an explicit `order:` is the operator's own declared write order.

**Absent versus empty is meaningful.** An absent `relationships` key means the operation carries no
relationship values at all; it is never `[]`. A `peers: []` inside a `cardinality: "many"` reference
means the peer set is deliberately empty, and the replace-set write acts on it. There is no field
grouping operations into write units at either level.

**A plan cannot clear a cardinality-one peer.** The asymmetry above only runs one way: an empty
*many* set is expressible, an emptied *one* peer is not. `cardinality: "one"` requires exactly one
peer, and an absent reference means "this operation carries no value for that field", not "empty it" —
so nothing in the format says *clear this*. Derivation cannot produce it either: a reference field
whose mapped value is `None` is treated as absent and skipped. This is an intended v1 scope limit and
**parity with live `sync`**, which skips a `None` there for the same reason; a relationship a plan does
not mention is one the apply leaves alone. Clearing a cardinality-one peer is done at the destination,
and encoding it requires a future `format_version` extension.

### The payload carries the identity

```text
payload = element.keys ∪ element.source_attrs   minus every key carried as a relationship reference
```

The identity components are inside the payload, and they are not decoration: the destination's
convergent write is keyed on the kind's human-friendly ID, whose components come from the identity, so
a write issued without them is unkeyed and duplicates on every re-apply.

`source_attrs` alone cannot supply them. DiffSync's `get_attrs()` explicitly "does not include the
fields in `_identifiers`", and the generator strips identifiers out of `_attributes`, so the identity
has to be unioned in from `element.keys`. Every identity key ends up in exactly one of `payload` or
`relationships[].field` — never neither, which is a model-level validation.

### Peer references are recursive

A peer is recorded as `{"peer_kind": "<kind>", "identity": {...}}`, recursively to whatever depth the
configuration nests, rather than as the peer's DiffSync `unique_id` string:

```json
{"cardinality":"one","field":"site","peer_kind":"LocationSite","peers":[{"name":"dc1"}]}
```

This is required rather than cosmetic. On the example NetBox configuration, ten schema-mapping entries
carry a reference *inside* their `identifiers`, and a reference field's value in a comparison model is
the peer's `unique_id`. At apply time, on a resolver miss, all the resolver holds is the peer's identity
mapping — so without the nested pair the only way to reach a component would be to split a `unique_id`
on `__`, which is the v1 defect the format exists to remove. A **schema path** is split; a **data
value** never is.

`peer_kind` is **probed from the store**, never read from the referring field's `reference` value. The
same destination kind can be declared by two mapping entries whose reference for the same field differs
(`DcimDevice.location` is `LocationRack` in one entry and `LocationSite` in the other), so the mapping
alone is ambiguous. The probe tries `store.get(model=candidate, identifier=peer_unique_id)` for each
candidate drawn from the mapping; exactly one hit is the answer, and both zero hits and more than one
hit fail the plan run, naming the owning kind, the field, the unique-id and the candidates tried. There
is deliberately **no fallback** to the single mapping-declared kind. The probe runs against the source
store for a create or update, and against the **destination** store for a derived delete, whose peers
are destination-only by construction.

### The operation identifier

```python
op_id = "op_" + sha256(canonical_json_bytes([action, kind, canonical_identity(identity)])).hexdigest()[:16]
```

A JSON array, in that order. The payload is excluded, so the identifier names the logical operation and
stays stable across re-plans; payload exactness is covered by `plan_checksum` instead. Exactly one
operation exists per `(action, kind, identity)`, so a collision is pathological and fails the plan run.
The data model rejects a stored identifier that does not match its own triple.

## The manifest

| Key | Meaning |
|---|---|
| `format_version` | `2` |
| `run_id` | The run the plan was produced under; excluded from the checksum, checked by equality |
| `created_at` | ISO-8601 UTC; excluded from the checksum |
| `config_version` | Opaque, non-empty printable ASCII; compared for equality, never parsed |
| `source_snapshot` | `{path, digest, row_count}` per bound snapshot, ordered by `path` |
| `operations_count` | Keeps an empty plan distinguishable from a torn one |
| `delete_operations_computed` | `false` when the destination side was loaded incrementally |
| `destination_binding` | `{url, branch}` — the destination the plan was computed against, resolved (env over settings) and URL-normalized, **never the token**. Compared for equality at apply time. Additive: absent on plans written before it existed, and the check is skipped for them |
| `plan_checksum` | Lowercase sha256 hex over the manifest body plus the operations bytes |
| *(any other key)* | Tolerated on read, preserved, and included in the checksummed bytes |

```python
excluded = {"plan_checksum", "run_id", "created_at"}
body     = {k: v for k, v in manifest.items() if k not in excluded}   # REMOVED, not blanked
digest   = sha256(canonical_json_bytes(body) + operations_jsonl_raw_bytes).hexdigest()
```

No separator between the two byte sequences. Removing `run_id` and `created_at` is what makes the
manifest byte-identical across re-plans.

The `config_version` default digests the parsed configuration with `directory` excluded — location is
not configuration, and including it would stop a plan produced in CI being applied from a checkout.
`settings` **is** included, which has a consequence worth knowing before it is met: **rotating a
credential invalidates every saved plan for that configuration**, and the next apply of any of them is
refused on the configuration-version check. Only the digest is written, so no credential is disclosed.

The `source_snapshot` digest covers the snapshot's **logical rows**, not the Parquet file's bytes: the
cache injects a per-run `_extract_ts` into every row, so a raw-bytes digest would differ on every
re-plan of an unchanged source. The digest is taken over the table with `_extract_ts` dropped, rows in
file order; `_source_id` and `_tombstone` stay in, both being deterministic for identical input. What
this stops detecting is a change confined to the extraction timestamp. An absent recorded file, a
disagreeing digest and a disagreeing row count are all still refusals.

## Determinism

Two plan runs over an unchanged source and destination, **at the same extraction mode on each side**,
produce a byte-identical `operations.jsonl` and a `manifest.json` byte-identical after removing
`run_id` and `created_at` from both sides. The same-extraction-mode precondition is part of the
procedure, not a caveat: `delete_operations_computed` is inside the checksum and is not masked, so two
runs at different extraction modes are *expected* to differ.

## Reading and verifying a plan

`infrahub_sync/plan/` is the whole surface. The public API re-exports `read_saved_plan`, `SavedPlan`,
`PlanManifest`, `PlannedOperation`, `PlanSummary`, `RelationshipReference`, `SourceSnapshotRecord` and
`VerificationFailure`.

- `reader.load_plan_artifact(run_dir)` classifies v1 / torn / unrecognized-version and returns the
  parsed manifest and operations.
- `verify_plan(*, artifact, run_id, config_version, write_surface_missing_on=None)` runs the pre-apply
  checks and returns **every** failure, each naming itself, the refused run, expected and found where
  neither is secret, and the operator's next action. An empty list means safe to apply. It takes the
  already-read `RawPlanArtifact`, **not** a `run_dir`: the bytes it verifies are the bytes the caller
  goes on to parse and apply, so no second read exists for a concurrent rewrite to slip through
  (FIX-008, DBR-006). Only the source snapshots it names are digested from disk, because they are
  verification subjects and are never applied.
- `review.read_saved_plan(...)` is the review path. It reads and renders without constructing an
  adapter and without creating a run directory, so a review writes nothing.

The format-version check is a **gate**. When it fails, the remaining checks are not evaluated and the
refusal says so, because a reader that does not know what the fields mean would otherwise report
failures that are artifacts of its own ignorance. When it passes, every remaining check is evaluated
and every failure is named, so one apply attempt tells the operator everything that is wrong.

## What the format does not cover

Recorded so nothing reads an obligation into the silence: retention, expiry or pruning of a stored
plan; pagination or truncation; volume or latency targets; the stability of *rendered* review text,
which is operator-facing output rather than a format; a governance process for changing the format
— `format_version` and the unknown-key tolerance are the two mechanisms provided; and clearing a
cardinality-one peer, which the operation shape cannot express (see **An operation** above).

## See also

- [ADR 0001](../adr/0001-saved-plan-artifact-format.md) — why the format is shaped this way.
- [Planned writes and apply](planned-write-and-apply.md) — what executes an artifact.
- [Incremental sync and cache](incremental-and-cache.md) — the run directory and snapshots it sits in.
- [Schema mapping](schema-mapping.md) — where identities and references come from.
