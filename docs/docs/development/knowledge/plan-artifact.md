---
title: "The saved plan artifact"
---

## The saved plan artifact

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

> Part of: Develop > Knowledge | Related: [Planned writes and apply](planned-write-and-apply.md), [Incremental sync and cache](incremental-and-cache.md), [ADR 0001](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0001-saved-plan-artifact-format.md)

A run records what it intends to change as a **plan artifact** under its cache run directory. The
artifact is what `infrahub-sync runs plan` renders for review and what an apply executes, and it
exists so that the set of changes an operator reads is the set that gets written. The format is owned
by `infrahub_sync/plan/` and is versioned: `format_version` is `5` for every plan written now.
Format `5` is format `4` with one change: a one-peer reference of an update can hold no peer, which
records that the relationship becomes empty
([ADR 0046](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0046-saved-plan-format-5-records-an-emptied-one-peer-relationship.md)).
Plans of format `4` are still read, verified, approved, applied and resumed. Plans of format `2`
and `3` can still be read and reviewed, but `apply` refuses them: a format `2` plan's updates
record no destination id, and neither format was checked for problems or records a count of
left-out records to confirm
([ADR 0022](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0022-saved-plan-format-4.md)).
A release earlier than format `5` refuses a format `5` plan, with a message that names the version
and the versions it supports, so the Sync API and the worker are upgraded together. `1` is
reserved for the pre-existing `plan.parquet` row format, which is still written for querying and
never read by this path.

For *why* the format is shaped this way, see [ADR 0001](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0001-saved-plan-artifact-format.md).

### What is on disk

```text
<cache_root>/<sync-name>/<run_id>/
├── A/<resource>.parquet      # source snapshot — the binding target
├── B/<resource>.parquet      # destination snapshot — not bound
├── plan.parquet              # v1 row format; still written, never read here
├── run.json, cursors.json, schema-sub-hash.txt
└── plan/                     # the artifact
    ├── operations.jsonl      # written FIRST
    ├── last_changes.jsonl    # written second, only when the destination is Infrahub
    ├── report.jsonl          # from format 4, written before the manifest: problems, left-out and kept records
    └── manifest.json         # written LAST — its presence is the commit point
```

Every file is written tmp-then-`replace`, the same way the other sidecars are. The write order is
load-bearing, and it is what makes the failure verdicts disjoint without any heuristic:

| Observation | Verdict |
|---|---|
| `plan/` absent entirely | A pre-existing v1 plan — re-plan |
| `plan/` present, `manifest.json` absent or malformed | **Torn** |
| `manifest.json` present, `operations.jsonl` absent | **Torn** |
| `manifest.json` present, line count ≠ `operations_count` | **Torn** |
| `manifest.json` present at format 4 or 5, `report` record absent or malformed, `report.jsonl` absent, its digest, size or counts differ, or one of its lines is not a valid report entry | Refused (`PlanVerificationError`); the pre-apply check reports `report` |
| `manifest.json` present, `format_version` unrecognized | Unrecognized version — worded distinctly from the v1 message |
| Any path exists but cannot be read | Unreadable, naming the path |

Unreadable is deliberately not flattened into a verification failure: it is a different condition with
a different remedy, so `verify_plan` raises `PlanArtifactUnreadableError` for it rather than returning
it as a failure entry.

### Canonical encoding

Both files use one encoding: UTF-8 without BOM,
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, LF endings only, one
operation object per line with every line terminated. An empty plan is a zero-byte `operations.jsonl`.

Values pass through `canonical_value` before encoding: `str | int | float | bool | None` pass through,
`datetime`/`date` become ISO-8601 strings, `Decimal` becomes its `str`, `list`/`tuple` descend **in
source order**, `dict` descends key-sorted, and anything else raises `UnserializablePayloadValueError`
naming the kind, the field and the Python type. There is no `default=` fallback.

