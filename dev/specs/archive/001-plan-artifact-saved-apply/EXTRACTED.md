# Extraction Record

**Extracted on**: 2026-07-28
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0001-saved-plan-artifact-format.md` (PD-001, PD-002, PD-003, PD-007, PD-008;
  `contracts/plan-artifact-format.md`)
- `dev/adr/0002-planned-write-destination-protocol.md` (PD-010; AD086)
- `dev/adr/0003-replace-set-flush-is-a-targeted-relationship-write.md` (PD-005; AD054, AD065, AD075,
  AD085, AD088)
- `dev/adr/0004-deletes-are-recorded-but-never-executed.md` (AD004, AD049, AD055; "confirmed
  non-questions")

## Knowledge Updated

- `dev/knowledge/plan-artifact.md` — **new**. The artifact on disk, canonical encoding, operation shape,
  recursive peer references, the manifest and checksum, determinism, and the reader/verify surface.
- `dev/knowledge/planned-write-and-apply.md` — **new**. The write surface and the limit of its runtime
  check, apply-time peer resolution, the enforced replace-set and its flush, the apply loop, and the
  delete contract. This closes retrospective finding R4, whose disposition was `local-only` because
  extraction owns it.
- `dev/knowledge/sync-architecture.md` (The Potenda engine; See also) — the plan-artifact stage between
  diff and first write, and why the tier branch computes every diff before writing.
- `dev/knowledge/schema-mapping.md` (Identifiers, references, and write order; See also) — `identifiers`
  is not the convergence key; read keying behaviour off the destination schema.
- `dev/knowledge/README.md` — indexes the two new pages and the decision records.

## Guidelines Updated

- `dev/guidelines/testing-adapters.md` — two new sections: "Never claim an unexecuted test as evidence"
  (AD090) and "Assert the effect that leaves the process, not the state before it" (AD065, AD068, AD075,
  AD088).
- `dev/guidelines/writing-an-adapter.md` — two new sections: "Implement the planned-write surface as a
  whole, or not at all" and "Do not change an existing write path to tidy a new one" (AD070), plus five
  anti-pattern rows.

## Also Updated

- `dev/adr/README.md` — a "Current records" index, the directory previously holding only its own naming
  convention.

## Not Extracted

- **AD006** (the constitution mandates `structlog`; every module uses stdlib `logging`) and **AD093**
  (`tasks/linter.py:41` runs bare `pylint`, so the declared gate cannot pass; the CI pylint step is
  commented out) are `governance` findings. They belong to a tooling or constitution amendment filed for
  maintainer review, not to extracted knowledge, and are not written anywhere as practice to follow.
  AD006 is noted once, descriptively and explicitly as an open question, in
  `dev/knowledge/planned-write-and-apply.md`, only so the pinned warning levels read unambiguously.
- Execution artifacts with no durable knowledge: `plan.md`, `tasks.md`, `quickstart.md`,
  `opsmill-implement-report.md`, `planner-feedback-additions.md`, `checklists/`, `critiques/`,
  `sessions/`.
- `retrospective.md` — a process record, not system knowledge. Its R4 documentation gap is discharged by
  the new knowledge page above; its remaining dispositions were proposed and not executed.
- PD-006 (the format-version gate) and PD-007 (tier under an explicit `order:`) — low materiality, folded
  into ADR 0001 and the artifact knowledge page rather than given records of their own.
- Retrospective R5 (record the repository's lint and type baseline) — the companion to the AD093
  governance issue, not settled practice.

## Archive

Spec directory moved to `dev/specs/archive/001-plan-artifact-saved-apply/` as a historical record.
