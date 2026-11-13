# Feature Specification: Convert a v1 or v2 configuration and prove the first V3 plan

**Feature Branch**: `012-v2-configuration-conversion`

**Created**: 2026-10-22

**Status**: Draft

**Release**: the testing release of V3. Work this specification names as out of scope belongs to
3.0 or later.

**Input**: User description: "Move a v1 or v2 user to V3 for the testing release. Today
docs/docs/operate/v2-to-v3.mdx says a V2 config.yml must be brought into the V3 package envelope by
hand, and that the first V3 plan reads objects a V2 run wrote. (1) A CLI command, for example
`infrahub-sync configs convert config.yml`, writes a V3 configuration package from a v2
configuration: token settings become `$credential` references, a `store` block (Redis etc.) is
dropped with a warning, an adapter V3 does not bundle (filesystem path, dotted path,
third-party) is refused with a named reason, and the output registers without edits for the
bundled adapters. It runs locally and does not need the Sync API. (2) Gate: the first V3 plan
against an Infrahub that v2 already synced from the same unchanged source shows no creates and
no updates; every update it does show is a bug. Proven on the NetBox example. (3) Custom
adapters are out of scope (INFP-654, 3.0)." The decisions below were taken with the product
owner on 2026-10-22, before this specification was written.

## Summary

- **Problem.** A customer team that runs Infrahub Sync v1 or v2 has a `config.yml` and an
  Infrahub that v2 already filled. To test V3, the team rewrites the file into a V3 configuration
  package by hand, following six manual steps (`docs/docs/operate/v2-to-v3.mdx`, "Move a V2
  configuration to V3", on the local branch `stack/v3-user-docs`, which is not pushed on
  2026-10-22). Nothing shows that the first V3 plan against the nodes v2
  wrote holds only real changes; the same page says that plan "can contain updates to those
  nodes".
- **What is needed.**
  - A command the team runs on its own, which shows every change it makes to the configuration
    and writes a package that registers without edits.
  - A release gate: on the NetBox example, the first V3 plan against an Infrahub that v2 synced
    from the same unchanged NetBox holds no creates, no updates, no records left out, no
    problems, and only the deletes the test expects.
- **Not decided yet.**
  - Whether a Jira card exists for writing to Peering Manager again in 3.0.
  - Whether briefing D covers the two dependencies named in Assumptions.

## Clarifications

### Session 2026-10-22

- Q: How does the gate treat deletes? → A: The test keeps a list of expected deletes, each with
  its kind, identity and reason. A delete that is not on the list fails the gate; a listed delete
  does not.
- Q: Which exit status does the command use when it writes nothing? → A: Without a terminal and
  without `--yes`, a failure status. When the engineer answers `n`, a success status.
- Q: How is each credential reference named inside the package? → A: `<adapter>-<setting>` in
  lower case with `_` changed to `-`, and `infrahub-token` for the Infrahub token, as in the
  shipped NetBox package.
- Q (planning, 2026-10-22): How are settings other than addresses that v2 read from the
  environment converted, such as `verify: ${CISCO_APIC_VERIFY}` in the v2 ACI example? → A: With
  the address rule (FR-029); otherwise SC-002 fails on the ACI example.
- Q (implementation review, 2026-10-22): Which credential settings get a reference? → A: Those the
  v2 authentication method uses, plus any that holds a value or placeholder in config.yml.
- Q (implementation review, 2026-10-22): Does a placeholder change the variable order? → A: No;
  v2's fixed order applies (FR-011).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Convert the v2 NetBox configuration and register it without edits (Priority: P1)

A network automation engineer on the customer team runs the convert command on the `config.yml`
that the team's v2 installation runs to import NetBox. They run it where the V3 CLI is installed,
ideally the computer where v2 ran. The command shows a report of every change it will make, then
asks one question before it writes the package. The engineer sets the variables that the report
names in the deployment's `.env` file and registers the package without opening it in an editor.

**Why this priority**: every later step of moving to V3 starts from a registered package. Today
this step is a manual rewrite that the team can get wrong.

