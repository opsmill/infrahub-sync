# Feature Specification: Register a configuration package from the web interface

**Feature Branch**: `025-register-config-web`

**Created**: 2026-11-18

**Status**: Draft

**Input**: An idea brief from a grilling session with the maintainer on 2026-11-18: "Right now to add
a new configuration into the API we have only one choice - pass by the cli. I think we could add a
easy 'drag and drop' on the UI to add a new version of an existing configuration or a new
configuration. The customer will have to update it on the side for now but we could improve this
after the MVP." The decisions taken in that session are listed under "Decisions".

Sources:

- [`infrahub_sync/cli.py`](../../../infrahub_sync/cli.py), commands `configs register` and
  `configs version`.
- [`infrahub_sync/service/config_routes.py`](../../../infrahub_sync/service/config_routes.py), the
  configuration routes of the Sync API.
- [`infrahub_sync/product_store/configs.py`](../../../infrahub_sync/product_store/configs.py),
  registration, new versions and validation.
- [`infrahub_sync/configuration/validation.py`](../../../infrahub_sync/configuration/validation.py),
  the validation findings.
- [`frontend/src/domain/mapping.ts`](../../../frontend/src/domain/mapping.ts), the comparison of two
  versions in the web interface.
- [Spec 007](../archive/007-plan-problem-reporting/spec.md), FR-034 to FR-038: validation against a
  branch, and what a registration response must not show.
- [ADR 0040](../../adr/0040-a-v2-configuration-converts-locally-by-v2-rules.md): a v2 configuration
  converts to a package on the team's own machine.
- [Spec 017](../017-serve-web-interface/spec.md): the web interface is served by the Sync service and
  each person enters their own token.
- [`AGENTS.md`](../../../AGENTS.md), section "Sync frontend": the web interface never names a user.

## Background

### What a team can do today

- An operator registers a new configuration with `infrahub-sync configs register <package>` and
  adds the next version of a configuration with `infrahub-sync configs version <config> <package>`.
  The CLI reads one JSON or YAML **configuration package** and sends it to `POST /configs` or
  `POST /configs/{config_id}/versions`. Both routes refuse a token that is not an administrator
  token.
- A team views configurations, their versions and the differences between the mappings of two
  versions in the web interface. The web interface computes those differences itself, because the
  Sync API has no comparison of two versions.
- A team validates a registered version with `infrahub-sync configs validate` or
  `POST /configs/{config_id}/versions/{registry_version}/validate`. With a branch, validation also
  checks the destination schema on that Infrahub branch and compares the package's credential
  references with the variables the live workers publish (spec 007, FR-034 to FR-036). Without a
  branch, it checks only the content of the package.

### What a team cannot do today

- **Register a configuration or a version without a terminal.** The web interface has no action
  that writes a configuration.
- **Check a package against the destination before it becomes a permanent version.** Validation
  accepts only a registered version: it reads the stored version back, then checks it. A registered
  version cannot be deleted. A team that finds a problem after registering can only register a
  corrected version on top of it. Why validation was limited to registered versions is not known:
  no specification or decision record in this repository states a reason.

### Configuration names are not unique

The store refuses a duplicate configuration ID, never a duplicate name. Registering a package whose
name is already in use creates a second configuration with that name. The name of a configuration
follows its newest version, so a new version can rename it.

## Decisions

Taken with the maintainer on 2026-11-18.

- **D-1, the web interface accepts only configuration packages.** A package is the output of
  `infrahub-sync configs convert` or a file written by hand. Converting a v2 `config.yml` depends on
  the v2 environment variables on the team's machine (ADR 0040), so it stays in the CLI. Converting
  in the web interface is a later specification.
- **D-2, two places to drop a file, each with one purpose.** A file dropped on the Configurations
  page always registers a new configuration. A file dropped on a configuration's page always adds the
  next version of that configuration. The web interface never chooses the configuration from the
  name in the package.
- **D-3, every registration goes through a review step.** Registered versions cannot be deleted, so
  the operator views what they are about to register before anything is stored.
- **D-4, validation runs before registration.** The review step validates the dropped package against
  the destination without storing it. This needs a new Sync API route.
- **D-5, the validation branch.** The review step validates against the branch the package declares
  for its destination, or `main` when it declares none. The operator can change it.
- **D-6, an unreachable destination does not block registration.** The operator can still register,
  and the version records that it was registered without validation.
- **D-7, this feature targets the second alpha release of V3**, after the first alpha release and
  before 3.0. The maintainer builds it. The frontend team may rework the web interface part later.
- **D-8, the "registered without validation" mark is kept** (User Story 4). The maintainer confirmed
  this on 2026-11-19.
