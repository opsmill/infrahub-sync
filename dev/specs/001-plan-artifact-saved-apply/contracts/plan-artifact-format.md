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
{"action":"create","identity":{"name":"prod"},"kind":"BuiltinTag","operation_id":"op_3f2a1c9d0e4b6a58","payload":{"description":"Production","name":"prod"},"tier":0}
```

With relationships (cardinality one and many together):

```json
{"action":"update","identity":{"name":"dc1-rack-a"},"kind":"LocationRack","operation_id":"op_9b1d77c204e3af10","payload":{"name":"dc1-rack-a"},"relationships":[{"cardinality":"one","field":"site","peer_kind":"LocationSite","peers":[{"name":"dc1"}]},{"cardinality":"many","field":"tags","peer_kind":"BuiltinTag","peers":[{"name":"prod"},{"name":"rack"}]}],"tier":2}
```

A delete carries no payload:

```json
{"action":"delete","identity":{"name":"retired"},"kind":"BuiltinTag","operation_id":"op_c40e9a71b3d5f682","tier":0}
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

## Operation-identifier derivation

```python
op_id = "op_" + sha256(
    canonical_json_bytes([action, kind, canonical_identity(identity)])
).hexdigest()[:16]
```

The hash input is a JSON **array** in the order (action, kind, identity) — fixed by PD-001. The
payload is deliberately excluded, so the identifier names the logical operation and stays stable across
re-plans; payload exactness is guaranteed by `plan_checksum` instead (AD002).

**Test vector** (must hold in `tests/plan/test_identity.py`):

```text
input bytes : ["create","BuiltinTag",{"name":"prod"}]
              (0x5b 0x22 0x63 ... — no whitespace, no escapes, UTF-8)
```

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
