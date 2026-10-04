# Feature Specification: Infrahub platform alignment

**Feature Branch**: `008-infrahub-platform-alignment`

**Created**: 2026-10-02

**Status**: Draft

**Input**: User description: "Run Sync on Infrahub's task manager, aligned with Infrahub's Prefect.
Keep configurations, their versions and runs in Infrahub, and plan files in Infrahub's storage.
Keep the write lock and idempotency records in a separate database on Infrahub's task-manager
database server for now. Drop Sync's own Prefect server, PostgreSQL and object store."

## Clarifications

### Session 2026-10-02

- Q: Infrahub's task manager has no API credential and Sync requires one (#342). How is access
  to start runs restricted? → A: Sync supports both: it uses a credential when the operator sets
  one, and otherwise documents that network isolation is the only protection.
- Q: How do users start runs? → A: A thin Sync HTTP API stays in front of the task manager; the
  CLI and Python client keep using it.
- Q: How do existing V3 candidate deployments move to this layout? → A: Reinstall; no data is
  carried over.
- Q: How is a configuration held in Infrahub? → A: One object per configuration holding the
  whole package (source, destination, mappings, order) as a single document field; a version is
  a copy of that document.
- Q: How are Sync API callers identified and authorized? → A: The Sync API authenticates callers
  with their Infrahub token; the right to plan, apply or sync comes from Infrahub permissions on
  Sync's kinds. Sync keeps no principals of its own.
- Q: When is a configuration version created? → A: When a run starts: the run reuses the
  version matching the current content, or creates the next one.
- Q: How is a configuration validated before merge? → A: On demand through the Sync API against
  any branch; merging is not blocked, and a run refuses invalid content with the findings.
- Q: Who installs and upgrades the Sync schema extension? → A: The operator, with Infrahub's
  usual tools, as an install and upgrade step; Sync only checks the loaded version.

- Q: Where do run records live in this release? → A: Run state stays in Sync's PostgreSQL
  database as the authority; each run is mirrored to an Infrahub `SyncRun` node for visibility and
  task-view links. Moving run state fully into Infrahub is a later step. Configurations and their
  versions move fully into Infrahub.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Sync on the Infrahub deployment I already have (Priority: P1)

An operator who already runs Infrahub adds Sync to that deployment. Sync's runs execute on
Infrahub's task manager, and each run appears in Infrahub's task views with its state and logs,
linked to the configuration it ran. The operator does not install or operate a second
orchestration server, a second database server or an object store for Sync.

**Why this priority**: It removes most of the components an operator has to run, and it ends the
drift between Sync's orchestration version and Infrahub's. Every later story builds on it.

**Independent Test**: Deploy Sync next to a stock Infrahub deployment, start a plan run, and
confirm the run executes and shows in Infrahub's task list with its logs, while the Sync
deployment adds only Sync's own services.

**Acceptance Scenarios**:

1. **Given** a running Infrahub deployment, **When** the operator installs Sync, **Then** Sync
   uses Infrahub's task manager and the existing database server, and adds no orchestration
   server, database server or object store of its own.
2. **Given** a registered configuration, **When** a plan run starts, **Then** the run appears in
   Infrahub's task list within seconds, with its state, logs, the target branch and a link to
   the configuration.
3. **Given** Infrahub restarts its task manager during startup, **When** it recreates its own
   work pools, **Then** Sync's work pool and deployment survive and later runs still execute.
4. **Given** an Infrahub release whose orchestration version Sync does not support, **When**
   Sync starts, **Then** it refuses to start and names both versions.

---

### User Story 2 - Edit and review configurations in Infrahub (Priority: P2)

A configuration author creates and changes a sync configuration in Infrahub, through the web
interface, the API or Infrahub's Git repository integration. Changes go through Infrahub's
branches and proposed changes like any other Infrahub data. When a run starts on content that
differs from every existing version, Sync records that content as the next immutable
configuration version. A run uses the current content unless the user names a version, and it
records exactly which version and content it used.

**Why this priority**: It replaces Sync's own register and version commands with tools users
already know, and gives configurations review, history and permissions without Sync-specific
machinery.

**Independent Test**: Change a mapping in a configuration on an Infrahub branch, merge it, start
a plan without naming a version, and confirm the plan used the new version and records its
checksum.

**Acceptance Scenarios**:

1. **Given** a configuration at version 3 and a merged change to it, **When** a plan run starts
   without naming a version, **Then** the run creates and uses version 4, version 3 is
   unchanged, and both remain readable.
