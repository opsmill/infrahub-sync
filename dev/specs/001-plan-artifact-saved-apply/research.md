# Phase 0 research: Saved plan artifact and apply-exactly-what-was-reviewed

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Date**: 2026-07-26

The specification arrives with 36 provisional decisions (AD001–AD036) that settle the design
questions a plan would normally research. This document therefore does **not** re-open any of them.
It records the ten details planning had to close underneath them — a decision AD001–AD036 left one
level of abstraction above the code, or an interaction between two of them that neither anticipated.

Each is numbered **PD-nnn**, states which AD it sits under, and carries a materiality judgment.
PD-003, PD-005, PD-008, PD-009 and PD-010 are reported upward as new decisions; the rest are
low-impact, reversible calls made inside the CHECKPOINT mandate.

---

## PD-001 — The operation-identifier hash input is a JSON array

**Sits under**: AD002 ("a SHA-256 over the canonical JSON of the triple (action, destination kind,
destination identity)").

**Question**: "canonical JSON of the triple" admits two encodings — a JSON array
`["create","BuiltinTag",{"name":"prod"}]` or a JSON object
`{"action":"create","identity":{...},"kind":"BuiltinTag"}`. They hash differently, so one must be
fixed before any artifact is written.

**Decision**: a JSON **array** in the order (action, kind, identity), with the identity a key-sorted
mapping. `operation_id = "op_" + sha256(canonical_json_bytes([action, kind, canonical_identity])).hexdigest()[:16]`.

**Rationale**: "the triple" is literally a 3-tuple, and an array has no key names to bikeshed and no
risk of a later reader disagreeing about whether the object's keys were sorted before or after
nesting. It is also shorter, which matters not at all for correctness but does for readability of the
test vectors.

**Alternatives considered**: the object form — rejected only because it adds three key strings to the
hash input for no benefit; both are equally deterministic.

**Materiality**: low, but it is part of a format nine outcomes consume, so it is written into
[contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md) with a worked test vector
rather than left to the implementation.

---

## PD-002 — Payload value normalization is explicit, not a `json.dumps(default=str)` hook

**Sits under**: AD001 (canonical JSON) and AD035 (determinism details).

**Question**: mapped source values are not all JSON-native. The Infrahub adapter already normalizes IP
types to strings (`infrahub_sync/adapters/infrahub.py:523-529`) but explicitly lets List, Number,
Boolean and DateTime pass through unchanged, with a comment warning that stringifying a real list
turns `[]` into `"[]"`. The existing plan writer papers over the rest with
`json.dumps(..., default=str)` (`infrahub_sync/potenda/__init__.py:324-325`).

**Decision**: a `canonical_value(v)` function applied before encoding, with an explicit table:
`str | int | float | bool | None` pass through; `datetime`/`date` become their ISO-8601 string;
`Decimal` becomes its `str`; `list`/`tuple` recurse **in source order**; `dict` recurses with sorted
keys; anything else raises `UnserializablePayloadValueError` naming the kind, the field and the
Python type. No silent `str()` fallback.

**Rationale**: a silent `default=str` makes the artifact's determinism depend on a type's `__str__`,
which is exactly the kind of invisible coupling SC-006 would fail on months later. Raising is safe
because the qualified path's mapped values are all in the table, and a refusal at plan time is the
loud failure the whole feature is built around.

**Alternatives considered**: keep `default=str` (rejected: non-deterministic for any type with a
memory-address `repr`); encode everything as strings (rejected: destroys the payload's type fidelity,
which FR-028.4 says must be applied to the destination).

**Materiality**: low-medium. Reversible; no cross-outcome contract depends on which types are
accepted, only that the encoding is deterministic.

---

## PD-003 — What the default configuration-version checksum covers

