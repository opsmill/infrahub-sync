# Contract: plan artifact format (format_version 2)

**Owner**: this outcome. **Consumers**: nine later outcomes — the public API's apply, the
configuration-version binding, a schema-fingerprint manifest field, branch review, the apply ledger's
operation identifiers, scoped plans, per-operation dependency tiers, plan summaries in the UI, and
byte-for-byte comparison against this format. **Any change after this ships is breaking for all nine.**

## Layout

```text
<cache_root>/<sync-name>/<run_id>/          # existing, infrahub_sync/cache/paths.py:56-59
├── A/<resource>.parquet                    # existing source snapshot, the binding target
├── B/<resource>.parquet                    # existing destination snapshot (not bound)
├── plan.parquet                            # existing; still written, NEVER read by this format
├── run.json, cursors.json, schema-sub-hash.txt
└── plan/                                   # NEW — the artifact
    ├── operations.jsonl                    # written FIRST
    └── manifest.json                       # written LAST — its presence is the commit point
```

### Presence rules

| Observation | Verdict | Message family |
|---|---|---|
| `plan/` absent entirely | Pre-existing (v1) plan | "re-plan" (FR-019) |
| `plan/` present, `manifest.json` absent or unparseable | **Torn** | "torn artifact" (FR-010) |
| `manifest.json` present, `operations.jsonl` absent | **Torn** | "torn artifact" |
| `manifest.json` present, line count ≠ `operations_count` | **Torn** | "torn artifact" |
| `manifest.json` present, `format_version` unrecognized | Unrecognized version | "version found / versions supported" — textually **distinct** from the v1 message (FR-027, SC-018) |
| Any path unreadable (permission or I/O) | Unreadable | names the path (AD036) |

The write order is what makes the v1 and torn verdicts disjoint by construction rather than by
heuristic (AD014). Both files are written tmp+`replace`, matching
`infrahub_sync/cache/sidecars.py:13-24`.

## Canonical encoding

Applies to both files.

