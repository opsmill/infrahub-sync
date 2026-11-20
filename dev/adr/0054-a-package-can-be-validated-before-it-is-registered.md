# 54. A package can be validated before it is registered

**Status**: Accepted
**Date**: 2026-11-19
**Source**: `dev/specs/025-register-config-web/spec.md` (FR-001 to FR-006, FR-020, FR-021,
decisions D-8 and D-9),
`dev/specs/025-register-config-web/research.md` (R1, R2, R3, R8, R10),
`dev/specs/025-register-config-web/critiques/critique-20261118.md` (E1, E2)

## Context

Before this decision, an operator could validate only a registered version, with
`POST /configs/{config_id}/versions/{registry_version}/validate` and `configs validate`. To learn
whether a package file had findings, the operator registered it first, and every registration
creates an immutable version. A registration that failed reported `configs-validation` and
stored nothing, so no route could report findings for that file.

Why validation accepted only a registered version is not known. No specification or decision
record in this repository states a reason. The specification of the configuration registry that
first defined validation is not in `dev/specs/archive/`, and spec 007 (FR-034 to FR-038) extends
the route without saying anything on registered and unregistered packages.

The code did not need a registered version. `validate` in
`infrahub_sync/product_store/configs.py` read the stored version, parsed its declared content and
ran every check on the parsed package. Only the report's `config_id`, `registry_version` and
`package_checksum` came from the stored version.

With a branch, validation builds an Infrahub client from the package's
`destination.settings.url` and resolves the package's `token` credential reference from the
service environment (`_resolved_client_settings` in
`infrahub_sync/configuration/capabilities.py`). For a registered version this is safe, because
only an administrator registers. For a package that the caller sends, the caller chooses the
address that receives the service's destination token.

## Decision

The Sync API validates a package that is not registered, with `POST /configs/validate`. The CLI
calls it with `infrahub-sync configs check PACKAGE`, and the Python client with
`SyncClient.check_package`.

- **One set of checks.** `_check_parsed_package` in `infrahub_sync/product_store/configs.py` runs
  every check on one parsed package. `validate` calls it for a registered version and
  `check_package` for a package that is not registered. For the same package, branch and
  destination state, both return the same findings (FR-002).
- **The refusals of registration.** Before the checks, `check_package` parses the package and
  checks its credentials as registration does, and raises the same refusal with the same status,
  code and reason.
- **Nothing is stored.** The route stores no version, records no audit event for a check that it
  accepts, and takes no `Idempotency-Key`.
- **Administrator only.** The route refuses a token that is not an administrator token with
  403 `forbidden`, before it parses the package in the body or reads the destination, and records the refusal
  with `audit_refusal` and the operation `check-config-package`, as registration records
  `register-config`. A body that is not valid JSON is refused with 422 `request-invalid` before the token check, as
  on every other route of the Sync API, and no destination read happens. Reason: with a branch, the service sends its destination token to the URL
  that the package declares, so only the people who can register a package can choose that URL.
- **The service parses the file.** The body holds exactly one of `package`, a JSON object as the
  registration routes take, or `document`, the text of a YAML or JSON file. The service parses
  `document` with `yaml.safe_load` (`_document_package` in
  `infrahub_sync/service/config_routes.py`), the parser the CLI uses. The web interface has no
  YAML parser, and one parser for the CLI, the service and the web interface keeps their results
  equal. The response returns the parsed package as the store would keep it (`declared_content`).
- **A v2 file gets a named refusal.** A body with no `configuration` key and with top-level
  `source` and `destination` keys gets 422 `validation` with the reason `v2_configuration`, and a
  message that names `infrahub-sync configs convert`.
- **The destination read is a field.** The response returns `destination_read`: `not_requested`
  without a branch, `read`, `not_read` when the destination adapter declares no schema read (no
  current adapter does, because the Sync API refuses such a destination first), or the reason the read failed (`timeout`, `unreachable`,
  `unauthorized`, `forbidden`, `nobranch`, `rejected`, `unconfigured`, `credentials`). The existing
  `destination-schema-read-failed` finding stays, because before this decision the reason was
  only in its message text.