Canonical *ordering* applies to the operations sequence and to relationship peer lists only. A
payload's list-valued attributes keep source order and are never re-sorted, because sorting them would
make the applied value differ from the reviewed source value.

### An operation

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
| `relationships` | `array` | present **if and only if** the operation carries any |

Operations are stored ordered by `(tier, operation_id)` and are executed in exactly that order — no
re-sorting and no recomputation at apply time. When the configuration supplies an explicit `order:`,
the engine computes no tiers, and an operation's tier is its kind's index in the flat `top_level`
order — which under an explicit `order:` is the operator's own declared write order.

**Absent versus empty is meaningful.** An absent `relationships` key means the operation carries no
relationship values at all; it is never `[]`. A `peers: []` inside a `cardinality: "many"` reference
means the peer set is deliberately empty, and the replace-set write acts on it. There is no field
grouping operations into write units at either level.

#### An emptied one-peer relationship

From format 5 on, a `cardinality: "one"` reference with `peers: []` on an update means that the
relationship becomes empty, for example an interface that leaves its LAG:

```json
{"cardinality":"one","field":"bundle","peer_kind":"InterfaceLag","peers":[]}
```

`RelationshipReference.empties_one_peer` is true for such a reference. An absent reference still
means "this operation carries no value for that field": a relationship a plan does not mention is
one the apply leaves alone. The data model refuses an empty one-peer reference when the plan is read
in these cases:

- on a create, because a node created without a peer holds no entry for the relationship;
- on a delete;
- on a field that is part of the operation identity;
- in a plan of format 4 or earlier, where a one-peer reference names exactly one peer.

Derivation (`infrahub_sync/plan/derive.py`) adds the empty reference to an update when all of these
hold:

- the field is a one-peer reference field of the mapping, and not an identifier;
- the source value is `None` and the destination node holds a peer, so the field is a difference;
- the destination schema does not require the field;
- the source adapter did not remove the peer through a configured filter. The shared reference
  helper records each such field in the adapter's `filtered_peer_fields`, and derivation leaves
  that relationship alone. The IP Fabric adapter records a field in the same set when the source
  names a peer that its read did not store.

When the adapter recorded filtered fields but the source store does not hold the record that the
update belongs to, derivation cannot tell whether a filter removed the peer. It leaves the
relationship alone and logs one warning that names the kind and the field.

A peer that the problem pass leaves out of the plan removes the reference from the update, so a
kept record does not record an emptied relationship.

A required one-peer relationship that the source empties gives a problem of type
`required_relationship_empty_in_source` that names the record and the field. The record is left
out of the plan and its destination node is kept, as for `required_peer_excluded_by_filter`. Two
steps report it:

- the source read, when the source mapping model does not accept `None` for the field;
- the problem pass (`infrahub_sync/plan/problems.py`), when the source mapping model accepts
  `None`, the destination schema requires the field, and the destination node holds a peer.

Apply writes the emptied relationship as `<field>: null` in the one Upsert of the operation. The
write is refused before it is sent when the rendered input holds anything else for that field, or
renders a relationship that the operation does not name. The pre-write checks compare the empty
planned peer set with the destination peer set like any other relationship, and
`runs plan --detail` prints `<field>: becomes empty`.

One case is not covered: a record with no source ID whose peer a configured filter removed is not
recorded in `filtered_peer_fields`, so the plan empties that relationship.

#### The payload carries the identity

```text
payload = element.keys ∪ element.source_attrs   minus every key carried as a relationship reference
```

Every identity component is carried by the operation, and they are not decoration. Each one comes from
**either** a payload field **or** a `relationships[].field` reference — a component that crosses a
relationship takes its value from the referenced peer's own identity — and no component may come from
both, because that would give the destination field two competing write sources.

A **create** carries no recorded destination id. For a kind with a human-friendly ID,
Infrahub matches the write on the complete components; Sync requires usable values from
identity fields or relationship references. For a kind without a human-friendly ID,
Sync requires usable identity values covering at least one declared uniqueness constraint
so the destination refuses duplicates. Sync refuses missing coverage or unusable values
before `client.create`. An **update** records the destination object's `id` and is keyed
by that, so it may omit a human-friendly-ID component and still write the intended object.

