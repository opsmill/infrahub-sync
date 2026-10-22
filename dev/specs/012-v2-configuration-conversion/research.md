# Research: Convert a v1 or v2 configuration and prove the first V3 plan

Every finding below was read on 2026-10-22 from branch `012-v2-configuration-conversion`
(based on `feature/v3-develop` at `8dc921b9`) or from the `2.0.2` and `1.6.1` tags.

## R1. Which v1 and v2 file shapes the command reads

- **Decision**: parse the input with the v2 model's keys (`name`, `store`, `source`,
  `destination`, `adapters_path`, `order`, `schema_mapping`, `diffsync_flags`, `incremental`),
  using the legacy `SyncConfig` that V3 still ships in `infrahub_sync/__init__.py:150`.
- **Rationale**: the `1.6.1` model is the `2.0.2` model without `incremental`
  (`git show 1.6.1:infrahub_sync/__init__.py`, line 69), so one reader covers both. V3's
  package already embeds the same legacy shape under `configuration`.
- **Alternatives considered**: a separate v1 reader. Rejected: no key differs except
  `incremental`, which v1 lacks.

## R2. Where v2 read values from the environment

v2 adapters read an environment variable before the file setting (`2.0.2`, `infrahub_sync/adapters/`).
The command needs this table to apply FR-008, FR-010 to FR-013 and FR-029.

| Adapter | Setting | v2 variables, in v2's order | Kind |
| --- | --- | --- | --- |
| `infrahub` | `url` | `INFRAHUB_ADDRESS`, `INFRAHUB_URL` | address |
| `infrahub` | `token` | `INFRAHUB_API_TOKEN` | credential |
| `netbox` | `url` | `NETBOX_ADDRESS`, `NETBOX_URL` | address |
| `netbox` | `token` | `NETBOX_TOKEN` | credential |
| `nautobot` | `url` | `NAUTOBOT_ADDRESS`, `NAUTOBOT_URL` | address |
| `nautobot` | `token` | `NAUTOBOT_TOKEN` | credential |
| `aci` | `url` | `CISCO_APIC_URL` | address |
| `aci` | `username`, `password` | `CISCO_APIC_USERNAME`, `CISCO_APIC_PASSWORD` | credential |
| `aci` | `verify` | `CISCO_APIC_VERIFY` | other (FR-029) |
| `ipfabricsync` | `base_url` | `IPF_URL`, read only when the file setting is empty | address |
| `ipfabricsync` | `auth` | `IPF_TOKEN`, read only when the file setting is empty | credential |
| `prometheus` | `url` | `PROM_URL`, `PROM_ADDRESS` | address |
| `prometheus` | `username`, `password`, `token` | `PROM_USERNAME`, `PROM_PASSWORD`, `PROM_TOKEN` | credential |
| `genericrestapi` | `url` | the `url_env_vars` setting, default `URL`, `ADDRESS` | address |
| `genericrestapi` | `token`, `username`, `password` | `token_env_vars` (default `TOKEN`), `username_env_vars` (`USERNAME`), `password_env_vars` (`PASSWORD`) | credential |
| `peeringmanager` | `url` | `PEERING_MANAGER_ADDRESS`, `PEERING_MANAGER_URL` (generic REST adapter with its own defaults) | address |
| `peeringmanager` | `token`, `username`, `password` | `PEERING_MANAGER_TOKEN`, `USERNAME`, `PASSWORD` | credential |
| `slurpitsync` | none | none | none |

- **Correction during implementation (chunk 2)**: the first version of this table said
  `peeringmanager` reads no variable and that `ipfabricsync` reads the variable first. The `2.0.2`
  code shows otherwise (`peeringmanager.py:31-34`, `ipfabricsync.py:48-56`); the table above
  follows the code. For `ipfabricsync` the file wins, so FR-011's "variable wins" applies only
  where v2 read the variable first.