**Independent Test**: convert the v2 NetBox example from the `2.0.2` release, answer yes, and
register the output on a V3 deployment. Registration succeeds with no change to the file.

**Acceptance Scenarios**:

1. **Given** the v2 NetBox example `config.yml`, **When** the engineer runs the command, **Then**
   the command prints the report before it writes anything and asks `Write package.yml? [y/N]`.
2. **Given** the same file, **When** the engineer answers `n`, **Then** no file is written and the
   command exits with a success status.
3. **Given** the same file, **When** the engineer answers `y`, **Then** `package.yml` holds a
   format version, the configuration and a credentials block, and registering it returns a
   configuration identity and version.
4. **Given** a `config.yml` that holds a literal NetBox token, **When** the engineer converts it,
   **Then** the package holds a credential reference to `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN`,
   the token value appears in neither the package, the report nor any log, and the report says
   that the old `config.yml` still holds a token.
5. **Given** a `config.yml` with no token, because v2 read `NETBOX_TOKEN` from the environment,
   **When** the engineer converts it, **Then** the report shows the change as
   `NETBOX_TOKEN (v2) → INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN (set in .env)`.
6. **Given** a `config.yml` with a Redis `store` block, **When** the engineer converts it, **Then**
   the package has no `store` block and the report says why it was dropped.
7. **Given** a `config.yml` whose Infrahub address is `http://localhost:8000`, **When** the
   engineer converts it, **Then** the report warns that the Sync worker runs in a container and
   may not reach that address.

---

### User Story 2 - View why a configuration cannot be converted (Priority: P1)

The engineer converts a `config.yml` that V3 cannot run in the testing release: it names a custom
adapter, or it writes to a system other than Infrahub. The command writes nothing and names every
reason, so the team learns this from the command and not from a registration error.

**Why this priority**: a team with a custom adapter or another destination must learn before it
registers that the testing release cannot run its configuration.

**Independent Test**: convert one file per refused case, and one file that holds every refused
case, and check the exit status, the files written and the reasons printed.

**Acceptance Scenarios**:

1. **Given** the v2 `custom_adapter` example, whose source sets `adapter:` to a filesystem path,
   **When** the engineer converts it, **Then** the command writes no file, exits with a failure
   status, and names the setting, the value and the reason: custom adapters are not available in
   this release and are planned for 3.0 (INFP-654).
2. **Given** the v2 `infrahub_to_peering-manager` example, **When** the engineer converts it,
   **Then** the command refuses it with the reason "V3 does not write to `peeringmanager` yet;
   this release writes only to Infrahub".
3. **Given** a source `name` that is not a bundled adapter, **When** the engineer converts it,
   **Then** the refusal lists the bundled adapter names.
4. **Given** a file that holds several refused cases, **When** the command stops, **Then** it
   reports every reason, not only the first one.
5. **Given** a source that sets `adapter:` to the bundled NetBox adapter's own module, **When** the
   engineer converts it, **Then** the command converts it and notes that V3 uses its bundled
   adapter.

---

### User Story 3 - Plan from V3 against the data v2 wrote and view no changes (Priority: P1)

The team has registered the converted NetBox package. Its Infrahub holds the nodes that v2 wrote
from the same NetBox, which has not changed since. The first V3 plan on that branch holds nothing
to create or update and leaves no record out, so any change a later plan shows is real.

**Why this priority**: a plan that proposes changes to unchanged data cannot be reviewed, and the
team cannot tell a real change from a difference between v2 and V3.

**Independent Test**: on a local NetBox with the pinned `demo` dataset, sync with v2 `2.0.2` into
an empty Infrahub, convert the same `config.yml`, register it, plan on the same branch and count
the operations.

**Acceptance Scenarios**:

1. **Given** an Infrahub 1.11 that v2 `2.0.2` synced from the pinned NetBox `demo` dataset with
   its NetBox example, and no change in NetBox since, **When** the first V3 plan runs with the
   converted package, **Then** it holds 0 creates, 0 updates, 0 records left out and 0 problems.
2. **Given** the same plan holds deletes, **When** the gate records its result, **Then** each
   delete on the test's list of expected deletes passes, and any other delete fails the gate.
