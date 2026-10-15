# Feature Specification: Convert a v2 configuration to a V3 package, and a first V3 plan with no false changes

**Feature Branch**: `012-v2-configuration-conversion`

**Created**: 2026-10-15

**Status**: Draft

**Release**: MVP (the testing release). Every requirement in this specification belongs to the MVP.
Work it names as out of scope belongs to 3.0 or 3.x, as listed in
[V3 release scope](../v3-release-scope.md).

**Input**: User description: "Move a v1 or v2 user to V3 for the testing release. Today
docs/docs/v2-to-v3.mdx says a V2 config.yml must be brought into the V3 package envelope by
hand, and that the first V3 plan reads objects a V2 run wrote. (1) A CLI command, for example
`infrahub-sync configs convert config.yml`, writes a V3 configuration package from a v2
configuration: token settings become `$credential` references, a `store` block (Redis etc.) is
dropped with a warning, an adapter V3 does not bundle (filesystem path, dotted path,
third-party) is refused with a named reason, and the output registers without edits for the
bundled adapters. It runs locally and does not need the Sync API. (2) Gate: the first V3 plan
against an Infrahub that v2 already synced from the same unchanged source shows no creates and
no updates; every update it does show is a bug. Proven on the NetBox example. (3) Custom
adapters are out of scope (INFP-654, 3.0). Scope = brief FR-008, P3, SC-004."

## Summary

Problem: a customer team that runs Sync v1 or v2 has a `config.yml` and an Infrahub that v2
already filled. To test V3 they must rewrite the file into the V3 package format by hand
(`docs/docs/configuration-package.mdx`, "Convert a config.yml file", lists five manual steps), and
nothing proves that the first V3 plan against the data v2 wrote holds only real changes.
`docs/docs/v2-to-v3.mdx` warns that this plan "can propose creates and updates against objects a
V2 run wrote".

What is needed for the testing release:

- A local command that writes a V3 configuration package from a v2 `config.yml`, which the team
  then registers with no edits when it uses only bundled adapters.
- A release gate: on the NetBox example, the first V3 plan against an Infrahub that v2 synced from
  the same unchanged NetBox holds zero creates and zero updates.

Not decided yet: what the command does with an adapter setting that V3 does not accept (see
FR-010).

Out of the testing release: custom adapters (INFP-654, 3.0), deletes, and moving any v2 state,
cache or generated files.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Convert the v2 NetBox configuration and register it without edits (Priority: P1)

A customer team has the `config.yml` that its v2 installation runs to import NetBox into
Infrahub. On a computer with Infrahub Sync V3 installed, a team member runs the convert command
on that file. The command writes a V3 configuration package next to it and prints what it
changed: the credential references it created, with the environment variable to set on the Sync
worker for each one, and any warnings. The team member sets those variables on the deployment and
registers the package with `infrahub-sync configs register`, without opening the package in an
editor.

**Why this priority**: every other step of journey P3 starts from a registered package. Today
this step is a manual rewrite that the team can get wrong, and registration does not name the
defect when it refuses a package.

**Independent Test**: convert the v2 NetBox example from the `main` branch and register the
output against a V3 deployment. Registration succeeds with no change to the file.

**Acceptance Scenarios**:

1. **Given** the v2 NetBox example `config.yml`, **When** the team runs the convert command on it,
   **Then** the command writes one package file that holds `format_version`, a `configuration`
   object with the v2 name, source, destination and schema mapping, and a `credentials` block, and
   it prints the name of each environment variable to set.
2. **Given** that package and a V3 deployment whose worker has those variables set, **When** the
   team registers it, **Then** registration succeeds and returns a configuration identity and
   version.
3. **Given** a v2 `config.yml` whose source and destination settings hold no token, because v2
   read `NETBOX_TOKEN` and `INFRAHUB_API_TOKEN` from the environment, **When** the team converts
   it, **Then** the package holds a `$credential` reference for the NetBox token and for the
   Infrahub token, and the output names the v2 variable that each reference replaces.
4. **Given** a v2 `config.yml` that holds a literal token value, **When** the team converts it,
   **Then** the package holds a `$credential` reference instead of the value, the value appears
   nowhere in the package or in the command output, and the output tells the team to move that
   value into the named environment variable.

---

### User Story 2 - View why a configuration cannot be converted (Priority: P1)

A team member converts a v2 `config.yml` that V3 cannot run as it is: it names a custom adapter
by a filesystem path, a dotted Python path or a third-party package, or it declares a Redis
store. The command either refuses and names the reason, or converts with a warning, so that the
team knows before registering what V3 does differently.

