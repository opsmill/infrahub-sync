# 13. Writes are keyed by a recorded id and a complete human-friendly ID

**Status**: Accepted
**Date**: 2026-09-14
**Source**: ADP-6-S2 (v3-mvp), closing AD067 and retiring AD066's gate

## Context

The planned-write path refused any operation whose SDK-rendered mutation carried no usable `id` or
`hfid`. The gate read `InfrahubNodeBase._generate_input_data` — a private pre-save render — and
compared it to nothing else. Three measurements on Infrahub 1.10.6 settled that this is not a safe
thing to read, and settled what to read instead.

**The private render stopped agreeing with the wire.** On infrahub-sdk 1.18.1 a `TestingSite` upsert
renders `hfid` in the private render and sends it. On 1.23.2 the private render still reports
`hfid: ["adp6-s01"]` while the mutation actually issued carries **no key at all**. A gate reading
that render classifies the write as keyed; the mutation the destination receives is not. The set of
kinds whose issued mutation carries no key is *wider* on the newer SDK, not narrower.

**The server converges without a key.** An upsert carrying only the human-friendly-ID components in
`data` — no `id`, no `hfid` — converged onto the existing object on both SDK versions, including for
`TestingInterface`, whose human-friendly ID crosses a relationship. AD067 assumed such kinds could not
converge and refused them. They converge. The premise was wrong.

**What is unsafe is an incomplete payload, which the gate never checked.** A payload omitting one
optional human-friendly-ID component does **not** match the existing object: the server answers
`ok: true` and creates a second one, on the raw path and through the SDK alike. Nothing downstream can
detect it. A kind with no human-friendly ID cannot converge at all — with a covered uniqueness
constraint the server refuses the duplicate (transport 200, `extensions.http_status` 422); with
neither it duplicates silently on every write.

**An id keys anything.** An upsert carrying a recorded destination `id` updates in place on both SDK
versions, including on a kind with neither a human-friendly ID nor a constraint. An id matching no
object returns `NODE_NOT_FOUND` / 404 and creates nothing.

Two facts about the product forced the rest. A plan is applied without re-reading the destination
(FR-012), so an id has to be recorded at plan time or not exist at all. And `identifiers` — what
DiffSync matches on — is a different thing from the destination's human-friendly ID: the shipped
NetBox example maps `IpamPrefix` on `[prefix, vrf]` while the destination kind's human-friendly ID
requires `ip_namespace`. A rule that simply demanded identifiers ⊇ HFID components would reject
shipped configurations that update objects correctly today.

## Decision

**An update is keyed by the destination `id` recorded for it at plan time.** Derivation reads it from
the destination store's `local_id` and records it on the operation; apply sets it on the node before
`save(allow_upsert=True)`, which renders it as the scalar top-level `id` the server keys on. It is
never put into the `data` mapping, where it would render as the attribute-shaped `id: {}` and key
nothing. An id the destination cannot find is `StaleDestinationIdError`, classified only from
`extensions.code` `NODE_NOT_FOUND` **and** `extensions.http_status` 404 in the same error. Both halves
are required: that refusal claims the write never happened, and a claim that suppresses reconciliation
needs the server's own status behind it, not just its code.

**A create is keyed by proving its payload carries the whole human-friendly ID.** Planning refuses
otherwise, per operation: every component's field must be named by the operation's identity, and each
must carry a **usable** value, proven through the peer's identity where the component crosses a
relationship. Usable means present and not an empty or whitespace-only string — the destination
matches on the value it is sent, and `"   "` matches nothing anyone meant. Falsiness is deliberately
not the test: `0` and `False` are values the destination matches on, and reading them as missing would
refuse a correctly keyed create.

The two arms are different mechanisms and stay so. Coverage is a question about the plan and is
answered from the operation's identity; value presence at the write surface is AD051's
`_assert_identity_components_accounted_for`, asked of the assembled write, because it is the only
check that can say *which* component is missing. Plan derivation, which has no assembled write, asks
both from the identity. A kind with no human-friendly ID is allowed only where a declared uniqueness constraint
is covered — the destination refuses the duplicate there — and refused otherwise. Several creates
projecting onto one destination human-friendly ID are refused rather than silently converged.

**The private-render gate is gone**, with `_require_keyed_render`, `UnkeyedWriteRefusedError` and the
tests that pinned them. No product code reads a private SDK render.

The two keying mechanisms are not symmetric, and that asymmetry is the point: an update can be keyed
by something the plan recorded, a create cannot. Every condition that refuses a create therefore stays
a warning for an update, which is what makes kinds with no human-friendly ID usable at all.

## Consequences

`PlannedOperation` gains `destination_id`, and because the record is `extra="forbid"` that is a hard
format change: `PLAN_FORMAT_VERSION` is 3. Reading and review accept format 2 and 3, so an older plan
still opens; `apply` accepts 3 alone, because a format-2 update carries no id to key on. The field is
inside the checksummed bytes, so a re-pointed update fails the plan checksum. See
[ADR 0001](0001-saved-plan-artifact-format.md).

The warm-incremental path had to carry the id across runs: the side-B snapshot now persists `local_id`
as its own column, never conflated with the engine-controlled `_source_id`. A snapshot written before
this change lacks the column and is treated as a cache miss for that resource.

AD067 closes: the server converges relationship-crossing human-friendly IDs, so such kinds are
supported for creates when their components are proven present, and for updates by id. AD066's gate is
retired; AD051's per-component check remains as the value arm.

Explicit `hfid` wire rendering is **not** reintroduced: `save(allow_upsert=True)` strips it on 1.23.2
and the server matches on complete components regardless, so rendering it would be a claim the wire
does not carry.

Two shipped NetBox IPAM mappings (`examples/netbox_to_infrahub/config.yml`) map identifiers that do not
cover their destination kind's human-friendly ID. Their **updates** are unaffected — those are keyed by
id — but a fresh **create** under them is now refused rather than silently duplicated. They need a
mapping or schema fix, which is a separate decision and is not made here.

A refusal raised before its own write, and the stale-id refusal the server proves wrote nothing,
are recorded as not-written rather than as possibly-partial. The claim is scoped to the **failing
operation**: operations applied earlier in the same plan stay written and are listed in the
record's `applied_operations`, and the apply stops there, so the destination is not as the plan
describes it. What changes is that there is no *uncertainty* about the failing operation, so the
run settles `failed` rather than interrupted/ambiguous and sets no `reconciliation_required` —
an operator is no longer sent to reconcile an operation that did nothing.

## Evidence

The keying spike and its G1 extension, measured on Infrahub 1.10.6 against infrahub-sdk 1.18.1 and
1.23.2, controller-adjudicated and independently verified. The decision packet records the panel and
Blake's ratification of `HFID_PLUS_RECORDED_ID` with its eight conditions.
