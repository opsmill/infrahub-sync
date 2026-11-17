# Feature Specification: Scheduled runs that write without a person present

**Feature Branch**: `021-scheduled-runs`

**Created**: 2026-11-17

**Status**: Draft

**Input**: User description: "A team should be able to schedule runs at a given time, on given
days, or every X hours. A scheduled run has nobody present to verify, approve and apply, so the
write gates need a policy. Review of a scheduled write happens in Infrahub: each run writes to a
new branch and opens a proposed change. Delivery of events to the outside and telemetry are other
specifications."

Sources:

- [INFP-656](https://opsmill.atlassian.net/browse/INFP-656): Schedule unattended, recurring runs
  with overlap control. This specification adds to that card how a scheduled run passes the write
  gates.
- [INFP-655](https://opsmill.atlassian.net/browse/INFP-655): Apply and review changes through
  Infrahub branches. Two parts of that card are a prerequisite, see
  [Prerequisites](#prerequisites).
- [ADR 0037](../../adr/0037-an-approval-is-a-record-on-the-run.md): an approval is a record on the
  run.
- [ADR 0033](../../adr/0033-an-event-is-saved-in-the-transaction-of-its-decision.md): an event is
  saved in the transaction of its decision.
- [ADR 0010](../../adr/0010-a-managed-write-ends-in-a-verdict-not-a-retry.md): one write at a time
  for each registered configuration.
- [Project constitution](../../../docs/docs/development/constitution.md), principles I and VII.
- The idea brief of 2026-11-17, agreed with the product owner in an interview. It is not stored in
  this repository; this specification holds its content.

## Background

### What a team does today

- A team that wants a recurring sync starts each run itself, or schedules the CLI with cron, a CI
  job or a workflow tool that it operates. Infrahub Sync has no schedule of its own.
- A `sync` stops and waits for a person in two cases:
    - The Sync API sets `INFRAHUB_SYNC_REQUIRE_APPROVAL`. It then refuses `sync`, and a person
      must plan, approve and apply.
    - The plan leaves out records. The run stops after the plan and writes nothing until a person
      applies the plan with `--confirm-left-out`.
- A run can write to an Infrahub branch that is not the default branch, but the team must create
  that branch in Infrahub first.

### What this specification changes for the team

- An operator creates a schedule once, for one version of a configuration. Infrahub Sync starts
  the runs.
- Each scheduled run arrives in Infrahub as a proposed change that the team reviews and merges
  with the tools it already uses for changes to its data. Infrahub Sync does not add a second
  approval procedure for runs that nobody watches.
- A team whose Sync API does not require approval can choose a direct write to the default branch
  for a schedule.

### Terms

- **Schedule**: a record that names one configuration, one version of it, a cadence and the
  settings of the runs it starts.
- **Start**: one point in time at which the cadence asks for a run.
- **Scheduled run**: a run that a schedule started.
- **Branch review**: the write target in which each scheduled run writes to a new Infrahub branch
  and opens a proposed change to the default branch.
- **Owner**: the actor who created the schedule, or the administrator who took it over.
- **Skipped start**: a start for which no run was started, because of the overlap policy or
  because the start was missed by more than the allowed duration.
- **Missed start**: a start whose time passed while the Sync service was not running.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Scheduled sync with review in Infrahub (Priority: P1)

An operator creates a schedule for version 3 of the configuration `from-netbox`, with the cadence
`0 2 * * *` in the time zone `Europe/Paris`. Every night at 02:00 a run starts with nobody
present. It writes the differences to a new Infrahub branch and opens a proposed change. In the
morning the team reviews and merges that proposed change in Infrahub. When the team did not merge
the proposed change of the night before, the new one replaces it, so the team always reviews one
proposed change, the newest. The operator can pause, resume, change and delete the schedule.

**Why this priority**: Branch review is the only write target that works on every Sync API, with
or without required approval, so every team can use it. It also keeps a person's review before
anything reaches the default branch.

**Independent Test**: On the development stack with a controlled clock, create a schedule with
branch review, let two starts pass with a changed source, and check the runs, the branches, the
proposed changes and the events. This needs the prerequisite of
[Prerequisites](#prerequisites).

**Acceptance Scenarios**:

1. **Given** an operator with a valid token and a registered version 3 of `from-netbox`, **When**
   they create a schedule with the cadence `0 2 * * *` and the time zone `Europe/Paris`, **Then**
   the answer holds the schedule's identifier and its next start time, and the write target is
   branch review.
2. **Given** that schedule and a source that changed, **When** 02:00 in `Europe/Paris` passes,
   **Then** a run starts whose record holds the trigger type `scheduled`, the schedule's
   identifier, version 3 and the owner as actor.
3. **Given** that run finished its write, **When** the team views Infrahub, **Then** a new branch
   named from the schedule's pattern holds the changes and an open proposed change targets the
   default branch.
4. **Given** the schedule's proposed change of the night before is open and nobody approved it,
   **When** the new run opens its proposed change, **Then** the older one is closed with a link to
   the new one and its branch is deleted.
5. **Given** the schedule's proposed change of the night before was approved by a person in
   Infrahub and not merged, **When** the new run opens its proposed change, **Then** the older one
   stays open and its branch stays.
6. **Given** a source that did not change, **When** the start passes, **Then** the run ends with
   no branch and no proposed change.
7. **Given** another run of `from-netbox` is in progress at 02:00, **When** the start passes,
   **Then** no run starts, and the schedule's events hold a skipped start that names the run in
   progress.
8. **Given** a Sync API that requires approval, **When** an operator asks for a schedule that
   writes to the default branch, **Then** the request is refused with a message that names the
   setting, and no schedule is created.
9. **Given** a Sync API that requires approval and a schedule with branch review, **When** the
   start passes, **Then** the run writes to its new branch without a Sync approval.
10. **Given** a schedule, **When** its owner pauses it, **Then** no run starts until the owner
    resumes it, and the schedule keeps its cadence, settings and events.
11. **Given** a schedule that names version 3, **When** its owner changes it to version 4,
    **Then** the next run uses version 4 and the schedule keeps its identifier, cadence, settings
    and events.
12. **Given** a schedule owned by one actor, **When** another actor who is not an administrator
    asks to pause, change or delete it, **Then** the request is refused.

---

### User Story 2 - Scheduled sync straight to the default branch (Priority: P2)

On a Sync API that does not require approval, an operator wants the nightly run to write to the
default branch with no review. They choose that write target when they create the schedule. They
also choose what the run does when its plan leaves out records: stop after the plan, as a `sync`
that a person starts does, or write the remaining records.

**Why this priority**: A `sync` to the default branch already exists, so this is a small addition.
It comes second because the left-out setting lets a schedule write with no review at all, and the
reviewed path must exist first.

**Independent Test**: On a Sync API that does not require approval, create a schedule that writes
to the default branch, once with the left-out setting on and once with it off, with a source that
holds one record with a problem. Check what each run wrote and recorded.

**Acceptance Scenarios**:

1. **Given** a Sync API that does not require approval, **When** an operator creates a schedule
   and names the default branch as the write target, **Then** the schedule is created, and its
   record shows that write target.
2. **Given** a schedule whose left-out setting is on, **When** a plan leaves out 13 records of
   4,013, **Then** the run writes the 4,000 remaining records and its result event holds a
   left-out count of 13.
3. **Given** a schedule whose left-out setting is off, **When** a plan leaves out records,
   **Then** the run stops after the plan, writes nothing, and its result names the review that is
   needed.
4. **Given** a schedule, **When** an operator turns the left-out setting on, **Then** the schedule
   records who turned it on and when.
5. **Given** a schedule with branch review whose left-out setting is on, **When** a plan leaves
   out records, **Then** the run writes the remaining records to its new branch and opens the
   proposed change.

---

### User Story 3 - Operating a schedule over time (Priority: P3)

After weeks of use, an operator needs more control. A long run should be followed by the next one
as soon as it ends, so they choose the `queue` overlap policy. After a restart of the host, a
nightly run that was missed by 40 minutes should still happen, and one that was missed by three
hours should not run in working hours. When a colleague registers a newer version of the
configuration, the operator wants to be told. When the operator leaves the team and their token
is removed, an administrator takes the schedule over.

**Why this priority**: These are needs of long operation, not of the first night. Until this story
is delivered, a missed start is skipped and recorded, and only the `reject` policy exists.

**Independent Test**: With a controlled clock, stop and start the Sync service around a start
time, start a run before a start time, register a newer version, and remove the owner's token.
Check the runs and the events each time.

**Acceptance Scenarios**:

1. **Given** a schedule whose "start late by at most" duration is one hour, **When** the Sync
   service returns 40 minutes after a missed start, **Then** one run starts.
2. **Given** the same schedule, **When** the Sync service returns 3 hours after a missed start,
   **Then** no run starts for it and the schedule's events hold a missed start that was skipped.
3. **Given** an hourly schedule and a Sync service that was not running for four starts, **When**
   the service returns inside the duration of the most recent start, **Then** one run starts, not
   four, and the events hold the four missed starts.
4. **Given** a schedule with the `queue` policy and a run of the same configuration in progress,
   **When** a start passes, **Then** one run waits and starts when the run in progress ends.
5. **Given** a schedule with the `queue` policy and one run already waiting, **When** another
   start passes, **Then** that start is skipped and recorded.
6. **Given** a schedule that names version 3, **When** version 4 of the configuration is
   registered, **Then** the schedule's record reports that a newer version exists, the CLI shows a
   warning where it lists or shows the schedule, and the schedule still runs version 3.
7. **Given** a schedule whose owner's token is no longer valid, **When** a start passes, **Then**
   no run starts and the schedule reports that it stopped for that reason.
8. **Given** that stopped schedule, **When** an administrator takes it over, **Then** the
   administrator is its owner and the next start produces a run.

---

### Edge Cases

- A person edits a value on a schedule's branch and does not approve or merge the proposed change.
  The next run replaces the proposed change and the edit is deleted with the branch. The guide
  states this. This specification does not prevent it.
- The reviewer is away for five nights. One proposed change of the schedule is open, the newest.
- A slow source keeps a run going past the next two starts. With `reject`, both starts are
  skipped and recorded, and neither is recorded as a failure.
- A run that a person started is in progress at a start time. The overlap policy applies, because
  it covers every run of the same configuration.
- A scheduled run fails. The schedule starts a run at the next start, and keeps doing so. It does
  not pause itself. The earlier proposed change stays as it was.
- A scheduled run fails after it created its branch and before it opened its proposed change. The
  earlier proposed change is not closed. What happens to the new branch follows the prerequisite's
  rules for a failed run.
- The host restarts at 01:30 and the Sync service returns at 02:40. With a duration of one hour,
  the 02:00 run starts at 02:40. A late run follows the overlap policy like any other start.
- A cadence that does not divide a day, such as every 5 hours written as a cron expression,
  restarts at midnight and gives one shorter gap each day. This is accepted.
- A start falls in the hour that a daylight-saving change removes or repeats in the schedule's
  time zone. The schedule starts at most one run for that start.
- An operator deletes a schedule while one of its runs is in progress. The run ends normally and
  no further run starts. The open proposed change of the schedule stays in Infrahub.
- The configuration version that a schedule names can no longer be run. The run fails with the
  reason, like a run that a person starts.
- Two schedules name the same configuration. Each start follows the overlap policy of its own
  schedule, and two runs of that configuration never write at the same time.

## Requirements *(mandatory)*

### Functional Requirements

#### Review and write gates

- **FR-001**: A schedule MUST be able to write each run to a new Infrahub branch that Infrahub
  Sync creates and names from a pattern, and MUST open a proposed change from that branch to the
  default branch.
- **FR-002**: On a Sync API that requires approval, a schedule MUST use branch review. A request
  for a schedule that writes to the default branch MUST be refused when it is made, with a message
  that names the setting. A write to a branch that Infrahub Sync created for that run MUST NOT
  need a Sync approval.
- **FR-003**: On a Sync API that does not require approval, users MUST be able to choose for each
  schedule between branch review and a write to the default branch. Branch review MUST be the
  write target when the request names none. A write to the default branch MUST be an explicit
  choice in the request.
- **FR-004**: A schedule MUST carry a setting "write when records are left out", off by default.
  When it is off, a scheduled run whose plan leaves out records MUST stop after the plan and write
  nothing, as a `sync` that a person starts does. When it is on, the run MUST write the remaining
  records. The setting MUST apply to both write targets.
- **FR-005**: The schedule MUST record who turned the left-out setting on and when. Every
  scheduled run MUST record its left-out count in its result event.

#### Schedule record

- **FR-006**: A schedule MUST be a record separate from the configuration, and MUST name one
  configuration and one version of it. Registering a newer version MUST NOT change what the
  schedule runs.
- **FR-007**: When a newer version of the configuration exists than the one a schedule names, the
  Sync API MUST report it on the schedule, and the CLI MUST show a warning wherever it lists or
  shows the schedule.
- **FR-008**: Users MUST be able to change the version that a schedule names without creating a
  new schedule. The schedule MUST keep its identifier, cadence, settings and events.
- **FR-009**: Users MUST be able to state a schedule's cadence as a cron expression with a named
  time zone. The time zone MUST default to UTC. A cron expression or a time zone that is not valid
  MUST be refused when the request is made, with the reason.
- **FR-010**: When a schedule is created or changed, the Sync API MUST return its next start time,
  and the CLI MUST show it.
- **FR-024**: Users MUST be able to list schedules, view one schedule, and pause, resume, change
  and delete a schedule, from the CLI and the Sync API. A paused schedule MUST start no run and
  MUST keep its cadence, settings and events.

#### Owner

- **FR-011**: A scheduled run MUST record the schedule's owner as its actor, the trigger type
  `scheduled` and the schedule's identifier. A run that a person or another system starts MUST
  record a trigger type that is not `scheduled`.
- **FR-012**: Any authenticated actor MUST be able to create a schedule. Only its owner or an
  administrator MUST be able to pause, resume, change or delete it.
- **FR-013**: When the owner's token is no longer valid on the Sync API, the schedule MUST stop
  starting runs and MUST report that state. An administrator MUST be able to take over the
  schedule, and then becomes its owner.

#### Overlap and missed starts

- **FR-014**: A schedule MUST carry an overlap policy, `reject` or `queue`, with `reject` as the
  default. The policy MUST apply when any run of the same configuration is in progress, whoever
  started it.
- **FR-015**: With `reject`, the start MUST be skipped and recorded as an event that names the run
  in progress. A skipped start MUST NOT be recorded as a failure.
- **FR-016**: With `queue`, at most one run of the schedule MUST wait. A further start while one
  waits MUST be skipped and recorded.
- **FR-017**: A schedule MUST carry a "start late by at most" duration, with a default of one
  hour. When the Sync service returns after one or more missed starts, it MUST start one run for
  each schedule whose most recent missed start is inside that duration, however many starts were
  missed.
- **FR-018**: A missed start outside that duration MUST be skipped. Every missed start MUST be
  recorded as an event that states whether a late run started.
- **FR-019**: A late run MUST follow the schedule's overlap policy like any other start.

#### Proposed changes

- **FR-020**: After a scheduled run finishes its write and opens its proposed change, the schedule
  MUST close the previous open proposed change that it created, record on it a link to the new
  one, and delete its branch. A failed run MUST replace nothing.
- **FR-021**: The schedule MUST NOT close a proposed change that a person approved in Infrahub,
  and MUST NOT delete its branch. It MUST close only proposed changes, and delete only branches,
  that it created.
- **FR-022**: A scheduled run whose plan has no change MUST create no branch and no proposed
  change.

#### Events

- **FR-023**: Scheduling MUST record these events in the event record that Infrahub Sync already
  keeps, each with the schedule's identifier:
    - a schedule was created, changed, paused, resumed or deleted
    - a start was skipped because of the overlap policy
    - a start was missed, and whether a late run started
    - a schedule stopped because its owner's token is no longer valid, and a schedule was taken
      over
    - a proposed change was opened, and a proposed change replaced an earlier one
- **FR-025**: No event, schedule record, message or log line of scheduling MUST hold a credential
  or source data.

#### Requirements for each user story

| User story | Requirements |
| --- | --- |
| 1 (P1) | FR-001, FR-002, FR-006, FR-008 to FR-012, FR-014, FR-015, FR-020 to FR-025 |
| 2 (P2) | FR-003, FR-004, FR-005 |
| 3 (P3) | FR-007, FR-013, FR-016 to FR-019 |

In user story 1, FR-014 covers the `reject` policy only, and a missed start is skipped and
recorded with no late run. The `queue` policy and the late run arrive with user story 3.

### Key Entities *(include if feature involves data)*

- **Schedule**: new. Names one configuration, one version of it and one owner. Holds the cadence,
  the time zone, the write target, the left-out setting with who turned it on and when, the
  overlap policy, the "start late by at most" duration, and its state: active, paused, or stopped
  because the owner's token is no longer valid.
- **Run**: existing. Gains a trigger type and, for a scheduled run, the schedule's identifier.
- **Event**: existing. Gains the event types of FR-023. Together they are the history of a
  schedule.
- **Configuration version**: existing, not changed.
- **Approval**: existing, not changed for a run that a person starts.
- **Infrahub branch and proposed change**: exist in Infrahub. Infrahub Sync creates them through
  the prerequisite, and records which ones a schedule created.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator creates a working schedule with one command or one request, and views
  its next start time in the answer.
- **SC-002**: Over 30 consecutive starts of a schedule with the Sync service running, every start
  produces either a run or a recorded skipped start. None is missing from the history.
- **SC-003**: On a Sync API that requires approval, no scheduled run changes the default branch
  without a merge by a person in Infrahub.
- **SC-004**: At any time, a schedule has at most one open proposed change that nobody has
  approved.
- **SC-005**: For every scheduled run, the run history states that a schedule started it, which
  schedule, which configuration version and who owns the schedule.
- **SC-006**: Two runs of the same configuration never write at the same time, whoever or whatever
  started them.

## Prerequisites

User story 1 cannot be built before two capabilities exist. They are parts of INFP-655 and are not
part of this specification. This specification depends on them through this contract:

- **Create a branch for a run.** Given a run and a branch name, Infrahub Sync creates that branch
  in Infrahub from the default branch, and the run writes to it. The capability also deletes a
  branch that it created.
- **Open a proposed change for a run.** Given a run that finished its write to such a branch,
  Infrahub Sync opens a proposed change from that branch to the default branch, and returns its
  identifier. The capability also reads whether a person approved a proposed change, and closes a
  proposed change with a comment.

Who delivers these two capabilities is not decided, and neither is their order relative to the
recovery of interrupted runs (INFP-650), which is the current priority.

User stories 2 and 3 do not need the prerequisite, except acceptance scenario 5 of user story 2.

## Assumptions

- **Delivery of events to the outside is a separate specification.** An event system will deliver
  the events of Infrahub Sync, by webhook or another channel, for successful and failed runs,
  whoever started them. Until it exists, a team reads a schedule's events with `GET /events` or
  `infrahub-sync events`. Whether that event system is delivered before user story 1 is not
  decided.
- **Telemetry is a separate specification.** Scheduling adds nothing to it.
- **Constitution principle I and user story 2.** Principle I asks for a person's approval of every
  `sync`. User story 1 meets it, because nothing reaches the default branch before a person merges
  in Infrahub. User story 2 does not meet it as written: a person gives the instruction once, when
  they create the schedule. This specification assumes that principle I will be amended to state
  that creating a schedule with an explicit choice of the default branch is that deliberate act.
  The product owner has not confirmed this. User story 2 must not be delivered before they do.
- **A new decision record is needed** for the rule that a write to a branch Infrahub Sync created
  for a run needs no Sync approval. The rule amends ADR 0037.
- **Branch name pattern.** The default pattern follows the one Infrahub MCP uses for its session
  branches: a fixed prefix, the schedule's name, the date and a short random suffix.
- **A failing schedule keeps starting.** A source outage ends without anyone acting, and the next
  run then succeeds.
- **Shared review duty.** A team in which several people manage one schedule makes them
  administrators, or creates the schedule with a shared team token.
- **The web interface** shows schedules from the same Sync API. It is built separately and is not
  part of this specification.
- **Infrahub is the destination.** It is the only destination measured for version 3.
- **Documentation.** The user documentation states today that there is no built-in schedule. It
  is updated with this work.

## Out of Scope

- A schedule that only creates plans.
- A standing approval on a schedule.
- A webhook on each schedule, delivery of events to the outside, and telemetry.
- A limit that stops a scheduled run when more than a set number of records are left out.
- A cadence stated as an interval counted from the creation of the schedule, and named fields such
  as a time of day and a list of days.
- Protection for an edit that a person makes on a schedule's branch.
- Following a proposed change's merge state from the run record, and the other parts of INFP-655.
- Starting a run from a change in Infrahub (INFP-658).
- The web interface for schedules.
- Building the two capabilities of [Prerequisites](#prerequisites).