- **D-9, one specification, delivered in two pull requests.** The first pull request holds User
  Story 1 (the Sync API route and `configs check`). The second holds the web interface and the mark
  (User Stories 2 to 4). The maintainer confirmed this on 2026-11-19.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Validate a package against the destination before registering it (Priority: P1)

An operator has a configuration package on their machine. Before registering it, they want to know
whether the destination accepts it: whether every kind and attribute it maps exists on the branch,
whether the token can write them, and whether the workers define the credential variables it refers
to. They send the package to the Sync API and receive the same findings that validating the
registered version would give, and nothing is stored.

**Why this priority**: Every other story depends on it. It also gives CLI and script users a check
they do not have today, and it can ship on its own.

**Independent Test**: Send a package that maps an attribute missing from a branch to the new route
with that branch, read the findings, then confirm the list of configurations and versions is
unchanged. Register the same package, validate the registered version against the same branch, and
compare the two sets of findings.

**Acceptance Scenarios**:

1. **Given** a package that maps an attribute missing from branch `b1`, **When** an operator
   validates it without registering against `b1`, **Then** the response lists a finding that names
   the kind and the attribute, and no configuration or version is created.
2. **Given** any package and branch, **When** the package is validated without registering and then
   registered and validated as a version against the same branch and the same destination state,
   **Then** both responses list the same findings in the same order.
3. **Given** a package that refers to a credential variable no live worker publishes, **When** it is
   validated without registering against a branch, **Then** the response names the variable and
   never a value.
4. **Given** a package that the registration would refuse, **When** it is validated without
   registering, **Then** the response gives the same refusal the registration gives.
5. **Given** a destination that cannot be reached, **When** a package is validated without
   registering against a branch, **Then** the response states that the destination could not be
   reached, distinctly from a list of findings.
6. **Given** a token that is not an administrator token, **When** it validates a package without
   registering, **Then** the Sync API refuses the request, connects to no destination, and records
   the refusal in the audit log.

---

### User Story 2 - Add a version to a configuration by dropping a file (Priority: P1)

An operator edits a configuration package outside Infrahub Sync. They open the configuration's page
in the web interface and drop the file on it. The review step shows what changes compared with the
newest version, the credential variables the package refers to, and the validation result against
the destination branch. They enter a reason if they want, register, and land on the page of the new
version.

**Why this priority**: Adding versions is the most frequent change to a configuration, and this is
the journey the maintainer described first.

**Independent Test**: With an administrator token, drop a modified package on a registered
configuration's page, check the review step, register, and confirm the new version's page shows the
next version number.

**Acceptance Scenarios**:

1. **Given** a registered configuration with newest version N and an administrator token, **When**
   the operator drops a valid package on that configuration's page, **Then** the review step shows
   the configuration name, source and destination, the mappings added, changed and removed compared
   with version N, the credential references, the validation branch and "Valid".
2. **Given** the review step shows "Valid", **When** the operator registers, **Then** version N+1 is
   created with the reason the operator entered, and the web interface opens its page.
3. **Given** a package with validation findings, **When** the operator drops it, **Then** the review
   step lists the findings and the operator cannot register it.
4. **Given** the review step is open, **When** the operator changes the validation branch, **Then**
   the review step validates again against the new branch and shows the new result.
5. **Given** a package identical to version N, **When** the operator registers it, **Then** the web
   interface states that the package is the same as version N and that no new version was
   registered.
6. **Given** a package that declares a different configuration name, **When** the review step opens,
   **Then** it states that this version renames the configuration from the current name to the new
   one.
7. **Given** the review step is open, **When** the operator cancels, **Then** nothing is registered
   and the configuration's page is shown as before.
8. **Given** the destination cannot be reached, **When** the operator drops a package, **Then** the
   review step states "Could not validate: the destination cannot be reached" and offers
   "Register without validation". Registering then creates the version as in scenario 2.

---

### User Story 3 - Register a new configuration by dropping a file (Priority: P2)

An operator has a package for a configuration that is not registered yet. They drop it on the
Configurations page, review it, and register it.

**Why this priority**: A team registers a configuration once and adds versions many times. The CLI
already covers this case.

**Independent Test**: With an administrator token, drop a package with a new name on the
Configurations page, register it, and confirm the new configuration's page opens with version 1.

**Acceptance Scenarios**:

1. **Given** an administrator token and a valid package, **When** the operator drops it on the
   Configurations page, **Then** the review step shows the configuration name, source and
   destination, the credential references and the validation result, with no comparison.
2. **Given** the review step shows "Valid", **When** the operator registers, **Then** a new
   configuration is created with version 1, and the web interface opens its page.