3. **Given** the gate finds a create, an update, a record left out or a problem, **When** it
   records the result, **Then** it records the kind and the identity, and for an update each
   field that differs with the value v2 wrote next to the value V3 would write.

---

### Edge Cases

- The command runs without a terminal and without `--yes`: it prints the report, writes nothing,
  says to run it again with `--yes`, and exits with a failure status.
- The command runs where none of the v2 address variables are set, for example in the Compose
  `cli` service: the report says that no v2 address variable was visible and that the addresses
  come from `config.yml`.
- The file and a v2 address variable give different addresses: the package gets the variable's
  address, as v2 used it, and the report prints both.
- Neither the file nor a variable gives an address: the command stops and names the flag and the
  v2 variables.
- The Compose Sync worker does not receive a credential variable the package names: the report
  warns and says to add it to the worker's environment.
- The output file already exists: the command refuses unless the engineer passes `--force`.
- The input already holds a format version: the command refuses and says the file is already a V3
  package.
- The input is not valid YAML, or is not a v1 or v2 configuration: the command refuses and names
  the first location that fails.
- A `${VARIABLE}` placeholder sits in a setting that v2 read from the environment first, such as
  `verify: ${CISCO_APIC_VERIFY}` in the v2 ACI example: the command follows FR-029 when the
  placeholder names a variable v2 read for that setting, and reads the variables in v2's order. A
  placeholder that names any other variable, such as `url: ${NETBOX_TOKEN}`, is not read (FR-030).
- The v2 authentication method does not use a credential setting, such as `token` when
  `auth_method` is `basic`: the package holds no reference for it unless `config.yml` holds a value
  or placeholder for it, and the report notes that it is not referenced (FR-006). An `auth_method`
  value that v2 did not accept is refused and the setting is named.
- An address holds a user name or password, such as `https://user:secret@netbox.example.com`: the
  command refuses and does not print the address (FR-030).
- A `${VARIABLE}` placeholder sits in any other setting that is not a credential: the command
  refuses and names the setting, because v2 used the placeholder text as the value.
- The v2 adapter and model files that `infrahub-sync generate` wrote beside `config.yml`: the
  command does not read them, and the report says V3 has no generation step.
- v2 `2.0.2` itself fails on the `demo` dataset: the gate cannot be set up until the dataset or the
  v2 example is adjusted, and the adjustment applies to both the v2 sync and the conversion.

## Requirements *(mandatory)*

### Functional Requirements

#### The command and its report

- **FR-001**: The CLI MUST provide `infrahub-sync configs convert <path>`, which reads one v1 or v2
  `config.yml` and writes one V3 configuration package.
- **FR-002**: The command MUST run without the Sync API and without a network connection, except
  with `--live-schema`, which reads the destination schema without a token. Added on 2026-11-13
  by the product owner, together with FR-031.
- **FR-031**: With `--live-schema`, for each mapped kind whose destination uniqueness constraint
  names a cardinality-one relationship to an IP namespace that the file does not map, the
  command MUST set it to the `default` namespace as a static reference to the one concrete
  namespace kind, make the constraint fields the identifiers, add a mapping of that kind with
  no source endpoint, and say so in the report. v2 wrote every prefix and address into
  `default`. The rule uses the uniqueness constraint, not the relationship's `optional` flag,
  because Infrahub declares `ip_namespace` optional and fills it on write. When the schema
  read fails, the command MUST say so and change nothing.
- **FR-003**: The command MUST print the full report before it writes anything, then ask
  `Write package.yml? [y/N]`. `--yes` MUST skip the question. Without a terminal and without
  `--yes`, the command MUST print the report, write nothing, say to run it again with `--yes`, and
  exit with a failure status. When the engineer answers `n`, the command MUST write nothing and
  exit with a success status.
- **FR-004**: When a decision is needed that the command cannot take from the file, the command
  MUST stop and name the flag that sets it. It MUST NOT ask a question about each change.
- **FR-005**: The command MUST write `package.yml` next to the input by default, and `--output`
  MUST set another path. It MUST NOT replace an existing file unless the engineer passes
  `--force`.