2. **Given** a merged change that leaves the content identical to version 3, **When** a run
   starts, **Then** the run uses version 3 and no new version is created.
3. **Given** several merged changes and no run in between, **When** a run starts, **Then** only
   the current content becomes a version; intermediate contents never run get no version.
4. **Given** a plan run in progress on version 4, **When** an author merges another change,
   **Then** the run in progress continues on version 4, and applying its plan is still bound to
   version 4.
5. **Given** a user without permission to edit Sync configurations, **When** they try to change
   one, **Then** Infrahub refuses the change.
6. **Given** a configuration changed on an Infrahub branch, **When** the author asks for
   validation of that branch, **Then** the author gets the findings without anything being
   merged or versioned.
7. **Given** invalid content merged to the default branch, **When** a run starts, **Then** the
   run is refused with the findings and no version is created.

---

### User Story 3 - Review a plan from Infrahub (Priority: P3)

A reviewer opens a run in Infrahub, reads its plan summary and downloads the plan files. The
reviewer approves the apply by the plan checksum, as today. The plan files and review artifacts
live in Infrahub's storage, attached to the run.

**Why this priority**: It removes Sync's own object store and puts the evidence for a write next
to the run that produced it.

**Independent Test**: Create a plan run, find it from the configuration in Infrahub, download its
plan files, and apply it by checksum; confirm the apply used the stored plan without re-planning.

**Acceptance Scenarios**:

1. **Given** a completed plan run, **When** the reviewer opens it in Infrahub, **Then** the
   summary, the checksum and the plan files are available from the run.
2. **Given** a reviewed plan, **When** the reviewer applies it with the matching checksum,
   **Then** the apply reads the stored plan and performs no new read of the source.
3. **Given** a stored plan whose files no longer match their recorded checksum, **When** an apply
   is requested, **Then** the apply is refused and nothing is written.

---

### Edge Cases

- The task manager restarts while an apply holds the write lock: no second write to the same
  configuration may start until the first is resolved.
- Infrahub is unreachable when a run must record its outcome: the outcome is not lost, and the
  run does not report success it cannot record.
- A run targets an Infrahub branch: Sync's own records (configurations, versions, runs, plan
  files) stay on the default branch and never appear in that branch's diff.
- A configuration with past runs is deleted: its runs and plan files stay readable until
  retention removes them.
- The Sync schema extension is missing or not the version the running Sync requires: Sync refuses to start
  and says which schema version it needs.
- Large plans: a plan for 88,000 objects (about 3 MB of plan files) is stored and served
  without truncation.
- An apply request is retried after a network failure: the write happens at most once.
- A user who may read a configuration but lacks the apply permission requests an apply: the
  Sync API refuses it and nothing is written. An expired or revoked Infrahub token is refused.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sync MUST run its work on the task manager of the Infrahub deployment it serves,
  in its own work pool, and MUST NOT require an orchestration server of its own.
- **FR-002**: Sync's orchestration version MUST match the version that the supported Infrahub
  release ships. Sync MUST refuse to start against an unsupported version and name both
  versions.
- **FR-003**: Every Sync run MUST appear in Infrahub's task views with its state and logs, its
  target branch, its operation (plan, verify, apply or sync) and a link to its configuration.
- **FR-004**: Infrahub's task manager startup MUST NOT remove or reset Sync's work pool or
  deployment.
- **FR-005**: Configurations and configuration versions MUST be stored as Infrahub data, defined
  by a Sync schema extension, on Infrahub's default branch. Run records MUST be mirrored there as
  well; in this release Sync's PostgreSQL database stays the authority for run state. The operator loads and
  upgrades the extension with Infrahub's usual tools as an install and upgrade step. Sync MUST
  NOT need schema-admin rights; it MUST check the loaded extension version at startup and refuse
  to start, naming the required version, when it is missing or does not match.
- **FR-006**: Changing a configuration MUST go through Infrahub's normal data workflow (branches,
  proposed changes, permissions, history). Versions MUST be created when a run starts: if the
  configuration's current content on the default branch matches an existing version, the run
  MUST reuse it; otherwise the run MUST create the next immutable version, numbered in order.
  Two runs starting at once on the same new content MUST end up on the same version.
- **FR-007**: Each run MUST record the configuration version and content checksum it used. A run
  MUST NOT change version after it starts, and applying its plan MUST stay bound to that version.
- **FR-008**: A run request that names no version MUST use the configuration's current content,
  as FR-006 describes. A request that names a version MUST use that version.
