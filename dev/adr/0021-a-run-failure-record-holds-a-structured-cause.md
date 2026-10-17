# 21. A run's failure record holds a structured cause

**Status**: Proposed, needs product sign-off (spec 009 FR-030)
**Date**: 2026-10-15
**Source**: `dev/specs/009-plan-problem-reporting` (research R12)

This record replaces the rule in the comment at `infrahub_sync/service/flow.py:723-725` ("A
class name, never a message"). It keeps ADR 0005: the cause is built at the remote boundary
only.

## Context

A failed run records only the name of an error class, because a destination's text "can carry
anything" (`infrahub_sync/service/flow.py:723-725`). A team cannot tell why a run failed
without the worker log (#369). An Infrahub rejection is reported as "a destination GraphQL
rejection", without Infrahub's error code (`infrahub_sync/potenda/__init__.py:231-232`, #370).
Masking credential values in free text was considered and rejected for two reasons:

- A short credential value also matches unrelated text, so masking it changes text that holds
  no secret.
- Masking can turn a published plan into text that is no longer valid JSON.

## Decision

A run's failure record holds a structured cause built only from these fields:

- the stage;
- the failure type, from a fixed list (since 2026-10-17 it includes `destination_peer_missing`
  and `destination_peer_ambiguous` for a peer that Infrahub does not resolve to one object at
  apply time);
- the system role and the adapter name;
- Infrahub's error code (`extensions.code`), and the node kind and attribute from
  `extensions.data` (`node_kind`, and `field_name` or the `fields` of a uniqueness violation);
- Infrahub's own error message, for a rejected write only;
- the kind, record identity and field that a typed Sync exception names;
- the apply progress.

One module, `infrahub_sync/failure_cause.py`, builds the cause, and only the remote boundary
calls it. The module reads typed attributes and exception classes. It never formats the text of
an exception, an address, a header or a traceback. Each field has a length limit.

## Consequences

- Infrahub's message reaches the run record. It describes the write that Infrahub received,
  which is source data.
- A failure of an unknown type records the class name of the innermost error that is not a
  wrapper, and a fixed message.
- The secret-redaction guideline and the comment at `infrahub_sync/service/flow.py:723-725` are
  rewritten to match this record.
- The existing value replacement is unchanged. It no longer protects the run record, because the
  run record holds no free text.

## Alternatives considered

- The error message with credential values masked: rejected for the two reasons in the
  context.
- Class names only (the behaviour before this decision): gives no cause.
- A fixed message for each error code: a code that the list does not hold produces no useful
  message.