- UTF-8, no BOM.
- `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
- LF line endings only. `operations.jsonl` holds exactly one operation object per line, each line
  terminated by `\n`, including the last. An empty plan is a zero-byte file.
- Values are normalized by `canonical_value` **before** encoding (PD-002): `str | int | float | bool |
  None` pass through; `datetime`/`date` → ISO-8601 string; `Decimal` → `str`; `list`/`tuple` recurse
  **in source order**; `dict` recurses key-sorted; anything else raises. There is no `default=`
  fallback.
- Canonical ordering applies to the operations sequence and to relationship-reference peer lists
  **only**. A payload's list-valued attributes keep source order and are never re-sorted, because
  sorting them would make the applied value differ from the reviewed source value (FR-005).

## Operation ordering

Operations are ordered by `(tier, operation_id)` — dependency tier ascending, then operation
identifier ascending as a byte-wise string comparison (AD001). Ties are impossible: two operations
sharing an identifier fail the plan run (FR-021).

## `operations.jsonl` — one line per operation

```json
{"action":"create","identity":{"name":"prod"},"kind":"BuiltinTag","operation_id":"op_3531c0d83d698fd1","payload":{"description":"Production","name":"prod"},"tier":0}
```

With relationships (cardinality one and many together). `LocationRack`'s identity is
`["name", "site"]` and `site` is a reference, so `site` appears in `identity` **and** as a
relationship reference, and is **not** duplicated into the payload:

```json
{"action":"update","identity":{"name":"dc1-rack-a","site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}},"kind":"LocationRack","operation_id":"op_42d8e04c060f61b8","payload":{"name":"dc1-rack-a"},"relationships":[{"cardinality":"one","field":"site","peer_kind":"LocationSite","peers":[{"name":"dc1"}]},{"cardinality":"many","field":"tags","peer_kind":"BuiltinTag","peers":[{"name":"prod"},{"name":"rack"}]}],"tier":2}
```

A peer whose own identity contains a reference nests, recursively — an `InterfacePhysical` whose
`device` is a `DcimDevice` whose `location` is a `LocationRack`:

```json
{"action":"create","identity":{"device":{"identity":{"location":{"identity":{"name":"rack-a","site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}},"peer_kind":"LocationRack"},"name":"dev1"},"peer_kind":"DcimDevice"},"name":"Ethernet1"},"kind":"InterfacePhysical","operation_id":"op_5dfe0d0bdc714b36","payload":{"name":"Ethernet1"},"relationships":[{"cardinality":"one","field":"device","peer_kind":"DcimDevice","peers":[{"location":{"identity":{"name":"rack-a","site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}},"peer_kind":"LocationRack"},"name":"dev1"}]}],"tier":4}
```

A delete carries no payload:

```json
{"action":"delete","identity":{"name":"retired"},"kind":"BuiltinTag","operation_id":"op_ba078d0eae6c9fc3","tier":0}
```

| Key | Type | Obligation |
|---|---|---|
| `operation_id` | `string`, `^op_[0-9a-f]{16}$` | always |
| `action` | `"create" \| "update" \| "delete"` | always |
| `kind` | `string` | always |
| `identity` | `object`, key-sorted | always |
| `tier` | `integer ≥ 0` | always |
| `payload` | `object` | required on `create`/`update`, **omitted** on `delete` |
| `relationships` | `array` | present iff the operation carries any; **absent**, never `[]`, when it carries none |

Absent versus empty is load-bearing: an absent `relationships` key means "no relationship values at
all"; a `peers: []` inside a `cardinality: "many"` reference means "the peer set is deliberately
empty", which the replace-set write then acts on (FR-028.2).

There is **no** field grouping operations into write units, at either level (FR-026).

### What `payload` contains (AD042)

```text
payload = element.keys ∪ element.source_attrs   minus every key carried as a relationship reference
```

**The identity components are inside the payload.** They are not optional decoration: the destination's
convergent write is keyed on the kind's human-friendly ID, whose components come from the identity, and
a write issued without them is unkeyed and duplicates on every re-apply — DBA-002 and DBA-003
unachievable.

`element.source_attrs` alone cannot supply them. It is built from `src_obj.get_attrs()`
(`.venv/…/diffsync/helpers.py:223`), whose own contract states it "does not include the fields in
`_identifiers`" (`.venv/…/diffsync/__init__.py:340-347`), and the generator strips identifiers out of
the `_attributes` tuple before the models are written (`infrahub_sync/generator/__init__.py:95`).
Today's create path avoids the problem by passing identifiers and attributes together
(`infrahub_sync/adapters/infrahub.py:602-604`); the artifact must do the same.

An identity component whose `SchemaMappingField.reference` is set travels as a relationship reference
instead of staying in the payload as a raw unique-id string, on the same rule as any other
reference-bearing field. Every identity key therefore appears in exactly one of `payload` or
`relationships[].field` — never neither, which is a model-level validation (data-model.md).

### Peer identity is recursive (AD043)

A peer identity component that is itself a reference records:

```json
{"peer_kind": "<kind>", "identity": { ... }}
```

rather than the peer's DiffSync unique-id string, recursively to whatever depth the configuration
nests. This is required, not cosmetic: **ten** schema-mapping entries on the qualified path carry a
reference inside `identifiers` (`LocationRack.site`, `DcimDeviceType.manufacturer`, `DcimDevice.location`
twice, `Interface{Physical,Virtual,Lag}.device`, `IpamVLAN.vlan_group`, `IpamPrefix.vrf`,
`IpamIPAddress.vrf`), and a reference field's value in a comparison model is the peer's unique-id
string. On a memo miss the apply-time resolver holds only the peer's identity mapping; without the
nested pair it could not build a nested destination filter without splitting a unique-id on `__` —
the v1 flaw the brief names.

`peer_kind` at every level is **probed from the store**, never read from the referring field's
`reference` value: `DcimDevice` is declared by two schema-mapping entries whose `location` reference
differs (`examples/netbox_to_infrahub/config.yml:212` → `LocationRack`, `:254` → `LocationSite`), so
the mapping alone is ambiguous and a wrong pick fails the whole apply run on the qualified path
(AD046).

The probe is bounded and its arms are fixed (AD050). AD046's own phrasing — "the loaded source store
entry knows its own kind" — is not constructible: `store.get(*, model, identifier)` requires the model
before the identifier is looked at, on the base class and in the local implementation alike
(`.venv/…/diffsync/store/__init__.py:40-52`, `.venv/…/diffsync/store/local.py:30-49`), so there is no
kind-free lookup by unique-id.

| Step | Rule |
|---|---|
| Candidate set | The kinds declared as `reference` for that field across **every** schema-mapping entry whose `name` is the owning destination kind. For `DcimDevice.location`: `{LocationRack, LocationSite}` (`examples/netbox_to_infrahub/config.yml:239`, `:281`) |
| Probe | `store.get(model=candidate, identifier=peer_unique_id)` per candidate, `ObjectNotFound` meaning "not this kind" |
| Which store | The **source** store when the owning operation is a `create` or `update`; the **destination** store when it is a derived `delete`, whose peers are destination-only by construction (AD049) |
| exactly 1 hit | That candidate is `peer_kind` |
| **0** hits | The plan run fails, naming the owning kind, the field, the unique-id and the candidates tried (FR-030) |
| **> 1** hit | The plan run fails, naming the same four things (FR-030) |
| Fallback | **None.** A one-candidate set is probed like any other and a miss still fails — returning the sole mapping-declared kind unprobed is the mapping-derived answer AD046 forbids, reached by another route |

Nested objects are key-sorted like any other, so the canonical encoding and the identifier derivation
are unaffected.

### A derived delete's identity (AD049)

A delete carries no payload and no `relationships`, but its `identity` obeys **exactly** the rules
above, including the recursive `{peer_kind, identity}` shape for a reference-valued component. The
only difference is which store the probe runs against: a delete exists because its object is present at
the destination and absent from the source, so its peers are destination-only and the source store has
nothing to resolve. Nine kinds on the qualified path (ten mapping entries) carry a reference inside
their identifiers — the **configuration-side** figure, which is the one this rule turns on; the
destination-side count of keys that cross a relationship is five and is a different question (AD091) —
so this is the ordinary case for deletes there. Deletes are **not** exempt: exempting
them would leave one place in the format where a consumer must split a unique-id on `__`, and would
make a delete's `operation_id` derive from an identity no reviewer is shown.

## Operation-identifier derivation

```python
op_id = "op_" + sha256(
    canonical_json_bytes([action, kind, canonical_identity(identity)])
).hexdigest()[:16]
```

The hash input is a JSON **array** in the order (action, kind, identity) — fixed by PD-001. The
payload is deliberately excluded, so the identifier names the logical operation and stays stable across
re-plans; payload exactness is guaranteed by `plan_checksum` instead (AD002).

Every `operation_id` in the worked examples above is the value this rule actually produces for that
example's `(action, kind, identity)` — recomputed, not illustrative. The data model rejects a stored
identifier that does not match its own triple, so an approximate example in this contract would be an
example the model refuses.

**Test vectors** (must hold in `tests/plan/test_identity.py`) — the canonical input string on the left,
the resulting identifier on the right. Each input is UTF-8, no whitespace, no escapes:

```text
["create","BuiltinTag",{"name":"prod"}]
    -> op_3531c0d83d698fd1