- **Decision**: keep this table as a constant in the conversion module, with a unit test per row.
  For `genericrestapi`, the `*_env_vars` settings are read to build the variable list and are not
  copied: V3 does not accept them (`_GENERIC_REST_SETTINGS`, `configuration/capabilities.py:135`).
  The report says they were dropped. Their entries are **not** read (critique E1): only the
  fixed names in this table are read, so an input file cannot make the command read another
  variable. A value taken from `genericrestapi`'s generic `URL` or `ADDRESS` is reported as a
  warning, because other software may set those names (critique X1).
- **Rationale**: the table is fixed history (v2 is frozen at `2.0.2`). Reading v2 code at runtime
  is not possible, because V3 does not ship it.
- **Alternatives considered**: refusing `*_env_vars`. Rejected: the settings say only where v2
  looked, and the command already applies that.
- **Note**: `genericrestapi`'s default names `URL` and `ADDRESS` are generic. The command follows
  v2, so an unrelated `URL` variable where the command runs wins over the file. The report always
  shows the origin and both values (FR-011), which makes this visible.

## R3. `${VARIABLE}` placeholders

- **Finding**: v2 does not expand `${VARIABLE}` (`2.0.2`, `infrahub_sync/utils.py` loads YAML with
  `yaml.safe_load` and nothing else). The v2 ACI example still holds `url: ${CISCO_APIC_URL}` and
  `verify: ${CISCO_APIC_VERIFY}`. It worked only because the ACI adapter read those variables first.
- **Decision**: a placeholder in a setting from the R2 table that names one of that setting's v2
  variables counts as no value in the file, and the variables are read in v2's order. v2 never
  expanded the placeholder, so the name in it did not change which variable v2 used (spec FR-011).
  A placeholder that names any other
  variable is not read; the report says so (spec FR-030, critique E1). A placeholder in a credential setting is reported as the v2
  origin. A placeholder in any other setting is refused (FR-015), because v2 sent the literal text.
- **Spec change**: FR-029 and an edge case were added to `spec.md`, because otherwise SC-002 fails
  on the ACI example.
- **Correction during implementation review (2026-10-22)**: the first decision moved the variable
  a placeholder names to the front of the list. With `url: ${NETBOX_URL}` and both
  `NETBOX_ADDRESS` and `NETBOX_URL` set, that picked `NETBOX_URL`, while v2 used `NETBOX_ADDRESS`.
  The product owner decided that v2's fixed order applies.

## R4. Credential settings, names and the prefix

- **Finding**: `credential_setting_paths` in `BUILTIN_ADAPTER_CAPABILITIES`
  (`configuration/capabilities.py:505-585`) lists each bundled adapter's credential settings.
  An `env` identifier must start with `INFRAHUB_SYNC_CREDENTIAL_`
  (`configuration/credentials.py:29`, commit `5e30656f`).
- **Decision**: read the credential settings from `BUILTIN_ADAPTER_CAPABILITIES`, not from a
  second table. Reference name `<adapter>-<setting>` (lower case, `_` to `-`); identifier
  `INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_<SETTING>`; Infrahub uses `infrahub-token` and
  `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` (spec FR-006, clarification of 2026-10-22).
- **Correction during implementation review (2026-10-22)**: the first decision gave every declared
  credential setting a reference. V3 refuses a referenced variable that is missing or empty, so
  converted `genericrestapi`, `prometheus` and `slurpitsync` packages could not run until the
  engineer set secrets that v2 never used. A setting now gets a reference when the v2
  authentication method uses it, or when `config.yml` holds a value or placeholder for it. The
  methods, read from `2.0.2`:

  | Adapter | v2 code | `auth_method` default | Credential settings used |
  | --- | --- | --- | --- |
  | `genericrestapi` | `genericrestapi.py:71,108-114`, `rest_api_client.py:32-50` | `token` | `token`, `x-auth-token`, `api-key`, `key`: `token`; `basic`: `username`, `password`; `none`: none; any other value raised an error |
  | `peeringmanager` | `peeringmanager.py:27-28` (generic REST adapter) | `token` | as `genericrestapi` |
  | `prometheus` | `prometheus.py:73,90-95,381-384` | `none` | `bearer`: `token`; `basic`: `username`, `password`; `none`: none |
  | `slurpitsync` | `slurpitsync.py:39-40` | no `auth_method` | `api_key`; the settings go to `slurpit.api(url, api_key, verify, ...)`, which has no `token` parameter |
  | other adapters | | no `auth_method` | every declared credential setting |

  An `auth_method` value outside the table is refused and the setting is named. For
  `prometheus`, v2 did not raise an error on such a value; it sent no credential. The value is
  still refused, as the product owner decided. The table is kept as `V2_AUTHENTICATION_METHODS`
  and `V2_FIXED_CREDENTIALS` in `infrahub_sync/configuration/conversion_authentication.py`. A setting that gets no reference is
  listed in the report as a note: `not referenced: <setting> (auth_method <value> does not use it)`.

