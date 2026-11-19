# Alignment check: spec.md against the idea brief

## Source

The idea brief "Register a configuration package from the web interface", written with the
maintainer in a grilling session on 2026-11-18 and passed inline as the input of the specify
phase. It has no URL.

## Verdict

⚠️ MINOR DRIFT (proceeding). Every requirement, edge case, success criterion and out-of-scope
item of the brief is in the specification. The differences either follow from facts found in
the code or tighten security.

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
|---|---|---|---|---|
| Minor | changed | FR-006: "list findings of lower severity" | FR-011, Key Entities | Every validation finding has the severity `error` (`validation.py`, line 169). "Valid" therefore means "no findings", and there is nothing of lower severity to list. Corrected on 2026-11-19: findings can also have the severity `warning`, so the spec now follows the brief (decision D-10, research R4). |
| Minor | added | (none) | FR-005, User Story 1 scenario 6 | The critique (E1) found that validating a package the caller sends would send the service's destination token to an address the caller chooses. The new route requires an administrator. The brief's edge case 1 already limits the web interface to administrators, so the operator notices no difference. |
| Minor | added | Open question 2: "one spec or two?" | Decision D-9, User Story 1 | Not answered in the grilling session. The maintainer confirmed on 2026-11-19: one specification, delivered in two pull requests, User Story 1 first. |
| Minor | added | "The CLI could also use the new route" (recommendation, not a requirement) | FR-006 | `infrahub-sync configs check` is a requirement. |
| Minor | added | (none) | FR-022, SC-005 | FR-022 states what happens when User Story 4 is left out. SC-005 adds a measurable check for findings parity. |
| Minor | added | (none) | FR-007, FR-009 | From the critique: choose a file without dragging; a validating state with Cancel available; a note when findings exceed one page. |
| Minor | changed | Open question 3: normalize or accept the difference | Edge Cases, research R5 | Resolved: the route returns the package as the store keeps it, and the review step compares that. |
| Corrected | changed | Question 5: "Register still allowed when the destination cannot be reached" (agreed) | FR-020, FR-022, User Story 2 scenario 8, User Story 4 | The first version of the spec put registration during an outage inside User Story 4 and blocked it when that story was left out. That contradicted the agreed answer. Corrected after review: registration during an outage is part of User Story 2, and User Story 4 holds only the mark. |

## Action

Proceed. No remediation pass is needed. On 2026-11-19 the maintainer confirmed that the
"registered without validation" mark (User Story 4) is kept (decision D-8).
