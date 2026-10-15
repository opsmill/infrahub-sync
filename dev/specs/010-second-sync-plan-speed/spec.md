# Feature Specification: Second sync shows only real changes, and planning stays fast at tier M

**Feature Branch**: `010-second-sync-plan-speed`

**Created**: 2026-10-15

**Status**: Draft

**Release**: MVP (the testing release). Every requirement in this specification belongs to the MVP.
Work it names as out of scope belongs to 3.0 or 3.x, as listed in
[V3 release scope](../v3-release-scope.md).

**Input**: User description: "Second sync shows only real changes, and planning stays fast at tier M, for the V3 testing release. After a first import, a second plan with no change in the source gives zero operations, and after a change it gives only that change. Fix: interface-to-LAG relationships crash or break the second sync (GitHub #167, PR #177; in 2.0.1 15 out of 15 runs failed), stale peers remain in many-cardinality relationships (#168, PR #172), and the built-in `default` IPAM namespace shows as a delete on every plan. Planning speed: at tier M (10,207 mapped NetBox objects) a plan with no changes takes about 20 minutes today versus about 4.5 minutes for the first-sync plan on the same data (infrahub-sync-process product/metrics.md); which part of the plan step takes the time is not known, so the spec must first require measuring the plan step by phase, then require a no-change or ~100-change plan to take no longer than the first-sync plan. Also at tier M a first sync completes with no size limits changed in the code. Scope = brief FR-005, P2, SC-001, SC-002 (the sync part; the UI part is spec 011). Tier L and the 64 MiB / 16 MiB limits are out of scope (3.0)."

## Summary

Problem: a customer team that has imported its NetBox into an Infrahub branch plans again to
pick up what changed. Today that second plan does not show only the real changes, and it is
slow:

- Every plan proposes a delete of Infrahub's built-in `default` IP namespace when no NetBox VRF
  has that name, so a plan on unchanged data is never empty
  ([`examples/netbox_to_infrahub/README.md`](../../../examples/netbox_to_infrahub/README.md);
  the benchmark runner excludes this delete by name, see the
  [benchmark runner guide](../../../docs/docs/development/guides/netbox-benchmark-runner.md)).
