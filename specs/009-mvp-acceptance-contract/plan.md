# Implementation Plan: MVP Acceptance Contract

**Branch**: `009-mvp-acceptance-contract` | **Date**: 2026-10-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-mvp-acceptance-contract/spec.md`

## Summary

Establish one version-controlled, human-readable MVP acceptance contract and a fail-closed validator
for its machine-readable qualification manifest. The contract assigns stable criterion identifiers,
maps every criterion to specs 010–014 and retained evidence, and enumerates the larger-v3 deferrals.
The manifest binds one decision to the current contract digest, source revision, immutable candidate
digest, complete criterion set, normalized evidence catalog, and a non-implementing evaluator's
approval.

The implementation extends the existing release-tooling boundary without changing the current
producer-side `.release/qualification.json` record. A separate final acceptance manifest references
that record and is validated through the existing `release` Invoke collection. Spec 014 will later
wire generation, retention, and release promotion into the candidate workflow; this feature supplies
the normative contract, result shape, and reusable validator only. Approval of each downstream spec
may refine its evidence obligations and increment the selected contract. Spec 014 supplies approval
provenance for those contract bytes and alone determines release eligibility.

## Technical Context

**Language/Version**: Python 3.10–3.13, matching the repository's supported profiles.

**Primary Dependencies**: Existing Pydantic v2 models for strict input validation and JSON Schema
generation; Python standard library (`datetime`, `hashlib`, `json`, `pathlib`, and `re`); and the
existing Invoke task surface. `jsonschema` is a direct development-only dependency so the shared
fixture corpus validates the published schema in every development and CI environment; runtime
acceptance validation does not import or require it.

**Storage**: A normative Markdown contract under `docs/docs/develop/knowledge/`; a caller-supplied
JSON acceptance manifest; existing `.release/qualification.json` and uploaded artifact records are
referenced but not rewritten.

**Testing**: Pytest unit and contract tests under `tests/release/`, Markdown validation with rumdl,
and the repository's aggregate format and lint gates.

**Target Platform**: Developer workstations and Linux candidate/release runners.

**Project Type**: Python release tooling plus version-controlled developer documentation.

**Performance Goals**: Linear, offline validation over criteria and evidence. No latency acceptance
target is introduced until measured candidate manifests demonstrate a need.

**Constraints**: Fail closed; no criterion waivers; no network lookup during validation; never emit
evidence bodies or credentials; accept only restricted relative-path or opaque-artifact locators;
report selected-contract consistency without claiming contract approval or release eligibility;
preserve the existing qualification-record schema; do not implement specs 010–014 in this feature.

**Scale/Scope**: One active MVP contract, one manifest per candidate/contract decision, a stable
catalog expected to remain below a few hundred criteria, and bounded metadata-only evidence
references.

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- **I. Read-Only and Reviewed by Default — PASS:** validation reads contract and manifest bytes and
  performs no product or destination write.
- **II. Idempotency, Ownership, and Honest Outcomes — PASS:** repeated validation is deterministic;
  missing, mismatched, or failed evidence cannot produce a passing decision.
- **III. One Registered Execution Contract — PASS:** the feature does not create an execution path;
  it records REST → SDK → CLI parity as an acceptance obligation owned by spec 013.
- **IV. Type Safety and Explicit Boundaries — PASS:** manifest input is treated as untrusted JSON and
  converted into strict Pydantic domain types with specific validation failures.
- **V. Test Discipline and Evidence — PASS:** tests cover complete, missing, duplicate, unknown,
  stale-contract, mismatched-candidate, and non-independent-evaluator cases.
- **VI. Secrets and Untrusted Input Stay Contained — PASS:** only bounded evidence metadata is read;
  diagnostics name fields and criterion identifiers but never render evidence content.
- **VII. Small, Reversible, Documented Changes — PASS:** the design reuses the release namespace,
  leaves candidate production unchanged, and adds no dependency or alternate release subsystem.

Post-design re-check: **PASS**. The data model and manifest contract preserve all seven gates. No
complexity exception is required.

## Project Structure

### Documentation (this feature)

```text
specs/009-mvp-acceptance-contract/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── acceptance-validation.md
│   └── qualification-manifest.schema.json
└── tasks.md
```

### Source Code (repository root)

```text
docs/docs/develop/knowledge/
├── index.md
└── mvp-acceptance-contract.md

