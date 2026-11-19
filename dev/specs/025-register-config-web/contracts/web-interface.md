# Contract: Web interface behavior

## Where the drop areas are

| Page | Drop area label | Effect |
|---|---|---|
| Configurations | "Register a configuration: drop a package file here, or choose a file" | Opens the review step for a new configuration. |
| One configuration | "Add a version: drop a package file here, or choose a file" | Opens the review step for the next version of this configuration. |

With a token that is not an administrator token, both are shown disabled with
"Registering needs an administrator token". The "choose a file" button makes the action usable
without a mouse.

## Messages before the review step

| Case | Message |
|---|---|
| More than one file | "Drop one package file." |
| Empty, or not YAML or JSON (reason `invalid_package`) | "This file is not a YAML or JSON configuration package." |
| v2 configuration (reason `v2_configuration`) | "This is a v2 configuration. Convert it with `infrahub-sync configs convert`, then drop the package here. Converting a v2 configuration in the web interface is planned for a later release." |
| 413, answered by a proxy in front of the Sync API (the Sync API sets no size limit) | "The file is larger than the Sync API accepts." |
| Any other refusal | The Sync API's message. |

## Review step content

1. Title: "Register a new configuration" or "Add version N+1 to <name>".
2. Configuration name, source adapter, destination adapter.
3. When the name differs from the current name (new version): "This version renames the
   configuration from A to B."
4. When a configuration with the same name exists (new configuration): "A configuration named A
   already exists." with a link "Add a version to it instead".
5. Comparison with version N (new version only), with the existing mapping comparison view.
6. Credential references: the variable names only.
7. Validation: branch field (default from the package, else `main`) and the result:
   - "Valid against branch B" when there are no findings;
   - the list of findings, otherwise;
   - "Could not validate: the destination cannot be reached" when `destination_read` is `timeout`
     or `unreachable`. Every other failure reason (`unauthorized`, `forbidden`, `nobranch`,
     `rejected`, `unconfigured`, `credentials`) comes with a `destination-schema-read-failed`
     finding and is shown in the list of findings, because the destination answered or the
     package needs a change;
   - "Validating against branch B…" while the request runs, with Cancel available.
   The review step requests the largest findings page (256). When `total_findings` is larger, it
   adds "N more findings. Run `infrahub-sync configs check` to list them all."
8. Reason, optional, at most 512 characters (the existing limit).
9. Register and Cancel. Register is enabled only when the result is "Valid", or when the
   destination cannot be reached; in that case its label is "Register without validation"
   (FR-020). With User Story 4, that registration also sends `registered_without_validation: true`.
10. When the validation request fails (no answer, or an error that is not a refusal of the
    package): "Validation did not complete." with "Validate again"; Register stays disabled.

## After Register

| Result | Behavior |
|---|---|
| Version created | Open the new version's page. |
| `created: false` | "This package is the same as version N. No new version was registered." |
| Newer version found before sending | "Version M was registered meanwhile." The comparison reloads. |
| Network failure | "The request did not complete. Retry." Retrying sends the same request with the same idempotency key; the reason field and the other inputs stay locked after the first attempt, because the Sync API refuses a key reused with a different request. |
| 403 | The existing "administrator access is required" handling. |

No text names a user or an actor.
