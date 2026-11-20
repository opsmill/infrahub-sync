# Alignment check: Stored credentials managed through the Sync API

**Date**: 2026-11-20 | **Spec**: [spec.md](spec.md), revised after the final review of the preparation

## Source

The idea brief "Stored credentials managed through the Sync API" (2026-11-20), given as a local file in
the session scratchpad. It has 11 decisions, 3 journeys, FR-001 to FR-012 with verification lines,
edge cases, SC-001 to SC-005, assumptions, an out-of-scope list, governance gates and 2 open
questions. No URL was given.

## Verdict

⚠️ MINOR DRIFT (proceeding)

Every decision, requirement, verification line, success criterion, edge case and out-of-scope item of
the brief is in the spec, with the brief's numbers. The differences below are clarifications that the
code required, or answers to the brief's own open questions.

## Findings

| Severity | Category | PRD reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-004, decision 5 | FR-004 sub-item | The brief releases values "only to the worker account". The spec also lets the Sync API use a value in its own process to read the destination schema at validation, and never return it. The code already does this read (`configuration/capabilities.py:195-232`); without it, registration could not check the destination schema. No value leaves the Sync API. |
| Minor | changed | Journey P3 | Assumptions, task T078 | The drop-area offer depends on spec 025, which is not merged on this branch's base. The spec keeps the requirement and moves it to spec 025's follow-up only if 025 is still not merged at implementation. The CLI path of P3 is unchanged. |
| Minor | changed | Open question 2 | Assumptions | The brief asks whether a plan records its revisions so an apply can warn. The spec records the revisions of each stage and shows both on the run, and does not add a separate warning. The brief's edge case ("the run records both revisions") is kept. |
| Minor | added | Open question 1 | FR-025, Out of Scope | The brief's suggested answer is adopted: changing the key is out of scope, losing it means re-entering every credential, and the deployment documentation says so. |
| Minor | added | Decisions 2, 6, 8, 9; edge cases | FR-013 to FR-024, FR-026 | Requirements that state the brief's decisions and governance gates as testable requirements (field sets, revisions, permission name, every stage, the worker account, redaction, failure causes, format removal, deployment, constitution). Each traces to a brief decision or gate. |
| Minor | added | — | FR-021, edge cases | A stored version in the old format is refused at run start with a cause that says to register a new version. The brief removes the format with no migration path; this states what happens to versions already in a database. |
| Minor | added | Governance, Helm | FR-024 | The Helm chart is in `opsmill/infrahub-helm`, so its change is a separate pull request (task T080). |

The first draft of the plan also had the package's `store` block name a credential. That was removed
after a check of the code: registered runs refuse a `store` block (`configuration/runtime.py:45`), so
the spec, like the brief, covers only the source and destination blocks.

No requirement, goal, non-goal or acceptance criterion of the brief is missing, softened or
contradicted.

## Action

Proceed. No remediation pass was needed (0 of 2 used).
