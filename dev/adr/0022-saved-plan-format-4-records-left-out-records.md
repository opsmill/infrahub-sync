# 22. Saved plan format 4 records left-out records

**Status**: Proposed
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
- `PlannedOperation` is unchanged, so operation identifiers and their bytes are unchanged.
- The report holds source data, schema names and fixed reason templates. No entry is built from
  connection settings or from the text of an error (ADR 0021).
- Reading and review accept formats 2, 3 and 4. Apply accepts format 4 only, and refuses
  format 3 with `PlanFormatApplyUnsupportedError` and the instruction to plan again
  (`infrahub_sync/potenda/__init__.py:148-163`).
- When `report.left_out` is greater than zero, apply requires the person's confirmation (a yes
  or no flag; the plan checksum binds it to the reviewed plan) before the first ownership proof,
  so a refusal writes nothing.

## Consequences

- Every plan saved before this release must be planned again before it is applied.
- An older Sync release refuses a format 4 plan instead of applying it without the
  confirmation.

## Alternatives considered

- An added manifest key with no format change: an older reader would ignore the confirmation.
- The report inside `operations.jsonl`: it would change the bytes and the meaning of the file
  that every consumer reads as operations.
