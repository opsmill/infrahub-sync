# Feature Specification: Name the run that holds a configuration's write lock

**Feature Branch**: `028-write-lock-holder`

**Created**: 2026-11-23

**Status**: Draft

**Input**: User description: "Name the run that holds a configuration's write lock (INFP-647 acceptance criterion 7).
When a sync, apply or runs resume times out waiting for the configuration's PostgreSQL advisory write lock, the
failure record and the CLI must name the run that actually holds the lock, without exposing secrets." The full
input, with the design agreed with the advisor and its conditions, is recorded in
[alignment-check.md](alignment-check.md) once Phase 5 runs.

## Context

Infrahub Sync lets only one run write to the destination of a registered configuration at a time. A run that
writes (`sync`, `apply` or `runs resume`) waits up to 30 seconds by default for the configuration's write lock.
When the wait ends without the lock, the run fails and changes nothing in the destination.

Branch `fix/write-guard-names-active-run` (commits `cf9f13b6` and `a50d5780`) already records this failure as
`write_guard_contended`, adds an `active_run_id` field to the failure record, and makes the CLI print
`active run: <run_id>` with a hint to run `infrahub-sync runs show <run_id>`. That branch picks the run from the
run records: the other run of the same configuration whose write was claimed first and has not ended. That is a
guess. It can name a run that is itself waiting, or a run whose worker stopped without ending its record. This
feature replaces the guess with the run that holds the lock.

## Clarifications

### Session 2026-11-23

- Q: Must the real-PostgreSQL test (A holds the lock, B and C wait, C names A) pass on every pull request, or is
  the nightly run enough? → A: Run the new PostgreSQL tests on every pull request, with a PostgreSQL service
  container in CI, and keep them in the nightly job too.
- Q: Should the web interface link to the run that holds the lock on the failed run's page? → A: Yes. The web
  interface is treated like the CLI: it names the holding run with a link to that run's page, and shows the "not
  known" and "has ended" cases.
- Q: How many extra database reads may a failed write make to name the holding run? → A: At most two: one on
  the lock session (holder and label) and one of the run records (run, configuration and whether its write has
  ended); none for a write that takes the lock.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An operator sees which run blocked their write (Priority: P1)

An operator, or an automation that calls the CLI, starts a `sync` or an `apply` for a configuration. Another run
of the same configuration is writing to the destination. The operator's run waits, times out and fails. The CLI
prints the ID of the run that held the lock, so the operator can view that run with `infrahub-sync runs show`,
wait for it to finish, and plan again.

**Why this priority**: This is INFP-647 acceptance criterion 7: "A concurrent conflicting CLI run is refused
safely and identifies the active Sync run without exposing secrets." Naming the wrong run sends the operator to
check a run that is not the cause.

**Independent Test**: Against a real PostgreSQL server, run A holds the lock of configuration X while runs B and
C wait for it. When C's wait ends, C's failure record and the CLI output name A, not B.

**Acceptance Scenarios**:

1. **Given** run A holds the write lock of configuration X, **When** run C's `sync` waits for the lock and the
   wait ends, **Then** C's failure record has `failure_type` `write_guard_contended` and `active_run_id` equal to
   A's run ID, and the CLI prints `active run: <A>` and the hint to view A with `infrahub-sync runs show`.
2. **Given** run A holds the lock and run B is also waiting for it, **When** C's wait ends, **Then** C names A,
   never B.
3. **Given** run A holds the lock, **When** an `apply` or a `runs resume` of run C times out, **Then** C names A
   in the same way, and the `runs resume` hint still names `verify` and then `runs resume`.
4. **Given** C's failure names A, **When** an operator views run C in the web interface, **Then** the failure
   notice names run A with a link to A's run page, and the next action is the same as the CLI's.

---

### User Story 2 - An operator is told when the run cannot be named (Priority: P1)

When Infrahub Sync cannot prove which run holds the lock, the failure says that the run is not known, instead of
naming a run that may be wrong. The failure is still recorded and the write still changes nothing.

**Why this priority**: A wrong run ID is worse than no run ID. It is part of the same acceptance criterion.

