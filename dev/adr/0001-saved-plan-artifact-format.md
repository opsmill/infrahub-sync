# 1. Saved plan artifact format

**Status**: Accepted
**Date**: 2026-07-28
**Source**: `dev/specs/archive/001-plan-artifact-saved-apply/research.md` (PD-001, PD-002, PD-003,
PD-007, PD-008),
`dev/specs/archive/001-plan-artifact-saved-apply/contracts/plan-artifact-format.md`

## Context

A run used to record its intended changes as `plan.parquet`, a row format that carried no identity
for a destination object and recovered a record's identifiers by splitting the DiffSync `unique_id`
on `__`. Nothing bound a stored plan to the data or the configuration it was computed from, so there
was no way to tell whether applying a plan would do what a reviewer had read.

The format that replaces it is not private to one command. It is produced by the derivation step in
`diff` and `sync`, read by `apply`, and named as an interface by nine later outcomes — the public API's apply, the configuration-version
binding, a schema-fingerprint field, branch review, the apply ledger's operation identifiers, scoped
plans, per-operation dependency tiers, plan summaries, and byte-for-byte comparison against this
format. A change to it after it ships is breaking for all nine, so its details are fixed here rather
than left to the implementation.

## Decision

A plan is a directory, `<run_dir>/plan/`, holding two files.

- `operations.jsonl` is written **first**, one operation object per line.
- `manifest.json` is written **last**. Its presence is the commit point.

Both are written tmp-then-`replace`. The write order is what makes "no plan here at all" and "a torn
plan" disjoint by construction rather than by heuristic: a `plan/` with no parsable manifest is torn,
and no `plan/` at all is a pre-existing v1 plan.

Four rules give the format its properties.

1. **One canonical encoding for both files.** UTF-8 without BOM,
   `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, LF only. Values pass
   through an explicit `canonical_value` table before encoding; a type not in that table raises and
   names the kind, the field and the Python type. There is no `default=str` fallback, because a
   silent one makes the artifact's determinism depend on some type's `__str__`.

2. **A derived operation identifier.**
   `operation_id = "op_" + sha256(canonical_json_bytes([action, kind, identity]))[:16]`. The hash
   input is a JSON **array** in that order, and the payload is deliberately outside it, so the
   identifier names the logical operation and survives a re-plan. Payload exactness is guaranteed by
   the checksum instead.

3. **A checksum over manifest-plus-operations.**
   `sha256(canonical_json_bytes(manifest_without(plan_checksum, run_id, created_at)) + operations_raw_bytes)`,
   with no separator between the two byte sequences. The three excluded keys are **removed** before
   canonicalization, not blanked. Removing `run_id` and `created_at` is what lets two plan runs over
   unchanged input produce a byte-identical manifest.

4. **Recursive relationship references.** A peer is named by `{peer_kind, identity}`, recursively to
   whatever depth the configuration nests, never by a `unique_id` string. Splitting a schema path is
   allowed; splitting a data value is not. Without the nested pair, an apply-time resolver holding
   only a peer's identity mapping would have to split a `unique_id` on `__` to build a nested
   destination filter — reintroducing the exact flaw this format exists to remove.

Two supporting choices follow from requirements that would otherwise conflict.

- **The configuration version digests the parsed configuration excluding `directory`.** `directory`
  is location, not configuration; including it would make a plan produced in CI unapplicable from a
  developer's checkout. `settings` **is** included, so rotating a credential invalidates every saved
  plan for that configuration. That is accepted and recorded, not mitigated: excluding `settings`
  would mean a changed destination address did not invalidate a plan, which is the worse failure.
- **The source-snapshot digest covers logical rows, not file bytes.** The cache injects a per-run
  `_extract_ts` into every snapshot row, so two runs over an unchanged source produce different
  snapshot bytes by construction. A raw-bytes digest could therefore never be byte-stable, while the
  determinism criterion fixes its mask at exactly two manifest fields. The digest is taken over the
  Parquet table with `_extract_ts` dropped, rows in file order.

## Consequences

Determinism is a property of the artifact rather than a hope about it: two plan runs over unchanged
input at the same extraction mode on each side produce a byte-identical `operations.jsonl` and a
`manifest.json` byte-identical after removing `run_id` and `created_at` from both sides.

Absent versus empty becomes load-bearing, and consumers have to respect it. An absent
`relationships` key means the operation carries no relationship values; a `peers: []` under
`cardinality: "many"` means the peer set is deliberately empty, and the replace-set write acts on it.
`relationships` is absent, never `[]`, when an operation carries none.

Unknown manifest keys are tolerated on read, preserved, and included in the checksummed bytes, so a
later outcome can add a field without a format bump. `destination_binding` — the resolved endpoint URL
and branch a plan was computed against, which `apply` compares before it writes — is the first field
added under that rule, and it demonstrates the shape the rule requires: additive, checksum-covered,
and absent rather than null on plans written before it existed, so the comparison is skipped for them
instead of failing. The current field list lives in `dev/knowledge/plan-artifact.md`; this record fixes
the rules, not the roster. An unrecognized `format_version` is a gate: it
short-circuits the remaining pre-apply checks, because a reader that does not know what the fields
mean would otherwise report failures that are artifacts of its own ignorance.

Two costs are real. Verification decodes the snapshot Parquet rather than digesting bytes, and a
benign reformat of the configuration that the parse is not sensitive to is the only reformat that
does not invalidate saved plans — key order, comments and whitespace are absorbed, but a semantic
edit is not meant to be.

## Alternatives Considered

- **Keep `plan.parquet` as the applied artifact.** Rejected: it carries no destination identity and
  recovers identifiers by splitting a `unique_id`, which is the defect being removed. It is still
  written for backward compatibility and is never read by this path.
- **A JSON object rather than an array as the identifier hash input.** Rejected only on cost: both
  are equally deterministic, and the array adds no key strings to bikeshed.
- **`json.dumps(default=str)` for non-native values.** Rejected: non-deterministic for any type with
  an address-bearing `repr`.
- **Digest the snapshot's raw bytes and widen the determinism mask.** Rejected: the mask is fixed at
  two fields and widening it was explicitly ruled out.
- **Exclude `source_snapshot` from the checksum.** Rejected: the checksum is what stops a recorded
  digest being tampered with.
- **Encode every payload value as a string.** Rejected: destroys the type fidelity the destination
  write needs.
