# Phase 0 research: Saved plan artifact and apply-exactly-what-was-reviewed

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Date**: 2026-07-26

The specification arrives with 36 provisional decisions (AD001–AD036) that settle the design
questions a plan would normally research. This document therefore does **not** re-open any of them.
It records the ten details planning had to close underneath them — a decision AD001–AD036 left one
level of abstraction above the code, or an interaction between two of them that neither anticipated.

Each is numbered **PD-nnn**, states which AD it sits under, and carries a materiality judgment.
PD-003, PD-005, PD-008, PD-009 and PD-010 were reported upward as new decisions and are now ratified
into the specification as **AD041, AD038, AD037, AD039 and AD040** respectively; the rest are
low-impact, reversible calls made inside the CHECKPOINT mandate.

A later cross-artifact analysis added **AD042–AD048** in the specification. Four of them land inside
entries here and are folded into them rather than duplicated: AD042 (the payload carries the identity
components) corrects a "confirmed non-question" below; AD043 (peer identities are recursive) is a
prerequisite of PD-004 and is noted there; AD046 (a peer's kind comes from the source store, not the
mapping) closes an ambiguity PD-004's own table exposed; and PD-009's `top_level` placement is
corrected in that entry.

A ratified three-lens critique round then added **AD054–AD064**. Two land inside entries here and are
folded in rather than duplicated: **AD054** corrects PD-005, whose claim that the existing replace-set is
"verified" and that its extraction is "behavior-preserving" turned out to be false in both halves — the
existing code adds without removing, and the reconciliation must re-read the destination peer set before
comparing or it is a guaranteed no-op; and **AD055** adds a consequence to the "where deletes go today"
non-question, since the flag set that hides deletes is the *fallback*, which makes a delete-bearing plan
the ordinary case. The remaining nine are contract- and requirement-level and are carried in `spec.md`,
`plan.md`, the contracts and `tasks.md`.

A second round added **AD065–AD074**. Two more land inside PD-005 and are folded in there: **AD065**, that
the re-read prescribed as "fetch first" performs no read at all, so the forcing mechanism is named; and
**AD070**, which withdraws the correction to `update_node` and confines the enforcement to new code on the
planned-write path, overruling this entry's own rejection of that option on scope rather than on
engineering. The other eight are carried in `spec.md`, `plan.md`, the contracts, `tasks.md` and
`quickstart.md`.

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

**Second consequence, operator-visible, recorded rather than left to be discovered**: because
`settings` is inside the hash input, **rotating a credential invalidates every saved plan for that
configuration**. The next apply of any of them is refused on the configuration-version check (FR-009)
and the operator must re-plan. This is accepted rather than mitigated — excluding `settings` would
mean a changed destination address did **not** invalidate a plan, which is the worse failure — but it
is stated in the spec's Assumptions and in AD041 so it reads as a designed consequence rather than a
bug report waiting to happen.

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
mapping becomes a destination query. On the qualified configuration this is not cosmetic: **ten
schema-mapping entries**, across nine distinct destination kinds, carry a relationship *inside* their
identity, so an identity value is sometimes a peer's DiffSync unique-id rather than a scalar. Verified
by parsing `examples/netbox_to_infrahub/config.yml` and cross-referencing each entry's `identifiers`
against its `fields[].reference` — one row per **mapping entry**, not per kind:

| # | Entry | `identifiers` | identity components that are references |
|---|---|---|---|
| 1 | `LocationRack` (`:119`) | `name`, `site` | `site` → `LocationSite` |
| 2 | `DcimDeviceType` (`:168`) | `name`, `manufacturer` | `manufacturer` → `OrganizationManufacturer` |
| 3 | `DcimDevice` (`:212`) | `location`, `name` | `location` → **`LocationRack`** (`mapping: rack`) |
| 4 | `DcimDevice` (`:254`) | `location`, `name` | `location` → **`LocationSite`** (`mapping: site`) |
| 5–7 | `InterfacePhysical` / `InterfaceVirtual` / `InterfaceLag` | `device`, `name` | `device` → `DcimDevice` |
| 8 | `IpamVLAN` | `name`, `vlan_id`, `vlan_group` | `vlan_group` → `IpamVLANGroup` |
| 9 | `IpamPrefix` | `prefix`, `vrf` | `vrf` → `IpamVRF` |
| 10 | `IpamIPAddress` | `address`, `vrf` | `vrf` → `IpamVRF` |

