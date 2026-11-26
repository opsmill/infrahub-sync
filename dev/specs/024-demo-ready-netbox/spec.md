# Feature Specification: A NetBox demo of Sync 3.x, ready to try locally and from a Kubernetes deployment

**Feature Branch**: `024-demo-ready-netbox`

**Created**: 2026-11-18

**Status**: Draft

**Input**: An idea brief agreed with the maintainer in a design conversation on 2026-11-18. OpsMill
solution architects and sales engineers try Sync 3.x on a working NetBox to Infrahub demo, on
their laptop or on a Styrmin deployment next to the other OpsMill demos.

Sources:

- The idea brief (design conversation, 2026-11-18): users, journeys, decisions, scope.
- [`docs/docs/development-stack.mdx`](../../../docs/docs/development-stack.mdx), sections
  "Run from source" and "Run a first sync": the manual path this feature shortens.
- [`examples/netbox_to_infrahub/`](../../../examples/netbox_to_infrahub/README.md): the NetBox
  package and its device status translation.
- [`tasks/dev.py`](../../../tasks/dev.py) and [`tasks/netbox.py`](../../../tasks/netbox.py): the
  `dev.*` presets, `netbox.seed --dataset demo` and `netbox.demo-package`.
- [`Dockerfile`](../../../Dockerfile): the published image holds neither `examples/` nor a schema.
- [Spec 018](../018-helm-chart-kubernetes/spec.md): the `infrahub-sync` Helm chart in
  `opsmill/infrahub-helm`, which a Styrmin driver will wrap.
- [ADR 0039](../../adr/0039-workers-publish-the-names-of-their-credential-variables.md): how a
  worker receives credentials.

## Scope

This specification covers the work in the `infrahub-sync` repository only.

- **In scope:** the local demo (User Story 1), a setup command that prepares any Sync deployment
  for the NetBox demo without a checkout (User Story 2), pre-release images (User Story 3), and the
  page that tells colleagues how to try the demo and where to send feedback (User Story 4).
- **Out of scope, done later from the `styrmin` repository:** the `infrahub-sync` Styrmin driver,
  the NetBox Styrmin driver, and the deployment on the Styrmin demo cluster. This specification
  delivers everything those drivers need from `infrahub-sync`, and it does not change
  `../styrmin` or `../infrahub-helm-sync-chart`.

## Clarifications

### Session 2026-11-18 (design conversation with the maintainer)

- Q: Who tries the demo first, and what does the round produce? → A: OpsMill solution architects
  and sales engineers. The round produces written feedback on whether they can plan, review and
  apply a NetBox import without help, and where they get stuck.
- Q: Which ways of trying the demo come first? → A: the local demo and the Styrmin deployment.
  An existing NetBox and Infrahub, such as an OpsMill Infrahub demo with its own schema, comes later.
- Q: Where does the Styrmin deployment read NetBox data from? → A: the public `demo.netbox.dev`
  for colleagues trying it, a NetBox Styrmin driver with the pinned `demo` dataset for customer
  demos. Both are delivered from the `styrmin` repository; this specification makes the setup
  command take any NetBox address.
- Q: Which Infrahub does Sync write into? → A: a dedicated Infrahub that holds only the
  destination schema, so that the demo never touches another demo's data.
- Q: Which interface do colleagues use? → A: both. The web interface is part of the Sync
  deployment, and the CLI comes from PyPI after the 3.x release, from a clone until then.
- Q: Must a demo be resettable to run it again? → A: no. Colleagues plan on a new Infrahub branch
  or use a new deployment.
- Q: Is approval required in the demo? → A: no. `INFRAHUB_SYNC_REQUIRE_APPROVAL` stays off by
  default. Approval is a temporary measure that will change (scheduled runs, branch review moving
  into Infrahub), so the demo does not show it.
- Q: Does the round wait for the 3.x release? → A: no. It starts on pre-release images.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Try the NetBox demo locally with one command (Priority: P1)

A solution architect clones the repository and runs one setup command. When it finishes, Infrahub
holds the destination schema, the local NetBox holds the pinned `demo` dataset, the Sync service
runs with its web interface, and the NetBox package is registered with addresses the worker
reaches. The command prints the address of the web interface, the token to enter, and the CLI
commands for a first plan. The solution architect's first manual step is a plan.

Today the same result takes the nine manual steps of "Run a first sync", about 30 minutes, with
tokens and addresses copied by hand.

**Why this priority**: it is the smallest change that lets the round start. Every piece exists
(`dev.*` presets, `netbox.seed --dataset demo`, `netbox.demo-package`); only the composition is
missing.

**Independent Test**: on a machine with Docker, `uv` and Python 3.11 to 3.13 and no stack running,
run the setup command from a fresh clone, then open the web interface and plan on a new branch.

**Acceptance Scenarios**:

1. **Given** a fresh clone and no stack running, **When** the solution architect runs the setup
   command, **Then** it finishes without a manual step, the web interface lists the NetBox
   configuration, and the command prints the next commands.
2. **Given** the setup finished, **When** the solution architect plans the NetBox configuration on
   a new Infrahub branch, **Then** the plan finishes with creates across the kinds of the package
   and no failure.
3. **Given** the setup finished once, **When** the solution architect runs the setup command
   again, **Then** it changes nothing that already exists: no second configuration is registered,
   and the schema load reports no change.
4. **Given** the plan, **When** the solution architect applies it by its checksum from the web
   interface or the CLI, **Then** the objects appear on that Infrahub branch.

---

### User Story 2 - Prepare any Sync deployment for the NetBox demo without a checkout (Priority: P2)

An operator deploys Sync next to a dedicated Infrahub, for example from the Helm chart of
spec 018 wrapped in a Styrmin driver. One command, run from the published Sync image, loads the
destination schema into that Infrahub and registers the NetBox package with the NetBox and Infrahub
addresses of that deployment. The deployment is then ready for a plan, with no manual step.

**Why this priority**: the Styrmin drivers cannot be written until this command exists in the
image. User Story 1 uses the same command, so both ways of trying the demo prepare it the same way.

**Independent Test**: start the published image against an empty Infrahub and a running Sync API,
run the command with the NetBox and Infrahub addresses, then list configurations and read the
Infrahub schema.

**Acceptance Scenarios**:

1. **Given** an empty Infrahub and a running Sync API, **When** the operator runs the setup command
   from the image with a NetBox address, an Infrahub address and the tokens, **Then** the
   destination schema is loaded and one NetBox configuration is registered with those two
   addresses.
2. **Given** the command ran once, **When** it runs again with the same addresses, **Then** it
   reports that the schema and the configuration are already in place and changes nothing.
3. **Given** the command ran once, **When** it runs again with a different NetBox address,
   **Then** it registers the new address as a new version of the same configuration and says so.
4. **Given** an Infrahub that holds a schema incompatible with the destination schema, **When** the
   command runs, **Then** it stops before registering anything, names the schema conflict, and
   exits with a failure.
5. **Given** any run, **When** the command reports, **Then** no token value appears in its output,
   its logs or the registered package.

---

### User Story 3 - Publish pre-release images (Priority: P2)

A maintainer publishes a pre-release image of Sync 3.x, for example `3.0.0-rc.1`, from
`feature/v3-develop`, so that the round and the Styrmin drivers can start before the 3.x release.

**Why this priority**: the Styrmin deployment runs a published image, and no 3.x image is published
yet. The round would otherwise wait for the release, after which the feedback can no longer change it.

**Independent Test**: dispatch the image workflow from `feature/v3-develop` with a pre-release
version, then pull that tag and run the setup command's help from it.

**Acceptance Scenarios**:

1. **Given** `feature/v3-develop`, **When** a maintainer dispatches the image workflow with a
   pre-release version, **Then** the image is published under that tag for linux/amd64 and
   linux/arm64, signed and with its SBOM, as a release image is.
2. **Given** a pre-release image, **When** anyone reads its tags, **Then** it is not tagged
   `latest` or `stable`.

---

### User Story 4 - Read how to try the demo and where to send feedback (Priority: P3)

A solution architect opens one documentation page that says which way of trying the demo to use,
how to run it, what a successful plan looks like, and where to send written feedback.

**Why this priority**: the round's output is written feedback; without a page that asks for it and
names where it goes, the feedback is scattered.

**Independent Test**: a reader who has not seen this specification follows the page from a fresh
clone to an applied plan.

**Acceptance Scenarios**:

1. **Given** the page, **When** a reader follows it, **Then** they run User Story 1 without opening
   another page, and they find the feedback destination at the end.
2. **Given** the page, **When** a reader looks for the Kubernetes way, **Then** the page says which
   command prepares a deployment (User Story 2) and that the Styrmin drivers are not yet available.

---

### Edge Cases

- **NetBox port or Infrahub port already in use on the laptop**: the setup command stops before
  starting anything and names the port, as the `dev.*` tasks do today.
- **An Infrahub container from another project already runs**: the existing presets already detect
  it; the setup command follows the same rule and says which Infrahub it uses.
- **The marketplace does not answer** (the documented `ReadTimeout`): the setup command never
  depends on the marketplace, because the destination schema it loads is a copy pinned in the
  repository and in the image.
- **The public NetBox demo changes** (User Story 2 with `demo.netbox.dev`): edits by other users
  can stop a plan. Accepted for colleagues trying the demo; the page points to "When a run fails".
- **A run with only part of the work done** (the command stopped after the schema load): running it
  again completes the remaining steps.
- **The NetBox token of the public demo is not known at setup time**: the setup command does not
  read it; the worker reads it from its environment when a plan runs (ADR 0039).

## Requirements *(mandatory)*

### Functional Requirements

**Local demo (User Story 1)**

