---
title: "V2 configuration conversion"
---

## V2 configuration conversion

`infrahub-sync configs convert` reads one v1 or v2 `config.yml` and writes one V3 configuration
package. What the command does for each key, the order in which it reads variables, its refusals
and its exit statuses are on [Move a V2 configuration to V3](../../v2-to-v3.mdx) and in the
[CLI reference](../../reference/cli.mdx). This page explains how the code is built, for a
developer who changes it. The reasons are in
[ADR 0040](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0040-a-v2-configuration-converts-locally-by-v2-rules.md)
and
[ADR 0041](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0041-an-unmapped-ip-namespace-converts-to-the-default-namespace.md).

### Modules

| Module | Owns |
|---|---|
| `infrahub_sync/configuration/conversion.py` | `convert_v2_configuration`, the result types, `V2_ENVIRONMENT_FIRST`, `COMPOSE_WORKER_CREDENTIAL_VARIABLES` and the connection-free checks before writing |
| `infrahub_sync/configuration/conversion_authentication.py` | `V2_AUTHENTICATION_METHODS` and `V2_FIXED_CREDENTIALS`: which credential settings each v2 authentication method used |
| `infrahub_sync/configuration/conversion_live_schema.py` | `with_live_schema`: the schema read without a token and the IP namespace fill |
| `infrahub_sync/configuration/conversion_report.py` | The lines of the report, of the refusals and of the next steps |
| `infrahub_sync/cli.py` (`configs convert`) | Reading the file, the visible variables, the confirmation and the write |

`configs convert` does not call the Sync API, so the client matrix of
`tests/cli/test_parity_and_closure.py` has no row for it.

### The conversion is a pure function

`convert_v2_configuration` takes the parsed file, the visible v2 variables and the two address
flags. It reads no file and no environment, and prints nothing. It returns a `ConversionResult`
that holds either a package mapping or one or more refusals, never both, and the report
entries.

- Every refusal in the input is gathered before the result is returned.
- The registration checks (`parse_configuration_package`, then `collect_findings`) run only when
  the input has no other refusal. An error finding becomes a refusal; a warning finding goes in
  the report.
- Input that is not a v1 or v2 configuration at all, such as a file that already declares
  `format_version`, raises `ConversionInputError`, which the command maps to exit status 2.
- The package names one stored credential in each block that uses authentication
  (`credential: <name>`). It has no credential setting, no `credentials` block and no value.
- No report entry carries a credential value. A `CredentialEntry` names the stored credential to
  create, the fields it needs, the command that creates it and where each v2 value came from. A
  `RefusalEntry` for a credential setting holds no value.

The command passes the conversion only the variables that `visible_v2_variables` returns: the
`address` and `other` names of `V2_ENVIRONMENT_FIRST`. The conversion never receives a
credential variable.

### Fixed tables of v2 behaviour

v2 receives no more changes, so the tables record what v2 did at its `2.0.2` tag and do not
follow the current adapters:

- `V2_ENVIRONMENT_FIRST`: for each adapter, each setting v2 read from the environment, its
  variables in v2's order, and whether it is an address, a credential or another value.
  `tests/configuration/test_conversion.py` holds each row a second time and fails when the two
  differ.
- `V2_AUTHENTICATION_METHODS` and `V2_FIXED_CREDENTIALS`: which credential settings v2 used for
  each authentication method. The report notes each credential setting the method did not use.
  When the file holds a value for such a setting, the value is left out and the note says so,
  because a block whose method needs no credential cannot name one.
- The settings a stored credential fills, and the fields it needs, come from
  `credential_field_map` in `capabilities.py`, the function that registration checks a named
  credential against. `credential_name` builds the name:
    - `<adapter>-<setting>` when the credential fills one setting, for example `netbox-token`.
    - `<adapter>-account` when it fills a username and a password, for example `aci-account`.
    - A `credential` key that the input block already holds is kept when it can be a credential
      name.
- `COMPOSE_WORKER_CREDENTIAL_VARIABLES` is built from `COMPOSE_WORKER_CREDENTIAL_SUFFIXES` in
  `credentials.py`, and `conversion.py` imports it again under the same name. The conversion no
  longer reads it.
  `tests/configuration/test_conversion_worker_variables.py` fails when the Compose file and the
  constant differ; see
  [Pin a code constant to the deployment file it mirrors](../guidelines/testing.md#pin-a-code-constant-to-the-deployment-file-it-mirrors).

Change a row only when a reading of the released v2 code shows it is wrong.

### The `--live-schema` fill

`with_live_schema` runs on a result that holds a package. It removes the `token` setting of the
destination from a copy of the package, reads the destination schema through the
`destination_schema_accessor` of the destination adapter, and fills each unmapped IP namespace that a uniqueness
constraint names. After a fill, it runs the registration checks again on the filled package. A
failed read adds a note and returns the package unchanged. `cli.py` imports the module only
when the flag is given.

### How the file is written

The command writes the package to a temporary file in the target directory, then moves it into
place. With `--force` the move replaces the target. Without it the move is a hard link, which
fails when the target appeared while the question waited for an answer; on a file system without
hard links the file is created exclusively instead. A YAML error in the input names only the line
and the column, because the parser's message can quote a credential.

### Tests

- `tests/configuration/test_conversion.py`: the tables, the naming and the conversion core.
- `tests/configuration/test_conversion_examples.py`: the example files of `tests/data/v2_examples/`.
- `tests/configuration/test_conversion_secrets.py`: a known test credential value is absent from
  the package, the output and the logs.
- `tests/configuration/test_conversion_live_schema.py`: the IP namespace fill.
- `tests/cli/test_convert_command.py`: the command, its confirmation and its exit statuses.
- The nightly `v2-gate` suite: see [Testing tiers](../guidelines/testing-tiers.md).