3. **Given** a configuration named `netbox-to-infrahub` exists, **When** the operator drops a package
   with that name on the Configurations page, **Then** the review step warns that a configuration
   with this name exists and offers to open that configuration to add a version instead. Registering
   is still possible.

---

### User Story 4 - Show that a version was registered without validation (Priority: P3)

An operator registered a version while the destination could not be reached (User Story 2,
scenario 8). Later, anyone who views that version learns that it was never validated against the
destination, so they know to validate it before the first plan.

**Why this priority**: Registering during an outage is already possible without this story (D-6).
This story only records it, and it is the only story that changes what the store keeps for a
version, so it is separate and can be left out without changing Stories 1 to 3.

**Independent Test**: Stop the destination, drop a package, register without validation, and
confirm the version's page and the version returned by the Sync API both show that it was
registered without validation.

**Acceptance Scenarios**:

1. **Given** the operator registered without validation, **When** anyone views the version in the
   web interface or reads it from the Sync API, **Then** the version shows that it was registered
   without validation.
2. **Given** a version registered after a successful validation, or through the CLI, **When** it is
   viewed, **Then** it does not show that mark.

---

### Edge Cases

- **A token that is not an administrator token**: the drop areas are shown but disabled, with
  "Registering needs an administrator token".
- **Several files dropped at once**: the web interface refuses the drop with "Drop one package file".
- **A file that is not YAML or JSON, or is empty**: the web interface refuses it before the review
  step and says why.
- **A file larger than the Sync API accepts**: the web interface refuses it before the review step.
  The limit is the one the Sync API already applies to a request body; this feature does not invent
  a new one.
- **A v2 `config.yml`**: the web interface recognizes it (it has no `configuration` key and has
  top-level `source` and `destination` keys) and says: "This is a v2 configuration. Convert it with
  `infrahub-sync configs convert`, then drop the package here. Converting a v2 configuration in the
  web interface is planned for a later release."
- **A network failure after Register**: a retry from the same review step reuses the same
  idempotency key, so it cannot create two versions.
- **A version registered by another session while the review step is open**: before registering, the
  web interface checks that the newest version is still the one it compared with. If not, it states
  "Version N+1 was registered meanwhile" and reloads the comparison before allowing registration. The
  message never names who registered it.
- **A raw file compared with a stored version**: the store fills in default values when it keeps a
  package. The comparison in the review step MUST be computed so that it matches the comparison
  shown after registration (SC-003).
- **A token removed while the review step is open**: the web interface returns to the token entry
  screen, as on every other page (spec 017).

## Requirements *(mandatory)*

### Functional Requirements

#### Validating without registering (User Story 1)

- **FR-001**: The Sync API MUST validate a configuration package that is not registered, optionally
  against an Infrahub branch, without storing anything.
- **FR-002**: For the same package, branch and destination state, FR-001 MUST return the same
  findings, in the same order, as validating the registered version.
- **FR-003**: FR-001 MUST give the same refusal as the registration for a package the registration
  would refuse, with one exception: a v2 configuration gets its own refusal that names
  `infrahub-sync configs convert`.
- **FR-004**: FR-001 MUST report an unreachable destination as a distinct result, not as a finding
  and not as a refusal of the package.
- **FR-005**: FR-001 MUST be refused for a token that is not an administrator token, and the refusal
  MUST be recorded in the audit log as a refused registration is. FR-001 MUST apply the same secret
  redaction as validating a registered version. Reason: validating against a branch connects to the
  destination address the package declares, with the destination credential the service holds. For
  a registered version, an administrator chose that address; for a package sent by the caller, the
  caller chooses it.
- **FR-006**: The `infrahub-sync` CLI MUST offer the check of FR-001 for a package file.

#### Registering from the web interface (User Stories 2 and 3)

- **FR-007**: Operators MUST be able to drop one package file on a configuration's page to add the
  next version of that configuration, and on the Configurations page to register a new
  configuration. Each place MUST also offer a way to choose the file without dragging it, usable
  with a keyboard.
- **FR-008**: The web interface MUST show a review step before any registration, containing:
  - the configuration name, the source and the destination;
  - for a new version, the mappings added, changed and removed compared with the newest version;
  - the credential references, by variable name only;
  - the validation branch and the validation result;
  - an optional reason;
  - Register and Cancel.
- **FR-009**: The review step MUST validate the package through FR-001 as soon as it opens, against
  the branch the package declares for its destination, or `main` when it declares none. While
  validation runs, the review step MUST show that it is validating, and Cancel MUST stay available.
  When the report has more findings than one page holds, the review step MUST state how many more
  exist and name `infrahub-sync configs check` as the way to list them all.