- **FR-001**: The repository MUST provide one command that, from a fresh clone, starts the
  development stack with a local Infrahub and the local NetBox, loads the pinned NetBox `demo`
  dataset, loads the destination schema, and registers the NetBox package with the addresses the
  worker reaches.
- **FR-002**: The command MUST give the worker the NetBox and Infrahub tokens of the local stack
  without the user exporting them.
- **FR-003**: When it finishes, the command MUST print the web interface address, the Sync API
  address, which token to enter, the configuration ID, and the CLI commands for a plan on a new
  branch.
- **FR-004**: Running the command again on a stack it already prepared MUST change nothing and say
  so.
- **FR-005**: The command MUST prepare the stack through the setup command of FR-006, so that the
  local demo and a Kubernetes deployment are prepared by the same steps.

**Setup command (User Story 2)**

- **FR-006**: The Sync image MUST provide a command that loads the destination schema into an
  Infrahub and registers the NetBox package with a NetBox address and an Infrahub address given as
  parameters, without a checkout.
- **FR-007**: The image MUST contain a pinned copy of the destination schema (the
  `infrahub/traditional-infrastructure-sot` collection, at the revision the repository already
  pins) and of the NetBox package. The command MUST NOT fetch either from the network.
- **FR-008**: The command MUST be safe to run again: an unchanged schema is not reloaded, an
  unchanged package is not registered a second time, and a changed address is registered as a new
  version of the same configuration.
- **FR-009**: The command MUST stop with a failure, before registering the package, when the schema
  load fails, and its message MUST name the cause.
- **FR-010**: The command MUST take the Infrahub token and the Sync API token from its environment,
  and MUST NOT write any token into the registered package, its output or its logs. The worker's
  NetBox and Infrahub tokens stay in the worker's environment (ADR 0039).
- **FR-011**: The command MUST leave approval off: it MUST NOT set or require
  `INFRAHUB_SYNC_REQUIRE_APPROVAL`.

**Pre-release images (User Story 3)**

- **FR-012**: A maintainer MUST be able to publish an image from `feature/v3-develop` under a
  pre-release version, through the existing image workflow, with the same platforms, signature and
  SBOM as a release image.
- **FR-013**: A pre-release image MUST NOT receive the `latest` or `stable` tag.

**Documentation (User Story 4)**

- **FR-014**: The documentation MUST have one page for colleagues trying the demo that covers the
  local demo end to end, names the setup command for a Kubernetes deployment, states that approval
  is off in the demo, and ends with where to send feedback.
- **FR-015**: Every page that documents the NetBox demo today ("Run a first sync", the NetBox
  tutorial, the example README) MUST point to the new page and stay correct.

### Key Entities

- **Destination schema**: the `infrahub/traditional-infrastructure-sot` collection that the NetBox
  package maps to. Existing; this feature ships a pinned copy in the image.
- **NetBox package**: `examples/netbox_to_infrahub/package.yml`. Existing; registered with
  addresses that differ for each deployment.
- **NetBox configuration**: the configuration the Sync API holds after registration, with its
  versions. Existing concept.
- **Setup command**: new. Prepares one Sync deployment and one Infrahub for the NetBox demo.
- **Pre-release image**: an image tag such as `3.0.0-rc.1`. New use of the existing workflow.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a fresh clone, on a laptop with Docker, `uv` and Python 3.11 to 3.13, a solution
  architect starts the first plan with one setup command, in under 15 minutes including image
  pulls.
- **SC-002**: Run from the published image against an empty Infrahub and a running Sync API, the
  setup command leaves the destination schema loaded and the NetBox configuration registered, with
  no other manual step.
- **SC-003**: At least 3 solution architects or sales engineers complete plan, review and apply on
  their own and send written feedback. Measured during the round, after delivery; no task
  implements it.
- **SC-004**: Running the setup command a second time changes nothing, every time.
- **SC-005**: No token value appears in the output or logs of the setup command, or in the
  registered package.

## Assumptions

- The round runs on pre-release images; the CLI comes from a clone until 3.x is on PyPI.
- The web interface is built by a colleague and served by the Sync image; this feature relies on it
  and does not change it.
- The Helm chart of spec 018 is published from `opsmill/infrahub-helm`; this feature does not
  depend on it, but the Styrmin drivers will.
- The local demo needs the same machine as the development stack today: Docker, `uv`,
  Python 3.11 to 3.13.
- The NetBox `demo` dataset stays pinned to its commit and checksum.
- Only NetBox is in scope. Other sources, an existing NetBox and Infrahub, resetting a demo, and
  showing approval are out of scope.

## Open Questions (not blocking)

- Which OpsMill Infrahub demo, and which source, a later "existing environment" way of trying
  Sync targets.
- Where colleagues send feedback: the maintainer names the channel in the invitation to the round.
  The page asks for feedback through that channel and names no public tracker, because feedback
  can name customers.
