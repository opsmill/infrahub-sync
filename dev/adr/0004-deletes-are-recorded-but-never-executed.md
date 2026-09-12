# 4. Deletes are recorded in the plan but never executed

**Status**: Accepted
**Date**: 2026-07-28
**Source**: `dev/specs/archive/001-plan-artifact-saved-apply/research.md` (confirmed non-questions),
`dev/specs/archive/001-plan-artifact-saved-apply/contracts/destination-write-surface.md`
(AD004, AD049, AD055)

## Context

A destination object that is mapped by the configuration but absent from the source is a deletion the
operator ought to be able to see. Today they cannot: when a project configures no flags, the engine
falls back to `DiffSyncFlags.SKIP_UNMATCHED_DST`, and DiffSync drops destination-only objects under
that flag before a diff element is ever created. Deletes are therefore invisible in the comparison
result the write path consumes.

Because that flag set is the **fallback** rather than an unusual configuration, any destination holding
mapped objects absent from the source yields deletes. A delete-bearing plan is the ordinary case, not
an exception — which decides what an apply must do when it meets one.

Executing deletes is not supported.

## Decision

Deletes are **derived** by set difference — destination identities minus source identities — and
recorded in the plan as first-class operations, then **never executed**.

A recorded delete carries no payload and no relationships, but its `identity` obeys exactly the same
rules as any other operation's, including the recursive `{peer_kind, identity}` shape for a
reference-valued component. Deletes are not exempt from that: exempting them would leave one place in
the format where a consumer must split a `unique_id`, and would make a delete's `operation_id` derive
from an identity no reviewer is ever shown.

An apply over a delete-bearing plan therefore completes in the **`applied`** state — the same state a
delete-free apply reaches. No new run state is introduced. What the run records instead:

| Obligation | Rule |
|---|---|
| Count | `summary["skipped_delete_count"]`, non-zero on any delete-bearing plan |
| Identifiers | `summary["skipped_delete_operations"]`, in stored order |
| Warning | One warning naming the count, at **warning level**, on the run's log stream |
| Completion line | When the count is non-zero, the command's own final line names it |
| Knowability | `summary["applied_operations"] ∪ summary["skipped_delete_operations"]` equals the plan's full identifier set, and the two lengths sum to `operations_count` |

The warning's level is pinned rather than described as "operator-visible", because `--quiet` floors the
package logger at warning level: an info-level emission would satisfy every prose description of the
obligation and vanish for exactly the scripted and CI invocations where the warning and the run record
are the only signals. The test asserts the level, not only the text.

The knowability clauses are checked **after** the loop and **not** on the rejection path. A partial
apply breaks both by construction, so an unconditioned check would replace a clear
destination-rejection message with an invariant error.

A destination adapter must still raise `SkippedDeleteOperation` if handed a delete. That obligation is
defensive, not the mechanism: the engine recognizes a delete in its own loop, records the identifier and
continues without dispatching it, so the write surface is never called with one on the engine's path.
The adapter-level raise exists so a caller that is not the engine gets a refusal rather than a deletion.

## Consequences

A run that declines to execute a delete is behaving exactly as designed, and a run behaving as designed
is not reported as broken. Every non-delete operation in the same plan is still applied; the engine
never stops on a delete.

The applied set stays provably knowable against the reviewed set, as a **recorded value** rather than an
inference, because both the applied and the skipped identifiers are recorded. That is the whole point of
the arrangement, and it is what a silent skip is not.

One class does still fail the run: an operation whose action is outside the recognized vocabulary. It is
refused while the artifact is being read, before any destination write, naming the operation identifier,
the action found, the recognized actions and the operator's next action. Nothing about such an operation
is designed, so what it would do to the destination is unknown and the run cannot claim to have applied
what was reviewed.

A dropped **peer** is treated the opposite way, and the distinction is deliberate: nothing declares an
unresolvable peer out of scope, and its effect on the destination is a half-written object, so it fails
the run where a recorded delete does not.

An apply that skipped deletes records `applied`, which the incremental path's success set already
contains, so it counts as a successful prior run for a later warm start. That is correct, and is
recorded here rather than left to be discovered.

## Alternatives Considered

- **Loosen the flags so DiffSync surfaces destination-only objects.** Rejected: it changes what the
  live `sync` path does to destination data, which this work does not authorize.
- **Fail the run when a plan contains a delete.** Rejected: a delete-bearing plan is the ordinary case
  under the fallback flags, so this would fail most applies for behaving as designed.
- **Skip deletes silently.** Rejected: it makes the applied set differ from the reviewed set with no
  record, which is the failure the recorded-identifier requirement exists to prevent.
- **Introduce a new run state for "applied with skipped deletes".** Rejected: a designed limitation is
  not a distinct outcome, and a new state would have to be handled by every consumer of run state.
- **Exempt deletes from the recursive identity rule.** Rejected: it would reintroduce `unique_id`
  splitting for one operation class and derive an identifier from an unreviewed identity.
