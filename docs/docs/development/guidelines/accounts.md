---
title: "Accounts in records and answers"
---

## Accounts in records and answers

<!-- Extracted from dev/specs/archive/022-local-accounts on 2026-11-18 -->

Rules for code that records which account acted, returns it in an answer of the Sync API, or
changes an account. How the accounts work is in
[Local accounts and sign-in](../knowledge/local-accounts.md); the reasons are in
[ADR 0049](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md)
and
[ADR 0050](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0050-a-record-holds-an-account-identifier-and-a-deletion-empties-the-account-row.md).

### Record the account identifier, never the name

Write `Principal.actor`, which holds the account identifier (`acct_` and 32 hexadecimal
characters), in every record that says who acted: runs, approvals, configuration versions,
audit events and stored answers. Compare identifiers in every rights check. Do not write
`Principal.name` to the store.

A record that holds the identifier keeps it after the account is deleted. The Sync API then
presents it as `Deleted account <short identifier>`, and a new account with the same name does not
match it. A name written to a record instead would stay after the deletion, and a new account with
the same name would match it and receive the rights of the deleted account on that record.

### Return an account identifier only in a field that `AccountNameMiddleware` presents

`AccountNameMiddleware` replaces an account identifier with the account name only in the fields
`actor`, `approved_by` and `registered_by`, and in the `id` of a resource of kind `account`.

- Name a new answer field that holds an account identifier with one of these names, or add its
  name to `ACCOUNT_FIELDS` in `infrahub_sync/service/presentation.py`.
- Add a route case for each new response model with such a field to `_ANSWERS` in
  `tests/service/test_account_presentation.py`.
  `test_every_response_model_with_an_account_field_has_a_route_case` fails until you do.
- Do not resolve names inside a route. `AccountNameMiddleware` also covers answers replayed from
  `mutation_receipts`, which a route does not build.

### Write an account action and its audit event in one transaction

Each accepted account action writes its audit event in the store transaction of the action, as
[ADR 0033](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0033-an-event-is-saved-in-the-transaction-of-its-decision.md)
requires for runs. Give the store method an `event` argument and insert the event with the same
cursor, as `disable_account` and `delete_account` in `infrahub_sync/product_store/store.py` do.
Do not write the event with a second store call after the action.

A store method that inserts a session or an API token locks the account row and checks that the
account is still active in the same transaction (`_LOCK_ACCOUNT` in
`infrahub_sync/product_store/accounts.py`). Keep that check in a new method that creates a
credential for an account.

### Refuse a change to the `admin` account

An account route that changes the password, the tokens, the level or the state of an account
with `environment_managed` set, or deletes it, answers `409 environment-managed-account`. The
environment defines that account, and `sync-api` restores it at its next start. The route that
ends the refusal after repeated failed sign-ins, `POST /accounts/{name}/unlock`, accepts that
account: it is the way to recover `admin` without a restart.

### Name accounts in the web interface

The rule is in `AGENTS.md`, section "Sync frontend".