**Why this priority**: a team that uses a custom adapter must learn that V3 cannot run its
configuration in the testing release, from the command and not from a registration error that
does not name the cause.

**Independent Test**: convert one fixture per refused and per warned case, and check the exit
status, the written files and the message text.

**Acceptance Scenarios**:

1. **Given** a v2 `config.yml` whose source sets `adapter:` to a filesystem path such as
   `./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter`, **When** the
   team converts it, **Then** the command writes no file, exits with a failure status, and names
   the setting, the value and the reason: V3 runs only the adapters it bundles in the testing
   release.
2. **Given** a v2 `config.yml` whose source or destination `name` is not a bundled adapter, or that
   sets `adapter:` to a dotted path or an entry point, **When** the team converts it, **Then** the
   command refuses it the same way and lists the bundled adapter names.
3. **Given** a v2 `config.yml` with a `store` block of type `redis`, **When** the team converts it,
   **Then** the package holds no `store` block, and the command prints a warning that V3 keeps sync
   data in memory during a run and that the block was dropped.
4. **Given** any refusal, **When** the command stops, **Then** it reports every reason it found in
   the file, not only the first one.

---

### User Story 3 - Plan from V3 against the data v2 wrote and view only real changes (Priority: P1)

The team has registered the converted NetBox package. Its Infrahub already holds the objects that
v2 wrote from the same NetBox. The team plans a V3 run against that Infrahub branch and reviews
the plan. When NetBox has not changed since the last v2 sync, the plan holds no creates and no
updates. When NetBox has changed, the plan holds only the operations those changes cause.

**Why this priority**: this is the brief's success criterion SC-004 and journey P3. A plan that
proposes thousands of updates against unchanged data cannot be reviewed, and the team cannot tell
a real change from a difference between v2 and V3.

**Independent Test**: on the pinned NetBox `demo` dataset, sync with the latest v2 release into an
empty Infrahub, convert the same `config.yml`, register it, and run a V3 plan on the same branch.
Count the creates and updates.

**Acceptance Scenarios**:

1. **Given** an Infrahub that the latest v2 release synced from the pinned NetBox `demo` dataset
   with the v2 NetBox example, and no change in NetBox since, **When** the team runs the first V3
   plan with the converted package, **Then** the plan holds zero creates and zero updates.
2. **Given** the same Infrahub, **When** one device is changed in NetBox before the first V3 plan,
   **Then** the plan holds only the operations that this change causes.
3. **Given** the gate run finds an update, **When** the test records the result, **Then** it
   records the kind, the destination identity, each field that differs, and the value v2 wrote
   next to the value V3 would write, so that the difference can be fixed as a defect.

---

### Edge Cases

- A v2 `config.yml` in which a setting holds `${VARIABLE}`, as in the v2 ACI example
  (`password: ${CISCO_APIC_PASSWORD}`): the package holds a `$credential` reference for a
  credential setting, and the output names the v2 variable it replaces.
- A setting that is not a credential and holds `${VARIABLE}`: the command refuses and names the
  setting, because a registered package cannot read the environment.
- A v2 `config.yml` with no `url` for the source or destination, because v2 read `NETBOX_URL` or
  `INFRAHUB_ADDRESS`: the command takes the address from that variable when it is set during the
  conversion and says so in the output; otherwise it refuses and names the setting and the
  variable. A registered V3 package reads no address from the environment. When the variable and
  the file hold different addresses, the command uses the variable, as v2 did, and prints both.
- `adapters_path` at the top level: dropped with a warning when both adapters are bundled. V3
  refuses the key in a registered package.
- `adapter:` on the destination: refused. V3 accepts only the bundled Infrahub adapter as a
  destination that it can write to.
- `order`: copied as it is. V3 accepts it and computes a write order when it is absent.
- `diffsync_flags`: copied as it is. V3 accepts the flag names and never executes a delete.
- `incremental`: copied when the source adapter supports incremental extraction (`infrahub`,
  `nautobot`, `netbox`); dropped with a warning for any other source.
- Generated adapter and model files that `infrahub-sync generate` wrote beside a v2
  `config.yml`: the command does not read them and the package does not need them. The output
  says that V3 has no generation step.
- The output file already exists: the command refuses and writes nothing, unless the team asks it
  to replace the file.
- The input is already a V3 package (it holds `format_version`): the command refuses and says so.
- The input is not valid YAML, or does not match the v2 configuration model: the command refuses
  and names the first location that does not parse.
- Infrahub holds objects that v2 wrote with `source` and `owner` lineage and `is_protected`: the
  first V3 plan proposes no update caused only by those values when the converted package keeps
  the v2 `source` and `owner` settings.