**Sits under**: AD035 ("the default checksum rule covers the declared content of the configuration
the run used, as parsed, not the file's bytes") and FR-011.

**Question**: "as parsed" still leaves the field set open. `SyncInstance` is `SyncConfig` plus
`directory: str` (`infrahub_sync/__init__.py:148-149`), and `directory` is an absolute filesystem path
assigned from wherever the config was found (`infrahub_sync/utils.py:130`, `:160`). Including it makes
the configuration version machine-dependent, so a plan produced in CI could never be applied from a
developer's checkout, and SC-013's round-trip would depend on the working directory.

**Decision**: the default value is `sha256(canonical_json_bytes(config.model_dump(mode="json",
exclude={"directory"})))`, lowercase hex. It covers `name`, `store`, `source`, `destination`,
`adapters_path`, `order`, `schema_mapping`, `diffsync_flags` and `incremental` — everything the run's
behavior depends on — and excludes only `directory`.

**Rationale**: FR-011 requires a value that is stable for an unchanged configuration and different for
a changed one. `directory` is location, not configuration. `settings` **are** included: a changed
destination URL is a changed configuration, and the value is a one-way digest, so no credential is
disclosed by including credentials in the hash input (FR-018 concerns what is *written*, and only the
digest is written).

**Consequence the plan accepts and states**: the spec's own Assumptions already note that "a benign
reformat of the configuration that the rule is sensitive to invalidates saved plans". Parsing before
hashing makes the rule insensitive to YAML comments, key order and whitespace, which is the
strongest form of that property available without a version registry (DB-008).

**Alternatives considered**: hash the file bytes (rejected by AD035 explicitly); hash only
`schema_mapping` (rejected: a changed destination URL would not invalidate a plan, which is worse than
over-invalidating); include `directory` (rejected as above).

**Materiality**: **medium — reported upward.** It decides when a saved plan stops being applicable,
which operators will feel, and DB-008 later replaces this rule with a real version identifier.

---

## PD-004 — Apply-time peer lookup is built from the destination schema's HFID component paths

**Sits under**: AD003 (peer referenced by kind and identity, resolved through a per-apply cache and,
on a miss, "by querying the destination for that identity") and AD017 (convergence rides on the
human-friendly ID).

**Question**: AD003 says "querying the destination for that identity" without saying how an identity
mapping becomes a destination query. On the qualified configuration this is not cosmetic: seven kinds
carry a relationship *inside* their identity, so an identity value is sometimes a peer's DiffSync
unique-id rather than a scalar. Verified in `examples/netbox_to_infrahub/config.yml`:

| Kind | `identifiers` | identity components that are references |
|---|---|---|
| `LocationRack` | `name`, `site` | `site` → `LocationSite` |
| `DcimDeviceType` | `manufacturer`, `name` | `manufacturer` → `OrganizationManufacturer` |
| `DcimDevice` | `location`, `name` | `location` → `LocationRack` / `LocationSite` |
| `InterfacePhysical` / `InterfaceVirtual` / `InterfaceLag` | `device`, `name` | `device` → `DcimDevice` |
| `IpamVLAN` | `name`, `vlan_group`, `vlan_id` | `vlan_group` → `IpamVLANGroup` |
| `IpamPrefix` | `prefix`, `vrf` | `vrf` → `IpamVRF` |
| `IpamIPAddress` | `address`, `vrf` | `vrf` → `IpamVRF` |

The naive approach — split the unique-id on `__` — is precisely the v1 flaw the brief names
("identifiers are recovered by splitting the diffsync unique-id on `__`").

**Decision**: the resolver builds its query from the **destination schema's own** `human_friendly_id`
component paths (`.venv/…/infrahub_sdk/schema/main.py:272`; the adapter already caches the whole
schema at `infrahub_sync/adapters/infrahub.py:345`), evaluating each path against plan data:

- a path of the form `<attr>__value` takes its value from the operation's identity mapping under
  `<attr>`;
- a path of the form `<rel>__<attr>__value` takes its value from the identity mapping recorded on the
  operation's relationship reference for `<rel>` — which the plan carries precisely because AD003
  requires peers to be named by kind and identity.

The resulting `{path: value}` mapping is passed to `client.filters(kind=..., **kwargs)` and the result
count drives the three arms: 1 → the node id, 0 → `PeerNotFoundError`, >1 → `PeerAmbiguousError`
naming the count (FR-014, SC-016). A schema path is split, never a data value.

**Rationale**: it reuses the exact key the upsert converges on (V15), so resolution and convergence
cannot disagree; it needs no new plan field; and it degrades into FR-024's already-required warning
when the HFID is absent or incomplete, which is the same precondition.

**Unverifiable offline**: the GraphQL filter spelling for a nested `<rel>__<attr>__value` argument.
Per AD007 there is no live Infrahub here. The `integration`-marked SC-008/SC-016 tests assert it, and
the failure mode if the spelling is wrong is a zero match — a loud refusal, never a silent drop.

**Alternatives considered**: filter on `<rel>__ids` after recursively resolving the sub-peer (kept as
the documented fallback in [contracts/destination-write-surface.md](./contracts/destination-write-surface.md)
for kinds whose HFID does not cover the identity); split the unique-id (rejected, v1 flaw); require
the plan to carry destination node ids (rejected by AD003 — they do not exist at plan time for peers
the same plan creates).

**Materiality**: medium, but it is an implementation of an already-settled decision rather than a new
one. Recorded here because the seven-kind table is load-bearing and was not in the spec.

---

## PD-005 — Cardinality-many replace-set is enforced explicitly after the upsert

**Sits under**: AD015 ("Cardinality-many relationships are **replace-set**, which is the existing
behavior: `update_node` computes `compare_lists(existing_peer_ids, new_peer_ids)` and then removes
`existing_only` and adds `new_only`").

**Question**: AD015's evidence for replace-set is `update_node` (`infrahub_sync/adapters/infrahub.py:149-175`),
which is on the path AD015 itself forbids the planned write from using. The path AD015 *mandates* —
`client.create(...)` + `save(allow_upsert=True)` (V10) — has no verified replace-set semantics in this
repository, and whether the server's upsert mutation replaces or merges a relationship list cannot be
determined without a live Infrahub (AD007).

**Decision**: `apply_planned_operation` performs the upsert, then, for every cardinality-many
relationship the operation carries, reconciles the saved node's peer set explicitly using the same
`compare_lists` remove/add logic, extracted from `update_node` into a shared
`_replace_relationship_set(node, rel_name, peer_ids)` helper. The extraction is behavior-preserving for
the existing caller.

**Rationale**: it makes FR-013's replace-set clause true by construction instead of by assumption
about server behavior nobody here can test, it reuses the only verified implementation in the tree, and
it costs one extra round trip per cardinality-many relationship on operations that carry one. If the
upsert already replaces, the reconciliation is a no-op and `compare_lists` returns empty
`existing_only`/`new_only` sets.

**Alternatives considered**: trust the upsert to replace (rejected: unverifiable, and a silent merge
would leave stale peers attached, which SC-008 would catch only against a live server — i.e. late);
always use `update_node` (rejected: it requires `client.get(id=local_id)`, which FR-012 forbids, V11).

**Materiality**: **medium-high — reported upward.** It adds a step AD015 does not describe to the
mandated write path.

---

## PD-006 — An unrecognized format version short-circuits the remaining pre-apply checks

**Sits under**: AD028 (format-version field and refusal) and AD036 ("the pre-apply checks are
evaluated in a stated order and **all** failures are named").

**Question**: FR-009 requires all five checks to be evaluated and every failure named. But check 1 is
"the manifest's declared format version is recognized". If it is not, the reader by definition does not
know what the remaining fields mean — evaluating a checksum rule or a snapshot-binding rule from a
future revision would produce failures that are artifacts of the reader's own ignorance.

**Decision**: check 1 is a gate. When the format version is unrecognized (or the manifest cannot be
parsed at all), that single failure is reported and checks 2–5 are not evaluated; the refusal message
says so explicitly ("remaining checks not evaluated: the artifact's format is not understood"). When
check 1 passes, checks 2–5 are all evaluated and every failure is named, as FR-009 requires.

**Rationale**: FR-009's "all failures" clause exists so one apply attempt tells the operator everything
that is wrong. Reporting four checks against a manifest whose semantics are unknown tells the operator
things that are not true, which is worse than telling them one thing that is.

**Alternatives considered**: evaluate all five regardless (rejected as above); refuse without naming
the check (rejected: FR-009 requires the check to be named).

**Materiality**: low. It is a reading of FR-009 rather than a change to it, and it is stated in the
contract so a reviewer can disagree in one place.

---

## PD-007 — The tier of an operation when the configuration declares an explicit `order:`

**Sits under**: AD022 ("a configuration supplying an explicit `order:` yields no tiers at all") and
FR-028.1 (the dependency tier "is required on every operation").

**Question**: those two are in tension. `compute_order_and_tiers` returns `tiers = None` when
`order:` is set (`infrahub_sync/__init__.py:132-133`), yet every operation must carry a tier.

**Decision**: when tiers are absent, an operation's tier is its kind's index in the flat `top_level`
order — which under an explicit `order:` *is* the operator's declared write order. When tiers are
present, the tier is the index of the tier set containing the kind.

**Rationale**: it keeps the field required and deterministic (both needed for SC-006's byte-identity
and for the artifact's ordering rule), and it preserves the meaning the field is supposed to have —
"write this before that". AD022's point is about what the tier *guarantees* for peer availability, and
that qualification is unaffected: under an explicit `order:` the guarantee is exactly as weak as AD022
says, and an unresolved peer is refused loudly by AD016.

**Alternatives considered**: emit tier `0` for every operation (rejected: it discards the operator's
declared order and makes the artifact's ordering rule collapse to identifier order, changing execution
order for `order:`-configured projects); make the field optional (rejected: FR-028.1 makes it required,
and an optional ordering key is a trap for the nine consumers).

**Materiality**: low-medium. Format-visible, so it is written into the contract.

---

## PD-008 — The source-snapshot digest covers logical rows, not raw file bytes

**Sits under**: AD008 ("a SHA-256 digest of its content") and SC-006 (byte-identical manifest across
two consecutive plan runs, masking only the run identifier and the creation timestamp).

**The conflict, verified in the tree**: `_write_side_snapshot` allocates
`extract_ts = datetime.now(timezone.utc)` once per side per run (`infrahub_sync/potenda/__init__.py:130`)
and `write_resource_side` injects it into **every row** as `_extract_ts`
(`infrahub_sync/cache/parquet_io.py:126`). Two consecutive plan runs over an unchanged source therefore
produce snapshot files whose bytes differ by construction. If the manifest's snapshot binding digested
those bytes, the digest would differ, the manifest would differ, and SC-006 would be unachievable —
while SC-006 forbids masking anything beyond the run identifier and the creation timestamp. AD008 and
SC-006 cannot both hold under a raw-bytes reading.

**Decision**: the recorded digest is a canonical digest over the snapshot's **logical rows** — the
Parquet table with the engine-injected `_extract_ts` column dropped, rows in file order, each row
encoded with `canonical_json_bytes` and joined by LF. `_source_id` and `_tombstone` are **kept** in the
digest: both are deterministic for identical input (`_source_id` is `get_unique_id()`;
`_tombstone` is `False` today) and both are semantically part of what the plan was computed against.
The record keeps AD008's three parts unchanged — run-relative path, digest, row count — and "match" is
still recomputed equality of all three.

**Rationale**: it is the only reading under which AD008 and SC-006 are simultaneously true. It
preserves everything the binding is for: an absent file, a truncated file (row count), a tampered
value (digest), and a swapped snapshot are all still detected. What it stops detecting is a change
confined to the extraction timestamp — which is not a change to the data the plan was computed
against, and is precisely the field SC-006 needs excluded.

**Cost**: verification reads and decodes the snapshot Parquet rather than digesting bytes. The apply
path already has to open those files to check the row count, so the extra cost is decode time on
files the engine wrote minutes earlier.

**Alternatives considered**: (a) digest the raw bytes and add the snapshot digests to SC-006's mask —
rejected, the spec fixes the mask at exactly two fields and warns that adding to it is not permitted;
(b) exclude `source_snapshot` from `plan_checksum` — rejected, it contradicts AD008's own reasoning
that the checksum is what stops the recorded digests being tampered with; (c) make `_extract_ts`
constant across runs — rejected, it would break the cursor machinery that reads it
(`infrahub_sync/potenda/__init__.py:405`, `infrahub_sync/cache/incremental.py:158`).

**Materiality**: **high — reported upward.** It reinterprets AD008's "digest of its content", and
AD008 is one of the manifest fields nine later outcomes consume.

---

## PD-009 — The tier sync branch computes every diff before the first write

**Sits under**: FR-001 ("MUST produce and save a plan artifact … before anything is written to the
destination") and FR-015 (a `sync`-mode run records deletes in its plan exactly as a `plan`-mode run
does).

**The conflict, verified in the tree**: `sync_in_tiers`' parallel branch interleaves diff and sync per
tier and writes the aggregated plan only after every write has completed
(`infrahub_sync/potenda/__init__.py:480-499`: diff at `:484`, accumulate at `:485`, sync at `:487`,
write at `:496-499`). The serial branch is already correctly ordered (`:462` writes, `:464` syncs; the
CLI does the same at `cli.py:271` then `:276`). So under `sync --parallel`, which is the default
(`infrahub_sync/cli.py:182-185`), no plan artifact can exist before the first destination write.

**Decision**: restructure the tier branch into two loops — compute and retain every tier's `Diff`,
write the plan artifact, then apply the retained diffs tier by tier through the existing
`sync(diff=...)` entry point (`infrahub_sync/potenda/__init__.py:292-295`). The destination
`top_level` narrowing stays exactly where it is, in the execution loop.

**Rationale**: it is the minimum change that makes FR-001 true on the default sync path. The two loops
are equivalent to the interleaved one because tiers partition kinds — tier *n*'s diff only involves
kinds tier *n−1*'s sync did not touch — and relationship values in the store are DiffSync unique-ids,
not destination ids, so an earlier tier's writes do not change a later tier's comparison.

**Cost**: every tier's `Diff` is held in memory simultaneously rather than one at a time. On the
qualified configuration that is six tiers of an already-in-memory comparison.

**Alternatives considered**: write the artifact only on the `diff` path and skip it for
`sync --parallel` (rejected: FR-015 requires sync-mode parity, and the artifact a sync leaves behind is
explicitly meant to be reviewable on the same terms); write a partial artifact per tier (rejected:
`plan_checksum` covers the whole ordered operation set, so a partial artifact is not a valid artifact).

**Materiality**: **medium — reported upward.** It reorders an existing, default-on execution path.

---

## PD-010 — The v1 dispatch is removed rather than kept alongside

**Sits under**: FR-019 ("no second apply path with weaker guarantees may be built") and AD014
(`plan.parquet` is left in place and never read by the new path).

**Question**: `Potenda.apply_plan` today reads `plan.parquet` and dispatches to
`destination.apply_cached_row(...)` (V1). Should the new artifact apply be added beside it, or should
it replace it?

**Decision**: replace. `apply_plan` reads the new artifact and dispatches to
`apply_planned_operation`; the `apply_cached_row` dispatch and its `hasattr` guard are removed, with
the guard's *shape* — a `NotImplementedError` naming the adapter class and telling the operator to use
`sync` — preserved for the new surface so FR-023 keeps the behavior the engine already has.
`plan.parquet` keeps being written (V23, AD014) and is simply never read.

**Rationale**: FR-019's plain text forbids a second apply path, and a wired v1 dispatch is that path.
Removal is safe in a way it rarely is: `apply_cached_row` has zero implementations anywhere in the
repository (V3), so nothing can be calling it successfully today. The only fallout is
`tests/cache/test_apply_plan.py`, whose `MagicMock` asserts the dispatch shape, and which Phase G
rewrites.

**Alternatives considered**: keep both and select on which artifact is present (rejected: that *is* the
two-paths-with-different-guarantees outcome D025 exists to prevent, and it would make the v1 rejection
message unreachable); deprecate `apply_cached_row` with a warning (rejected: nothing implements it, so
there is no user to warn).

**Materiality**: **medium — reported upward.** It removes a public-looking adapter extension point,
even though nothing implements it.

---

## Confirmed non-questions

Recorded so a later reader does not re-investigate them.

- **Where deletes go today.** Not a configuration accident: `Potenda` falls back to
  `DiffSyncFlags.SKIP_UNMATCHED_DST` when a project configures no flags
  (`infrahub_sync/potenda/__init__.py:92-93`), and diffsync drops destination-only objects under that
  flag before an element is ever created (`.venv/…/diffsync/helpers.py:191-192`). AD004's set-difference
  derivation is therefore the only way to see them without loosening the flag, exactly as it says.
- **Whether the full payload is available at plan time.** Yes. `DiffElement.source_attrs` is the
  complete source attribute set, not the delta (V4); today's writer simply does not use it, taking
  `get_attrs_diffs()` instead (`infrahub_sync/potenda/__init__.py:314-316`).
- **Whether the destination identity is available at plan time.** Yes. `DiffElement.keys` is the
  identifiers mapping (V4); today's writer hardcodes `dest_id: ""` (`:322`).
- **Whether review can avoid constructing an adapter.** Yes, and it must branch early to do so:
  `get_potenda_from_instance` imports and instantiates both adapters (`infrahub_sync/utils.py:183-235`)
  and creates the run directory before any check (V21), so the review branch has to sit above it in
  `diff_cmd`, not inside it.
- **Whether a new dependency is needed.** No. `hashlib` and `json` cover canonical encoding and both
  digests; `pathlib` covers the artifact I/O; the atomic-write helper already exists
  (`infrahub_sync/cache/sidecars.py:13-24`).
- **Whether the `integration` marker needs extending.** No. It already exists with the right semantics
  and the right documented environment variables (V28), and `tests/integration/` already contains a
  test using it.