## R5. The worker's credential variables (FR-009)

- **Finding**: the `sync-worker` service of `docker-compose.yml` receives three credential
  variables on this branch: the Infrahub, NetBox and Nautobot tokens. An installed CLI has no
  `docker-compose.yml` to read.
- **Decision**: a constant tuple `COMPOSE_WORKER_CREDENTIAL_VARIABLES` in the conversion module,
  and a unit test that parses `docker-compose.yml` and asserts the tuple equals the
  `INFRAHUB_SYNC_CREDENTIAL_*` keys of the `sync-worker` environment. When the Compose work adds
  the other variables, the test fails until the tuple is updated. The warning reads "the Compose
  file shipped with this version does not pass <variable> to the Sync worker" (critique E5).
- **Alternatives considered**: no warning. Rejected by the spec (FR-009).

## R6. Which checks run before writing (FR-020)

- **Finding**: `parse_configuration_package` (`configuration/models.py:913`) and
  `collect_findings` (`configuration/validation.py:782`) need no connection. `collect_findings`
  includes the store check (`validation.py:615`). Registration also refuses a credential
  identifier outside the namespace (`service/service.py:237-240`).
- **Decision**: the command builds the package as a plain mapping, runs
  `parse_configuration_package` then `collect_findings` on it, and treats any error finding as a
  defect of the conversion: it refuses, names the finding code and location, and writes nothing.
  Warning findings are added to the report.

## R7. Bundled adapters and `adapter:` values

- **Finding**: `plugin_loader.py` accepts built-in names, dotted paths, filesystem paths and
  entry points. The bundled package is `infrahub_sync.adapters`.
- **Decision**: `adapter:` is converted with a note only when its value is
  `infrahub_sync.adapters.<name>` or `infrahub_sync.adapters.<name>:<Class>` and `<name>` equals
  the configuration's `name`. Any other value is refused with the INFP-654 reason.
- **Rationale**: string matching needs no import, so the command stays local and loads no code
  from the customer's directory (constitution VI).

## R8. Where the command lives and how it prints

- **Finding**: `infrahub_sync/cli.py` is the Sync API client. `tests/cli/test_parity_and_closure.py`
  pins the commands that call the client and forbids HTTP code in `cli.py`; a local command needs
  no row in that matrix. Output uses `typer.echo`; input errors exit with status 2
  (`ClientInputError`), other client errors with 1.
- **Decision**:
  - A pure module `infrahub_sync/configuration/conversion.py` holds the conversion: input mapping,
    a visible-environment mapping and the two URL overrides in; a result with the package
    mapping or the refusals, and the report entries, out. It does no I/O and prints nothing.
  - `cli.py` adds `configs convert` as a thin command: read the file, call the module, print the
    report, confirm, write.
  - The confirmation is asked only when standard input is a terminal (critique E3).
  - Exit status: 0 when written or when the engineer answers `n`; 1 when refused or when there is
    no terminal and no `--yes`; 2 when the input cannot be read, is not YAML, is already a V3
    package, or the arguments are invalid.
  - The file is written by creating a temporary file in the target directory and renaming it,
    so a failed write leaves nothing behind. Without `--force`, an existing target is refused
    before anything is written.
- **Rationale**: a pure function is testable without a terminal and keeps the CLI module thin,
  as the other commands are.

## R9. Keeping credential values out (FR-007, SC-003)