#### Credentials

- **FR-006**: For each credential setting that the bundled adapter declares and that the v2
  authentication method of the configuration uses, the package MUST hold a credential reference
  with an environment entry named `INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_<SETTING>` in upper case.
  A credential setting that holds a literal value or a `${VARIABLE}` placeholder in `config.yml`
  MUST also get a reference. A credential setting that the method does not use and that holds
  nothing MUST get no reference, and the report MUST list it as not referenced with the reason.
  The methods are those of v2 `2.0.2`: for `genericrestapi` and `peeringmanager`, `auth_method`
  (default `token`) `token`, `x-auth-token`, `api-key` and `key` use `token`, `basic` uses
  `username` and `password`, and `none` uses none; for `prometheus`, `auth_method` (default
  `none`) `bearer` uses `token`, `basic` uses `username` and `password`, and `none` uses none;
  `slurpitsync` uses `api_key`; every other adapter uses every credential setting it declares.
  An `auth_method` value that v2 did not accept MUST be refused, naming the setting. The Infrahub
  token MUST use `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`. The reference MUST be named
  `<adapter>-<setting>` in lower case, with each `_` in the setting name changed to `-` (for
  example `netbox-token`, `aci-password`, `slurpitsync-api-key`). The Infrahub token's reference
  MUST be named `infrahub-token`, as in the shipped NetBox package
  ([`package.yml`](../../../examples/netbox_to_infrahub/package.yml)).
- **FR-007**: The command MUST NOT read, print or write a credential value.
- **FR-008**: For each credential, the report MUST show the setting, where v2 got the value (a
  literal in `config.yml`, a `${VARIABLE}` placeholder, or a variable such as `NETBOX_TOKEN`), and
  the V3 variable to set. When the input holds a literal credential, the report MUST say that the
  old file still holds it.
- **FR-009**: The report MUST warn about each credential variable that the Compose Sync worker
  does not receive.

#### Addresses

- **FR-010**: The package MUST hold an address for the source and the destination, and the report
  MUST show where each one came from: `config.yml`, a v2 variable such as `NETBOX_URL`, or
  `--source-url` / `--destination-url`.
- **FR-011**: When `config.yml` and a v2 address variable both give an address and they differ,
  the command MUST use the value v2 used and print both: the variable for every adapter except
  `ipfabricsync`, where v2 read the variable only when the file setting was empty. The flags MUST
  override both.
- **FR-012**: When neither the file, a variable nor a flag gives an address, the command MUST stop
  and name the flag and the v2 variables.
- **FR-013**: The report MUST say which v2 address variables were visible where the command ran.
- **FR-014**: The report MUST warn when an address in the package is `localhost` or `127.0.0.1`.
- **FR-029**: Every other setting that is not a credential and that v2 read from an environment
  variable before the file MUST follow the rules of FR-010, FR-011 and FR-013, for example ACI
  `verify` from `CISCO_APIC_VERIFY`. A `${VARIABLE}` placeholder in such a setting counts as no
  value in the file when it names one of the variables v2 read for that setting; the variables
  are then read in v2's order. When the setting gets no
  value from the file or a variable, the command MUST stop and name the setting and the
  variables, because no flag sets it.
- **FR-030**: The command MUST read from the environment only the fixed, non-credential variable
  names that v2 read for each adapter. It MUST NOT read a variable that the input file names in a
  placeholder or in another setting when that variable is not one of those names; the report
  MUST say that the variable was not read. The command MUST refuse an address that contains a
  user name or password, name the setting, and not print the address.

#### Refused, dropped and copied keys

- **FR-015**: The command MUST refuse, write no file and exit with a failure status when the input
  has any of the following. The refusal MUST list every case it found, each with the setting, the
  value and the reason.
  - a source or destination `name` that is not a bundled adapter; the refusal lists the bundled
    names
  - an `adapter:` value that is a filesystem path, a dotted path or an entry point, other than
    the bundled adapter's own module; the reason cites INFP-654 and 3.0
  - a destination other than `infrahub`
  - an adapter setting that the bundled adapter does not accept
  - a `${VARIABLE}` placeholder in a setting that is not a credential and that v2 did not read
    from the environment first

  A refusal MUST NOT print the value of a credential setting.
