# 21. A run's failure record holds a structured cause built from allowlisted fields

**Status**: Accepted
**Date**: 2026-11-12, amended 2026-11-23 (see [Amendment, 2026-11-23](#amendment-2026-11-23-a-write-that-could-not-take-the-write-lock))
**Source**: `dev/specs/archive/007-plan-problem-reporting/spec.md` (FR-014 to FR-020, FR-029, FR-030),
`dev/specs/archive/007-plan-problem-reporting/research.md` (R12, R13, R14),
`dev/specs/archive/007-plan-problem-reporting/data-model.md` ("Stage failure record additions")

This record replaces the rule "a class name, never a message" that a comment in
`_failure_evidence` (`infrahub_sync/service/flow.py`) stated before this decision. It keeps
[ADR 0005](0005-translate-run-failures-only-at-the-remote-boundary.md): the record is built at
the remote boundary only, by the service worker.

## Context

When a run fails, the service worker writes a `results.<stage>_failure` record, which
`GET /runs/{run_id}`, `GET /runs/{run_id}/results`, the CLI and `runs show` return. Writing it
is best effort (`_record_failure` in `infrahub_sync/service/flow.py`): when the store cannot
record it, or the worker no longer holds the run, the run has no failure record, the worker
reports `failed` and points to `runs show`, and the store keeps the run nonterminal until
reconciliation decides its outcome. Before this
decision the comment in `_failure_evidence` said the record holds the class name of the error
and of its immediate cause, and never a message, because a destination's text can hold
anything. The code no longer matched the comment: spec 012 already recorded fixed-template
texts for a lineage refusal and a destination schema read failure, and the permission or
message Infrahub returns with a 403.

A team could not tell from the record why a run failed. A source that refused the token, an
Infrahub that rejected a write with an error code, and a record that the source adapter could
not load all showed one class name, and the team had to read the worker log (#369).

Replacing credential values in error text was considered and rejected:

- The masking pass needs at least six characters (`MIN_SECRET_LENGTH`), so a shorter credential
  stays in the text.
- The text of an adapter, an HTTP library or a configuration parser can hold an address with a
  user name and password, a request header or a traceback (FR-019).

## Decision

A failure record holds the class name of the error and of its immediate cause, unchanged, and
a structured cause built only from these fields (FR-014, FR-019):

- the stage and a failure type from a fixed list of 17 names, `internal_error` for an error
  class without a template (amended 2026-11-23: the list gains `write_guard_contended`; see the
  amendment below);
- the system involved: the role (`source` or `destination`) and the adapter name;
- Infrahub's `extensions.code`, `extensions.data.node_kind`, `extensions.data.field_name` and
  `message`, for a write that Infrahub rejected;
- the permission or message Infrahub returns with a 403, as spec 012 records it;
- the kind, the source record and the field that a typed Sync error names;
- the operations written, the operation count of the plan, and the failed operation's action,
  kind and identity;
- (amended 2026-11-23) `active_run_id`, for a write that could not take the configuration's
  write lock: see the amendment below;
- text from a fixed template per failure type, filled with the fields above, setting names,
  kind names, node names, account names, branch names and IDs.

`failure_record` in `infrahub_sync/service/failure_cause.py` maps the cause chain to a failure
type through a fixed table of error classes and HTTP status codes, and reads typed attributes
only. It never formats the text of an exception. Each value is stripped of control characters
and capped: 128 characters for a code, a kind, an attribute or a field, 1,000 characters for
Infrahub's message and for a template text. The keys the record held before this decision keep
their values.

## Consequences

- Infrahub's own message of a rejected write reaches the run record (FR-016). It describes the
  write that Infrahub received. It still goes through the masking pass.
- No field is built from connection settings or from error text, except the messages Infrahub
  returns: the message of a rejected write and the permission or message of a 403 (FR-029).
  Through the other fields, a credential value cannot enter the record, whatever its length or
  characters, and this protection does not depend on replacing values in text. A credential
  that Infrahub repeats in one of its messages can reach the record: the masking pass that the
  message of a rejected write goes through does not replace a value shorter than
  `MIN_SECRET_LENGTH`.
- The masking pass is neither extended nor removed (FR-030). The worker log and the Prefect
  logs keep the masked error text until version 3.0.
- The comment in `_failure_evidence` and the
  [secret redaction guideline](../../docs/docs/development/guidelines/secret-redaction.md)
  state this rule.
- An adapter error that the classification table does not name records `internal_error`, with
  the system set when the error happened while one side was read. Adding a failure type means
  adding a typed attribute or an error class to the table, and a template.
- A record written by an older Sync API has none of the new fields. The CLI prints it as before.

## Alternatives considered

- The error text with credential values masked: rejected for the reasons in the context.
- Class names only, as the comment stated: gives the team no cause.
- A fixed message for each Infrahub error code: a code that the list does not hold would
  produce no useful message, so Infrahub's own message is recorded instead.

### Amendment, 2026-11-23: a write that could not take the write lock

A `sync`, `apply` or `runs resume` waits for the configuration's write lock (a PostgreSQL
advisory lock, described in the apply guard knowledge page). When another session holds the
lock for the whole wait, the write does not start and changes nothing. Its failure record now
has its own failure type and names the run that held the lock when that can be proven.

- **Failure type.** The fixed list gains `write_guard_contended`. Since this record was
  written, the scheduled runs that write to a branch for review also added seven types
  (`destination_default_branch_not_main`, `branch_list_failed`, `branch_create_failed`,
  `branch_delete_failed`, `proposed_change_open_failed`, `proposed_change_read_failed`,
  `proposed_change_close_failed`). The list therefore holds 25 names. The Sync HTTP API
  reference lists all of them.
- **Allowed field.** The record may hold `active_run_id`: a run ID read from the label of the
  database session that holds the configuration's write lock, and confirmed in the run records.
  Each write-lock session sets its label (`application_name`) to
  `infrahub-sync-guard:<run ID>` before it requests the lock. After a lock timeout, the waiting
  session reads the label of the one session that holds the same lock in the same database. The
  worker accepts the run ID only when the label has that exact prefix and a run ID of the run
  records' form, the run ID is not the failing run's own, and the run records hold a run with
  that ID for the same configuration. Otherwise `active_run_id` is `null` and the cause says
  that the run is not known. The record stores only the run ID, never the label.
- **Templates.** Three fixed templates fill the cause and the next action: the named run is
  still writing, the named run's write has ended but its worker still holds the lock, and the
  run is not known.

Consequences of the amendment:

- The label is not private. On PostgreSQL 16 with `track_activities` on, a session of another
  account that is not a superuser, connected to the same database, read the holder's label
  (integration test `test_a_holder_of_another_account_is_named_correctly_or_not_known`). Any
  account that can connect to the Sync service's database can therefore read the run ID in the
  label. A run ID is not a secret, and the label never holds anything else.
- The label is not trusted. Any session that can connect to the Sync service's database can set
  its own label and take the same advisory lock, so it can present a false run ID. The check in
  the run records limits a false label to a real run of the same configuration. This risk is
  accepted: the record then names a wrong run of the same configuration, and no value other
  than a run ID can reach the record.
- A write that takes the lock does no extra work. The lookup runs only after a lock timeout: one
  query on the waiting session, limited to 2 seconds and accepted only when it returns exactly
  one row, and one read of the run records.