**Independent Test**: Against a real PostgreSQL server, the session that holds the lock has no label, a label in
another format, or a label that the reading session cannot see. The failing run records `active_run_id` as empty
and the cause says which run holds the lock is not known.

**Acceptance Scenarios**:

1. **Given** the lock is held by a session that has no Infrahub Sync label, **When** C's wait ends, **Then** C's
   failure record says the run that holds the lock is not known.
2. **Given** the label names a run ID that does not exist in the run records, or a run of another configuration,
   **When** C's wait ends, **Then** C's failure record says the run is not known.
3. **Given** the label may have been cut short by PostgreSQL's length limit, **When** C's wait ends, **Then** C's
   failure record says the run is not known.
4. **Given** the lookup of the holder fails (for example, the database connection drops), **When** C's wait
   ends, **Then** C's failure record is still stored as `write_guard_contended` with the run not known, and the
   lookup failure never replaces the original failure.
5. **Given** the holder releases the lock between the end of C's wait and the lookup, **When** C reads the
   holder, **Then** C's failure record says the run is not known.

---

### User Story 3 - An operator is told when the named run has already ended (Priority: P2)

The lock is held by the session of a run whose record shows that it has ended, for example because the lock was
not released after the run finished. The CLI names that run and says that it has ended, instead of telling the
operator to wait for it.

**Why this priority**: Waiting for a run that has ended never helps. This case is rare, but the hint must not be
wrong.

**Independent Test**: In a test, the lock is held by a session labelled with a run whose record has a finish
time. The failure names that run and the next action does not say to wait for it.

**Acceptance Scenarios**:

1. **Given** the session that holds the lock is labelled with run A, and A's record shows that A has ended,
   **When** C's wait ends, **Then** C's failure names A, says that A's write has ended, and its next action tells
   the operator that the lock is released only when the worker of run A stops, so the administrator of the Sync
   service must stop or restart that worker, then plan again.

---

### Edge Cases

- Two configurations are written at the same time. Their locks do not contend, and no failure is recorded.
- The PostgreSQL server holds several databases. Only a lock held in the Sync service's own database can name a
  run.
- The run ID contains only characters that PostgreSQL keeps unchanged in a session label. Run IDs generated by
  the service have 22 characters (`YYYYMMDDTHHMM-` and 8 hexadecimal characters). The run records accept IDs of
  up to 128 characters, which do not fit in a label.
- A session outside Infrahub Sync that uses the same database account sets a label that looks like an Infrahub
  Sync label. The run it names is accepted only if that run exists and belongs to the same configuration.
- The failing run's own session holds a label with its own run ID. The holder is never the failing run.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A write of a run (`sync`, `apply`, `runs resume`) MUST label its write-lock database session with
  the run ID before it requests the lock, so that another session can read which run holds the lock.
- **FR-002**: When a write cannot take the lock before its wait ends, the service MUST read which database
  session holds that configuration's lock in the Sync service's database, and read that session's label, before
  it closes its own session.
- **FR-003**: The service MUST accept a label only when it has the exact Infrahub Sync prefix followed by a value
  that has the format of a run ID, and only when that run exists in the run records and belongs to the same
  configuration. Otherwise the run is not known.
- **FR-004**: The service MUST treat a label that may have been cut short by the length limit of a session label
  as not known.
- **FR-005**: The failure record MUST store only the run ID in `active_run_id`, never the raw label or any other
  value read from the database session.
- **FR-006**: A failure of the holder lookup MUST NOT replace or change the `write_guard_contended` failure; the
  failure is stored with the run not known. The lookup MUST end within a short, fixed time (2 seconds); a lookup
  that takes longer gives "not known".
- **FR-006a**: The service MUST name a run only when exactly one session holds that configuration's lock in the
  Sync service's database; any other result gives "not known".
- **FR-007**: When the named run's record shows that it has ended, the failure MUST say so, and its next action
  MUST NOT tell the operator to wait for that run.
- **FR-008**: The lookup of the run from the run records (the earliest claimed write that has not ended) MUST be
  removed, not kept as a fallback.