docs/static/schemas/
└── infrahub-sync-mvp-qualification-manifest-v1.schema.json

tasks/
├── acceptance.py
├── release.py
└── __init__.py

tests/release/
├── fixtures/acceptance/
│   ├── valid/
│   └── invalid/
├── test_acceptance_contract.py
├── test_acceptance_command.py
└── test_acceptance_manifest.py
```

**Structure Decision**: Keep contract parsing and strict manifest models in `tasks/acceptance.py`,
with a thin `release.validate-acceptance` Invoke wrapper in `tasks/release.py`. Generate the committed
JSON Schema from those models and assert byte-equivalence in tests, so the design and runtime cannot
drift. The normative contract lives
in the published developer knowledge tree so there is one human-readable source rather than a
private release copy and a public documentation copy. Tests stay beside the existing candidate and
qualification tests.

## Delivery Boundaries

### Included

- Publish the normative contract with stable criterion identifiers, owner specs, validation methods,
  evidence expectations, and explicit post-MVP deferrals.
- Define and validate the acceptance-manifest schema.
- Bind results to contract content digest, contract version, source revision, candidate digest, and
  the existing producer qualification record.
- Enforce exact criterion accounting, no waivers, decision consistency, and independent evaluator
  approval.
- Store each evidence description once in a top-level catalog and reference it by identifier from
  criterion results.
- Require every evidence reference to declare an evidence type and permit reuse only when that type
  is accepted by every referencing criterion.
- Validate evidence locators as normalized relative POSIX paths or bounded opaque artifact IDs;
  reject URI schemes, user information, queries, fragments, traversal, absolute paths, and
  backslashes.
- Provide a read-only Invoke validation entry point and actionable typed failures.

### Normative criterion-table grammar

Immediately below its title, the human-readable contract contains exactly one version declaration:

```markdown
**Contract version:** 1
```

The value is a positive integer. The contract then contains exactly one
`## Required MVP criteria` section followed immediately by this exact six-column Markdown table
header:

```markdown
| ID | Owner spec | Class | Requirement | Validation | Evidence |
|---|---|---|---|---|---|
```

Each subsequent non-empty table row, up to the next level-two heading, defines one criterion. Cells
are single-line plain text and may not contain an additional pipe or HTML. The parser fails closed on
a missing, repeated, misplaced, or invalid version declaration; a missing or repeated criteria
section; a changed header; a malformed row; a duplicate identifier; an unknown owner or class; or an
empty cell. This table is the only criterion catalog; no identifier list is copied into Python. The
validator parses it before checking exact manifest accounting.

The `Evidence` cell is a comma-and-space-separated list of lowercase evidence-type identifiers.
Each identifier matches `[a-z][a-z0-9-]*`; duplicates and empty entries are invalid. These declared
types are the only types accepted for that criterion.

### Deferred to owning specifications

- Producing secret-containment evidence (spec 010).
- Producing configuration-preflight evidence (spec 011).
- Producing failure and reconciliation evidence (spec 012).
- Implementing and evidencing lifecycle interface parity (spec 013).
- Populating the final manifest, packaging it, retaining it, and gating candidate promotion
  (spec 014).
- Supplying approved-contract provenance and deciding release eligibility (spec 014). The 009
  validator establishes only consistency with selected contract bytes.
- Verifying referenced artifact availability and bytes (spec 014). The 009 validator reports that
  it validates metadata only.

## Complexity Tracking

No constitution violations or exceptional complexity are introduced.
