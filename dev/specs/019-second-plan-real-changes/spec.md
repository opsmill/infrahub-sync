# Feature Specification: A second plan shows only real changes, and the plan step is measured by part

**Feature Branch**: `019-second-plan-real-changes`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "A second plan shows only real changes, and the plan step is measured
by part at tier M. A team that has imported its NetBox data into an Infrahub branch plans again to
pick up what changed in NetBox. That second plan must hold zero operations when nothing changed,
and only the changed records when something changed. Part 1: the second plan. Part 2: timings of
the plan step. Part 3: measurements at tiers S and M, which need the benchmark machine. Out of
scope: tier L, a change of the size limits, how `runs plan --detail` prints peers (#373), new
filters or columns for `runs list`."

Sources:

- Issue [#387](https://github.com/opsmill/infrahub-sync/issues/387): sync does not clear a
  cardinality-one relationship that is empty on the source.
- Issue [#167](https://github.com/opsmill/infrahub-sync/issues/167): the second diff fails after
  LAG member interfaces are synchronized.
- [`examples/netbox_to_infrahub/README.md`](../../../examples/netbox_to_infrahub/README.md),
  section "Current limitations".
- [NetBox benchmark tiers](../../../docs/docs/development/guides/netbox-benchmark-tiers.md) and
  [NetBox benchmark runner](../../../docs/docs/development/guides/netbox-benchmark-runner.md).
- [ADR 0042](../../adr/0042-the-v2-gate-passes-on-no-creates-and-reviewed-lists.md): the v2 gate
  passes on no creates and reviewed lists.

## Background

### What a team does today

- A team registers a configuration package that maps its NetBox data to Infrahub kinds, creates a
  plan with `infrahub-sync plan`, reviews it and applies it. After the first apply, the Infrahub
  branch holds the imported data.
- The team then plans again, regularly, to pick up what changed in NetBox. This specification
  calls that plan a "second plan": any plan created for a configuration after its first plan was
  applied in full.

### What does not work today

- **A second plan on unchanged data is never empty.** Every plan of the NetBox example holds one
  delete of the `default` IP namespace, a node that Infrahub creates itself, when no NetBox VRF
  has that name. Apply does not execute deletes, so the node stays, and the delete comes back in
  every plan. A reviewer has to know that this one operation is not a change. Three places accept
  it by name: the benchmark runner, the list of deletes that the v2 gate accepts, and the README
  of the NetBox example.
- **A second plan misses one kind of real change.** When NetBox empties a relationship that holds
  one peer, for example when an interface leaves its link aggregation group (LAG), the plan step
  writes a warning to the log and leaves the change out of the plan. The Infrahub branch keeps
  the old peer, and no later plan reports it (#387). Plan format 4 has no way to record this
  change.
- **Nothing proves the second plan in the offline unit tier.** No test of that tier plans the
  same data twice. In release 2.0.1, interfaces that belong to a LAG made every second sync fail
  (#167). No test shows what the current code does in that case.
- **Nobody can tell where the time of a plan goes.** The run response holds the start and the end
  of the source read and of the destination read. It holds nothing for the other parts of the
  plan step, so a team that waits for a plan cannot tell which part is slow, and a developer
  cannot decide what to make faster.
- **No measurement exists for the current code.** The repository holds no recorded result for
  the benchmark tiers S (560 mapped NetBox objects) and M (10,207 mapped NetBox objects).

### Terms used in this specification

- **Operation**: one create, update or delete that a plan holds for a destination node.
- **Empty plan**: a plan that holds zero creates, zero updates and zero deletes.
- **Plan report**: the problems, the records left out and the kept records of one saved plan, as
  defined for plan format 4. They are not operations.
- **Node that Infrahub creates itself**: a node that exists in a new Infrahub branch before any
  import, such as the `default` IP namespace.
- **One-peer relationship**: a relationship that holds at most one peer (cardinality one), such as
  the LAG of an interface.
- **Part of the plan step**: one of the seven periods listed in FR-014.
- **Benchmark machine**: a machine with 8 CPUs and 16 GB of memory that runs no other work during
  a measurement.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A second plan on unchanged data is empty (Priority: P1)

A network engineer imported NetBox into an Infrahub branch last week. Nothing changed in NetBox
since. They create a plan to confirm it and view an empty plan: zero creates, zero updates and
zero deletes. They do not have to recognize an operation that is always there and ignore it.

**Why this priority**: A team reviews every plan before it applies it. A plan that always holds
an operation teaches reviewers to ignore operations, and an automated check cannot use "the plan
is empty" as a signal.

**Independent Test**: Plan and apply a data set into an empty destination, then plan again with
the same source data. The second plan holds zero operations.

**Acceptance Scenarios**:

1. **Given** a destination that holds a node that Infrahub creates itself and that no source
   record matches, **When** the engineer creates a plan, **Then** the plan holds no delete for
   that node.
2. **Given** a first plan applied in full and unchanged source data, **When** the engineer
   creates a second plan, **Then** the plan holds zero creates, zero updates and zero deletes.
3. **Given** unchanged source data that includes interfaces that belong to a LAG, prefixes and IP
   addresses without a VRF, and interfaces with tagged VLANs, **When** the engineer creates a
   second plan, **Then** the plan step succeeds and the plan is empty.
4. **Given** unchanged source data, **When** the engineer creates three plans one after the
   other, **Then** the three plans hold the same operations and the same plan report.
5. **Given** a first plan whose plan report holds records left out or kept records, and unchanged
   source data, **When** the engineer creates a second plan, **Then** the plan holds zero
   operations and its plan report holds the same records left out, kept records and problems as
   the first plan.

---

### User Story 2 - A second plan holds every real change, and only those (Priority: P1)

A network engineer removes an interface from its LAG in NetBox and renames one device. They
create a plan and view exactly two updates: the interface, with its LAG emptied, and the device,
with its new name. After apply, the interface in the Infrahub branch has no LAG.

**Why this priority**: A plan that leaves out a real change makes the Infrahub branch differ from
NetBox without telling anyone. This is a correctness defect, and it is as important as the empty
plan of User Story 1.

**Independent Test**: After a first plan is applied, empty a one-peer relationship of one source
record, plan, and apply. The plan holds one update for that record, and the destination node has
no peer for that relationship after apply.

**Acceptance Scenarios**:

1. **Given** a first plan applied in full, **When** one source record changes one attribute and
   the engineer creates a plan, **Then** the plan holds one update for that record and no other
   operation.
2. **Given** a destination node whose one-peer relationship holds a peer, **When** the source
   record has no peer for that relationship and the engineer creates a plan, **Then** the plan
   holds an update for that node which records that the relationship becomes empty, and the plan
   step writes no warning that it left a change out.
3. **Given** a plan that records an emptied one-peer relationship, **When** the engineer applies
   it, **Then** the destination node has no peer for that relationship, and a later plan on
   unchanged source data is empty.
4. **Given** a destination node whose relationship holds the peers `A` and `B`, **When** the
   source record holds the peers `A` and `C`, and the engineer plans and applies, **Then** the
   destination node holds `A` and `C`, and a later plan on unchanged source data is empty.
5. **Given** a source record that is created with no peer for a one-peer relationship, **When**
   the engineer creates a plan, **Then** the create holds no entry for that relationship, as
   today.
6. **Given** a saved plan in a plan format that the installed release cannot apply, **When** the
   engineer runs `apply`, `verify` or `runs plan` for it, **Then** the command is refused with a
   message that names the plan format, and nothing is written to the destination.

---

### User Story 3 - View how long each part of a plan took (Priority: P2)

An operator waits ten minutes for a plan on a large NetBox. They run `infrahub-sync runs show`
and view how long the run waited for a worker and how long each part of the plan step took. They
can tell whether the time went into reading NetBox, reading Infrahub or comparing the two.

**Why this priority**: Without these timings, nobody can decide what to change to make a plan
faster, and the measurements of User Story 4 cannot be made. It does not change what a plan
holds, so it comes after User Stories 1 and 2.

**Independent Test**: Create a plan, then view the run with `runs show` and with the run
response. Both show a duration for each part of the plan step that ran.

**Acceptance Scenarios**:

1. **Given** a plan that finished, **When** the operator views the run with `runs show`,
   **Then** they view a duration for each of the seven parts of FR-014.
2. **Given** a plan that failed while it read the destination, **When** the operator views the
   run, **Then** they view the duration of each part that ended, the duration of the part that
   failed up to the failure, and no duration for the parts that did not start.
3. **Given** a plan that is still running, **When** the operator views the run, **Then** they
   view the duration of each part that ended and which part is running.
4. **Given** a run created by a release that did not record these timings, **When** the operator
   views it, **Then** the command succeeds and shows no timing for the parts that have none.
5. **Given** the API reference and the CLI reference, **When** a developer reads the description
   of these timings, **Then** each one is marked as subject to change in a later release.

---

### User Story 4 - Measure the plan step on the benchmark tiers (Priority: P3)

A maintainer runs the benchmark on tier S and tier M on the benchmark machine. For each run, the
benchmark record holds the duration of each part of the plan step, the read mode of each side,
the size of the saved plan and a digest of the plan's operations. The maintainer commits a
results page that states, for a first sync, a plan with no change and a plan with about 100
changes, how long each part took.

**Why this priority**: The measurement needs the benchmark machine, which is not always
available, and it depends on User Stories 1 and 3. Its result decides whether User Story 5
requires a code change.

**Independent Test**: Run the benchmark for one tier and one scenario. The record holds the four
new groups of fields, and two runs on the same data hold the same operations digest.

**Acceptance Scenarios**:

1. **Given** a benchmark run of the current release, **When** the run finishes, **Then** its
   record holds the duration of each part of the plan step, the read mode of the source and of
   the destination, the size of the saved plan in bytes and the operations digest.
2. **Given** two plans of the same source and destination data, **When** the maintainer compares
   their records, **Then** the operations digests are equal.
3. **Given** a plan with no change on tier S or M, **When** the benchmark checks the result,
   **Then** the check passes only if the plan is empty, and no operation is excluded by name.
4. **Given** tiers S and M on the benchmark machine, **When** the maintainer runs the first sync,
   the no-change plan and the plan with about 100 changes three times each, **Then** the
   repository holds a results page with the duration of each part for each of the 18 runs, the
   commit measured and the Infrahub version.
5. **Given** a first sync on tier M, **When** it runs with the size limits of the release,
   **Then** it completes, and the results page states the size of the saved plan.

---

### User Story 5 - A second plan at tier M takes no longer than the first plan (Priority: P3)

A team with about 10,000 NetBox objects plans every night. The nightly plan, which holds no
change or about 100 changes, takes no longer than the first plan took on the same data.

**Why this priority**: It depends on the measurements of User Story 4. Until they exist, it is
not known whether the current code already meets this target.

**Independent Test**: On the benchmark machine, compare the measured plan time of the no-change
plan and of the plan with about 100 changes at tier M with the plan time of the first sync at
tier M.

**Acceptance Scenarios**:

1. **Given** the results page of User Story 4, **When** the median plan time of the no-change
   plan at tier M is not longer than the median plan time of the first sync at tier M, **Then**
   the target is met and no code change is required for it.
2. **Given** a median plan time that is longer than the first plan's and a part of the plan step
   that accounts for the difference, **When** a maintainer changes that part, **Then** a new
   measurement meets the target, and the operations digests are equal to those recorded before
   the change.
3. **Given** a median plan time that is longer than the first plan's and no single part that
   accounts for the difference, **When** the maintainer completes the measurement, **Then** the
   results page states this, and the target stays open in a GitHub issue that links the results.

---

### Edge Cases

- A NetBox VRF is named `default`. The `default` IP namespace then has a source record: the plan
  treats it like any other node, and it can hold an update for it.
- An operator created a node by hand in the Infrahub branch, and no source record matches it. The
  plan still holds a delete for it. Only nodes that Infrahub creates itself are left out of
  deletes.
- A one-peer relationship is required by the destination schema and the source record has no
  peer. The plan does not record an emptied relationship that the destination would refuse; it
  reports the record through the plan report, as plan format 4 does for a record it cannot write.
- A one-peer relationship is empty on both sides. The plan holds no operation for it.
- The peer of a one-peer relationship was left out of the plan. The existing rule for kept
  records applies, and the plan does not record the relationship as emptied.
- A plan created by an earlier release, in plan format 4, is applied after an upgrade. The
  release applies it as before.
- A plan created by this release is read by an earlier release of the CLI or of the worker. The
  earlier release refuses it and names the plan format.
- The plan step is cancelled, or its worker stops, during a part. The run response shows the
  parts that ended and marks the part that was running as not completed.
- The source read and the destination read run at the same time. Their durations overlap, so the
  sum of the durations of all parts can be longer than the duration of the plan step.
- A side is read in incremental mode in one benchmark run and in full mode in another. The
  record states the mode of each side, so the two runs are not compared as equals.

## Requirements *(mandatory)*

### Functional Requirements

#### Nodes that Infrahub creates itself

- **FR-001**: A plan MUST NOT hold a delete for a node that Infrahub creates itself when no
  source record matches it. The `default` IP namespace is the first such node; the plan phase of
  this specification lists the others that the Infrahub version in use creates.
- **FR-002**: The rule of FR-001 MUST apply to every configuration and every source adapter. It
  MUST NOT depend on the name of the configuration or on a list kept in the package.
- **FR-003**: When a source record matches a node that Infrahub creates itself, the plan MUST
  treat that node like any other node of its kind.
- **FR-004**: The three places that accept the delete of the `default` IP namespace by name MUST
  stop doing so:
  - the benchmark runner and its record field for built-in deletes;
  - the list of deletes that the v2 gate accepts;
  - the "Current limitations" section of the NetBox example README, and the benchmark runner
    guide.

#### Emptied one-peer relationships

- **FR-005**: When the destination node holds a peer for a one-peer relationship and the source
  record holds none, the plan MUST hold an update for that node which records that the
  relationship becomes empty.
- **FR-006**: `apply` MUST write an emptied one-peer relationship, so that the destination node
  holds no peer for it after the write.
- **FR-007**: A create MUST NOT record an emptied relationship. A record created without a peer
  holds no entry for that relationship, as today.
- **FR-008**: The plan step MUST NOT write the warning that it left an emptied relationship out,
  once the plan records the change.
- **FR-009**: The plan summary, `runs plan` and `runs plan --detail` MUST show an emptied
  relationship as a changed field of its update, and `runs plan --detail` MUST name the
  relationship and state that it becomes empty.
- **FR-010**: A plan that records an emptied relationship MUST be saved in a new plan format
  version. A plan in this format MUST be covered by the plan checksum in full, as plan format 4
  is.
- **FR-011**: Every command and service that reads a saved plan MUST refuse a plan format that it
  cannot apply, before it reads any operation, with a message that names the format of the plan
  and the formats that the release supports.
- **FR-012**: This release MUST continue to read, verify, approve, apply and resume a plan saved
  in plan format 4.
- **FR-013**: The checks that run before a write (`verify`, and the pre-write checks of `apply`
  and `runs resume`) MUST cover an emptied relationship in the same way as any other changed
  field of an update.

#### Timings of the plan step

- **FR-014**: For every `plan` execution, and for the plan step of a `sync` execution, the Sync
  service MUST record the start and the end of each of these seven parts:
  - waiting for a worker: from the submission of the execution to the moment a worker takes it;
  - preparing: from that moment to the start of the first read;
  - reading the source;
  - reading the destination;
  - comparing: deriving the operations and the plan report from the two reads;
  - checking the plan: the checks that run on the derived plan before it is saved;
  - saving: writing the plan and publishing it, up to the moment the run shows its plan.
- **FR-015**: The timings of the source read and of the destination read MUST be the ones that
  the run response holds today. This feature MUST NOT record them a second time under another
  name.
- **FR-016**: When the plan step fails, is cancelled or is interrupted, the Sync service MUST
  keep the timings of the parts that ended. It MUST record the part that was running, with its
  start and with the time at which it stopped.
- **FR-017**: The run response MUST hold, for each execution that has a plan step, the timings of
  FR-014. A part that did not run MUST be absent or empty; it MUST NOT hold a zero duration.
- **FR-018**: `infrahub-sync runs show` MUST print the duration of each part that has one, and
  the name of the part that is running while the plan step runs.
- **FR-019**: A run that holds no timing for a part, including every run created before this
  release, MUST be returned and printed without an error.
- **FR-020**: The API reference and the CLI reference MUST mark each timing of FR-014 as subject
  to change in a later release.
- **FR-021**: No timing field MUST hold an error message or a value from the source or
  destination data.

#### Tests of the second plan

- **FR-022**: The offline unit tier MUST hold tests that plan a data set, apply the plan to a
  stand-in destination, and plan again. They MUST cover:
  - no change, with interfaces that belong to a LAG;
  - no change, with prefixes and IP addresses that have no VRF;
  - no change, with interfaces that have tagged VLANs;
  - one changed attribute, which gives one update and no other operation;
  - an emptied one-peer relationship, through plan and apply;
  - a peer replaced by another peer in a relationship with several peers, through plan and
    apply;
  - the same data planned three times, which gives the same operations and the same plan report;
  - a first plan whose plan report holds records left out or kept records.
- **FR-023**: For each of the NetBox, Nautobot and generic REST adapters, a test of the offline
  unit tier MUST show that the order in which the source returns the peers of a relationship
  does not change the plan.
- **FR-024**: The existing opt-in checks that accept the delete of the `default` IP namespace
  MUST be changed to fail on it.

#### What "zero operations" means

- **FR-025**: A plan MUST count as empty when it holds zero creates, zero updates and zero
  deletes. Records left out, kept records and problems are part of the plan report and MUST NOT
  count as operations.
- **FR-026**: A second plan on unchanged source data MUST hold the same plan report as the plan
  before it: the same problems, the same records left out and the same kept records.
- **FR-027**: The documentation of `runs plan` MUST state the rule of FR-025, so that a reviewer
  who views an empty plan with a non-empty plan report understands that records were not
  imported.

#### Benchmark records and measurements

- **FR-028**: Each benchmark record of the current release MUST hold:
  - the duration of each part of the plan step (FR-014);
  - the read mode of the source and of the destination;
  - the size of the saved plan in bytes;
  - the operations digest: one value computed from the operations of the plan, equal for two
    plans that hold the same operations and different otherwise.
- **FR-029**: The benchmark MUST read plans in the new plan format and in plan format 4, and MUST
  handle a plan whose plan report holds records left out.
- **FR-030**: The check that the benchmark makes on a plan with no change MUST pass only on an
  empty plan (FR-025).
- **FR-031**: A maintainer MUST measure, on the benchmark machine, tiers S and M, with three
  scenarios (first sync, plan with no change, plan with about 100 changes) and three runs each.
  The measurement MUST be made on the code of this feature before any change made to reduce the
  plan time.
- **FR-032**: The results of FR-031 MUST be committed to the repository as a page of the
  developer documentation. The page MUST state, for each run, the duration of each part, the
  read mode of each side, the size of the saved plan and the operations digest, and for the
  whole measurement the commit, the Infrahub version and the description of the machine.
- **FR-033**: The first sync at tier M MUST complete with the size limits of the release
  unchanged: 64 MiB for a stored bundle and 16 MiB for a response read by the client.

#### Plan time at tier M

- **FR-034**: At tier M, the median plan time of the plan with no change, and of the plan with
  about 100 changes, MUST NOT be longer than the median plan time of the first sync on the same
  data, measured as in FR-031.
- **FR-035**: When FR-034 is not met and the measurement names one part of the plan step that
  accounts for the difference, that part MUST be changed until FR-034 is met. The operations
  digests after the change MUST be equal to those recorded before it.
- **FR-036**: When FR-034 is not met and no single part accounts for the difference, the results
  page MUST state this, and a GitHub issue MUST track the target with a link to the results.

### Key Entities

- **Plan operation**: one create, update or delete for a destination node. An update gains a way
  to record that a one-peer relationship becomes empty.
- **Plan format version**: the number that states how a saved plan is written. This feature adds
  one version after plan format 4.
- **Plan report**: the problems, records left out and kept records of a saved plan. Unchanged by
  this feature.
- **Plan step timing**: for one execution, the start and the end of one of the seven parts of
  FR-014.
- **Benchmark record**: the result of one benchmark run. It gains the timings by part, the read
  mode of each side, the size of the saved plan and the operations digest.
- **Operations digest**: one value that identifies the set of operations of a plan.
- **Results page**: the committed page that holds the measurements of tiers S and M.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a first plan of the NetBox example is applied in full, a second plan on
  unchanged data holds 0 creates, 0 updates and 0 deletes, on the pinned `demo` dataset and on
  tiers S and M.
- **SC-002**: After one interface leaves its LAG in the source, the next plan holds exactly 1
  update, and after apply the plan that follows holds 0 operations.
- **SC-003**: No file of the repository accepts or excludes a planned delete by the name of the
  node.
- **SC-004**: The second-plan tests of FR-022 and FR-023 run in the offline unit tier on every
  pull request, without a NetBox or an Infrahub server.
- **SC-005**: For 100% of finished plans created by this release, an operator views the duration
  of each of the seven parts of the plan step in `runs show`.
- **SC-006**: For a failed plan, an operator can name the part of the plan step that failed from
  `runs show` alone, without reading a log.
- **SC-007**: The repository holds the measured duration of each part of the plan step for 18
  runs: 2 tiers, 3 scenarios, 3 runs each.
- **SC-008**: At tier M, the median plan time of a plan with no change and of a plan with about
  100 changes is at most the median plan time of the first sync, or an open GitHub issue links
  the results that show why it is not.
- **SC-009**: The first sync at tier M completes with the size limits of the release, in 3 runs
  out of 3.

## Assumptions

- The Infrahub version of the measurements and of the tests is 1.11.9, the version that the
  development stack and the benchmark pin today.
- "About 100 changes" is the `changed` scenario of the benchmark runner as it exists today. The
  number of changed records it produces is taken as given.
- "Plan time" in FR-034 is the duration of the plan step from the submission of the execution to
  the moment the run shows its plan. This is the value that the benchmark records today as the
  plan duration.
- The benchmark machine is available to a maintainer during this feature. User Stories 1, 2 and 3
  do not need it. If it is not available, User Stories 4 and 5 keep their code changes (the
  record fields and the check on an empty plan) and the measurement stays open.
- Apply does not execute deletes today, and this feature does not change that.
- Whether the current code still fails on a second plan with LAG member interfaces (#167) is not
  known. The test of FR-022 decides it: if the test fails, the defect is fixed in this feature.
- How Infrahub marks a node that it creates itself, and which nodes other than the `default` IP
  namespace it creates, is not known at the time of writing. The plan phase finds it. If
  Infrahub exposes no such mark, the plan phase chooses another rule that meets FR-002.
- A team that upgrades keeps its saved plans in plan format 4 and can apply them (FR-012). No
  saved plan is converted.
- An earlier release of the CLI or worker already refuses a plan format it does not know. FR-011
  requires a test of this, and a fix only if the test fails.

## Out of Scope

- Tier L (87,815 mapped NetBox objects).
- A change of the size limits of the release.
- How `runs plan --detail` prints the peers of an operation
  ([#373](https://github.com/opsmill/infrahub-sync/issues/373)).
- New filters for the list of runs, and new columns in `runs list`.
- Executing deletes during apply.
- Timings of the `verify`, `apply` and `resume` steps.
- Showing the timings in the web interface.
- A comparison of plan times with release 2.

## Open Questions

- It is not known whether the benchmark machine is available during this feature. See
  "Assumptions".
- It is not known whether pull request [#177](https://github.com/opsmill/infrahub-sync/pull/177),
  which is open against `main` for release 2, applies to the current code. The test of FR-022
  for LAG member interfaces answers it.