`source_attrs` alone cannot supply them. DiffSync's `get_attrs()` explicitly "does not include the
fields in `_identifiers`", and the generator strips identifiers out of `_attributes`, so the identity
has to be merged in from `element.keys`. Every identity key ends up in exactly one of `payload` or
`relationships[].field` — never neither, which is a model-level validation.

#### Peer references are recursive

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

#### The operation identifier

```python
op_id = "op_" + sha256(canonical_json_bytes([action, kind, canonical_identity(identity)])).hexdigest()[:16]
```

A JSON array, in that order. The payload is excluded, so the identifier names the logical operation and
stays stable across re-plans; payload exactness is covered by `plan_checksum` instead. Exactly one
operation exists per `(action, kind, identity)`, so a collision is pathological and fails the plan run.
The data model rejects a stored identifier that does not match its own triple.

### The manifest

| Key | Meaning |
|---|---|
| `format_version` | `5` for every plan written now; `4` is read and applied; `2` and `3` are accepted for review only |
| `run_id` | The run the plan was produced under; excluded from the checksum, checked by equality |
| `created_at` | ISO-8601 UTC; excluded from the checksum |
| `config_version` | Opaque, non-empty printable ASCII; compared for equality, never parsed |
| `source_snapshot` | `{path, digest, row_count}` per bound snapshot, ordered by `path` |
| `operations_count` | Keeps an empty plan distinguishable from a torn one |
| `delete_operations_computed` | `false` when the destination side was loaded incrementally |
| `destination_binding` | `{url, branch}` — the destination the plan was computed against, URL-normalized, **never the token**. A registered package takes the URL from its declared settings only; a local configuration directory lets `INFRAHUB_ADDRESS` or `INFRAHUB_URL` override it. Compared for equality at apply time. Additive: absent on plans written before it existed, and the check is skipped for them |
| `lineage` | `{import_account: {name, id}, source: {kind, name}, owner: {kind, name} or null, protect}` — the import account the plan was computed with and the lineage its writes carry. `source` is `CoreAccount` and the import account's name when the package names no source; `owner` is `null` when it names none. An Infrahub apply refuses before any write when its own import account's `id` differs, and refuses a plan without this field, asking for a new plan. Additive: absent when the destination is not Infrahub and on plans written before it existed |
| `last_changes` | `{path, sha256, count, metadata_read}` — the record of `last_changes.jsonl`. `path` is always `last_changes.jsonl`, relative to `plan/`; `sha256` is the digest of the file's bytes; `count` is its number of records; `metadata_read` is `false` when the plan did not ask Infrahub for `updated_by` and `updated_at`, because the server is older than 1.7.0, its version could not be parsed, or Infrahub refused the version query. Additive: absent when the destination is not Infrahub and on plans written before it existed; review then shows no last-change data |
| `report` | From format 4: `{digest, size, problems, left_out, left_out_with_problem, left_out_dependent, kept}` — the SHA-256 digest and size of `report.jsonl` and its counts. A plan with no problem writes an empty file and zeros. Required at format 4 and 5; absent on format 2 and 3 plans |
| `dropdown_values_checked` | From format 4: written only as `false`, when the run had no destination schema snapshot and the plan could not compare Dropdown values with their choices. Absent otherwise |
| `plan_checksum` | Lowercase sha256 hex over the manifest body plus the operations bytes |
| *(any other key)* | Tolerated on read, preserved, and included in the bytes the checksum covers |

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

### The last-change file

`last_changes.jsonl` records who last changed each field that an update proposes to change, and
when. It is display data: `runs plan --detail` renders it, and apply does not read it. A create has
no record. One canonical JSON object per line, every line LF-terminated, sorted by `operation_id`
then `field`, so the same plan inputs give the same bytes; zero records encode to zero bytes.

