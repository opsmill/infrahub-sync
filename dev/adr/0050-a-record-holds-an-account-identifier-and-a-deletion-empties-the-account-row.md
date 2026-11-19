# 50. A record holds an account identifier, and a deletion empties the account row

**Status**: Accepted
**Date**: 2026-11-18
**Source**: `dev/specs/archive/022-local-accounts/spec.md` (decisions D-6, D-8 and D-10,
FR-024, FR-030 to FR-033), `dev/specs/archive/022-local-accounts/research.md` (R1, R7, R8),
`dev/specs/archive/022-local-accounts/critiques/critique-20261117.md` (Q1, E1, E2)

## Context

Runs, approvals, configuration registrations and audit events record the caller that acted.
Before local accounts ([ADR 0049](0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md)),
they held the caller's name, from the variable that listed the tokens. The stored answers that
the Sync API replays (`mutation_receipts.response_body`) held the same name.

An administrator can now delete an account. A deletion must remove the account's name from the
account data that Sync stores, must not change a past run, approval or audit event, and must
not give a new account with the same name any right over the runs of the deleted one. A request
that is in progress when the account is deleted can still write a record after the deletion.

## Decision

- **A record holds an account identifier.** Each account gets an identifier of the form `acct_`
  followed by 32 hexadecimal characters when it is created, and a short identifier of 8
  hexadecimal characters. The existing text columns that held a name, and the stored answers,
  hold the identifier. No existing table changes. Rights compare identifiers.
- **One boundary turns identifiers into names.** `AccountNameMiddleware`
  (`infrahub_sync/service/presentation.py`) presents every JSON answer of the API application,
  whether a route just built it or it is replayed from a stored receipt. It replaces the value
  of `actor`, `approved_by` and `registered_by`, at any depth, and the `id` of a related
  resource or of a primary resource of kind `account`, when the value has the form of an account
  identifier. It reads the accounts it needs with one store call for each answer. An answer that
  is not `application/json`, or that carries a `Digest` header (an artifact), is passed on
  unchanged.
    - An active or disabled account is shown by its name.
    - A deleted account is shown as `Deleted account <short identifier>`. The short identifier is
      the same on every record of that account.
    - A value that is not an account identifier, such as a name in a record made before this
      change, is shown as it is stored and gives no right to any account.
- **A deletion empties the account row.** In one transaction, the deletion:
    - sets the name, the lower-case name key and the password hash of the row to empty
      (`NULL`) and sets its deletion time; the row keeps the identifier and the short
      identifier;
    - deletes the account's tokens, its sessions and the row of failed sign-ins for its name;
    - writes its audit event. A failure of the event rolls the deletion back.

  No other record changes. A deletion cannot be undone.
- **What keeps the name.** Text that a person typed (`reason`, `approval_reason`,
  `registration_reason`) and log lines are not changed.
- **This keeps the rule of [ADR 0037](0037-an-approval-is-a-record-on-the-run.md)** that an
  approval is never changed: `approved_by` holds the identifier before and after a deletion, and
  only the way it is displayed changes.

## Consequences

- A record written after a deletion by a request that was in progress carries the identifier of
  the deleted account. It displays the placeholder and gives no right to anyone, so no write
  needs a check of the account.
- A deleted name is free again, because the deletion empties the name key. A new account with
  that name gets a new identifier, so it has no right over the old runs, and no old record
  displays it.
- A disabled account keeps its name key, so its name stays taken and its records keep showing
  its name.
- An answer that does not pass through the middleware would show an identifier. A test builds
  every response model that has an account field and checks its answer.
- A search of everything Sync stores finds the name of a deleted account only in text that a
  person typed.

## Alternatives considered

- **Keep the name in records and rewrite it at deletion.** This was the first design. Rejected
  after the critique (Q1, E1, E2): it rewrites several columns and the stored JSON of
  `mutation_receipts`, it changes `approved_by`, which ADR 0037 says is never changed, and every
  request in progress can write the old name after the deletion, so a new account with that name
  would own the run.
- **Resolve names in each route.** Rejected: about ten routes and two replay paths would each
  need it, and a new route could forget it. One boundary is tested once against every response
  model.