- The first V3 plan records a delete: the gate counts only creates and updates. Apply never
  executes a delete in the testing release. The built-in `default` IP namespace delete is fixed by
  spec 010.

## Requirements *(mandatory)*

### Functional Requirements

#### The convert command (brief FR-008)

- **FR-001**: The CLI MUST provide a command, `infrahub-sync configs convert <path>`, that reads one
  v1 or v2 `config.yml` and writes one V3 configuration package file.
- **FR-002**: The command MUST run on the team's computer without the Sync API, without a network
  connection, and without reading any credential value from the environment.
- **FR-003**: The package MUST hold `format_version`, a `configuration` object and a
  `credentials` block, in the format that `docs/docs/configuration-package.mdx` describes.
- **FR-004**: The package MUST keep, without change, the v2 `name`, the source and destination
  adapter names, `schema_mapping` (with every field, filter, transform and identifier), `order`
  and `diffsync_flags`.
- **FR-005**: For each credential setting that the adapter declares (for example `token` for
  `netbox`, `nautobot` and `infrahub`, `username` and `password` for `aci`, `auth` for
  `ipfabricsync`), the package MUST hold a `$credential` reference and a matching entry in the
  `credentials` block with provider `env` and an identifier that starts with
  `INFRAHUB_SYNC_CREDENTIAL_`. The command MUST add the reference whether the v2 setting held a
  literal value, a `${VARIABLE}` placeholder, or nothing.
- **FR-006**: Reference names and identifiers MUST follow the shipped NetBox package:
  `netbox-token` with `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN`, and `infrahub-api-token` with
  `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`; other adapters use `<adapter>-<setting>` and the
  matching upper-case identifier.
- **FR-007**: The command MUST NOT write a credential value to the package, to its output or to a
  log. For each reference it MUST print the identifier to set on the Sync worker and, when one
  exists, the v2 variable or the literal setting it replaces.
- **FR-008**: The package MUST hold the address that v2 would have used for each adapter. v2
  adapters read an environment variable before the `url` setting (for example `NETBOX_URL` or
  `NETBOX_ADDRESS`, `NAUTOBOT_URL` or `NAUTOBOT_ADDRESS`, `INFRAHUB_URL` or `INFRAHUB_ADDRESS`,
  `CISCO_APIC_URL`, `IPF_URL`, `PROM_URL`). When such a variable is set during the conversion, the
  command MUST use its value and print it, and MUST print both values when the file holds a
  different one. When neither the variable nor the setting holds an address, the command MUST
  refuse and name the setting and the variables.
- **FR-009**: The command MUST drop a `store` block and print a warning that V3 keeps sync data in
  memory during a run. It MUST drop `adapters_path` with a warning when both adapters are bundled.
  It MUST drop `incremental` with a warning when the source adapter does not support incremental
  extraction.
- **FR-010**: For a source or destination setting that the bundled adapter does not accept in a
  registered package, the command MUST
  [NEEDS CLARIFICATION: drop the setting and print a warning that names it, so the package still
  registers, or refuse the conversion and name the setting, because dropping it can change what
  the adapter reads from the source?]
- **FR-011**: The command MUST refuse a configuration whose source or destination is not an
  adapter that V3 bundles: a `name` that is not bundled, an `adapter:` value that is a filesystem
  path, a dotted Python path or an entry point, or a destination that is not `infrahub`. The
  refusal MUST name the setting, the value and the reason, and list the bundled adapter names.
- **FR-012**: On any refusal the command MUST write no file, exit with a failure status, and list
  every reason it found in the file.
- **FR-013**: Before it writes the file, the command MUST run every check that registration runs
  that needs no connection to the Sync API, the source or Infrahub. A package that passes these
  checks MUST register with no edit when the worker's credential variables are set.
- **FR-014**: The command MUST NOT replace an existing file unless the team passes an explicit
  option to replace it.
- **FR-015**: The command MUST refuse an input that already holds `format_version`, and say that
  the file is already a V3 package.
- **FR-016**: The command MUST NOT read, convert or copy the generated adapter and model files that
  v2 wrote beside a `config.yml`, and MUST say that V3 does not need them.
- **FR-017**: The command MUST keep the destination `source` and `owner` settings, so that V3 writes
  carry the same lineage as the v2 writes.

#### The first V3 plan against data v2 wrote (brief P3, SC-004)

- **FR-018**: On the pinned NetBox `demo` dataset, after the latest v2 release has synced the v2
  NetBox example into an empty Infrahub branch, the first V3 plan from the converted package on
  that branch MUST hold zero creates and zero updates.
- **FR-019**: Every create or update that the first V3 plan holds against unchanged source data MUST
  be treated as a defect, and fixed before the testing release, in V3 or in the conversion.