- **FR-009**: The CLI output for a contended write MUST stay as it is on branch `fix/write-guard-names-active-run`
  (`active run: <run_id>` and the `runs show` hint) when a run is named, and MUST show the "not known" and "has
  ended" texts in the other cases.
- **FR-010**: The secret-redaction guideline MUST be corrected: the failure record now reads the label of the
  database session that holds the lock, and the guideline MUST say which database accounts can read that label.
- **FR-011**: The decision on whether ADR 0021 (failure records hold a structured cause) needs an amendment for
  the `write_guard_contended` type and the `active_run_id` field MUST be recorded, with its reason.
- **FR-012**: Refusing a second write when it is submitted is out of scope (ADR 0010). A write still waits for the
  lock and fails only when the wait ends.
- **FR-013**: The real-PostgreSQL tests of this feature MUST run on every pull request, against a PostgreSQL
  service container in CI, and MUST also stay in the nightly end-to-end job. A pull request whose holder lookup
  fails these tests MUST NOT show green checks.
- **FR-014**: The web interface MUST show a contended write the same way the CLI does. When a run is named, the
  failure notice of the failed run names it with a link to that run's page. When the run is not known, or its
  write has ended, the notice shows the same meaning as the CLI's "not known" and "has ended" texts. The web
  interface reads only the failure record the Sync API returns (`active_run_id`, cause, next action); it never
  reads the session label.

### Key Entities

- **Write-lock session**: the dedicated database session that holds a configuration's write lock for the length
  of one write. It now carries a label with the run ID of the write.
- **Failure record of a contended write**: the stage failure of the run whose wait ended. It holds
  `failure_type` `write_guard_contended`, `active_run_id` (a run ID or empty), a cause and a next action from a
  fixed template.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the test with one run holding the lock and two runs waiting, repeated 20 times, the run whose
  wait ends names the holding run every time and never names the other waiting run.
- **SC-002**: In every case where the holder cannot be proven (no label, a label in another format, a label that
  may be cut short, a run that does not exist or belongs to another configuration, a failed lookup, a lock
  released before the lookup), the failure says the run is not known, and no case names a wrong run.
- **SC-003**: The failure record and the CLI output for a contended write contain no database address, account
  name, password or raw session label; the shared secret-leak test cases pass for the new failure cases.
- **SC-004**: The holder lookup adds at most two database reads to a write whose wait has ended: one on the lock
  session (which session holds the lock and its label) and one of the run records (whether the run exists, belongs
  to the same configuration, and whether its write has ended). It adds nothing to a write that takes the lock.

## Assumptions

- The Sync service uses one database account for its run records and for its write-lock sessions, so its
  sessions can read each other's labels. PostgreSQL shows all of a session's details only to the same account, a
  superuser, or an account with `pg_read_all_stats`; the test must cover a label that cannot be read.
- Run IDs generated by the service (`generate_run_id`) fit in a session label. A run ID that does not fit is
  treated as not known; no run ID is shortened to fit.
- The write lock stays a PostgreSQL advisory lock as described in
  `docs/docs/development/knowledge/apply-guard.md`; this feature does not change how the lock is taken or
  released.
- The web interface reads the same failure record through the Sync API, so it receives the same `active_run_id`,
  cause and next action. Today it shows the cause and the next action
  (`frontend/src/domain/run-state.ts`, `frontend/src/features/runs/run-notices.tsx`) but no link to another run;
  FR-014 adds the link and the "not known" and "has ended" cases.
- Any session that uses the service's database account can set a label. The check in the run records limits a
  false label to a real run of the same configuration; only the Sync service uses that account. This remaining
  risk is accepted and recorded in the secret-redaction guideline and in ADR 0021.
- The real PostgreSQL tests follow the existing integration tests of the write lock
  (`tests/integration/test_apply_guard_integration.py`, `APPLY_GUARD_TEST_POSTGRESQL_DSN`). Today those tests run
  only in the nightly end-to-end job (`.github/scripts/nightly_e2e.py`); this feature adds a pull-request CI job
  that sets the DSN for the new tests (FR-013).