["update","LocationRack",{"name":"dc1-rack-a","site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}}]
    -> op_42d8e04c060f61b8

["create","InterfacePhysical",{"device":{"identity":{"location":{"identity":{"name":"rack-a","site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}},"peer_kind":"LocationRack"},"name":"dev1"},"peer_kind":"DcimDevice"},"name":"Ethernet1"}]
    -> op_5dfe0d0bdc714b36

["delete","BuiltinTag",{"name":"retired"}]
    -> op_ba078d0eae6c9fc3
```

The fourth vector is a **delete**, and it is here deliberately: a derived delete's identity is
canonicalised by the same rule as any other operation's, so its identifier derives from the same
three-element array (AD049).

Under FR-002's closed action vocabulary exactly one operation exists per
`(action, kind, identity)`, so a collision is always pathological and fails the plan run (FR-021).

## `manifest.json`

```json
{"config_version":"5f2c…","created_at":"2026-07-26T18:04:11.512034+00:00","delete_operations_computed":true,"format_version":2,"operations_count":37,"plan_checksum":"a91c…","run_id":"20260726T1804-9f3ac210","source_snapshot":[{"digest":"7e10…","path":"A/BuiltinTag.parquet","row_count":12},{"digest":"cc48…","path":"A/LocationSite.parquet","row_count":6}]}
```

| Key | Type | Meaning |
|---|---|---|
| `format_version` | `integer` | `2`. `1` is reserved for the pre-existing row format and never appears in a manifest |
| `run_id` | `string` | The run the plan was produced under; excluded from the checksum, checked by equality (AD012) |
| `created_at` | `string` | ISO-8601 UTC; excluded from the checksum |
| `config_version` | `string` | Opaque, non-empty printable ASCII; compared for equality, never parsed |
| `source_snapshot` | `array` of `{path, digest, row_count}`, ordered by `path` | The binding (see below) |
| `operations_count` | `integer` | Keeps an empty plan distinguishable from a torn one |
| `delete_operations_computed` | `boolean` | `false` when the destination side was loaded incrementally (AD024) |
| `plan_checksum` | `string` | Lowercase sha256 hex (see below) |
| *(any other key)* | any | **Tolerated on read, preserved, and included in the checksummed bytes.** A later outcome adds a schema-fingerprint field here (FR-027) |

## `plan_checksum`

```python
excluded = {"plan_checksum", "run_id", "created_at"}
body     = {k: v for k, v in manifest.items() if k not in excluded}   # REMOVED, not blanked
digest   = sha256(canonical_json_bytes(body) + operations_jsonl_raw_bytes).hexdigest()
```

No separator between the two byte sequences (AD035). The three fields are removed before
canonicalization, not set to `null` or `""`.

Excluding `run_id` and `created_at` is what makes the manifest byte-identical across re-plans (SC-006).
`plan_checksum` needs no SC-006 mask of its own, because it is a function of the checksummed bytes
alone and is therefore already identical whenever they are.

## `source_snapshot` digest — logical rows, not file bytes

**This is PD-008 and it is the one place this contract reinterprets AD008.** The recorded `digest` is
computed over the snapshot's logical rows, not the Parquet file's bytes:

```python
table = read_table(run_dir / path)
cols  = [c for c in table.column_names if c != "_extract_ts"]
rows  = table.select(cols).to_pylist()          # file order preserved
digest = sha256(b"\n".join(canonical_json_bytes(canonical_value(r)) for r in rows)).hexdigest()
row_count = table.num_rows
```

`_extract_ts` is `datetime.now(timezone.utc)` allocated once per side per run
(`infrahub_sync/potenda/__init__.py:130`) and injected into every row
(`infrahub_sync/cache/parquet_io.py:126`), so a raw-bytes digest would differ on every re-plan of an
unchanged source and make SC-006 unachievable while SC-006 forbids extending its two-field mask.
`_source_id` and `_tombstone` stay inside the digest: both are deterministic for identical input.

"Match" at apply is recomputed equality of all three parts. An absent recorded file, a disagreeing
digest, or a disagreeing row count is a refusal (FR-004, FR-010, SC-004). Because `plan_checksum`
covers the canonical manifest, tampering with a recorded digest fails the checksum too.

## Determinism guarantee (SC-006)

Two plan runs over an unchanged source and destination, **at the same extraction mode on each side**,
produce:

- a byte-identical `operations.jsonl`, and
- a `manifest.json` byte-identical after removing `run_id` and `created_at` from **both** sides
  (masking is key removal, applied symmetrically, before the byte comparison — AD035).

The same-extraction-mode precondition is part of the evidence procedure, not a caveat:
`delete_operations_computed` is inside the checksum and is not masked, so two runs at different
extraction modes are *expected* to differ (FR-015, AD024).

## What this contract does not cover

Recorded so a consumer does not read an obligation into the silence:

- Retention, expiry or pruning of a stored plan (AD030).
- Pagination or truncation of anything (AD030).
- Volume or latency targets (AD030).
- The stability of *rendered* review text — that is operator-facing output, not a format (AD030).
- A governance process for changing this format. `format_version` and the unknown-field tolerance are
  the two mechanisms provided; the process around them belongs elsewhere (AD030).