- **FR-020**: The gate MUST record, for each update it finds, the kind, the destination identity,
  each field that differs, and the value v2 wrote next to the value V3 would write.
- **FR-021**: After one device is changed in NetBox, the first V3 plan MUST hold only the operations
  that this change causes.
- **FR-022**: The gate MUST run against a local NetBox with the pinned `demo` dataset, never against
  the public NetBox demo, and MUST be repeatable from documented commands.

#### Documentation

- **FR-023**: `docs/docs/v2-to-v3.mdx` and `docs/docs/configuration-package.mdx` MUST describe the
  convert command in place of the manual steps, list each v2 key and how the command handles it,
  and state the gate result on the NetBox example.
- **FR-024**: The CLI reference MUST document the command, its options, its warnings and its
  refusals.

### Key Entities

- **v2 configuration**: a `config.yml` with `name`, `source`, `destination`, optional `store`,
  `adapters_path`, `order`, `schema_mapping`, `diffsync_flags` and `incremental`. A source or
  destination has `name`, optional `adapter` and `settings`.
- **V3 configuration package**: one file with `format_version`, a `configuration` object and a
  `credentials` block. Registration stores it as an immutable version.
- **Credential reference**: a `$credential` name in a setting, with a `credentials` entry that names
  the provider `env` and the environment variable the Sync worker reads.
- **Conversion report**: what the command prints: each credential reference and its identifier,
  each dropped key with the reason, each address taken from the environment, and each refusal
  reason.
- **Gate result**: the counts of creates, updates and deletes in the first V3 plan, and for each
  update the kind, identity, fields and both values.

### How each v2 key is handled

| v2 key | V3 package | Command behavior |
| --- | --- | --- |
| `name`, `schema_mapping` | accepted | copied |
| `order`, `diffsync_flags` | accepted | copied |
| `source.name`, `destination.name` | bundled adapters only | copied, or refused when not bundled |
| `source.adapter`, `destination.adapter` | refused for filesystem paths and on the destination | refused with the reason |
| credential settings | `$credential` reference only | replaced by a reference |
| `url` | required in the package | the address v2 would have used (variable first), or refused |
| other settings | only the adapter's accepted settings | see FR-010 |
| `store` | refused | dropped with a warning |
| `adapters_path` | refused | dropped with a warning |
| `incremental` | accepted for `infrahub`, `nautobot`, `netbox` sources | copied, or dropped with a warning |
| generated adapter and model files | not used | ignored, with a note |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (brief SC-004): on the NetBox example and the pinned `demo` dataset, the first V3
  plan against an Infrahub that v2 synced, with no change in NetBox, holds 0 creates and 0 updates.
- **SC-002** (brief FR-008 verification): the v2 NetBox example from `main` converts and registers
  with no edit to the package.
- **SC-003**: every v2 example on `main` that uses only bundled adapters converts, and its package
  passes the checks of FR-013. Every v2 example that uses a custom adapter is refused with a named
  reason.
- **SC-004**: in 100% of the conversion tests, no credential value from the input appears in the
  package, the command output or the logs.
- **SC-005**: a team member converts and registers the NetBox configuration in under 5 minutes,
  compared with the five manual steps that `configuration-package.mdx` lists today.

## Assumptions

- A v1 configuration that v2 can load is converted in the same way. A v1 configuration that v2
  cannot load is refused like any other file that does not match the v2 model. The gate runs with
  the latest v2 release only.
- The command converts one `config.yml` per call. A v2 project directory with several
  configurations is converted file by file.
- By default the command writes `package.yml` in the input's directory; an option sets another
  path. YAML comments in the v2 file are not carried into the package.
- The gate uses the v2 NetBox example mapping on `main` for both the v2 sync and the conversion, so
  that both lines map the same NetBox data. The V3 example package
  (`examples/netbox_to_infrahub/package.yml`) is not part of the gate.
- Lineage (`source` and `owner`) and `is_protected` are set on Infrahub writes on both lines
  (`infrahub_sync/adapters/infrahub.py`). This spec relies on that only to keep the plan free of
  false updates; it does not change how lineage is used. Deletes are out of scope (3.0).
- Spec 010 removes the built-in `default` IP namespace delete and the second-sync defects (#167,
  #168, #177). This spec depends on that work for the V3-to-V3 case and covers only the v2-to-V3
  case.
- Testing customers use only bundled adapters (brief assumption). Custom adapters wait for the
  adapter contract (INFP-654, 3.0).
- No v2 state is moved: no cache directory, Redis store or Parquet plan. The first V3 plan reads
  Infrahub as it finds it.