Rows 3 and 4 are the same destination kind declared **twice**, with complementary filters (racked
devices versus everything else) and a **different** `location` reference. That has a consequence
beyond the query construction and is recorded as AD046 below.

The naive approach — split the unique-id on `__` — is precisely the v1 flaw the brief names
("identifiers are recovered by splitting the diffsync unique-id on `__`").

**Prerequisite settled separately as AD043**: a peer identity component that is itself a reference is
recorded in the plan as a nested `{peer_kind, identity}` pair rather than a raw unique-id string,
recursively. Without that, the `<rel>__<attr>__value` arm of the decision below is not constructible:
on a memo miss the resolver holds only the peer's identity mapping, and if the component under `<rel>`
were a unique-id string the only way to reach `<attr>` would be to split it — the very flaw this
decision exists to avoid.

**Peer-kind ambiguity settled separately as AD046, made constructible by AD050**: because rows 3 and 4
declare the same kind with different references, a peer's `peer_kind` is **never** taken from the
referring field's `SchemaMappingField.reference` — deriving it from the mapping picks one of two answers
arbitrarily for `DcimDevice.location`, and a wrong pick fails the whole apply run on the brief's own
qualified path. AD046's stated mechanism, "the loaded source store entry knows its own kind", turns out
to be circular: `BaseStore.get(*, model, identifier)` and `LocalStore.get` both require the model before
the identifier is used, selecting the per-model bucket first
(`.venv/…/diffsync/store/__init__.py:40-52`, `.venv/…/diffsync/store/local.py:30-49`), and the only
kind-free call on the store is `get_all_model_names()` (`.venv/…/diffsync/store/local.py:22-28`), which
enumerates every loaded kind rather than answering for one unique-id. There is no way to ask "what kind
is this unique-id" without already having a kind to ask about.

