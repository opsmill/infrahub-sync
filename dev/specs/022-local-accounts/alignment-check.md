# Alignment check: specification against the request

**Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

## Source

The preparation command was started with no text. The request is the idea brief "Local accounts
and stored credentials for Sync", which the maintainer and the agent wrote in an interview on
2026-11-17, in the part that concerns local accounts (the brief's "change A"). The agent passed
that part to the specify phase as the feature description. It has four journeys, eleven
requirements, four edge cases, five success criteria, an out-of-scope list and two open points.
No URL was given, so nothing was fetched.

## Verdict

⚠️ MINOR DRIFT (proceeding)

Every requirement, journey, success criterion and out-of-scope item of the brief is in the
specification with the same meaning. The differences are additions and one move of content
between user stories. All come from the critique, and none removes or weakens a point of the
brief.

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | Journeys P1 and P3 | User Stories 1 and 3 | The brief puts the own password change, disable and enable in P3. The spec moves them into User Story 1, with the administrator's set-password, because the first release could not otherwise remove a person's access or replace a lost password. User Story 3 keeps delete, set level and another account's tokens. The priorities P1 to P4 are unchanged. |
| Minor | added | FR-006, FR-008 | D-10, FR-024, FR-032a | A record refers to an account and not to its name, so a deletion changes no past record. The brief asks for the result (the placeholder on every past record, no right for a new account with the same name) and does not say how. The agent decided this in the critique; the maintainer has not confirmed it. |
| Minor | added | FR-009 | FR-021 | The brief says "several failed attempts", "a short time" and "a few minutes". The spec sets 5 attempts and 15 minutes, counts a wrong current password, does not extend a refusal, and adds an administrator action that ends a refusal. The values are recorded as the agent's choice under "Assumptions". |
| Minor | added | none | FR-021a | A limit on the number of passwords checked at the same time. It protects the memory of the Sync API. |
| Minor | added | none | FR-024a | A request whose reason contains a token is refused, and a reason has a maximum length. The first replaces a protection that exists today for the tokens of the environment. |
| Minor | added | FR-002 | FR-012 | The names `unknown` and `unauthenticated` are refused, beside `admin`. |
| Minor | added | none | FR-040 | One sentence of the constitution's principle VI, to allow displaying a token value once. The brief lists principle VI as a rule to rework, for the stored credentials of the later specification. |
| Minor | added | SC-001 to SC-004, SC-007 | SC-006, SC-007 | SC-006 (a script changes only the value of its token) and SC-007 (a deleted name is found nowhere outside text a person typed) are new. The brief's SC-007 is the spec's SC-005. The brief's SC-005 and SC-006 concern stored credentials and are out of scope. |
| Minor | added | Edge cases | Assumptions | A person who knows an account name can delay that account's sign-in with a password. The spec states this limit. The brief does not mention it. |
| None | missing | all | all | Nothing is missing. |
| None | dropped | all | all | Nothing is dropped or softened. |
| None | contradicted | all | all | Nothing is contradicted. |

## Action

Proceed. No phase is run again. Two points are left for the maintainer to confirm, and both are
stated in the spec: decision D-10, and the rule of FR-021 against its alternative, a delay that
grows with each failure.
