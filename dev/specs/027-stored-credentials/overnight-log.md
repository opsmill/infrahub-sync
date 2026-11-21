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

### A2. A schedule with branch review reads earlier proposed changes before a run exists (2026-11-20, about 21:00)

**Context.** Chunk 8 left part of T032 undone. Before a scheduled start creates its run, the admission
reads the state of the schedule's earlier proposed changes from the destination
(`schedule_admission.py:326-346`). The result decides whether a run is created: an approved, open
proposed change skips the start. The worker route releases values only to a claimed execution of a run
(FR-004, R5). So a destination that names a stored credential makes every such start fail with
`credential_unavailable`. Once chunk 13 removes the environment path, that becomes a regression of
spec 021 for every schedule with branch review.

**Options.** (a) Move the read after the claim. (b) Release values to the worker before a run exists.
(c) The Sync API does the read itself through a second worker-only route and returns only the states.
(d) Leave it as a known limit.

**Recommendation sent.** None given in advance; the question was put open.

**Answer.** It is a reversible code question, so the advisor answers it. Option (c): a worker-only
route that takes a schedule id and returns only the proposed-change states. The route reads the
destination address and the proposed-change ids from the stored version and the schedule rows, never
from the request body, so a worker cannot send the token to an address it chooses. No value leaves
the Sync API, the same shape as the schema read that FR-004 already allows. Do not add (b). Option (a)
changes spec 021 because the read decides whether a run is created; the code confirmed this. Update
the worker account's route list and its boundary tests, FR-004 and the worker account text in
`spec.md` and `contracts/http-api.md`, and the T057 decision record text. List it under Risks as a
wider worker boundary. Run the full unit tier at HEAD, check the three chunk 7 tests that chunk 8
changed, check the registration refusal against the contract, and check that a test covers the
readiness read made without a token.

**What I did next.** The three tests still assert a refusal (they now delete or change the credential
after the run is accepted). The HTTP registration refusal is `configs-validation` with a `reason`, the
existing pattern; the findings come from the validate route; the contract wording is made exact in
chunk 8b. `test_the_destination_of_a_scheduled_start_is_read_without_a_token` covers the readiness
read. Then I dispatched chunk 8b for option (c).

### A3. Chunk 13 refuses every earlier version, and T049 stays open (2026-11-21, about 03:00)

**Context.** Chunk 13 refuses any stored version that holds a `credentials` key, including an empty
one. The earlier build wrote `"credentials": {}` into every version, so every version registered
before this change is refused with `credential-format-removed`. Its schedules fail at each start, and
its saved plans cannot be approved or applied. `spec.md:259` refuses only a version that "still uses
them". The wording "empty or not" in `contracts/http-api.md:139` was written by chunk 13 itself
(`22eb0d61`). Chunk 13 also left T049 open. Outside a registered package, `select_runtime_credential`
still reads environment variables, and those include token and password names such as
`NETBOX_TOKEN` and `CISCO_APIC_PASSWORD`. Inside a registered package it reads the settings only.

**Options for the refusal.** (a) Keep the wider refusal and correct the spec. (b) Ignore an empty
block when reading a stored version, and refuse only a non-empty block or a `$credential` reference.

**Recommendation sent.** None given in advance; the question was put open.

**Answer.** Check three things first:
- who wrote "empty or not";
- whether a stored version keeps its checksum when an empty block is ignored;
- whether an empty-block version can still get a token by any path.

If narrowing keeps the checksum and such versions can run, narrow the refusal (b) in a follow-up
chunk. Otherwise keep (a), correct `spec.md:259` and `research.md:209`, and list the upgrade effect
under Risks. In both cases, T060 and T062 must state the same.

For T049, remove the token, password and username names from the environment loop, keep the
address reads, or tick T049 with a test if the loop reads addresses only.

The same follow-up chunk also does the following:
- checks that the read routes (version, runs, results, artifacts) still answer 200 for a version in
  the removed format;
- makes schedule creation and change answer `422 credential-format-removed`, not
  `503 configuration-version-invalid`.

The web interface text for the new code goes to chunk 19.

**What I did next.** "Empty or not" came from chunk 13, so it is not evidence for the wider reading.
The loop does read token and password names, outside a registered package only. I dispatched chunk
13b with the checks above.

**Result of chunk 13b.** The narrowing keeps the checksum, so it was applied (option b). The earlier build's
digest differs from this build's only by the empty `credentials` key, so a stored version is now
digested with that key, and a test pins a checksum the earlier build wrote. A version that used a
secret is still refused, because the earlier build needed a `$credential` reference for every secret.
Registering the same file again creates a new version with a different checksum. T049 stays open:
the direct Prefect flow (`orchestration/flow.py:214`, `execution.py:785`) builds adapters outside a
registered package and needs the environment reads, and `spec.md:334` says that flow does not change.
The read routes answer 200 for a version in the removed format; schedule creation and change answer
`422 credential-format-removed` (commits `fa957165`, `2412a942`, `ca183709`, `f189abdd`).

## Progress

- 2026-11-20 evening: implementation started.