| Field | Meaning |
|---|---|
| `operation_id` | The `operation_id` of the update the field belongs to |
| `field` | The attribute or relationship name |
| `field_type` | `attribute` or `relationship` |
| `updated_by` | `{id, display_label}` of the account Infrahub recorded, or `null` |
| `updated_at` | ISO 8601 time Infrahub recorded, or `null` |

A `null` is rendered as `not recorded`. For a cardinality-many relationship the record is the edge
that changed most recently. The checksum covers the file through the manifest's `last_changes.sha256`.
The reader splits the file's checks in two:

- Integrity checks refuse the plan. Review and the pre-apply check (`last_changes`) both refuse a
  plan whose file is absent, or whose digest or record count differs from the manifest's record,
  because such a file was changed after the plan was written.
- Content checks do not refuse it. A file that matches its digest but holds a bad record comes from
  a writer bug, and apply does not read the file, so the plan can still be reviewed and applied. A bad
  record is a line that is not UTF-8 JSON, that fails `LastChangeRecord` validation, that names no
  update of the plan, whose field its update does not write (an `attribute` not in the update's
  `payload`, or a `relationship` not in its `relationships`), or that repeats a field of an update.
  The reader stops at the first bad line and sets `LoadedPlan.last_changes` to `None` and
  `LoadedPlan.last_changes_problem` to the line number and the reason. Review adds one
  verification note, for example `last_changes.jsonl line 4 repeats field 'name' of operation
  'op_3f9a1c2b7d4e6a80' (first recorded on line 2); last-change details are not shown`, and
  shows no last-change details for any field. The Sync API sends that note in
  `PlanResource.verification_notes` with
  `last_changes: null`. Apply reads the same plan as the plan without the bad line.

An update field with no record is accepted: the payload holds every source attribute, not only the
changed ones, so the operations do not show which fields must have a record.

### The report file

`report.jsonl` holds what the problem pass found: one canonical JSON object per line, each with
`entry` set to `problem`, `left_out` or `kept` and the fields of that entry. Problems come
first, ordered by `problem_type`, `kind`, canonical identity and `source_id`; then the left-out
records, by `kind`, identity and `source_id`; then the kept records, by `kind`, identity and
`relationship`. Ties are broken by the entry's canonical bytes, so the order does not depend on
the order in which the source was read.

The checksum covers the file through the manifest's `report.digest`. Unlike
`last_changes.jsonl`, the file is not display data: an apply checks the left-out count it
records before it writes, so the reader and the pre-apply check refuse a plan whose file is
absent or whose digest, size or counts differ from the manifest's record. The canonical plan
fingerprint of ADR 0007 does not cover the report, because it hashes `plan.parquet`, which is
written before the problem pass.

### Determinism

Two plan runs over an unchanged source and destination, **at the same extraction mode on each side**,
produce a byte-identical `operations.jsonl` and a `manifest.json` byte-identical after removing
`run_id` and `created_at` from both sides. The same-extraction-mode precondition is part of the
procedure, not a caveat: `delete_operations_computed` is inside the checksum and is not masked, so two
runs at different extraction modes are *expected* to differ.

### Reading and verifying a plan

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

### What the format does not cover

Recorded so nothing reads an obligation into the silence: retention, expiry or pruning of a stored
plan; pagination or truncation; volume or latency targets; the stability of *rendered* review text,
which is operator-facing output rather than a format; a governance process for changing the format
— `format_version` and the unknown-key tolerance are the two mechanisms provided.

### See also

- [ADR 0001](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0001-saved-plan-artifact-format.md) — why the format is shaped this way.
- [Planned writes and apply](planned-write-and-apply.md) — what executes an artifact.
- [Incremental sync and cache](incremental-and-cache.md) — the run directory and snapshots it sits in.
- [Schema mapping](schema-mapping.md) — where identities and references come from.
