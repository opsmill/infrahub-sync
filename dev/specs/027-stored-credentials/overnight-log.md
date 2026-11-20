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

(none yet)

## Progress

- 2026-11-20 evening: implementation started.
