# 10. A managed write holds one configuration guard and ends in a verdict, not a retry

**Status**: Accepted
**Date**: 2026-09-03
**Source**: PH-3 apply-guard unit 1B

## Context

Two workers can hold destination credentials for the same registered configuration at the
same time. Nothing in the engine stopped them writing that configuration concurrently, so
a reviewed plan could be applied against a destination another writer was still changing —
and the schema snapshot the plan was validated against could describe a destination that no
longer existed by the time the first operation was dispatched.

Failure made the same problem worse in the other direction. Applying one recorded operation
is not one write: the base upsert precedes the relationship flush, so an operation can change
the destination and belong to neither the applied nor the skipped set. A worker that fails,
loses its session, is cancelled, or disappears entirely therefore cannot say what reached the
destination. Recording that as an ordinary failure invites the obvious next step — apply
again — which is exactly the step nobody can justify without looking.

Serializing writers was only half the answer. Holding a lock proves nothing about whether
the process still holds it: a session can die, an advisory key can be released by a stray
unlock, and a caller that only checked at the start would keep writing regardless.

## Decision

A managed write holds one PostgreSQL advisory guard, keyed by the registered configuration
it writes, for the whole write — and the engine proves that hold around every operation it
dispatches.

The order is fixed. Deterministic refusals settle first, outside the guard, so a request
that was never going to succeed does not make another writer wait. The guard is acquired
next. Everything destination-sensitive then happens inside it: the live schema read, the
runtime models built from it, the comparison those models exist for, and every dispatch.
Ownership is proven immediately before each dispatch and once after the last one, before
anything may record the apply as complete. Release is confirmed before the local applied
sidecar becomes product success. Managed sync acquires the guard before destination
extraction and holds it across plan, verify, and apply, so the plan it applies cannot go
stale behind another writer.

The ownership boundary is a required argument with no default, no `None`, and no no-op
implementation. `Potenda.apply_plan`, `PlanApplier.apply_plan`, and
`execute_run(operation="apply")` each refuse without one. Tests pass an explicit fake; a
boundary that could be absent would be absent exactly when it mattered.

One run admits one write. The reservation is decided inside a single store transaction on
the run's own locked row, before either competitor can submit anything to Prefect: a write
needs the run quiet — no existing admission, no other unresolved submission, no unfinished
execution — and a read needs a run that has admitted no write. The loser keeps a durable `409
run-execution-conflict` on its own receipt, so replaying its client key replays the refusal.
Each execution names the unresolved receipt that appended it, uniquely, so the admitted
receipt spends its admission exactly once.

A write that may have started ends in a verdict. One in-memory Boolean, owned by the worker
that ran the stage, separates a failure that certainly wrote nothing from one that may have.
Before the first proven dispatch a failure is an ordinary failed run. After it, every
non-success outcome — an adapter failure, a lost session, a failed final proof, a failed
applied sidecar, cancellation, or losing the worker entirely — is `interrupted` /
`ambiguous`, and `product_runs.reconciliation_required` is set in the same transaction that
makes the execution terminal. That column is derived from the verdict and the execution's
purpose rather than passed in, so no caller can write it false, and nothing clears it.

The operator reconciles by planning again. An empty diff shows the desired state is already
present; anything else is reviewed and applied as a new run.

Two mechanisms were removed rather than kept beside the guard. The managed write path no
longer takes the local pipeline lock, because two correctness guards for one property means
neither is the answer. The composed `execute_run(operation="sync")` writer is refused
outright: it derives its own plan and applies it in one call, so it can offer no
per-operation proof over a reviewed artifact, and keeping it would have left an unguarded
write path in the package.

## Consequences

A configuration's writes are serialized across processes and hosts, and the guard is a
provider fact rather than a repository convention: it requires a direct, session-mode
PostgreSQL connection, and transaction-mode PgBouncer is unsupported.

Contention is a clean refusal — nothing read, nothing written, no ambiguity — but its remedy
is a new plan, because another writer may have changed the destination while this one waited.

An uncertain run is terminal. There is no replay, no recovery service, and no endpoint that
clears the reconciliation column; an operator inspects the destination and plans again.
That is deliberate under-automation: every automatic answer requires knowing what reached
the destination, which is exactly what an uncertain write does not know.

The direct Prefect remote-run deployment is now plan-only. The Sync HTTP API is the
supported write boundary.

Pre-release V3 development databases are recreated rather than migrated across the new
column and the receipt-owned execution link.

## Alternatives considered

**A write-attempt table with generations**, so a failed write could be retried under a new
attempt. It answers the wrong question: the record would say how many attempts happened, not
what reached the destination, and the retry it enables is the unsafe step.

**Proving ownership once, at acquisition.** A session can die mid-write, so the proof would
be stale exactly when it mattered — and the engine would keep dispatching against a key it
no longer held.

**An optional guard, defaulting to absent for existing callers.** Every unguarded write path
that survived would be the one a deployment used, and the absence would be invisible until a
concurrent write corrupted a destination.

**Keeping the local pipeline lock alongside the guard.** A filesystem lock cannot serialize
workers that share no filesystem, so it would have added failure modes without adding
exclusion.
