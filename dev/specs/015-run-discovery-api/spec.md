# Feature Specification: Discoverable runs, configurations and versions, and consistent verbs

**Feature Branch**: `015-run-discovery-api`

**Created**: 2026-11-04

**Status**: Draft

**Input**: Idea brief grilled with the product owner on 2026-11-04, based on the
`feature/v3-develop` code and the `opsmill/infrahub-sync-process` product records (AD-1, AD-5,
`product/capabilities.md`, `product/path-to-3.0.md`).

## Context

The Sync HTTP API is the one public surface of V3. The CLI is a client of it (AD-16), and the
operator web UI planned in INFP-664 will be a pure client of it (AD-5). A capability the API
lacks is therefore missing from every interface.

Today an API client must already know a run identifier to read anything about a run. It
cannot list the runs of a configuration version, so it cannot tell which plans were applied.
`product/path-to-3.0.md` records this gap for the API (`GET /runs`), the CLI (`runs list`) and
the web UI, and notes that no card covers it.

Three further problems make the workflow hard to follow:

- The CLI and the API use different verbs for the same action. The CLI creates a plan with
  `diff`; the API creates it with `POST /runs` and `operation: plan`. The API can verify a
  plan; the CLI cannot.
- A Prefect flow run carries only the fixed tags `infrahub-sync` and `service`. An operator
  who opens one flow run cannot filter the other flow runs of the same Sync run, or of the
  same configuration.
- A client cannot check its token, or whether it is an administrator, without performing
  another action.

### Terms used in this specification

- **Configuration**: a registered sync definition, identified by `config_id`.
- **Configuration version**: one immutable registered revision of a configuration,
  identified by `config_id` and `registry_version`.
- **Run**: one attempt to synchronize one configuration version to one branch. A run creates
  exactly one plan and applies that plan at most once. The term is kept (AD-1).
- **Plan**: the creates, updates and recorded deletes that a run computes before it writes,
  with their checksum. Verify and apply act on the plan of the run they name.
- **Stage**: one execution step of a run: `plan`, `verify`, `apply` or `sync`. Each stage
  runs as one Prefect flow run.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - List the runs of a configuration version (Priority: P1)

An API client lists the runs of a configuration, optionally narrowed to one version, and
views for each run whether its plan was applied. It does not need to know any run
identifier beforehand.

**Why this priority**: every other interface (CLI `runs list`, the web UI run list) depends
on this capability, and without it a client cannot answer "what happened to this
configuration?".

**Independent Test**: register a configuration, create three runs across two versions,
apply one of them, then list runs with and without filters and compare the result with the
runs created.

**Acceptance Scenarios**:

1. **Given** configuration C has two runs on version 1 (one applied, one only planned) and
   one run on version 2 (only planned), **When** the client lists runs filtered on C,
   **Then** it receives three runs, newest first, each showing its run identifier, version,
   branch, actor, start and finish times, phase, outcome and whether reconciliation is
   required.
2. **Given** the same runs, **When** the client lists runs filtered on C and version 1,
   **Then** it receives the two runs of version 1.
3. **Given** 120 runs match a filter, **When** the client reads the list without a page
   size, **Then** it receives 50 runs and a reference to the next page; following the
   references returns all 120 runs, each exactly once.
4. **Given** a client filters on a configuration that is not registered, **When** it lists
   runs, **Then** the request is refused with a not-found error, not an empty list.
5. **Given** a request without a valid token, **When** it lists runs, **Then** the request
   is refused as unauthenticated.

---

### User Story 2 - List configurations and their versions (Priority: P1)

An API client finds a configuration by its readable name, lists its versions, and views for
each version its latest run and its latest applied run, without downloading every package.

**Why this priority**: it is the entry point of the journey "configuration → version →
runs". Without a readable name and a light version list, a client cannot reach User Story 1
from what an operator knows.

**Independent Test**: register two versions of one configuration and a second configuration
with the same name, apply a run on version 1 only, then list configurations by name and list
the versions of the first configuration.

**Acceptance Scenarios**:

1. **Given** configuration C, named `netbox-inventory`, has versions 1 and 2 and only
   version 1 has an applied run, **When** the client lists configurations filtered on the
   name `netbox-inventory`, **Then** it receives C with its name.
2. **Given** the same configuration, **When** the client lists the versions of C, **Then**
   it receives two version summaries, newest first, without package content; version 1
   shows its latest run and latest applied run, and version 2 shows its latest run and no
   applied run.
3. **Given** two configurations share the name `netbox-inventory`, **When** the client lists
   configurations filtered on that name, **Then** it receives both.
