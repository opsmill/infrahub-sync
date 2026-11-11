# 22. Saved plan format 4 holds the plan report

**Status**: Proposed
**Date**: 2026-11-11
**Source**: `dev/specs/019-plan-problem-reporting/research.md` (R9, R14),
`dev/specs/019-plan-problem-reporting/contracts/plan-artifact-format-4.md`

## Context

A plan now leaves out the records that have a problem
([ADR 0020](0020-records-with-problems-are-left-out-of-a-plan.md)). The team reviews the
problems, the left-out records and the kept records before an apply, and the apply asks for a
confirmation when the plan leaves out records. Both need the report in the saved plan, and the
left-out count that the apply checks must be one the team cannot change without changing the
plan checksum they confirm. Format 3 ([ADR 0013](0013-writes-are-keyed-by-recorded-id-and-complete-hfid.md))
has no place for the report.

## Decision

- `PLAN_FORMAT_VERSION = 4` and `SUPPORTED_FORMAT_VERSIONS = {2, 3, 4}`.
- Format 4 is format 3 plus `plan/report.jsonl`: one line per problem, left-out record and kept
  record, in the canonical encoding of [ADR 0001](0001-saved-plan-artifact-format.md), in a
  stable order that does not depend on read order. The writer writes it after the operations
  and before the manifest, so the manifest stays the commit point.
- The manifest gains a `report` object with the report's SHA-256 digest, its size and its
  counts (`problems`, `left_out`, `left_out_with_problem`, `left_out_dependent`, `kept`), and
  `dropdown_values_checked: false` when the run had no destination schema snapshot.
- A plan with no problem still writes an empty report and a `report` object of zeros, so every
  format 4 plan has one shape.
- A reader refuses a format 4 plan whose report is missing, whose digest or size does not
  match, or whose counts differ from the report's lines (`PlanVerificationError`). The
  pre-apply verifier applies the same check.
- Formats 2 and 3 stay readable for review. Their apply is refused with
  `PlanFormatApplyUnsupportedError`, because they were never checked for problems and carry no
  left-out count to confirm.

### How the report is bound to the checksum the team confirms

The team confirms `plan_checksum`. It covers the manifest and `operations.jsonl`
(`compute_plan_checksum`), and the manifest holds the report's digest, so the checksum covers
the report's bytes through the digest. No new hashing rule was added:

- Changing `report.jsonl` alone makes its digest differ from the manifest's: the apply refuses
  before any write.
- Changing `report.jsonl` and the manifest's `report` record together changes the checksum: the
  recorded checksum no longer matches the files, or, when it is recomputed, it differs from the
  checksum the team confirmed. The apply refuses before any write in both cases.

`tests/plan/test_format_4.py` checks both cases on the apply path (`Potenda.apply_plan`).

The canonical plan fingerprint of [ADR 0007](0007-canonical-plan-fingerprint-as-equivalence-oracle.md)
does not cover the report. It hashes `plan.parquet`, which the plan writes from the DiffSync
comparison before the problem pass, so it also still holds the rows of left-out records. It is
an oracle for comparing two run paths in tests, not what an apply checks, and its field list is
an accepted contract. The format 4 contract's sentence that the fingerprint covers the report
was corrected to say this.

## Consequences

- A format 3 plan saved before the upgrade cannot be applied; the team plans again.
- The plan report travels with the plan: the plan checkpoint, the review document of the Sync
  API and the paged routes `GET /runs/{run_id}/plan/problems`, `/plan/left-out` and
  `/plan/kept` read it.
- Two plans that differ only in their report have the same ADR 0007 fingerprint. A test that
  compares run paths and needs the report compares `report.jsonl` itself.
