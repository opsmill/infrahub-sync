# 40. A v2 configuration converts to a package locally, by v2's own rules

**Status**: Accepted; partly superseded on 2026-11-21 by
[ADR 0057](0057-the-sync-api-stores-credentials-and-releases-them-to-the-worker-account.md) — the converted package names one stored credential in each block that uses
authentication (`credential: <name>`), in place of `$credential` references and a `credentials`
block, and the report lists the credentials to create. The body below is unchanged.
**Date**: 2026-11-14
**Source**: `dev/specs/archive/006-v2-configuration-conversion/research.md` (R1 to R4, R6 to R9),
`dev/specs/archive/006-v2-configuration-conversion/spec.md` (FR-006, FR-019, FR-020, FR-030)

## Context

A team that ran infrahub-sync v1 or v2 has a `config.yml`. V3 registers only a configuration
package: the same mapping under a `configuration` key, with a format version, credential
references instead of credential values, and only the settings each bundled adapter accepts.
Converting by hand took six steps.

v2 did not take every value from the file. Its adapters read environment variables first, in a
fixed order for each adapter: for example `NETBOX_ADDRESS`, then `NETBOX_URL`, then the file. v2
did not expand `${NAME}` placeholders. The v2 ACI example holds `url: ${CISCO_APIC_URL}` and
worked only because the adapter read `CISCO_APIC_URL` before the file. Some adapters used a
credential only for one authentication method. A v2 `source` or `owner` group name was looked
up as a `CoreAccountGroup`. A converted package must write what v2 used, or the first V3 plan
would not match the data v2 wrote.

The team runs the conversion where its v2 environment and its credentials are. The conversion
must not copy a credential value into the package, the report or a log.

## Decision

`infrahub-sync configs convert <path>` converts one v1 or v2 `config.yml` into one package, on
the machine where it runs:

- The conversion is a pure function, `convert_v2_configuration` in
  `infrahub_sync/configuration/conversion.py`. It takes the parsed file, the visible v2
  variables and the address flags, and returns either a package or a list of refusals, never
  both. It reads no file and no environment, and prints nothing. The `configs convert` command
  in `cli.py` reads the file, prints the report, asks for confirmation and writes.
- The command does not call the Sync API. It opens no network connection, except with
  `--live-schema`
  ([ADR 0041](0041-an-unmapped-ip-namespace-converts-to-the-default-namespace.md)).
- One reader handles v1 and v2 files. A v1 file is a v2 file without the `incremental` key.
- Fixed tables reproduce what v2 did at its `2.0.2` tag:
    - `V2_ENVIRONMENT_FIRST` lists, for each adapter, the settings v2 read from the environment,
      the variables in v2's order, and whether each is an address, a credential or another
      value.
    - `V2_AUTHENTICATION_METHODS` and `V2_FIXED_CREDENTIALS`
      (`conversion_authentication.py`) list which credential settings each v2 authentication
      method used. A credential setting gets a reference when v2 used it, or when `config.yml`
      holds a value or a placeholder for it.
- A `${NAME}` placeholder that names one of the variables v2 read for that setting counts as no
  value in the file, and the variables are read in v2's order. A placeholder in any other
  setting that is not a credential is refused, because v2 used the placeholder text as the
  value.
- A v2 `source` or `owner` group name becomes `{kind: CoreAccountGroup, name}`, the kind v2
  looked up. The report says that V3 stops the run when the group does not exist, where v2 fell
  back to an account.
- The command reads only the fixed, non-credential variable names of `V2_ENVIRONMENT_FIRST`
  (`visible_v2_variables`). The environment is filtered to those names before the conversion
  runs. A variable that the file names in a placeholder or a `*_env_vars` setting is not read,
  and the report says so.
- The command never reads, prints or writes a credential value. Each credential becomes a
  reference named `<adapter>-<setting>` with the variable
  `INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_<SETTING>`; the Infrahub token uses
  `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`. An address that holds a user name or a
  password is refused without being printed. A YAML error reports only the line and the
  column.
- Before it writes, the command runs every registration check that needs no connection
  (`parse_configuration_package`, then `collect_findings`). An error finding is a refusal, and
  nothing is written.
- The command writes through a temporary file in the target directory. Without `--force`, it
  does not replace an existing file, even one created while the question waited for an answer.

## Consequences

- A team converts and registers its v2 configuration without editing the package, and the
  report names the origin of every value and the variable to set for every credential.
- The tables record what v2 did at its `2.0.2` tag, and v2 receives no more changes. They change
  only when a reading of the released v2 code shows a row is wrong.
  `tests/configuration/test_conversion.py` holds the rows of `V2_ENVIRONMENT_FIRST` a second
  time and fails when the two differ.
- A converted package references only the credentials that v2 used, and those that
  `config.yml` holds a value or a placeholder for.
- A configuration with a custom adapter, a destination other than Infrahub, or a setting the
  bundled adapter does not accept is refused with every reason at once.
- `genericrestapi` reads the generic names `URL` and `ADDRESS`, as v2 did. Another program can
  set them, so the report warns about an address taken from one of them.
- This record does not cover what the first V3 plan of a converted package holds. The check of
  that plan is
  [ADR 0042](0042-the-v2-gate-passes-on-no-creates-and-reviewed-lists.md).

## Alternatives considered

- **A separate reader for v1 files.** Rejected: the only difference is the `incremental` key,
  which v1 lacks.
- **A reference for every credential setting the adapter declares.** Rejected: V3 refuses a
  referenced variable that is missing or empty, so a converted `genericrestapi`, `prometheus` or
  `slurpitsync` package would not run until the team set secrets that v2 never used.
- **Expanding any `${NAME}` placeholder.** Rejected: an input file could then make the command
  read any variable, including a credential.
- **Letting a placeholder move its variable to the front of the order.** Rejected: with
  `url: ${NETBOX_URL}` and both variables set, it would pick `NETBOX_URL`, while v2 used
  `NETBOX_ADDRESS`.
- **Refusing `*_env_vars` settings.** Rejected: they say only where v2 looked for a value, and
  the command already applies that; they are dropped and listed in the report.
