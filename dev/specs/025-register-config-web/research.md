# Research: Register a configuration package from the web interface

Each entry gives the decision, the reason and the alternatives. File references are to this
repository at commit `87d5a47f`.

## R1. Why validation accepts only a registered version

- **Finding**: not known. No specification or decision record in this repository states a reason.
  [Spec 007](../archive/007-plan-problem-reporting/spec.md) FR-034 to FR-038 extend the existing
  route and say nothing on registered versus unregistered packages. The registry specification that
  first defined validation is not in `dev/specs/archive/`.
- **What the code shows**: `validate` in
  [`product_store/configs.py`](../../../infrahub_sync/product_store/configs.py) (line 863) reads the
  stored version, parses its `declared_content` with `_parse(..., refuse_store=False)`, then runs
  `collect_findings`, and with a branch `collect_destination_schema_findings` and the worker
  credential comparison. Every check works on the parsed `ConfigurationPackage`. Only the report's
  `config_id`, `registry_version` and `package_checksum` come from the stored version.
- **Decision**: extract the checks into one function that takes a parsed package and returns the
  findings and the schema fingerprint. `validate` (registered) and the new `check_package`
  (unregistered) both call it. This makes FR-002 (same findings, same order) true by construction.
- **Alternative rejected**: register into a temporary store and validate there. It writes, needs
  cleanup, and gives nothing the shared function does not.

## R2. Where the browser's file is parsed

- **Finding**: the web interface has no YAML parser (`frontend/package.json` runtime dependencies:
  `@infrahub/ui`, TanStack, `openapi-fetch`, React, Tailwind). The CLI parses with
  `yaml.safe_load` (`_package` in [`cli.py`](../../../infrahub_sync/cli.py), line 340).