4. **Given** a version that has never run, **When** the client lists versions, **Then** that
   version shows no latest run and no latest applied run.

---

### User Story 3 - Filter Prefect flow runs by Sync run and configuration (Priority: P2)

An operator who is in the Prefect UI filters the flow runs of one Sync run, of one
configuration, or of one configuration version.

**Why this priority**: it removes the dead end between Prefect and Sync, but operators can
already read the identifiers in a flow run's parameters, so it is less urgent than listing.

**Independent Test**: create a run, verify and apply it, then filter flow runs in Prefect on
each identifying tag.

**Acceptance Scenarios**:

1. **Given** run R of configuration C has a plan flow run and an apply flow run, **When** the
   operator filters Prefect flow runs on the run tag of R, **Then** both flow runs appear.
2. **Given** configuration C has several runs, **When** the operator filters on the
   configuration tag of C, **Then** the flow runs of every run of C appear.
3. **Given** Prefect refuses the tag update for a flow run, **When** the run is submitted,
   **Then** the flow run still executes and the run is unaffected.

---

### User Story 4 - Check a token (Priority: P3)

A client checks, in one call, that its token is valid and whether it belongs to an
administrator.

**Why this priority**: it removes a workaround and lets the CLI and the UI show only the
actions the caller may perform, but no workflow is blocked without it.

**Independent Test**: call the route with an administrator token, a non-administrator token
and an invalid token.

**Acceptance Scenarios**:

1. **Given** a valid non-administrator token, **When** the client calls the token-check
   route, **Then** it receives the actor name and `administrator: false`.
2. **Given** an invalid token, **When** the client calls the route, **Then** the request is
   refused as unauthenticated and the refusal is recorded like on other routes.

---

### User Story 5 - Use the same verbs in the CLI and the API (Priority: P3)

A CLI user creates, verifies and applies a plan, lists runs and checks their token with
commands named after the API actions.

**Why this priority**: it removes the vocabulary confusion, and it depends on User Stories 1,
2 and 4 for the new commands.

**Independent Test**: run each CLI command against a test service and check which API route
it calls.

**Acceptance Scenarios**:

1. **Given** a registered configuration, **When** the user runs `infrahub-sync plan`,
   `verify`, `apply`, `runs list` and `whoami`, **Then** each command calls the API action
   with the same verb.
2. **Given** a user runs `infrahub-sync diff`, **When** the command starts, **Then** it
   behaves as `plan` and prints a warning naming `plan` as the command to use.
3. **Given** a configuration name that matches exactly one configuration, **When** the user
   passes that name where a configuration identifier is expected, **Then** the CLI uses that
   configuration.
4. **Given** a name that matches several configurations, **When** the user passes it,
   **Then** the CLI refuses and lists the matching configurations with their identifiers.

---

### Edge Cases

- A filter on a configuration or version that is not registered is refused with not-found,
  so a typing error is not mistaken for "never ran".
- Runs created before configurations were registered (legacy runs) have no configuration
  identifier. They appear in the unfiltered run list with empty configuration fields and
  never match a configuration filter.
- New runs created while a client reads the pages do not cause a run to appear twice or a
  run that existed when the first page was read to be missed.
- Two runs that start at the same instant have a defined, stable order.
- A page reference that is malformed, or that was issued for different filters, is refused
  as an invalid request.
- A configuration whose name changes between versions shows the name of its latest version.
- A configuration version that has never run, or never been applied, shows empty latest-run
  fields.
- A failure to set Prefect tags does not stop or fail the flow run, the same way a failure to
  set its title does today.

## Requirements *(mandatory)*

### Functional Requirements

Run list:

- **FR-001**: The system MUST return the run list to any authenticated principal, and MUST
  refuse a request without a valid token as unauthenticated.
- **FR-002**: The run list MUST show the same public run fields to every principal and MUST
  NOT contain secret values.
- **FR-003**: The run list MUST be returned in pages of 50 runs by default and at most 200
  runs. When more runs match, each page MUST include a reference to the next page. Responses
  MUST NOT include a total count.
- **FR-004**: The run list MUST be ordered newest first with a stable order. A client that
  follows page references MUST NOT receive a run twice, and MUST NOT miss a run that existed
  when it read the first page.
- **FR-005**: The run list MUST be filterable by configuration and by configuration version,
  and MUST refuse a filter on a configuration or version that is not registered as
  not-found.
- **FR-006**: The unfiltered run list MUST include runs that have no registered
  configuration.

Configurations and versions:

- **FR-007**: Users MUST be able to list the versions of a configuration and view, for each
  version, its number, package checksum, creation time, registering actor, registration
  reason, latest run and latest applied run, without receiving the package content. The
  full package MUST remain available from the single-version read.
- **FR-008**: The configuration list and the version list MUST follow the paging and ordering
  rules of FR-003 and FR-004. Paging parameters that are accepted MUST take effect.
- **FR-009**: The configuration list and the configuration summary MUST show a readable name,
  taken from the latest version's package, and the configuration list MUST be filterable by
  name. Names are not required to be unique.

Prefect link:

- **FR-010**: Each Prefect flow run that Sync submits MUST carry tags that identify its run,
  its configuration, its configuration version and its stage, so an operator can filter flow
  runs on each of these in the Prefect UI.
- **FR-011**: These tags MUST NOT contain secret values, actor names, branch names or the work
  pool name.
- **FR-012**: A failure to set the tags MUST NOT prevent or fail the flow run.

Token check:

- **FR-013**: An authenticated principal MUST be able to view its own actor name and
  administrator flag in one request that performs no other action.
- **FR-014**: The token check MUST refuse a request without a valid token as unauthenticated,
  MUST record the refusal like other routes do, and MUST NOT return the token or any part of
  it.

CLI:

- **FR-015**: The CLI MUST provide `plan`, `verify`, `apply` and `sync` as top-level commands,
  and `list`, `show`, `plan`, `results`, `artifacts` and `cancel` in the `runs` group, and
  `whoami`. Each command MUST use the same verb as the API action it calls.
- **FR-016**: `infrahub-sync diff` MUST keep working as a hidden alias of `plan` and MUST print
  a warning that names `plan`, until 3.0.
- **FR-017**: The CLI MUST accept a configuration name wherever it accepts a configuration
  identifier. When the name matches several configurations, it MUST refuse and list the
  matching configurations with their identifiers. When it matches none, it MUST refuse with a
  not-found message.

Documentation:

- **FR-018**: The HTTP API reference MUST define run, plan and stage, MUST state that verify
  and apply act on the run's plan, and MUST include a table that maps each CLI command to its
  API action.
- **FR-019**: The guide for running a sync MUST use `plan` instead of `diff`, and MUST explain
  how to list runs, check a token, and find a run's flow runs in Prefect by tag.

### Key Entities

- **Configuration** (existing): gains a readable name in its summary. The name is not unique.
- **Configuration version** (existing): gains a summary form with its latest run and latest
  applied run, without package content.
- **Run** (existing): unchanged. It becomes listable and filterable by configuration and
  configuration version.
- **Prefect flow run** (existing): one stage of a run. Gains identifying tags.
- **Principal** (existing): gains a read-only view of itself.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Knowing only a configuration's name, an operator finds its versions, the runs of
  each version and which plans were applied with at most three CLI commands or three API
  requests. Today this needs each run identifier in advance.
- **SC-002**: From any Prefect flow run that Sync submitted, an operator views all flow runs of
  the same Sync run with one filter.
- **SC-003**: Every API action that acts on or reads a run has a CLI command with the same
  verb. Today `plan` differs and five actions have no CLI command.
- **SC-004**: A client learns whether its token is valid and whether it is an administrator
  with one request.
- **SC-005**: Paging through a configuration that has 10,000 runs, while new runs are created,
  never returns a run twice and never misses a run that existed when the first page was read.

## Assumptions

- V3 stays in private testing until 3.0 (`infrahub-sync-process/README.md`), and the API
  reports itself as `v3-unstable`. Renaming the CLI `diff` command and changing the content of
  the version list need no deprecation period beyond the `diff` alias.
- AD-1 keeps "run" as the product term. Renaming the resource to "plan" is out of scope.
- Prefect lets operators filter flow runs by tag, and Sync can set tags on a flow run after
  submitting it, as it already sets the title.
- The project constitution names `diff` as a non-applying command that must stay safe to run.
  The renamed `plan` command keeps that property; the constitution text needs the matching
  update.
- Any authenticated principal can already read any run by its identifier, so listing all runs
  to every principal exposes no new data.

## Out of Scope

- Renaming "run" to "plan", or renaming the `plan` stage.
- A link from a Prefect flow run to the web UI. It waits for INFP-664.
- Run filters other than configuration and configuration version: actor, phase, date and
  trigger type (INFP-656).
- Total counts in list responses.
- Token expiry and per-user roles.
- Unique configuration names.
- The web UI itself (INFP-664).

## Open Questions

- Which JPD card owns this work is not known. `product/path-to-3.0.md` records that no card
  covers the run list. INFP-647 covers the CLI commands, and INFP-664 needs the run list.
