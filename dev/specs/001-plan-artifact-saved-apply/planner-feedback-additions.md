# Planner-feedback additions

Two items for root to fold into the planner report for this batch. Both arose from the brief owner's
resolution of the two escalated findings (AD086, AD087) and neither is a defect in the delivery.

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