- **Decision**: the new route accepts either `package` (a JSON object, as the registration routes
  do) or `document` (the file's text, YAML or JSON). The service parses `document` with
  `yaml.safe_load`, the same parser as the CLI. The response returns the package exactly as the
  store would keep it (`declared_content`). The web interface registers that object, not the raw
  file. Replaced by spec decision D-12 on 2026-11-20: the web interface registers the file's text
  as `document`, because the browser's JSON serialization of `declared_content` changes numbers.
- **Reason**: no new frontend dependency; one parser for CLI, service and web interface; and the
  comparison in the review step uses the same normalized content the store keeps, which satisfies
  SC-003 by construction (see R5).
- **Alternative rejected**: add a YAML library to the web interface. It is a new dependency, and a
  second YAML parser could read some files differently from `yaml.safe_load`.

## R3. How an unreachable destination is reported today

- **Finding**: `_infrahub_call` in
  [`configuration/capabilities.py`](../../../infrahub_sync/configuration/capabilities.py) (line 253)
  raises `DestinationSchemaReadError` with `reason` set to `timeout`, `unreachable`,
  `unauthorized`, `forbidden`, `nobranch`, `rejected` (the destination answered, but the SDK raised
  an error the other reasons do not cover, or the response is not a schema Sync can use) or
  `unconfigured` (the declared settings cannot configure a client); `_resolved_client_settings`
  (line 194) adds `unconfigured` (no destination URL) and `credentials` (the token reference does
  not resolve).
  [`schema_validation.py`](../../../infrahub_sync/configuration/schema_validation.py) (lines 282 and
  374) turns it into a finding with code `destination-schema-read-failed`. The reason is only in the
  message text.
- **Decision**: keep the finding (FR-002), and also return a structured `destination_read` value on
  the new route: `not_requested` (no branch), `read`, or the failure reason. The web interface
  treats `timeout` and `unreachable` as "the destination cannot be reached" (FR-004, FR-020). It
  treats `unauthorized`, `forbidden`, `nobranch`, `rejected`, `unconfigured` and `credentials` as ordinary
  findings that block registration,
  because they describe a problem the operator must fix, not an outage.
- **Alternative rejected**: parse the reason from the finding message. Message text is not a
  contract.

## R4. Validation findings have two severities

- **Finding**: `_finding` in [`validation.py`](../../../infrahub_sync/configuration/validation.py)
  (line 169) builds only `error` findings, but `ValidationFinding.severity` in
  [`models.py`](../../../infrahub_sync/configuration/models.py) accepts `error` and `warning`.
  `configuration/warnings.py` reports `intentional-omission` warnings, and
  [`configs.py`](../../../infrahub_sync/product_store/configs.py) reports the
  `worker-environment-unknown` warning while no worker has published its credential variables.
  The first version of this note read only `validation.py` and concluded that every finding is an
  error; the code review on 2026-11-19 found that this is wrong.
- **Decision**: "Valid" means the report has no `error` finding. Warnings are listed and do not
  block registration, and `configs check` exits 1 only on an `error` finding (spec decision D-10).

## R5. Comparing a dropped file with the newest version

- **Finding**: the web interface compares two stored versions with `diffMappings` in
  [`domain/mapping.ts`](../../../frontend/src/domain/mapping.ts), reading
  `declared_content.configuration.schema_mapping`. The store keeps
  `ConfigurationPackage.declared_content()`, a `model_dump(mode="json", by_alias=True)` with
  defaults filled (`configuration/models.py`, line 633).
- **Decision**: the review step compares the `declared_content` returned by the new route with the
  newest stored version's `declared_content`, using `diffMappings` unchanged. Both sides are the
  same normalized form, so the comparison after registration is identical (SC-003).

## R6. Request size limit

- **Finding**: no request body limit was found in `infrahub_sync/service/`. The bundle limit
  `MAX_BUNDLE_BYTES` (64 MiB, `product_store/bundle.py`) applies to run bundles, not to requests. A
  reverse proxy in front of the service can apply its own limit and answer HTTP 413.
- **Decision**: no new limit (spec Edge Cases). The Sync API sets no size limit on the request
  body, so it never answers 413. A proxy in front of it may answer 413, and the web interface shows
  "The file is larger than the Sync API accepts" for that answer.

## R7. Default validation branch

- **Finding**: a run resolves the Infrahub branch as the adapter's `settings.branch`, then the run's
  branch, then `main` (`adapters/infrahub.py`, line 86; `configuration/runtime.py`, line 30).
- **Decision**: the review step uses `declared_content.configuration.destination.settings.branch`,
  or `main` when it is not set (D-5).

## R8. Storing the "registered without validation" mark

- **Finding**: the store creates its tables with `CREATE TABLE IF NOT EXISTS` and has no column
  migration (`_initialize` in [`product_store/store.py`](../../../infrahub_sync/product_store/store.py),
  line 869). Adding a column to `configuration_versions` would be the first column migration, for
  both SQLite and PostgreSQL.
- **Decision**: a new table, `configuration_versions_unvalidated (config_id, registry_version)`,
  primary key on both, foreign key to `configuration_versions`, created with
  `CREATE TABLE IF NOT EXISTS`. A row means the version was registered without validation. The row
  is written in the same transaction as the version. Existing deployments get the empty table at
  start-up.
- **Alternative rejected**: a new column declared in `CREATE TABLE`. This is the precedent of spec
  019 (`plan_step`, [research R11](../019-second-plan-real-changes/research.md)): PR #425 removed
  the start-up migration, so a database created by an earlier build does not gain the column. For
  `plan_step` that is a missing timing; here every registration would fail with an unknown-column
  error on a database created by the first alpha release, which this feature follows. A new table
  created with `IF NOT EXISTS` reaches existing databases at start-up.
- **Credential check (for `check_package`)**: the store runs `validate_package_credentials(package)`
  before inserting a version (`ProductProjection.create_configuration`, `store.py` line 3824, and
  the version path at line 3842). `check_package` calls the same function, so it gives the same
  refusal without writing.
- **Idempotency**: `ConfigurationRoutes.mutate` fingerprints `package` and `reason`
  (`config_routes.py`, lines 318 to 331) and refuses a key reused with a different fingerprint.
  The web interface therefore locks the review step's inputs after the first attempt (FR-017), and
  `registered_without_validation` joins the fingerprint.

## R9. Route address

- **Finding**: `CONFIG_ID_PATTERN` (`config_routes.py`, line 60) accepts `validate` as a
  configuration ID, but no route answers `POST /configs/{config_id}`; only `GET` does.
- **Decision**: `POST /api/configs/validate`. It cannot be confused with a configuration, because the
  only route on `/configs/{config_id}` is a `GET`.

## R10. Access rule for the new route

- **Finding**: the existing validate route accepts any authenticated token; registration requires an
  administrator (`config_routes.py`, lines 458 to 551).
- **Second finding (critique E1)**: with a branch, validation builds a client from the package's
  `destination.settings.url` and resolves the package's `token` credential reference from the
  service environment (`_resolved_client_settings` in `configuration/capabilities.py`, line 194).
  For a caller-supplied package, the caller would choose the address that receives the service's
  destination token.
- **Decision**: the new route requires an administrator, like registration, and records a refused
  attempt with `audit_refusal`, as registration does (FR-005). A permitted check stores nothing and
  records no audit event, like the existing validate route.
- **Alternative rejected**: the access rule of the existing validate route (any authenticated
  token). It is safe only because an administrator registered the package being validated.

## R11. A version registered meanwhile

- **Finding**: `POST /configs/{config_id}/versions` takes no expected version.
- **Decision**: the web interface reads the configuration's latest version just before registering
  (FR-018). A registration between that read and the request is still possible. Adding an expected
  version to the API was rejected for this feature: the remaining window is a few milliseconds, and
  the new version's page shows its number and comparison.

## R12. Testing the web interface

- **Finding**: the web interface has no test runner. Its checks are `biome check` and `tsc --noEmit`
  (`frontend/package.json`).
- **Decision**: put the logic that must be tested in the service (parsing, v2 detection,
  normalization, reachability, the mark) and test it with pytest. Check the web interface with
  `biome check`, `tsc --noEmit` and the scenarios in [quickstart.md](quickstart.md). Adding a
  frontend test runner is a new dependency and is left out.

## R13. Drop area and keyboard use

- **Finding**: `@infrahub/ui` has no drop area component (its `src/components` lists autocomplete,
  breadcrumbs, button, card, checkbox, label, list-box, menu, meter, modal, popover, select, sheet,
  spinner, tooltip and tree, among others).
- **Decision**: use the browser's drag events and a "Choose a file" button backed by a file input,
  so the action works without a mouse. Use the `@infrahub/ui` modal for the review step.