- **FR-016**: When `adapter:` names the bundled adapter's own module, the command MUST convert the
  configuration and note that V3 uses its bundled adapter.
- **FR-017**: The command MUST drop the following with a warning that gives the reason: a `store`
  block, `adapters_path`, and `incremental` when the source adapter does not support incremental
  extraction.
- **FR-018**: The command MUST NOT read the adapter and model files that v2 generated beside the
  input, and the report MUST say that V3 has no generation step.
- **FR-019**: The command MUST copy without change `name`, `schema_mapping`, `order`,
  `diffsync_flags`, `incremental` for sources that support it, and every destination setting that
  V3 accepts. The `source` and `owner` lineage settings are the exception: a v2 group name becomes
  `{kind: CoreAccountGroup, name}`, the kind v2 2.0.2 looked up, because spec 014 FR-003 refuses
  a bare name. A `{kind, name}` value is copied. The report says that V3 stops the run when the
  group does not exist, where v2 fell back to the account named after the source adapter (spec
  014 FR-005). Added on 2026-11-13, when the work was moved onto `feature/v3-develop`.
- **FR-020**: Before it writes, the command MUST run every registration check that needs no
  connection to the Sync API, the source or Infrahub.
- **FR-021**: The command MUST refuse an input that already holds a format version, and an input
  that is not valid YAML or not a v1 or v2 configuration, naming the first location that fails.

#### The release gate

- **FR-022**: A test MUST set up the gate as follows:
  - a local NetBox with the pinned `demo` dataset, never the public NetBox demo
  - an empty Infrahub 1.11 with the schema library loaded
  - one sync with v2 `2.0.2` and its NetBox example `config.yml`
  - that same `config.yml` converted with `--yes`, registered, and planned on the same branch
    with no NetBox change in between
- **FR-023**: The gate MUST pass only when the first V3 plan holds 0 creates and 0 updates, when
  every delete it holds is on the test's list of expected deletes, and when every record left
  out and every problem is on the test's list of expected left-out records. Each entry on either
  list MUST give the kind, the identity and the reason; an entry on the left-out list also gives
  the problem type of spec 020. A delete, a record left out or a problem that is not on its list
  MUST fail the gate. Changed on 2026-11-13 by the product owner: a record that the destination
  schema cannot represent, such as NetBox devices that share a name where Infrahub identifies a
  device by name alone, is a difference between the two schemas, not a defect.
- **FR-024**: Every create, update, unlisted delete, unlisted record left out or unlisted problem
  that the gate finds MUST be treated as a defect, in V3 or in the conversion, and fixed before
  the testing release.
- **FR-025**: The gate MUST record, for each finding, the kind and the identity, and for an update
  each field that differs with the value v2 wrote next to the value V3 would write.
- **FR-026**: The gate MUST run in the nightly end-to-end workflow, and a failure MUST block the
  testing release.

#### Documentation

- **FR-027**: The "Move a V2 configuration to V3" section of `v2-to-v3.mdx` MUST describe the
  command in place of the manual steps and state the gate result.
- **FR-028**: The CLI reference MUST document the command, its options, its warnings and its
  refusals.

### Key Entities

- **v2 configuration**: a `config.yml` with `name`, `source`, `destination`, and optional
  `store`, `adapters_path`, `order`, `schema_mapping`, `diffsync_flags` and `incremental`. A v1
  configuration has the same keys without `incremental`.
- **V3 configuration package**: one file with a format version, a configuration and a credentials
  block. Registration stores it as an immutable version.
- **Credential reference**: a named reference in a setting, with a credentials entry that names
  the environment variable the Sync worker reads.
- **Conversion report**: what the command prints. It is not stored. It lists each credential and
  its variable, each address and its origin, each dropped key and why, each warning, and each
  refusal reason.
- **Gate result**: the counts of creates, updates, deletes, records left out and problems in the
  first V3 plan, and the details of each finding.
