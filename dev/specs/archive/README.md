# Archived specs

Each directory holds one completed spec. The file `EXTRACTED.md` in a directory lists what was
recorded in the developer documentation and the decision records, and what was open when the spec
was archived.

## Spec numbers before the renumbering of 2026-11-15

The specs were renumbered from 001 to 016, in the order of their first commit. Text in the
repository uses the new numbers. Branch names, pull requests, issues and changelog entries
written before that date use the earlier numbers.

| Number | Earlier number | Directory |
|--------|----------------|-----------|
| 001 | 001 | `001-plan-artifact-saved-apply` |
| 002 | 001 | `002-prefect-managed-remote-run` |
| 003 | 004 | `003-cli-core-migration` |
| 004 | 006 | `004-mvp-interface-conformance` |
| 005 | 007 | `005-harbor-image-publishing` |
| 006 | 012 | `006-v2-configuration-conversion` |
| 007 | 020, and before that 019 and 009 | `007-plan-problem-reporting` |
| 008 | 010 | `008-prefect-from-sync-image` |
| 009 | 014 | `009-import-lineage` |
| 010 | 015 | `010-run-discovery-api` |
| 011 | 016 | `011-prefect-run-observability` |
| 012 | 017 | `012-sync-run-events-logs` |
| 013 | 018 | `013-plan-read-progress` |
| 014 | 019 | `014-plan-approval` |
| 015 | 021 | `015-compose-store-credentials-ca` |
| 016 | 022 | `016-resume-interrupted-apply` |

## Numbers that the renumbering did not change

- A branch name keeps its number, for example `020-plan-problem-reporting` or
  `010-prefect-pr2-preview-remote`.
- "Old 012", "old specification 012", "old 013" and "old specification 013" name two earlier
  specifications that have no directory here. Old specification 012 described a v2 configuration
  conversion command. Spec 015 was written from what was left of old specification 013.
- "Old specification 009" in `007-plan-problem-reporting` names the document that spec 007 was
  written from.
- Requirement, task and decision identifiers such as `FR-020`, `T040` and `DBR-010` are not spec
  numbers.
