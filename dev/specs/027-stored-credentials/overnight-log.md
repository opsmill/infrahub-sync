# Overnight log: spec 027, night of 2026-11-20

Work area: branch `027-stored-credentials` in the worktree `.worktrees/027-stored-credentials`, created
from `feature/v3-develop` at `5c22b693`. Nothing is pushed and no pull request is opened. Advisor: the
`advisor` tool of the orchestrating session (Fable 5.2).

## Rules for the night (from the maintainer)

- Implement with `/speckit-opsmill-implement`, specification first.
- A critical blocker (deletes data, changes production, cannot be reversed, needs access only the
  maintainer has, or two readings with very different results) stops that item only. It goes in the
  "Blocked" section of the morning report.
- Every other question goes to the advisor with context, options and a recommendation. Each one is
  recorded below with the answer and what was done next.
- No push to `main`, no branch or data deletion, no messages to customers, no spending, no change to
  shared configuration.
- The same failure three times stops that item.

## Known stopping points

- **T074** (`emil-prototype`): the maintainer picks one version per screen. The prototypes are built,
  then T075 to T078 wait for the choice.
- **T080** (Helm pull request in `opsmill/infrahub-helm`): opened only on request.

## Chunks

Base commit `e73deca4`. One clean-context subagent per chunk, one at a time.

| Chunk | Tasks | Content |
| --- | --- | --- |
| 1 | T001–T003 | Dependency and settings |
| 2 | T004–T009 | Encryption and store tables |
| 3 | T010–T013 | The worker account and its route boundary |
| 4 | T014–T017 | `credential:` key and field map |
| 5 | T018–T020 | Redaction registry and shared test helper |
| 6 | T021, T026, T027, T029 | Credential routes and client methods |
| 7 | T022, T023, T028, T030 | Worker route and worker client |
| 8 | T024, T025, T031–T036 | Runtime, worker flow, admission and run-start checks |
| 9 | T037, T038 | CLI group |
| 10 | T039–T041 | Examples, configuration and store tests moved |
| 11 | T042–T043 | Service and other tests moved |
| 12 | T044–T046 | Development tasks and conversion |
| 13 | T047–T053 | Removal of the old path |
| 14 | T054–T056 | Deployment files, Compose tests, marker test |
| 15 | T057–T062 | Decision record, constitution, documentation, changelog |
| 16 | T063–T065 | US2: revisions on runs |
| 17 | T066–T069 | US4: deletion |
| 18 | T070–T072 | US3: conversion report |
| 19 | T073–T074 | Web: queries and prototypes; stops for the maintainer's choice |
| 20 | T081 | Final verification |

Not run: T075–T079 (wait for the T074 choice), T080 (Helm pull request, on request only).

Test infrastructure: a private PostgreSQL container `sync027-pg` on 127.0.0.1:55427 with the
databases `sync027_store`, `sync027_guard` and `sync027_storage`. The development stack
`infrahub-sync-dev-*` belongs to another session and is not used.

## Advisor questions

### A1. Chunk 6 replaces the receipt check of an existing table at start-up (2026-11-20, about 19:00)

**Context.** The credential routes write mutation receipts with `resource_kind = 'credential'`. The
`mutation_receipts` check of an earlier build refuses that kind. Chunk 6 renamed the check
(`mutation_receipts_resource_kinds`) and, at start-up, drops the check of an earlier build by its old
name, only when the catalog names it, then creates the new one. The plan said no existing table changes,
and PR #425 removed start-up migrations.

**Options.** (a) Keep chunk 6's swap. (b) Edit the check text under the same name, as spec 021 did for
`schedule`. (c) Refuse an earlier database at start-up.

**Recommendation sent.** (a), if it matches the precedent.

**First answer.** Keep it if spec 021 also replaced the check by name; otherwise match 021's pattern. Check
that the swap is conditional and that the fingerprint key is derived, not the raw key.

**What the code showed.** Spec 021 edited the text under the same name, and also listed its new
`product_runs` columns in `_COLUMNS_ADDED_SINCE_AN_EARLIER_BUILD`, so an earlier database was refused
outright (option c in effect). Spec 027 adds no column, so an earlier database starts under this build;
with option (b) it would then refuse every credential change. The drop is conditional on the catalog, and
the fingerprint key is a labelled SHA-256 derivation of the key.

**Corrected answer.** Keep chunk 6's swap (a). Record the exception in `plan.md` and `data-model.md`,
correct the T057 text with two limits (an earlier build that starts later adds its old check back and on
PostgreSQL probably fails once a credential receipt exists; old and new builds on one database at the same
time can leave both checks in place), and list it under Risks with option (c) as the alternative.

**What I did next.** Applied those edits, then dispatched chunk 7.

## Progress

- 2026-11-20 evening: implementation started.
