# Planner-feedback additions

**Nine items** for root to fold into the planner report for this batch. Nothing here edits the brief.

Items 1–2 arose from the brief owner's resolution of the two escalated findings (AD086, AD087). Items
3–9 are the rows [plan.md](./plan.md#risks) marks **"Material — reported to root"** (six of them) plus
AD081's planner-feedback-only item — the disclosures plan.md asserts reach a planner, which until now
reached one nowhere. None of the nine is a defect in the delivery: each is a brief gap, an inherited
constraint, or a governance boundary.

The count is stated because it was wrong before: this file opened "Two items" while carrying two, against
six risk rows claiming to have been reported here and an AD081 item that was absent.

## 1. Runtime enforcement of the write surface — `brief-gap`

Making FR-023's refusal real at runtime needs an **explicit opt-in** from the destination — inheritance
from an abstract base class, or a class-level marker the engine can test. AD086 replaced the by-name
reach for the write surface with a `runtime_checkable` Protocol, which fixes the **static** boundary
but is **presence-checking only**: `isinstance` against such a Protocol verifies that the members
exist, never that their signatures match, so against a duck-typed destination it is exactly equivalent
to the `hasattr` gate it replaced. A destination whose members carry the right names and the wrong
shapes still passes the pre-write gate and fails mid-apply.

The gap is the brief's: it assigned a write surface to **one adapter of nine** without saying how a
non-conforming destination is to be **detected**, so the refusal it mandates can only be a presence
check. Choosing the opt-in mechanism is a design decision with consequences for the other eight
adapters — an ABC changes the adapter contract; a marker does not, but is weaker — and a delivery brief
scoped to the plan artifact is not where it should be taken. A later brief should either state the
mechanism or state explicitly that presence-checking is what FR-023 means.

## 2. Docs scope boundary — `brief-gap`

The brief should state that documentation edits are limited to **current** documentation and never
touch **shipped release notes**.

Nothing in the brief bounded which documentation was in play. A worker correcting a false claim — that
the apply path refuses on schema-sub-hash drift, which it does not — fixed it in the current
cache-layout reference, which was right, and in good faith also deleted the same sentence from the
**shipped 2.0.0 release note**, which was not: a release note records what that release claimed at the
time it claimed it. AD087 reverted that edit and kept every current-documentation fix. The remedy for a
false claim in a shipped note is an erratum or a fix to the code the note described, and choosing
between those is out of scope for a plan-artifact brief either way — which is exactly why the boundary
needs to be stated rather than inferred.

## 3. `LocationRack` is not convergent on the qualified path — `inherent`

Found by the live run (AD091). The qualified configuration's identity for `LocationRack` is
`identifiers: ["name", "site"]`, while the destination schema keys the kind on the **rack name alone**
(`human_friendly_id: ['name__value']`, `uniqueness_constraints: [['name__value']]`). Thirteen NetBox demo
racks are named `Comms closet`, one per site, so thirteen distinct plan identities collapse onto one
destination object whose `site` is whichever operation wrote last. Every re-derived plan then reports a
create **and** a delete for the same rack, forever, and the churn cascades through every identity that
nests a rack — `DcimDevice.location`, then `InterfaceLag` / `InterfacePhysical`.

Why a planner needs it: FR-024's convergence-key warning **cannot detect this class**. It tests
`constraint <= identity` — "would the destination refuse a duplicate keyed on what the plan supplies" —
and here the constraint *is* a subset of the identity, so both arms stay silent. The hazard runs the other
way: the destination's key is strictly **coarser** than the plan identity, so distinct identities collapse
rather than duplicate. A third arm detecting `identity ⊄ any constraint` would catch it. That is new scope
and was deliberately not added here.

It also bounds this delivery's evidence, which is the part a planner must carry forward: SC-002 and SC-003
pass on a **bounded** live slice from which this kind was filtered out (`ADDED_FILTERS`), so
"convergence verified" must not be read as covering the qualified path. A later brief should own the
third detector arm, or state that coarser-than-identity destination keys are a configuration problem
rather than a tool problem.

## 4. The destination extract cannot rebuild a relationship-bearing peer identity — `inherent`

Found by the live run (AD091), and **pre-existing** — both the predicate and its gate are byte-identical
to `main`. `resolve_peer_node` re-fetches a peer only when `_node_has_complete_attributes` is false, and
that predicate walks **attributes** only, so a peer that is attribute-complete but carries no relationship
data is never re-fetched; `infrahub_node_to_diffsync` then skips its relationship for want of `rel.id`,
and `_resolve_peer_unique_id` raises `PeerIdentifierError` naming the missing identifier.

Consequence: `infrahub-sync diff` against the qualified path **fails once `InterfacePhysical` exists at
the destination**. It is not on the apply path — a saved-plan apply re-extracts nothing (FR-012) — so
nothing this outcome delivers is affected, and it was recorded rather than repaired because the defect
lives on the destination-extract path shared with the live `sync` command, which AD070 put off limits for
this delivery.

Why a planner needs it: it is a **reproduction precondition** for this batch's live evidence, not only a
future defect. The seven passing Phase H results were obtained against a destination holding no
`InterfacePhysical`, so reproducing them requires clearing `InterfacePhysical`, `InterfaceVirtual` and
`InterfaceLag` from the destination first. A later brief should own the predicate.

## 5. Five brief acceptance criteria had no passing evidence at merge — `governance`

AD007/AD045b recorded that DBA-001, DBA-002, DBA-003 and DBA-008 in full, plus the live half of DBA-007
and of the derived SC-016, had no passing evidence at merge, so the brief's completion condition — every
acceptance criterion has inspectable passing evidence — was **not met**.

**Five of the six were subsequently closed** by a live run (AD091) and are no longer owed. The sixth,
SC-016's live half, is not merely deferred: it **cannot be satisfied on this destination schema**, because
seeding a genuine peer ambiguity needs a referenced kind whose uniqueness constraints do not cover the
components the resolver filters on, and every one of the 20 kinds this configuration touches declares one
that does. Its test is written, committed, and left **erroring in setup** rather than skipped, weakened or
mocked, so the condition stays visible. The offline half passes.

Why a planner needs it: a completion condition of the form "inspectable passing evidence for every
criterion" is **unsatisfiable for a criterion whose evidence requires a destination schema the batch does
not control**. A later brief should either supply such a schema, scope the criterion to the offline half,
or state that a criterion may be closed by a recorded impossibility.

## 6. Nested HFID resolution depends on the SDK client store — `inherent`

For a destination kind whose `human_friendly_id` crosses a relationship, the SDK cannot form the
mutation's `hfid` from a peer supplied as a resolved id: `get_path_value` needs the peer out of the client
store, a bare-id relationship value carries no `__typename` so the store is never consulted, and one
`None` component nulls the whole HFID — the mutation then goes out with neither `id` nor `hfid`. The
saved-plan resolver is specified to return ids and never to touch that store, so it cannot close this.

Not mitigated away and not claimed solved: the per-component diagnostic raises *before* the create, the
keyedness gate warns once per affected destination kind at `logging.WARNING`, and the conformance harness
carries the assertion as a `xfail(strict=True)` so the day the hole closes the suite says so. The live run
found that Infrahub **does** key such an upsert server-side on its own uniqueness constraint, which
narrows the exposure to a relationship-crossing key with **no** covering constraint — a kind this
configuration does not have.

Why a planner needs it: the exposure is **five kinds and five mapping entries**, not the "ten entries
across nine kinds" first recorded — that figure counted plan identities containing a reference, a
configuration-side fact, for a question decided by the destination kind's `human_friendly_id` (AD091). A
later brief inheriting the write path inherits this narrowing, and should be told the corrected figure and
the artifact it was read from.

## 7. `--continue-on-error` does not exist on `diff` — `governance`

FR-030 puts new hard failures on `diff` — an unformable identity, an unresolvable source-side peer, an
unencodable payload value, a duplicate identifier — and `--continue-on-error` is a `sync`-only option, so
there is no tolerance switch on that path. An operator's `diff` therefore starts exiting non-zero on data
that used to render.

Deliberate, and the reading that permits it is stated in plan.md's Constitution Check so it is reviewable:
Principle I's "`diff` MUST stay safe to run at any time" is read as *performs no destination mutation*,
not as *never exits non-zero*. Warn-and-skip was rejected because a silently incomplete plan is exactly
the reviewed-set/applied-set divergence DBR-016 exists to prevent.

Why a planner needs it: it is a **user-visible behaviour change to an existing command**, taken on a
reading of a constitutional principle rather than on an explicit brief instruction. A later brief should
either ratify that reading or say that new hard failures on a read-only command need their own tolerance
flag.

## 8. A source snapshot's bytes vary every run — `inherent`

`_extract_ts` is per-run, so a snapshot's raw bytes differ on every extraction and a **byte-level**
binding digest would make DBA-006 unachievable. PD-008 therefore defines the snapshot digest over the
**logical rows**, excluding `_extract_ts`.

Why a planner needs it: DBA-006 as written implied a byte-level binding, and the criterion is met only
under the logical-row reading. It is carried **conditionally** — the pinned-extraction-mode precondition
is part of the criterion (AD064). A later brief that reuses the snapshot-binding language should state the
digest's domain rather than leaving "the snapshot is unchanged" to be read as byte identity.

## 9. The brief's convergent-write-path dependency row is only partly satisfied — `brief-gap`

AD081, recorded as planner-feedback-only. The brief marks "convergent write path — **Satisfied**" as a
dependency. That holds only for destination kinds whose convergence key is **all-direct**; where the key
crosses a relationship it is unverified by the offline evidence, and the live evidence establishes it for
one destination's uniqueness constraints rather than in general.

Why a planner needs it: **three later outcomes inherit the same partial satisfaction** from the same
dependency row, and inherit it as "Satisfied". A later brief should qualify the row — satisfied for
all-direct convergence keys, conditional for relationship-crossing ones — so the three consumers inherit
the qualification rather than the unqualified claim.