- **FR-010**: Operators MUST be able to change the validation branch, which validates again.
- **FR-011**: The review step MUST show "Valid" only when validation returns no findings, and MUST
  list every finding otherwise. Registering MUST NOT be possible while findings are listed.
- **FR-012**: After a successful registration, the web interface MUST open the page of the new
  version. When the Sync API reports that the package is identical to the newest version, the web
  interface MUST say that no new version was registered.
- **FR-013**: For a new configuration whose name is already in use, the review step MUST warn and
  offer to open the existing configuration instead, without refusing the registration.
- **FR-014**: For a new version that changes the configuration name, the review step MUST state the
  rename.
- **FR-015**: The web interface MUST disable the drop areas for a token that is not an administrator
  token, and say why.
- **FR-016**: The web interface MUST refuse, before the review step, with a message for each case:
  - a drop of more than one file;
  - a file that is not YAML or JSON, or is empty;
  - a file larger than the Sync API accepts;
  - a v2 `config.yml`, with the message given under Edge Cases.
- **FR-017**: All registration attempts from one review step MUST use the same idempotency key and
  the same request. After the first attempt, the reason and the other inputs of the review step
  MUST stay locked; the operator can only retry or cancel. Reason: the Sync API refuses a key reused
  with a different request.
- **FR-017a**: When the validation request itself fails (no answer from the Sync API, or an error
  that is not a refusal of the package), the review step MUST say so, keep Register disabled and
  offer to validate again.
- **FR-018**: Before registering a version, the web interface MUST check that the newest version is
  still the one the review step compared with, and reload the comparison when it is not.
- **FR-019**: The review step and the messages that follow it MUST NOT name a user or an actor.

- **FR-020**: When FR-001 reports an unreachable destination, the review step MUST allow
  registration, with the warning shown next to Register (D-6).

#### Recording a registration without validation (User Story 4)

- **FR-021**: A registration made after an unreachable-destination result MUST be recorded on the
  version as registered without validation, and the Sync API and the web interface MUST show it on
  that version.
- **FR-022**: Until User Story 4 is built, a registration after an unreachable-destination result is still
  allowed (FR-020), and no mark is recorded.

### Key Entities

- **Configuration package** (existing): one JSON or YAML document holding the configuration, its
  credential references, its package metadata and its omissions.
- **Configuration** and **configuration version** (existing): a configuration has an ID and a name;
  its versions are numbered, immutable and cannot be deleted. A version records the reason given at
  registration.
- **Validation report** (existing): the findings for one package against the destination. Every
  finding has the severity `error`. User Story 1 makes it available for a package that is not
  registered.
- **Registered without validation** (new, User Story 4): a mark on a configuration version, set only
  when the operator registered after validation could not reach the destination. A version
  registered with `configs register` carries no mark, because the CLI does not validate before
  registering; the absence of the mark does not mean a version was validated.
- **Review step** (new, web interface only): the screen between dropping a file and registering it.
  It stores nothing on the Sync API.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator with an administrator token and a package file registers a new
  configuration, or a new version, from the web interface without opening a terminal, in under 2
  minutes from dropping the file to viewing the registered version.
- **SC-002**: No version registered from the web interface has validation findings at the time of
  registration, except versions registered after the destination could not be reached. With User
  Story 4, every such version carries the mark.
- **SC-003**: For every package in the acceptance test set, the comparison in the review step
  matches the comparison the configuration page shows after registration.
- **SC-004**: Retrying after a network failure never creates two versions from one review step.
- **SC-005**: For every package in the acceptance test set, validating without registering and
  validating the registered version give the same findings.

## Assumptions

- The operator already has a configuration package. Converting a v2 `config.yml` in the web
  interface is out of scope (D-1).
- The default validation branch is `main` when the package declares none. This may change if
  Infrahub later requires reviews to happen on a branch.
- The Sync API stays the only authority on whether a package is valid. The web interface checks only
  that the file is one YAML or JSON document.
- The maintainer agreed that an unreachable destination does not block registration (D-6), and
  confirmed that the version records it with the "registered without validation" mark (D-8). The
  mark stays alone in User Story 4, which is built last. Until it is built, registration during an
  outage is possible and records no mark (FR-022).
- The person using the web interface has entered their own token (spec 017). The Sync API's existing
  administrator rule decides who can register. Spec 022 (local accounts, in progress on branch
  `022-local-accounts`) keeps the distinction between administrators and other accounts, so
  "administrator token" in this specification means "a token or session of an administrator" once
  spec 022 is merged.
- Out of scope: converting a v2 configuration in the web interface; editing a package in the web
  interface; dropping several files at once; deleting or withdrawing a version; starting a plan from
  the review step (the existing start-run dialog stays the way to start one).