- **`configs check` exits 1 on an error finding** on any page of findings, with or without
  `--branch`. A warning, such as the one reported while no worker has published its credential
  variables, does not change the exit code. `configs validate` keeps its rule: exit 1 only on an
  error finding, and only with `--branch`.

## Consequences

- An operator can view the findings of a package file before registering it, and register only a
  file that has no error finding. Warnings are listed and do not block registration.
- A token that is not an administrator token can validate a registered version but cannot check an
  unregistered package.
- A file that registration refuses gets the same refusal from the check, so the check does not
  name the defect behind a `configs-validation` refusal either.
- `yaml.safe_load` expands aliases, so a crafted `document` can use much memory. Only
  administrators reach the parser, and they can already register any package. The risk is
  accepted.
- The Sync API sets no size limit on the request body. A proxy in front of it may answer 413,
  and the web interface shows a message for that answer.

## Alternatives considered

- **The access rule of the existing validate route, which accepts any authenticated token.**
  Rejected: a token that is not an administrator token could declare its own destination URL and
  receive the service's destination token.
- **Parsing the file in the web interface.** Rejected: it adds a YAML parser to the web interface,
  and a second parser can read a file differently from the CLI and the service.
- **A second set of checks for unregistered packages.** Rejected: the findings of the two routes
  could differ for the same package.

## The "registered without validation" mark

When the check reports `destination_read` as `timeout` or `unreachable` and no finding other than
`destination-schema-read-failed`, the web interface lets an administrator register the package
anyway, with "Register without validation" (FR-020). The version records that it was registered
that way (FR-021).

### Decision

- **The request states the mark.** `POST /configs` and `POST /configs/{config_id}/versions`
  accept `registered_without_validation`, `false` by default. The Sync API records the value that
  the request sends and does not check it against a validation result. The web interface sends
  it as `true` only from "Register without validation". The CLI has no option to set it.
- **The mark is stored in its own table.** A row in
  `configuration_versions_unvalidated (config_id, registry_version)` marks a version. The primary
  key is both columns, with a foreign key to `configuration_versions`. The store writes the row in
  the transaction that inserts the version. Version resources return
  `registered_without_validation`, `true` when the row exists.
- **The table is created with `CREATE TABLE IF NOT EXISTS`.** The store creates its tables this
  way at start-up and has no column migration (`_initialize` in
  `infrahub_sync/product_store/store.py`). A database created before this change gains the empty
  table at its next start-up, so its existing versions read as not marked.
- **The mark joins the idempotency fingerprint only when it is `true`.** A key reused with a
  different mark is refused with `idempotency-conflict`, as a key reused with a different reason
  is. A request without the mark keeps the fingerprint that receipts stored before the mark
  existed were given, so retries of those requests still replay.
- **Only a created version is marked.** When the package is identical to a version already
  stored for the configuration (`created: false`), the Sync API returns that version unchanged and
  ignores the mark.

### Consequences

- An operator who views a version in the web interface, in `configs show --version` or in a
  registration response can tell that it was registered while the destination could not be
  reached.
- A version without the mark is not known to be validated. `configs register` and
  `configs version` do not validate a package before registering it, and versions registered
  before this change have no mark.

### Alternative considered

- **A `registered_without_validation` column in `configuration_versions`.** Rejected. A column
  added to the `CREATE TABLE` statement does not reach a table that already exists, because
  `CREATE TABLE IF NOT EXISTS` does not change it. On a database created by the first alpha
  release, every registration would then fail with an unknown-column error. Spec 019 met the same
  limit with its `plan_step` column, where the cost was only a missing timing. Adding a column
  migration for SQLite and PostgreSQL would be the store's first, which is a larger change than
  this mark needs.
