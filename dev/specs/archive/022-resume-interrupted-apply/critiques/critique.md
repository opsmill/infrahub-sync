# Critique: Resume an interrupted apply

Dual-lens review of [spec.md](../spec.md) and [plan.md](../plan.md) on 2026-11-12, before tasks.

**Verdict**: ⚠️ PROCEED WITH UPDATES. Four must-address findings, all fixed in the spec, research and data model in
the same commit as this report.

## Engineering lens

### 🎯 E1 (Must-Address): `verify` is refused on an interrupted run today

`_reserve_existing` refuses every stage on a run that holds a `write_admissions` row
(`infrahub_sync/product_store/store.py`, the `SELECT 1 FROM write_admissions` check). An interrupted run always holds
one, so the `verify` that FR-012 requires would end in `run-execution-conflict`.

**Fix**: research R5 now admits a `verify` on a resumable run (no unfinished execution, `reconciliation_required`
true) as a read stage, and the reservation tests cover it. Spec FR-005 states that `verify` is accepted on an
interrupted run.

### 🎯 E2 (Must-Address): the run's marker when a resume saved nothing

The data model took the marker from the latest write execution. A resume killed before its first save has no marker,
so the run would appear to have made no progress.

**Fix**: the run's position is the highest saved position over all its write executions (data model, "Run progress
view"). A resume's `start_after` is that position.

### 🎯 E3 (Must-Address): which operations are "uncertain" when the worker was killed

When the liveness monitor ends an execution, the failing operation is unknown. The data model counted only one
operation as uncertain, which understates what may have reached the destination: with successful saves, up to 500
operations or 30 seconds of writes follow the marker.

**Fix**: the uncertain window is the operations after the marker through the failing operation when it is known.
When it is unknown, the window is the next 500 operations if the last save attempt succeeded, otherwise every
operation after the marker. `verify` reports exactly this window (FR-005, data model). The 2,000-operation report
limit stays for the second case.

### 💡 E4 (Recommendation, applied): measure the 5-minute stop from the first failed save

"No save has succeeded for 5 minutes" is measured from the first failed save attempt since the last success, so a
quiet period between scheduled saves is never counted. Applied to research R4.

### 💡 E5 (Recommendation, noted): SC-003 needs a baseline

The tier M and L benchmark results on `product/metrics.md` predate spec 018 and this branch. The implementation
report must record a run without the marker on the same machine before the run with it. Noted in plan step 6.

## Product lens

### 🎯 P1 (Must-Address): operators need to know a resume overwrites changes made meanwhile

The spec lists the overwrite as an edge case, but the operator reads `runs show` and `verify`, not the spec. A node
reported as "differs from the plan" will be overwritten by the resume.

**Fix**: FR-006 now requires the `verify` output to say that the resume writes the plan's values over every
operation reported as "differs from the plan", and the CLI contract prints that line.

### 💡 P2 (Recommendation, applied): document the change to ADR 0010 for operators

`running-a-sync.mdx` currently says an uncertain run cannot be continued. The docs task updates it.

### 🤔 P3 (Question, resolved): should a resume of a run planned long ago warn?

Resolved: no. `apply` has no age limit, and the stronger `verify` reports drift for the window that matters. Kept
out of scope.