- **FR-009**: Authors MUST be able to ask the Sync API to validate a configuration as it stands
  on any Infrahub branch and get the findings back. Merging is not blocked by validation. A run
  MUST validate the content it is about to use and refuse to start, with the findings, if the
  content is invalid; no version is created for invalid content.
- **FR-010**: Plan files and review artifacts MUST be stored in Infrahub's storage, attached to
  the run that produced them, with their checksums.
- **FR-011**: An apply MUST read the stored plan, verify its checksum against the approved one,
  and refuse on mismatch. It MUST NOT re-plan.
- **FR-012**: Writes to one configuration MUST stay serialized, including across a task manager
  restart, as decided in ADR 0010.
- **FR-013**: Retried run and apply requests MUST NOT create duplicate runs or duplicate writes.
- **FR-014**: The write lock and the duplicate-protection records MUST be stored in a separate
  database on the database server that backs Infrahub's task manager, not in Infrahub's own data.
- **FR-015**: Sync MUST NOT require its own object store or its own database server.
- **FR-016**: Run records and plan files MUST be removed after a retention period that the
  operator can set.
- **FR-017**: Credentials MUST still come from the worker environment through credential
  references. No credential value is stored in Infrahub data, run records or plan files.
- **FR-018**: Sync MUST authenticate to the task manager when the operator sets a task manager
  credential. When none is set, Sync MUST still run, and the documentation MUST state that the
  task manager is then protected only by network isolation. Sync MUST log at startup which of
  the two modes is active.
- **FR-019**: Users MUST start plan, sync and apply runs and read their results through the Sync
  HTTP API, which stays the only entry point for the CLI and the Python client. The Sync HTTP API
  submits runs to the task manager; users need no direct access to the task manager.
- **FR-021**: The Sync HTTP API MUST authenticate every caller with the caller's Infrahub token
  and MUST authorize plan, apply and sync requests from that user's Infrahub permissions on
  Sync's kinds. Sync MUST NOT keep user accounts, tokens or roles of its own. Run records and
  audit entries MUST name the Infrahub user who made the request.
- **FR-020**: Existing V3 candidate deployments MUST move to this layout by reinstalling. No
  configuration or run data is carried over, and the upgrade documentation MUST say so.

### Key Entities

- **Sync configuration**: one sync between one source and one destination. It has a unique
  name and one document field holding the whole package (source, destination, mappings, order),
  and it relates to its versions. Tasks attach to it.
- **Configuration version**: an immutable copy of the configuration's document, with a number
  and a checksum. Created when a run starts on content no existing version holds.
- **Run**: one plan, verify, apply or sync execution. It links to a configuration version and to
  its task in Infrahub. It records its state, target branch, plan checksum, counts and outcome.
  Tasks attach to it.
- **Plan file**: a stored file produced by a run (plan bundle or review artifact), with its
  checksum, size and type.
- **Write admission**: the record that a configuration's write is reserved, held outside
  Infrahub's data, together with the configuration's write lock and the idempotency keys.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Adding Sync to an existing Infrahub deployment adds no more than two long-running
  services, down from five today (database, object store, orchestration server, API, worker),
  and no new database server or object store.
- **SC-002**: Every run started in a test campaign of 100 runs appears in Infrahub's task list
  within 10 seconds of starting.
- **SC-003**: An author can change a mapping, get it reviewed and merged, and start a plan on
  the new version in under 5 minutes, without any Sync-specific registration command.
- **SC-004**: In a fault test that restarts the task manager during 20 applies, no two writes to
  the same configuration overlap and no apply writes twice.
- **SC-005**: A reviewer can reach a run's plan files from its configuration page in Infrahub
  within 3 navigation steps.
- **SC-006**: A plan for 88,000 objects is stored and applied with no loss, with the same
  checksum before and after storage.

## Assumptions

- Infrahub's task manager exposes flow runs tagged `infrahub.app` in its task views and links
  them to nodes through node tags. The phase 1 test confirms the web interface shows them.
- Sync supports the Infrahub releases whose orchestration version it is qualified against,
  starting with the release that ships Prefect 3.8.6.
- Retention defaults to 90 days for run records and plan files.
- Infrahub's own lock registry is not available to external workers. If it becomes available,
  the separate lock database is revisited.
- Run records are written by a dedicated Sync account with permission limited to Sync's kinds.
- Deletes stay recorded and never executed, as in ADR 0004.
- Scheduling and event-triggered runs are out of scope.
- This specification supersedes process decisions AD-2, AD-3 and AD-14 in
  `infrahub-sync-process/product/decisions.md`. The ADR is extracted from it after
  clarification.
