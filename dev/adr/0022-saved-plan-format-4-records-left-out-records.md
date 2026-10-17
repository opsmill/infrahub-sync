# 22. Saved plan format 4 records left-out records

**Status**: Accepted (2026-10-17)
**Date**: 2026-10-15
**Source**: `dev/specs/009-plan-problem-reporting` (research R10)

## Context

A plan can now leave out records (ADR 0020). Apply must then require the person to confirm the
records left out. ADR 0001 lets a later change add a manifest key without a format change,
because a reader accepts keys it does not know. For this key that is not safe: a reader that
does not know the key would apply the plan without the confirmation.

## Decision

The plan format becomes 4.

- The plan directory holds a third file, `report.jsonl`, written after `operations.jsonl` and
  before `manifest.json`, in the same canonical encoding. `manifest.json` stays the commit
  point.
- The manifest gains a `report` object: `digest`, `size`, `problems`, `left_out`,
  `left_out_with_problem`, `left_out_dependent` and `kept`. The checksum rule of ADR 0001 is
  unchanged and covers the report through the digest.
- `PlannedOperation` keeps its fields, so operation identifiers and their bytes are unchanged.
  Format 4 reserves one optional field on an update for clearing a relationship,
  `cleared_relationships`, a list of relationship names (decided on 2026-10-17). This release
  never writes it, and an operation without it keeps its bytes. Readers accept it. This release
  refuses to apply a plan whose update carries a non-empty list, before any write, so a plan
  that clears a relationship is never applied without the clearing. Spec 010 implements the
  clearing in format 4, with no format 5.
- The manifest records `dropdown_values_checked: false` when the run had no destination schema
  snapshot, so the plan did not check Dropdown values; the field is absent otherwise.
- The report holds source data, schema names and fixed reason templates. No entry is built from
  connection settings or from the text of an error (ADR 0021).
- Reading and review accept formats 2, 3 and 4. Apply accepts format 4 only, and refuses
  formats 2 and 3 with `PlanFormatApplyUnsupportedError` and the instruction to plan again
  (`infrahub_sync/plan/apply_gate.py`).
- When `report.left_out` is greater than zero, apply requires the person's confirmation (a yes
  or no flag; the plan checksum binds it to the reviewed plan) before the first ownership proof,
  so a refusal writes nothing.

## Consequences

- Every plan saved before this release must be planned again before it is applied.
- An older Sync release refuses a format 4 plan instead of applying it without the
  confirmation.
- A plan that clears a relationship, written by the release of spec 010, is read by this
  release and refused at apply with `plan-feature-not-supported`.

## Alternatives considered

- An added manifest key with no format change: an older reader would ignore the confirmation.
- The report inside `operations.jsonl`: it would change the bytes and the meaning of the file
  that every consumer reads as operations.