- **Decisions**:
  - The conversion reads only the non-credential variables of the R2 table from the environment.
    The environment mapping it receives is filtered to those names before the call.
  - A literal credential value in the input is never copied into the report, a refusal or an
    exception. The report names the setting and says "a literal value in config.yml".
  - An address that holds a user name or password is refused without printing it
    (spec FR-030, critique E2).
  - A YAML parse error reports only the line and column. PyYAML messages can quote the failing
    line, which could hold a token.
  - Tests use a canary value, following `tests/test_credential_canary.py`, and assert it is
    absent from the package, stdout, stderr and captured logs.

## R10. The release gate

- **Findings**:
  - The nightly workflow `workflow-nightly-e2e.yml` runs `.github/scripts/nightly_e2e.py` suites
    (`integration`, `preview`, `saved-plan`, `from-netbox`). `from-netbox` seeds the `demo`
    dataset, starts the preview stack, loads `tests/data/nightly_schema`, registers a package,
    creates a branch and plans (`nightly_e2e.py:150-203`).
  - All 20 kinds that the `2.0.2` NetBox example maps exist in `tests/data/nightly_schema` or are
    built-in Infrahub kinds (checked on 2026-10-22).
  - The `2.0.2` example `config.yml` equals the one on `main`.
  - The plan summary the client returns has `by_action`, `by_kind`, `total`,
    `delete_operations_computed` and `deletes_not_executed` (`client/models.py:315`). Records left
    out and problems are not on this branch: they come with spec 009
    (`009-plan-problem-reporting`, not merged into `feature/v3-develop`).
  - The preview worker runs on the host, so host addresses work (`tasks/netbox.py:501-515`).
- **Decision**: add a suite `v2-gate` to `nightly_e2e.py`:
  1. Seed the `demo` dataset, start a fresh preview stack and load the nightly schema, as
     `from-netbox` does.
  2. Create a branch and run v2 `2.0.2` once, isolated from the repository's environment, with
     `uvx --from infrahub-sync==2.0.2 infrahub-sync sync --name from-netbox --directory <tmp>`.
     The `2.0.2` example is copied into `<tmp>`. `NETBOX_ADDRESS`, `NETBOX_TOKEN`,
     `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` point at the local NetBox and the preview
     Infrahub, and the `branch` destination setting is set to the gate branch.
  3. Run `infrahub-sync configs convert <tmp>/config.yml --yes` with the same `NETBOX_ADDRESS` and
     `INFRAHUB_ADDRESS` visible. This exercises the environment-first rule.
  4. Register the package, run `diff` on the branch, read `runs plan --detail`.
  5. Compare with `tests/data/v2_gate/expected_deletes.yml` and write `gate-result.json`.
  6. Fail when creates, updates, an unlisted delete, records left out or problems are above 0.
- **Dependency**: the left-out and problem checks need spec 009 merged. Until then the suite
  stops with "records left out and problems cannot be counted on this build", rather than
  passing, so the gate cannot report a pass it did not check.
- **Installation of v2**: v1 and v2 were distributed only as the `infrahub-sync` package on
  PyPI (product owner, 2026-10-22), so the suite installs `infrahub-sync==2.0.2` from PyPI, as a
  customer did. No Git-tag fallback.
- **Alternatives considered**: a pytest integration test. Rejected: the `from-netbox` check is
  already a nightly script, not a pytest test (`testing-tiers.md`), and the gate needs the same
  stack.

## R11. Documentation on this branch

- **Finding**: on this branch the pages are `docs/docs/v2-to-v3.mdx`,
  `docs/docs/configuration-package.mdx` and the generated `docs/docs/reference/cli.mdx`
  (`tasks/docs.py:24`). The V3 docs overhaul (`stack/v3-user-docs`, not pushed) moves them to
  `operate/`, `reference/` and `sync-your-data/`.
- **Decision**: edit the pages at their paths on this branch. Whoever merges the docs overhaul
  moves the new section with its page.
- Changelog: one fragment `changelog/+v2-configuration-conversion.added.md`.