- With release 2.0.1, every second sync failed while reading interfaces that belong to a link
  aggregation group (LAG): 15 out of 15 runs on tier S
  ([#167](https://github.com/opsmill/infrahub-sync/issues/167); the fix,
  [PR #177](https://github.com/opsmill/infrahub-sync/pull/177), is open against `main`).
- When the source replaces a peer in a relationship that holds several peers, the old peer can
  stay in Infrahub, and repeating the sync never removes it
  ([#168](https://github.com/opsmill/infrahub-sync/issues/168); the fix,
  [PR #172](https://github.com/opsmill/infrahub-sync/pull/172), is open against `main`).
- At tier M (10,207 mapped NetBox objects), the plan of a second sync with no change in NetBox
  takes a median of about 20 minutes, and with 102 changes about 20 minutes. The plan of the
  first sync on the same data takes 4 min 33 s. Which part of the plan step takes that time is
  not known; the benchmark does not measure it (`infrahub-sync-process`, `product/metrics.md`,
  "Where the v3 time goes" and "Not known yet").

What is needed: after a first import, a second plan with no change in the source holds zero
operations, and a plan after a change holds only that change. At tier M, a plan with no change
or with about 100 changes takes no longer than the first-sync plan on the same data. To reach
that, the plan step is first measured phase by phase, and the measurement is recorded before
any change meant to speed it up. A first sync at tier M keeps completing with no size limit
changed in the code.

Not decided yet: whether the time of each plan phase is shown to the customer team in the run
record, or recorded only for OpsMill engineers and the benchmark. It is marked in the
requirements below.

This specification covers requirement FR-005, journey P2, and the sync part of success criteria
SC-001 and SC-002 of the V3 idea brief (2026-10-15). Opening a tier M plan in the UI, and the
paged plan detail that allows it, are specified separately (spec 011 and brief FR-007). Tier L
and the 64 MiB plan checkpoint and 16 MiB client response limits belong to 3.0.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Plan again after a first import and view zero operations (Priority: P1)

A customer network or automation team completed a first import from their NetBox into an
Infrahub branch. Their NetBox holds physical interfaces that belong to LAG interfaces, prefixes
and IP addresses with no VRF, and VLANs tagged on several interfaces. Nothing has changed in
NetBox since. They start a plan. The plan completes, and in the CLI or through the Sync API they
view a summary with zero creates, zero updates and zero deletes not executed. There is nothing
to apply.

**Why this priority**: a plan that shows operations on unchanged data makes every later plan
harder to review, because the team cannot tell real changes from noise. A plan that fails on
data the first import wrote stops the team at their second run (idea brief, journey P2).

**Independent Test**: import a source fixture that holds LAG members, prefixes and IP addresses
without a VRF, and interfaces with several tagged VLANs. Plan again without changing the source.
The plan completes and holds zero operations.

**Acceptance Scenarios**:

1. **Given** a completed first import whose physical interfaces belong to LAG interfaces,
   **When** the team starts a second plan with no change in the source, **Then** the plan
   completes and holds no create, no update and no delete for any interface or LAG.
2. **Given** a completed first import in which some NetBox prefixes and IP addresses have no
   VRF and no NetBox VRF is named `default`, **When** the team starts a second plan with no
   change in the source, **Then** the plan holds no delete of Infrahub's built-in `default` IP
   namespace.
3. **Given** a completed first import of the NetBox example data, **When** the team starts a
   second plan with no change in NetBox, **Then** the plan summary shows zero creates, zero
   updates and zero deletes not executed.
4. **Given** a completed first import, **When** the team starts the same plan three times in a
   row with no change in the source, **Then** every plan holds zero operations.
5. **Given** a completed first import, **When** the team views the Infrahub branch after a
   second plan, **Then** every physical interface still belongs to the same LAG as after the
   first import, because a plan writes nothing.

---

### User Story 2 - Plan after a change and view only that change (Priority: P1)

The team changes NetBox: they edit one device description, add an interface, delete an
interface, and move an interface from one set of tagged VLANs to another. They start a plan.
The plan holds only operations for those changes. They apply it. Infrahub then holds exactly the
peers that NetBox holds for each relationship, and a further plan holds zero operations.

**Why this priority**: this is journey P2 of the idea brief. Reviewing only the real changes,
and applying exactly them, is the gain V3 offers over v2 on every run after the first import.

**Independent Test**: after a first import, apply a known change file to the source, plan, and
compare the plan's operations with the change file. Apply, then plan again and check that the
plan holds zero operations.

**Acceptance Scenarios**:

1. **Given** a completed first import, **When** the team changes the description of one device
   in NetBox and starts a plan, **Then** the plan holds exactly one update, for that device,
   and that update changes only the description.
2. **Given** an interface whose tagged VLANs in Infrahub are `A` and `B`, **When** the team
   changes them to `A` and `C` in NetBox, plans and applies, **Then** the interface in Infrahub
   has the tagged VLANs `A` and `C` and no longer `B`, and a further plan holds zero operations.
3. **Given** an interface with tagged VLANs in Infrahub, **When** the team removes every tagged
   VLAN from it in NetBox, plans and applies, **Then** the interface in Infrahub has no tagged
   VLAN, and a further plan holds zero operations.
4. **Given** a completed first import, **When** the team moves a physical interface from one
   LAG to another in NetBox and plans, **Then** the plan holds one update for that interface,
   and after apply the interface belongs only to the new LAG.
5. **Given** a completed first import, **When** the team deletes an interface in NetBox and
   plans, **Then** the plan records one delete not executed for that interface (ADR 0004) and
   no other operation.
6. **Given** the tier M data and its change file of 102 changes (72 updates, 20 creates and
   10 deletes), **When** the team plans after applying the change file to NetBox, **Then** the
   plan's creates and updates per kind match the change file, and its deletes not executed
   match the change file with no delete of Infrahub's built-in `default` IP namespace.

---

### User Story 3 - Plan a second sync at tier M as fast as a first-sync plan (Priority: P2)

The team synced a NetBox of about 10,000 mapped objects. They plan again, either with no change
or after about 100 changes. The plan is ready for review in no more time than the plan of their
first import took on the same data, which is about 4.5 minutes on the benchmark machine today,
instead of about 20 minutes.

**Why this priority**: a team that plans often, for example before each change window, waits
20 minutes for every plan today at tier M, and on most runs nearly all of that wait is the plan
step (metrics: the apply step of a no-change sync takes 4 s). This story depends on the
measurement in User Story 4.

**Independent Test**: on the tier M benchmark data, run the first-sync, no-change and
102-change scenarios three times each and compare the medians of the plan step.

**Acceptance Scenarios**:

1. **Given** a completed first import of the tier M data, **When** the team plans with no change
   in NetBox, **Then** the median plan time over three runs is no longer than the median plan
   time of the first sync on the same data, machine and commit.
2. **Given** a completed first import of the tier M data and the 102-change file applied to
   NetBox, **When** the team plans, **Then** the median plan time over three runs is no longer
   than the median plan time of the first sync on the same data, machine and commit.
3. **Given** an empty Infrahub and the tier M data, **When** the team plans a first sync,
   **Then** the median plan time is no more than 10% longer than the 4 min 33 s measured with
   3.0.0a5.
4. **Given** a faster plan, **When** the same source and Infrahub data are planned before and
   after the speed change, **Then** both plans hold the same operations.

---

### User Story 4 - Measure which phase of the plan step takes the time (Priority: P2)

Before anyone changes the plan step to make it faster, an OpsMill engineer runs the tier M
benchmark and views how long each phase of the plan step took in each run: waiting for a worker,
reading the source, reading Infrahub, comparing the two, checking the plan, and saving and
publishing the plan for review. The result is recorded next to the existing tier M results, so
the speed change in User Story 3 addresses the phase that takes the time.

**Why this priority**: the cause of the 20-minute plan is not known ("Not known yet" in the
metrics). A fix chosen without a measurement may address the wrong phase.

**Independent Test**: run one tier S plan and check that the run reports a duration for each
phase, and that the phase durations add up to the plan step's duration within a small
remainder.

**Acceptance Scenarios**:

1. **Given** any plan run, **When** it completes, **Then** a duration is recorded for each phase
   of the plan step listed in FR-009.
2. **Given** the tier M first-sync, no-change and 102-change scenarios, **When** the benchmark
   runs them, **Then** each result holds the duration of each plan phase, next to the existing
   plan and apply times.
3. **Given** a recorded tier M measurement, **When** an engineer views it, **Then** it names the
   phase or phases that account for the difference between the first-sync plan and the
   no-change plan.
4. **Given** a plan run that fails, **When** the team or an engineer views it, **Then** the
   durations of the phases that completed before the failure are recorded.

---

### User Story 5 - Complete a first sync at tier M with no size limit changed (Priority: P2)

The team runs a first sync of a NetBox of about 10,000 mapped objects into an empty Infrahub
with the released Sync images. The plan is saved, the apply writes every object, and the run
completes, without OpsMill changing a size limit in the code.

**Why this priority**: the testing release targets up to about 10,000 mapped objects (idea
brief, "Assumptions"). On tier L, v3 needed two size limits raised to complete; tier M must not
need that (idea brief SC-002).

**Independent Test**: run the tier M first-sync scenario of the benchmark on an unmodified
commit and check that it completes and passes the runner's checks.

**Acceptance Scenarios**:

1. **Given** an empty Infrahub and the tier M data, **When** the team runs a first sync with
   the size limits as released, **Then** the plan is saved, the apply writes every mapped object,
   and the run completes.
2. **Given** the changes made for User Stories 1 to 4, **When** the tier M first sync runs,
   **Then** the saved plan stays within the plan checkpoint size limit with room to spare, and
   the size of the saved plan is recorded with the result.

---

### Edge Cases

- A NetBox VRF is named `default`: the configuration maps it to Infrahub's built-in `default`
  IP namespace, and the plan treats that namespace like any other synced node: it proposes an
  update when the VRF changes and no operation when it does not.
- The source no longer holds any prefix or IP address without a VRF: the plan still proposes no
  delete of Infrahub's built-in `default` IP namespace, because Infrahub created it and no
  configuration did.
- A LAG interface is deleted in NetBox while its member interfaces remain: the plan records the
  LAG delete as not executed, and updates the members to belong to no LAG.
- Two LAG interfaces on different devices have the same name: each member interface is matched
  to the LAG on its own device.
- A relationship that holds several peers refers to a peer that the plan left out (spec 009):
  the rules of spec 009 for left-out records apply, and the plan does not remove existing peers
  from Infrahub for that relationship on that basis alone.
- Infrahub holds a node that a person created by hand and that no source record produces: the
  plan records a delete not executed, as it does today for every other kind (ADR 0004). Only
  Infrahub's built-in nodes are excluded (FR-004).
- A second plan runs while a person edits the Infrahub branch: the plan shows the difference
  between the source and the branch at the time it reads the branch.
- The tier M benchmark machine is shared with other work: the measurement is not valid. The
  benchmark method requires a machine with no other work running.
- A speed change lowers the no-change plan time but raises the first-sync plan time: User Story 3
  scenario 3 fails, and the change is not accepted.

## Requirements *(mandatory)*

### Functional Requirements

#### A second plan shows only real changes (brief FR-005)

- **FR-001**: After a completed first import, a plan with no change in the source MUST hold zero
  creates, zero updates and zero deletes not executed.
- **FR-002**: After a change in the source, a plan MUST hold only the operations that the change
  requires, and each update MUST change only the attributes and relationships that differ.
- **FR-003**: A plan MUST complete when Infrahub holds physical interfaces that belong to LAG
  interfaces written by an earlier sync, and MUST keep each interface's LAG unchanged when the
  source has not changed it (#167). The same MUST hold for every relationship whose peer is
  identified by a relationship of its own, such as a LAG identified by its device and name.
- **FR-004**: A plan MUST NOT propose a delete of an Infrahub node that Infrahub creates itself
  and that no source record produces. Infrahub's built-in `default` IP namespace is the known
  case. When a source record maps to that node, for example a NetBox VRF named `default`, the
  plan treats it as a synced node.
- **FR-005**: After a plan and apply, a relationship that holds several peers MUST hold in
  Infrahub exactly the peers that the source holds. A peer removed in the source MUST be removed
  from the relationship in Infrahub, and a source that holds no peer MUST leave the relationship
  empty (#168).
- **FR-006**: When the source has not changed a relationship that holds several peers, the plan
  MUST NOT propose an update for it, whatever the order in which the source and Infrahub return
  the peers.
- **FR-007**: Planning the same source against the same Infrahub branch any number of times MUST
  give plans with the same operations.
- **FR-008**: These behaviors MUST hold for every bundled source adapter that maps the
  relationships involved, and MUST be checked on the NetBox example configuration and the
  benchmark tier configuration.

#### The plan step is measured by phase

- **FR-009**: Every plan run MUST record how long each phase of the plan step took:
  - waiting for a worker to start the run
  - reading the source
  - reading Infrahub
  - comparing the source with Infrahub to derive the operations
  - checking the plan before it is saved
  - saving the plan and publishing it for review
- **FR-010**: A plan run that fails MUST record the durations of the phases that completed and
  the phase in which it failed.
- **FR-011**: The benchmark results MUST hold the duration of each plan phase for every v3 run,
  next to the plan and apply times it records today.
- **FR-012**: The tier M first-sync, no-change and 102-change scenarios MUST be measured by phase
  with three runs each, and the result MUST be recorded next to the existing tier M results,
  before any change meant to speed up planning is merged.
- **FR-013**: The phase durations of a plan run MUST be
  shown to the customer team in the run record that the CLI and the Sync API return, and so in
  the UI of spec 011. The API reference MUST mark these fields as subject to change.

#### Planning time at tier M (brief SC-001)

- **FR-014**: At tier M, the median plan time of a second sync with no change in NetBox MUST be
  no longer than the median plan time of a first sync on the same data, measured on the same
  machine and commit with the benchmark method in `metrics.md`.
- **FR-015**: At tier M, the median plan time of a second sync after the 102-change file MUST be
  no longer than the median plan time of a first sync on the same data, measured the same way.
- **FR-016**: At tier M, the median plan time of a first sync MUST stay no more than 10% longer
  than the 4 min 33 s measured with 3.0.0a5, so that FR-014 and FR-015 are not met by making the
  first-sync plan slower.
- **FR-017**: A change made to speed up planning MUST NOT change the operations of a plan for
  the same source and Infrahub data. The plans before and after the change MUST be compared on
  the tier S and tier M scenarios.

#### A first sync at tier M completes (brief SC-002, sync part)

- **FR-018**: At tier M, a first sync into an empty Infrahub MUST complete, and pass the
  benchmark runner's checks, with every size limit as released.
- **FR-019**: The size of the saved plan MUST be recorded for every benchmark run, so that the
  distance to the plan checkpoint size limit is known at tier M.

### Key Entities

- **Plan**: the operations that a plan run derives from comparing the source with an Infrahub
  branch: creates, updates and deletes not executed, by kind, with the records left out that
  spec 009 adds.
- **Built-in Infrahub node**: a node that Infrahub creates itself and that no configuration
  created, such as the `default` IP namespace. A plan never proposes to delete it.
- **Relationship that holds several peers**: a relationship of a node, such as the tagged VLANs
  of an interface, whose set of peers in Infrahub must equal the set in the source after apply.
- **Plan phase duration**: for one plan run, the name of a phase of the plan step and how long it
  took. The phases are listed in FR-009.
- **Benchmark result**: for one benchmark run, the tier, the scenario, the version and commit,
  the plan and apply times, and, after this specification, the plan phase durations and the size
  of the saved plan.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (brief SC-001): at tier M (10,207 mapped NetBox objects), the median plan time of a
  second sync with no change, and of a second sync after the 102-change file, is no longer than
  the median plan time of the first sync on the same data, machine and commit. With 3.0.0a5 the
  first-sync plan took 4 min 33 s, the no-change plan about 19 min 33 s and the 102-change plan
  about 19 min 39 s.
- **SC-002** (brief SC-002, sync part): at tier M, a first sync completes and passes the
  benchmark runner's checks in three out of three runs, with no size limit changed in the code.
- **SC-003** (brief FR-005): on the NetBox example data, on tier S and on tier M, a first sync
  followed by a second plan with no change gives a plan with zero operations in 100% of runs,
  with no delete of the built-in `default` IP namespace.
- **SC-004**: on tier S and tier M, a plan after the change file holds exactly the creates and
  updates per kind that the change file expects, and the deletes not executed that it expects,
  and a plan after applying it holds zero operations.
- **SC-005**: in a test where the source replaces peer `B` with `C` in a relationship that holds
  `A` and `B`, Infrahub holds `A` and `C` after one plan and apply, in 100% of runs.
- **SC-006**: the recorded tier M measurement names the plan phase or phases that account for at
  least 80% of the difference between the first-sync plan time and the no-change plan time.
- **SC-007**: the benchmark runner no longer needs to exclude the built-in `default` IP namespace
  delete by name to accept a no-change run as having zero operations.

## Assumptions

- The V3 code at `8dc921b9` already fetches the peers of a relationship that holds several peers
  before comparing them, completes shallow LAG peers from Infrahub, and writes a relationship
  list by replacing it (ADR 0012). The v3 runs on tiers S and M in `metrics.md` completed. #167
  and #168 remain open, and their fixes (#177, #172) target `main` (v2). This specification
  therefore requires the V3 behavior to be proven by tests on V3 data, and fixed where a test
  fails, rather than requiring #177 and #172 to be ported as they are.
- The plan time is measured with the benchmark method in `metrics.md`: the tier M NetBox dump,
  a new empty Infrahub 1.11.3 for every run, a machine with 8 CPUs and 16 GB of memory and no
  other work, three runs per scenario, and the median. The plan time is the benchmark's
  `plan_seconds`, which ends when the plan is published for review and includes waiting for a
  worker and checking the plan.
- "About 100 changes" means the tier M change file of 102 changes (72 updates, 20 creates and
  10 deletes).
- The first-sync plan time used as the reference is measured in the same benchmark session as
  the second-sync plans. The 10% allowance in FR-016 is chosen because the three 3.0.0a5 runs
  differed by about 1 second, so 10% allows for machine variation but not for a deliberate
  slowdown.
- Tier S is measured and reported with the same phases, but has no speed target in the testing
  release. At tier S the no-change plan took about 2 minutes against about 35 seconds for the
  first-sync plan.
- The cause of the slow no-change plan can be fixed within the testing release (idea brief,
  "Assumptions"). If the measurement shows otherwise, the result is reported to the product
  owner before FR-014 and FR-015 are changed.
- Infrahub's built-in `default` IP namespace is the only built-in node that the NetBox example
  and the benchmark configurations meet today. FR-004 applies to any other built-in node found
  later.
- Deletes stay recorded and never executed in the testing release (ADR 0004). "Zero operations"
  includes zero deletes not executed.
- The first sync at tier M already completed with 3.0.0a5 at `09ff444c` (60 runs on tiers S
  and M completed). FR-018 keeps that true after the changes of this specification.
- Reading a tier M plan in the CLI or the UI despite the 16 MiB client response limit, through
  plan detail returned in pages, is covered by brief FR-007 and spec 011, not here.
- Records left out of a plan, and the problems that leave them out, are defined by spec 009.
  This specification uses its vocabulary and does not change its rules.
- The move from v2 (journey P3: a first V3 plan against data that v2 wrote) is a separate
  testing-release specification.
- Out of scope: tier L, the 64 MiB plan checkpoint limit and the 16 MiB client response limit
  (3.0, INFP-659), deletes executed, incremental reading of the source, and the UI.
