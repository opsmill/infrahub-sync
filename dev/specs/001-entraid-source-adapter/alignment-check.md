# Alignment check: spec versus brief

## Source

The inline brief given to `speckit-opsmill-auto` ("Microsoft Entra ID source adapter for Infrahub Sync"; the same
text as `/home/ubuntu/src/overnight/brief.md`). No URL was referenced.

## Verdict

⚠️ MINOR DRIFT (proceeding)

Every requirement, non-goal and constraint of the brief maps to the spec (FR-001 to FR-029, Assumptions, SC-005).
The differences below are small additions that fill gaps in the brief, plus one conflict inside the brief that the
plan resolves and reports.

## Findings

| Severity | Category | PRD reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Low | added | Requirement 2 ("apply a request timeout", "bounded number of attempts") | Assumptions; data-model `timeout`, `max_retries` | The brief does not name these settings. The spec adds `timeout` (default 60) and `max_retries` (default 5) so the timeout and the bound can be set. Necessary detail. |
| Low | added | Requirement 6 (errors) | FR-030, edge cases | A specific error for token acquisition failures, added by the critique. Same intent as the brief's error requirements. |
| Low | added | Requirement 3 (collections) | Edge cases, Assumptions | Collection names `users` and `groups` are reserved, so the bare resource names keep one meaning. |
| Low | added | Requirement 4 (membership) | Edge cases, data model | A groups field mapping `members` without `reference` is refused at start-up. |
| Low | changed (clarified) | Requirement 4 ("logs one warning with the count") | FR-017, research R8 | One warning per groups kind with dropped references, logged at the end of that kind's load. With one groups kind (the usual case and the example) this is one warning per run. |
| Low | added | Requirement 8 (example) | Plan, tasks T020 | The example ships `schema.yml` with the example kinds, because the generated code needs an Infrahub schema and the kinds are not standard. |
| Low | added | Requirement 9 (documentation) | Tasks T023 | `entraid` is added to the adapter lists in `AGENTS.md`, `dev/guides/adding-an-adapter.md` and, if present, the adapter chooser page. One-line consistency edits. |
| Low | contradicted (inside the brief) | Requirement 1 (`# ty: ignore[unresolved-import]`) and Constraints ("no ty findings") | Plan "Design notes", research R11 | With the SDK installed, `ty` reports `unused-ignore-comment` warnings (exit code 0) on these lines; without it, the comment is needed. No comment form is clean in both environments (probed). The plan follows requirement 1 and reports the warnings. |
| Info | unspecified | Requirement 5 (`auth`) | data-model `auth` default | The brief gives no default for `auth`. The spec uses `client_secret`. |

No requirement is missing, no acceptance criterion is dropped or softened, and no non-goal is brought into scope.

## Action

Proceed to implementation. No remediation pass was needed (0 of 2 used). The `ty` warning conflict is reported in
the implementation report so the user can decide whether to keep the brief's comment form.