AD050 restates the rule as a **bounded probe** over a candidate set drawn from the mapping — the kinds
declared as `reference` for that field across every mapping entry whose `name` is the owning destination
kind. Each candidate is probed with `store.get(model=candidate, identifier=peer_unique_id)`;
`ObjectNotFound` means "not this kind". Exactly one hit gives the answer; zero hits and more than one
hit both fail the plan run under FR-030, naming the owning kind, the field, the unique-id and the
candidates tried. There is deliberately **no fallback** to the mapping-declared kind, not even when the
candidate set has one member: an unprobed single candidate *is* the mapping-derived answer AD046
forbids, and taking it would make a wrong pick silent on the path where it fails the whole apply run.
Full rule and worked case in [data-model.md](./data-model.md#resolving-a-nested-peer-kind-ad050).

**Deletes use the same rule against the other store (AD049)**: a derived delete's identity is
canonicalised identically, with the probe run against the **destination** store. A delete exists because
its object is at the destination and not at the source, so its peers are destination-only and the source
store has nothing to resolve — which is why AD046's source-side phrasing does not reach the delete case
and why deletes are nonetheless not carved out of the recursive rule.

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
`compare_lists` remove/add shape, in a **new** `_replace_relationship_set(node, rel_name, peer_ids)` on the
planned-write path. **Narrowed by AD070**: this was first specified as an *extraction* from `update_node`,
shared by both callers. It is not. `update_node` keeps its present code and its present behavior; the shape
is written a second time, roughly eight lines, and that duplication is the price of leaving the live write
path untouched.

**Corrected by AD054 in two places.** This entry originally added that "the extraction is
behavior-preserving for the existing caller" and that it "reuses the only verified implementation in the
tree". Neither holds:

1. **The existing code is not a replace-set.** It reads `attr_manager.peer_ids` at
   `infrahub_sync/adapters/infrahub.py:151` and only calls `fetch()` at `:168-169`, so it compares the
   desired peer set against an unloaded one and **adds without removing**. Corrected code fact V12.
2. **A locally built node reports the desired set as its existing set.** A relationship manager sets
   `self.initialized = data is not None` (`.venv/…/infrahub_sdk/node/relationship.py:264`) and `fetch()`
   returns immediately once initialized (`:286-299`), so on the node this reconciliation runs against —
   built from the write payload — `peer_ids` **is** `new_peer_ids`. `compare_lists` returns two empty
   difference sets and the reconciliation removes nothing. It is a guaranteed no-op that can pass only
   against a mock.

So the helper must genuinely read the destination's peer set before comparing. **Ordering is not the
mechanism (AD065).** "Fetch first, then read `peer_ids`" was the fix this entry first prescribed, and it
performs no read at all: `fetch()` opens with `if not self.initialized:`
(`.venv/…/infrahub_sdk/node/relationship.py:286-288`) and the manager it is called on already reports
itself initialized, so the guarded `client.get` inside it (`:290-296`) never runs. The helper must therefore
**force the manager cold** — set `initialized` false, call `fetch()`, then read `peer_ids`. The distinction
that matters is that a destination read is *issued*,
which is also what the test must observe, because "the manager was fetched before `peer_ids` was read" is
satisfied by the no-op.

**And the reconciliation must be flushed, or it is discarded (AD075).** `RelationshipManagerSync` has **no
`save`**: `add()` (`.venv/…/infrahub_sdk/node/relationship.py:322-332`) and `remove()` (`:339-357`) only
mutate `self.peers` and set `_has_update = True`, issuing no client call, so the reconciled set reaches the
destination **only on a subsequent write of the node**. The helper therefore leaves the node unwritten — the
same shape as `update_node`, whose flush lives in its caller (`infrahub_sync/adapters/infrahub.py:177`,
`:625-626`) — and `apply_planned_operation` issues one write once, after the loop
over every cardinality-many relationship. An update, not `save(allow_upsert=True)`, which would re-render
the upsert create (`.venv/…/infrahub_sdk/node/node.py:1533-1534` → `:1838-1846`); the mutation is
`f"{kind}Update"` and the manager renders the full peer list (`relationship.py:68-69`), which is
what makes the write a replace. The observable moves onto the **issued destination write carrying the
reconciled peer list**; manager state is satisfied by a helper that never writes.

**And the flush is `do_full_update=True`, not a plain `node.save()` (AD085, amending AD075).** AD075 pinned
the plain form on the strength of `_strip_unmodified` keeping a relationship whose update flag is set. That
arm is real — the first loop (`:354-364`) does not pop an emptied manager, and the `not relationship_property`
guard at `:356` never fires for one, since a relationship manager defines neither `__bool__` nor `__len__`
and is always truthy. But it is not the only arm. A plain save renders with `exclude_unmodified=True`, and
the **second** loop (`:365-370`) pops any key whose rendered value equals the create payload's: for a
relationship reconciled to the **empty** set that comparison is `[] == []`, because
`generate_payload_create` writes a cardinality-many relationship as `[]`
(`.venv/…/infrahub_sdk/schema/__init__.py:179`), and the pop fires because a relationship manager is not an
`Attribute` (`:368-370`). So `peers: []` — which the plan artifact format defines as "empty the set" — never
reaches the destination under a plain save. (The differing-payload path is not where it is lost:
`_strip_unmodified_dict` is dispatched only under `isinstance(original_data[item], dict)` (`:372`) and the
create payload writes a **list**, so that branch is not reached and the key survives.)
`node.update(do_full_update=True)` renders with `exclude_unmodified=False` (`:1870`), so `_strip_unmodified`
never runs at all (`:290-291`), the emptied set survives as `[]`, and `id` is still rendered (`:295-296`) so
the update targets the right node. Non-empty replaces are unaffected, and AD075's conclusions and its
shipped implementation stand — only the flush call changes.

**And the flush is not a whole-node update either — it is a targeted relationship write (AD088, amending
AD085's form and correcting its attribution).** Everything above about the stripping is correct and stands;
what is wrong is the remedy, because both candidate calls render the **whole node**, and that render emits
`data[<rel>] = None` for every **optional cardinality-one** relationship left uninitialized once `_existing`
is `True` (`.venv/…/infrahub_sdk/node/node.py:260-266`, whose own comment says it is there "to allow
clearing relationships"). `save(allow_upsert=True)` sets `_existing = True` through
`_process_mutation_result` (`:1811`), so the flush nulls every optional cardinality-one relationship the plan
never mapped — which FR-013 forbids in the same requirement that asked for the full form.

**That null is independent of the stripping, so it predates AD085 and was latent in AD075's own design.**
Both paths verified: with `exclude_unmodified=False` nothing is stripped at all (`:290-291`); with
`exclude_unmodified=True` the first loop (`:354-364`) does not pop the key, because its `if` requires a
non-optional `RelatedNodeBase` and its `elif` requires a `RelationshipManagerBase`, and an uninitialized
optional cardinality-one relationship is neither, while the second loop (`:365-370`) never visits the key
because an unmapped field is absent from the `original_data` it walks. AD075 specified the flush as a write
*of the node*; the null follows from that alone, and AD085 changed only which stripping setting that render
ran under.

Pre-initialising or restoring the unmapped relationships before the flush is rejected: it treats the symptom,
and it would require reading every unmapped relationship of the destination object first. The flush is
instead built by hand in `_flush_replaced_relationship_sets`, carrying `id` plus only the replaced fields:
`Mutation(mutation=f"{kind}Update", input_data={"data": {…}}, query=node._generate_mutation_query())`
rendered and issued through `client.execute_graphql`, then handed to `_process_mutation_result` — the same
construction `update()` performs (`:1867-1888`), minus the whole-node walk. Each peer list is still rendered
by `RelationshipManagerBase._generate_input_data` (`relationship.py:68-69`), so the replaced fields render
byte-identically to what the full update produced for them, `[]` for an emptied set included.

A **relationship-level** mutation was evaluated first and rejected. The SDK exposes no general relationship
method on either client; `RelationshipAdd` appears exactly once, hand-interpolated into a GraphQL string in
`.venv/…/infrahub_sdk/groups.py:5-25`, on the async client only, for one hard-coded relationship name. There
is no `RelationshipRemove` anywhere in the package, so a replace-set would need two mutations per
relationship — breaking AD075's single write after the loop — and emptying a set could not be expressed at
all through the add half. Generalising that helper to an arbitrary kind and relationship would mean writing
a new untyped GraphQL string builder with peer ids interpolated by hand, against a mutation the SDK does not
model.

**And that flush is what withdraws the second re-read mechanism (AD075).** This entry originally offered a
scoped `client.get(id=node.id, kind=…, include=[rel_name])` with the manager read off the node that comes
back as equivalent to forcing the manager cold. The two stopped being equivalent once the flush became a
separate step in the caller: the `add`/`remove` calls would land on the fetched node's manager, while the
node the caller flushes still holds the manager built from the create payload, so the update goes out
carrying that payload's peer list — the desired set, never compared against the destination's — and the
reconciliation is discarded. **The node that is updated must be the node whose manager was reconciled.** Forcing the manager cold satisfies that by
construction, because `fetch()` assigns the peers it reads back onto the manager it was called on
(`.venv/…/infrahub_sdk/node/relationship.py:290-299`) — it *is* the scoped read plus the write-back — and it
is also the only form compatible with one flush per operation rather than one per relationship.

**And the correction stops at this helper (AD070).** `update_node`'s additive ordering is a genuine
pre-existing defect, and it stays. Its only caller is `InfrahubModel.update`
(`infrahub_sync/adapters/infrahub.py:625`) — the live `sync` write path — so correcting it would make
`infrahub-sync sync` start **removing** destination relationship peers absent from the source, on
configurations that have never removed one. That is a data-removing change to an existing command, which
this outcome does not authorize, no requirement states, no criterion measures and no documentation entry
discloses. It is recorded here as a **pre-existing defect for a later outcome to own**.

**Rationale**: it makes FR-013's replace-set clause true by construction instead of by assumption
about server behavior nobody here can test, and it costs one extra round trip per cardinality-many
relationship on operations that carry one. If the upsert already replaces, the reconciliation is a no-op —
and, with the re-read, a no-op **for the right reason**: the difference sets are empty because the
destination already holds the desired set, not because the comparison never looked.

**Alternatives considered**: trust the upsert to replace (rejected: unverifiable, and a silent merge
would leave stale peers attached, which SC-008 would catch only against a live server — i.e. late);
always use `update_node` (rejected: it requires `client.get(id=local_id)`, which FR-012 forbids, V11);
share one helper between both callers and correct its ordering for both (**ratified first, then overruled by
AD070**: the objection to leaving the two paths different — that a helper which is a replace-set on one path
and additive on the other is the shape defects hide in — is sound engineering and is *not* authority to
change what an existing command does to destination data; the option that keeps the live path byte-for-byte
identical wins on scope, and the duplication it costs is eight lines).

**Materiality**: **medium — reported upward.** It adds a step AD015 does not describe to the
mandated write path. Under AD054 it was also going to correct existing behavior on the live update path;
AD070 withdrew that, so no existing path changes and the materiality drops accordingly.

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

**Materiality**: low. It began as a reading of FR-009 that FR-009's own text did not admit — the
requirement said all checks are evaluated and every failure named, without qualification, while this
decision and a task both assumed a short-circuit. FR-009 has since been amended to state the gate
explicitly (AD053), so the requirement and this decision now say the same thing and a reviewer can
disagree with either in one place.

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
`sync(diff=...)` entry point (`infrahub_sync/potenda/__init__.py:292-295`).

**Where the `top_level` narrowing goes — corrected.** It governs **diff computation**, not execution.
In the tree, `self.destination.top_level = tier_list` is assigned at `:483`, immediately *before*
`self.diff()` at `:484`; and `top_level` is read only by the comparison engine's differ
(`.venv/…/diffsync/helpers.py:79-88`, inside `DiffSyncDiffer.calc_diff`), never by the synchronizer,
which walks the children of whatever `Diff` it is handed. So the **compute loop** must set the
narrowing around each `self.diff()` call, exactly as the interleaved loop does today, and the
**execution loop** replays the retained per-tier diffs with the narrowing restored to `saved_top` — it
is irrelevant there. An earlier reading of this decision said the narrowing "stays in the execution
loop"; that would have computed every tier's diff against the *whole* destination, producing six
identical full diffs instead of six disjoint per-tier ones, and the artifact would have recorded each
operation once per tier. The regression test therefore asserts on per-tier diff **contents**, not only
on call order.

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

**Amended by AD086.** The replacement guard is not a `hasattr` test either: the surface is a
`runtime_checkable` Protocol with two members — `apply_planned_operation` and the peer-resolver factory
`new_peer_resolver` — and the gate is `isinstance` against it. That is a **presence** check, not a
signature check, so what it refuses is exactly what the `hasattr` form refused and FR-023's runtime
refusal is unchanged in strength. What the Protocol fixes is the **static** boundary: the `getattr`
dispatch and the `cast` to the concrete adapter are gone, and `ty` checks both call sites. Runtime
enforcement of conformance would need an explicit opt-in from the destination and is a separate
decision.
`plan.parquet` keeps being written (V23, AD014) and is simply never read.

**Rationale**: FR-019's plain text forbids a second apply path, and a wired v1 dispatch is that path.
Removal is safe in a way it rarely is: `apply_cached_row` has zero implementations anywhere in the
repository (V3), so nothing can be calling it successfully today. The only fallout is
`tests/cache/test_apply_plan.py`, whose `MagicMock` asserts the dispatch shape. That rewrite lands in
**the same phase as the removal, immediately after it** — an earlier ordering put it two phases later,
which would have left the removal task's done-condition ("`uv run pytest -q` passes") unsatisfiable
while a test still asserted the removed behavior.

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
  derivation is therefore the only way to see them without loosening the flag, exactly as it says. **One
  consequence, surfaced by the critique round and resolved by AD055**: because that flag set is the
  *fallback*, every destination holding mapped objects absent from the source now yields deletes, so a
  delete-bearing plan is the ordinary case rather than an exception. Under AD055 an apply over such a plan
  completes `applied` with a recorded skipped-delete count and a warning naming it, rather than failing.
- **Whether the full payload is available at plan time.** Yes, but **not from `source_attrs` alone** —
  this was a near-miss and is now AD042. `DiffElement.source_attrs` is the complete source *attribute*
  set rather than the delta (V4), and today's writer does not use it, taking `get_attrs_diffs()`
  instead (`infrahub_sync/potenda/__init__.py:314-316`). But `source_attrs` is built from
  `src_obj.get_attrs()` (`.venv/…/diffsync/helpers.py:223`), whose contract states it "does not
  include the fields in `_identifiers`" (`.venv/…/diffsync/__init__.py:340-347`), and the generator
  strips identifiers out of `_attributes` (`infrahub_sync/generator/__init__.py:95`). The payload is
  therefore `element.keys ∪ element.source_attrs`. Taking `source_attrs` alone would have produced
  payloads with no identity fields, an unkeyed upsert, and duplication on every re-apply.
- **Whether the destination identity is available at plan time.** Yes, and separately from the
  attributes. `DiffElement.keys` is the identifiers mapping (V4), carried as its own constructor
  argument (`.venv/…/diffsync/helpers.py:212-219`); today's writer hardcodes `dest_id: ""` (`:322`).
  It is the *only* source of identity on the element, which is why AD042 unions it into the payload.
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