- **Expected-delete list**: kept with the gate test. Each entry names the kind, the identity and
  the reason the first V3 plan proposes that delete on unchanged data.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the pinned NetBox `demo` dataset, after a v2 `2.0.2` sync into an empty Infrahub
  1.11, the first V3 plan from the converted package holds 0 creates, 0 updates, 0 records left
  out, 0 problems, and no delete outside the test's list of expected deletes.
- **SC-002**: 12 of the 14 v2 examples on `main` convert, and each package registers with no edit.
  The other 2, `custom_adapter` and `infrahub_to_peering-manager`, are refused with a named reason.
- **SC-003**: In 100% of the conversion tests, no credential value from the input appears in the
  package, the report or the logs.
- **SC-004**: During the testing release, a customer team converts and registers its NetBox
  `config.yml` without editing the package and without OpsMill writing any part of it, in under
  10 minutes from running the command to a registered configuration. Today the same step takes
  six manual steps.

## Assumptions

- **Users.** Testing customers read from bundled adapters and write to Infrahub. They run the V3
  CLI wherever they install it. The Compose `cli` service is one possible place, not the default.
- **v1.** A v1 `config.yml` uses the v2 keys without `incremental`
  ([`1.6.1` model](https://github.com/opsmill/infrahub-sync/blob/1.6.1/infrahub_sync/__init__.py)),
  so it converts the same way. Conversion tests cover the v1 NetBox example. The gate runs with
  v2 `2.0.2` only.
- **Credential variable names.** V3 accepts only environment variables that start with
  `INFRAHUB_SYNC_CREDENTIAL_`, so that a package cannot read the worker's own storage, database or
  Prefect settings
  ([`credentials.py`](../../../infrahub_sync/configuration/credentials.py), added in commit
  `5e30656f`).
- **Destinations.** V3 declares every bundled adapter except `infrahub` as source-only
  ([`capabilities.py`](../../../infrahub_sync/configuration/capabilities.py)), and its write path
  depends on the Infrahub adapter
  ([ADR 0002](../../adr/0002-planned-write-destination-protocol.md)). No decision record says
  this must stay so.
- **Lineage.** The V3 Infrahub adapter accepts the `source` and `owner` settings as `{kind, name}`
  ([spec 014](../014-import-lineage/spec.md) FR-003). v2 protected every attribute it created;
  V3 protects none unless the package sets `protect` (spec 014 D-4). Decided by the product owner
  on 2026-11-13: the conversion does not set `protect`, because protection is not the V3 default. Lineage and protection are not compared by a plan, so they are not
  expected to cause an update in the gate; the gate would show it if they did.
- **No v2 state moves.** No cache directory, Redis store or saved plan is moved. The first V3
  plan reads Infrahub as it finds it.
- **Deletes** are not executed in the testing release.
- **Defaults** taken without a separate decision: one `config.yml` per call; YAML comments are not
  carried into the package.
- **Dependencies.**
  - The Compose Sync worker must receive the credential variable of every bundled adapter. On
    2026-10-22 it receives three: the Infrahub, NetBox and Nautobot tokens
    ([`docker-compose.yml`](../../../docker-compose.yml)). Without the others, SC-002 holds for
    registration but a plan for those sources fails. It is not known whether briefing D covers
    this.
  - A documented way to install the V3 CLI outside Compose. It is listed as open for the release
    and does not exist yet.
  - The fixes that make a V3 plan on unchanged data empty. It is not known whether briefing D
    covers them.
- **Out of scope for the testing release.**
  - Custom adapters (INFP-654, 3.0).
  - Writing to Peering Manager. v2 writes to it
    ([v2 `peeringmanager.py`](https://github.com/opsmill/infrahub-sync/blob/2.0.2/infrahub_sync/adapters/peeringmanager.py),
    `create` and `update`). 3.0 must write to it again; otherwise a v2 customer loses a
    capability, which the 3.0 definition ("a v2 customer can replace v2 in production and lose
    nothing") excludes. It is not known whether a Jira card tracks this.
  - Converting several configurations in one call.
