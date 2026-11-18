# Quickstart: validate and register a configuration package from the web interface

These scenarios prove the feature end to end. Routes and messages are defined in
[contracts/sync-api.md](contracts/sync-api.md) and [contracts/web-interface.md](contracts/web-interface.md).

## Prerequisites

- The full development profile: `uv sync --extra dev --extra prefect --extra service`.
- The development stack with Infrahub: `uv run invoke dev.start` (preset `infrahub`).
- The web interface built and served by the Sync service, or `uv run invoke dev.frontend`.
- An administrator token and a token that is not an administrator, both in the Sync API's token file.
- A package file, for example the output of `infrahub-sync configs convert` on an example in
  `examples/`.

## Automated checks

```bash
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
cd frontend && pnpm check && pnpm typecheck
```

## Scenario 1: validate without registering (User Story 1)

1. `infrahub-sync configs list` and note the count.
2. `infrahub-sync configs check package.yml --branch main`.
3. Expected: the report prints `destination_read: read` and the findings, and `configs list` shows
   the same count.
4. Edit the package to map an attribute that does not exist; run the check again. Expected: one
   finding names the kind and the attribute; exit status 1.
5. Register the package and run `configs validate <config> 1 --branch main`. Expected: the same
   findings as step 4.

## Scenario 2: add a version from the web interface (User Story 2)

1. Open a configuration's page with the administrator token. Drop a modified package.
2. Expected: the review step shows the name, source, destination, the mappings changed compared with
   the newest version, the credential variable names, and "Valid against branch main".
3. Enter a reason and click Register. Expected: the page of version N+1 opens, and its comparison
   with version N matches what the review step showed.
4. Drop the same file again and register. Expected: "This package is the same as version N+1. No new
   version was registered."

## Scenario 3: register a new configuration (User Story 3)

1. On the Configurations page, drop a package whose name is already used. Expected: the warning and
   the "Add a version to it instead" link.
2. Drop a package with a new name and register. Expected: the new configuration's page with
   version 1.

## Scenario 4: destination unreachable (User Story 4)

1. Stop Infrahub (`docker compose stop` on the Infrahub service of the development stack).
2. Drop a package. Expected: "Could not validate: the destination cannot be reached" and a
   "Register without validation" button.
3. Register. Expected: the version page and `infrahub-sync configs show <config> --version N` show
   `registered_without_validation: true`.

## Scenario 5: refusals and access

- Drop two files. Expected: "Drop one package file."
- Drop a v1 or v2 `config.yml` from `examples/`. Expected: the v2 message naming
  `infrahub-sync configs convert`.
- Drop a text file that is not YAML. Expected: the "not a YAML or JSON configuration package"
  message.
- Use the token that is not an administrator token. Expected: both drop areas are disabled with
  "Registering needs an administrator token".
