# 3. A canonical plan fingerprint is the equivalence oracle between run paths

**Status**: Accepted
**Date**: 2026-07-31
**Source**: `dev/specs/archive/001-prefect-managed-remote-run/spec.md` (D001),
`contracts/run-result-and-errors.md`

## Context

Once a second caller can drive the plan lifecycle, "the remote path produces the same plan
as the CLI" has to be a claim a test can settle. Comparing run directories does not settle
it: run identifiers, timestamps and filesystem paths differ by construction on every run,
and comparing `run.json` summary counts is too coarse — two different plans can carry the
same create/update/delete totals.

## Decision

One shared helper, `infrahub_sync/cache/fingerprint.py::compute_plan_fingerprint(run_dir)`,
returns a SHA-256 hex digest of the canonicalized plan rows in `<run_dir>/plan.parquet`:

1. project exactly `PLAN_FINGERPRINT_FIELDS` — `action`, `resource`, `source_id`,
   `attribute`, `new_value` — from each row;
2. serialize each row as compact sorted-key JSON;
3. sort rows by `(resource, source_id, action, attribute)` with the row's full serialized
   form as the final tie-breaker, normalizing `None` to `""` in the **sort key only** so
   the tuple sort stays total for null-bearing rows (the serialized form is unchanged —
   `None` still serializes as JSON `null`);
4. join with newlines, encode UTF-8, digest.

Timestamps, run identifiers and paths are excluded by construction. The same helper computes
both sides of any comparison, and tests may not reimplement the algorithm.

## Consequences

Two runs over identical inputs compare equal even though nothing else about their run
directories does, which is what makes a reset-fixture equivalence test possible at all. The
digest is also the natural check for any future third caller: the equivalence claim is
one function call, not a bespoke comparison.

The field list is the contract. Adding a column to the plan schema does not change the
digest unless it is added to `PLAN_FINGERPRINT_FIELDS`, and adding it there is a deliberate
break for anyone comparing digests across versions. The current writer emits one row per
element, so `source_id` is unique within `resource` and ties cannot occur; the tie-breaker
and the null normalization exist so the digest stays total if that stops being true.

The helper lives in `infrahub_sync/cache/` rather than in the execution surface, keeping
`pyarrow` out of the import-light module the flow depends on.

## Alternatives Considered

Compare `run.json` summaries only — too coarse to detect a differing plan with matching
counts. Diff the Parquet files directly — row order and file-level metadata are not stable
across runs. Put the helper in `execution.py` — drags `pyarrow` into the seam module for no
benefit, since all Parquet I/O already lives under `infrahub_sync/cache/`.
