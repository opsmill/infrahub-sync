# 21. A run's failure record holds a structured cause built from allowlisted fields

**Status**: Accepted
**Date**: 2026-11-12
**Source**: `dev/specs/019-plan-problem-reporting/spec.md` (FR-014 to FR-020, FR-029, FR-030),
`dev/specs/019-plan-problem-reporting/research.md` (R12, R13, R14),
`dev/specs/019-plan-problem-reporting/data-model.md` ("Stage failure record additions")

This record replaces the rule "a class name, never a message" that a comment in
`_failure_evidence` (`infrahub_sync/service/flow.py`) stated before this decision. It keeps
[ADR 0005](0005-translate-run-failures-only-at-the-remote-boundary.md): the record is built at
the remote boundary only, by the service worker.

## Context

When a run fails, the service worker writes a `results.<stage>_failure` record, which
`GET /runs/{run_id}`, `GET /runs/{run_id}/results`, the CLI and `runs show` return. Before this
decision the comment in `_failure_evidence` said the record holds the class name of the error
and of its immediate cause, and never a message, because a destination's text can hold
anything. The code no longer matched the comment: spec 017 already recorded fixed-template
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
  class without a template;
- the system involved: the role (`source` or `destination`) and the adapter name;
- Infrahub's `extensions.code`, `extensions.data.node_kind`, `extensions.data.field_name` and
  `message`, for a write that Infrahub rejected;
- the permission or message Infrahub returns with a 403, as spec 017 records it;
- the kind, the source record and the field that a typed Sync error names;
- the operations written, the operation count of the plan, and the failed operation's action,
  kind and identity;
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
- A credential value cannot enter the record, whatever its length or characters, because no
  field is built from connection settings or from error text (FR-029). This protection does not
  depend on replacing values in text.
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
