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
- **Owner**: the actor who created the schedule, or the administrator who took it over. An
  actor is a name that the Sync API maps to a token.
- **Skipped start**: a start for which no run was started, because of the overlap policy or
  because the start was missed by more than the allowed duration.
- **Missed start**: a start whose time passed while the worker or the task manager of the Sync
  service was not running.

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

**Independent Test**: In the unit tier, with a clock that the test controls, create a schedule,
let two starts pass and check the runs and the events. On the development stack, in real time,
create a schedule with branch review, let two starts pass with a changed source, and check the
branches and the proposed changes. The second part needs the prerequisite of
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
   the outcome `no-change`, and Infrahub holds no branch and no proposed change of that run.
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
13. **Given** a cron expression or a time zone that is not valid, or a name that another
    schedule holds, **When** an operator asks for a schedule, **Then** the request is refused
    with the reason and no schedule is created.
14. **Given** a schedule whose owner's actor no longer exists on the Sync API, **When** a start
    passes, **Then** no run starts and the schedule reports that it stopped for that reason.
15. **Given** that stopped schedule and an owner's actor that exists again, **When** the owner
    resumes the schedule, **Then** it is active and the next start produces a run.
16. **Given** a configuration version that declares its own destination branch, **When** an
    operator asks for a schedule with branch review for it, **Then** the request is refused with
    the reason.
17. **Given** a scheduled run with branch review, **When** a person asks to verify, apply or
    resume it, **Then** the request is refused.
18. **Given** a schedule, **When** an operator lists the events of that schedule, **Then** the
    list holds its creation, each skipped start, each proposed change that was opened or
    replaced, and the events of its runs.

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
6. **Given** a schedule with branch review whose left-out setting is off, **When** a plan leaves
   out records, **Then** the run stops after the plan, Infrahub holds no branch of that run, and
   the run's result tells the team to correct the source records or to turn the setting on.

---

### User Story 3 - Operating a schedule over time (Priority: P3)

After weeks of use, an operator needs more control. A long run should be followed by the next one
as soon as it ends, so they choose the `queue` overlap policy. After a restart of the host, a
nightly run that was missed by 40 minutes should still happen, and one that was missed by three
hours should not run in working hours. When a colleague registers a newer version of the
configuration, the operator wants to be told. When the operator leaves the team and their actor
is removed from the Sync API, an administrator takes the schedule over.

**Why this priority**: These are needs of long operation, not of the first night. Until this story
is delivered, a missed start is skipped and recorded, and only the `reject` policy exists.

**Independent Test**: In the unit tier, with a clock that the test controls, let start times
pass while the worker does not run, start a run before a start time, register a newer version,
and take over a stopped schedule. Check the runs and the events each time.

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
7. **Given** a schedule that stopped because its owner's actor no longer exists, **When** an
   administrator takes it over, **Then** the administrator is its owner and the next start
   produces a run.
8. **Given** a late run and another run of the same configuration in progress, **When** the
   Sync service returns, **Then** the late run follows the schedule's overlap policy.

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
- A scheduled run fails, is interrupted or is cancelled after it created its branch. The earlier
  proposed change is not closed. The schedule holds a record of the new branch, and its next
  successful run deletes it. A person cannot resume such a run.
- A scheduled run finishes its write, and opening the proposed change fails. The write stays on
  the branch, the run records the failure, and the next successful run deletes that branch.
- A person approved a schedule's proposed change and did not merge it. The next run opens a
  second proposed change that holds the same changes and the newer ones. The team merges one
  and closes the other.
- A person's `plan` or `verify` of the same configuration is in progress at a start time. The
  overlap policy applies to it too.
- An earlier run of the configuration ended with an outcome that a person must reconcile. A
  scheduled run still starts, as a `sync` that a person starts does today.
- The host restarts at 01:30 and the Sync service returns at 02:40. With a duration of one hour,
  the 02:00 run starts at 02:40. A late run follows the overlap policy like any other start.
- A cadence that does not divide a day, such as every 5 hours written as a cron expression,
  restarts at midnight and gives one shorter gap each day. This is accepted.
- A start falls in the hour that a daylight-saving change removes or repeats in the schedule's
  time zone. The schedule starts at most one run for that start.
- An operator deletes a schedule while one of its runs is in progress. The run ends normally and
  no further run starts. The open proposed change of the schedule stays in Infrahub.
- The configuration version that a schedule names can no longer be run. No run is created, and
  the start is recorded as failed with the reason.
- The Sync API cannot reach its task manager when an operator creates or changes a schedule. The
  request reports it, the schedule is stored, and it starts no run until it is handed over.
- The Sync API is not running at a start time. The run still starts, because the Sync API takes
  no part in a start.
- Two schedules name the same configuration. Each start follows the overlap policy of its own
  schedule, and two runs of that configuration never write at the same time.
- The Sync API is restarted with required approval turned on, while a schedule writes to the
  default branch. At its next start the schedule starts no run, stops, and reports that reason.
  It becomes active again when its owner changes its write target to branch review.
- The Sync service was not running for so long that one schedule missed more than 500 starts.
  The 500 most recent are recorded one by one, and the older ones in one event that holds their
  count and their first and last start times.

## Requirements *(mandatory)*

### Functional Requirements

#### Review and write gates

- **FR-001**: A schedule MUST be able to write each run to a new Infrahub branch that Infrahub
  Sync creates and names from a fixed pattern, and MUST open a proposed change from that branch
  to the default branch.
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
  records. The setting MUST apply to both write targets. With branch review, a run that stops
  after the plan MUST leave no branch, and its result MUST tell the team to correct the source
  records or to turn the setting on.
- **FR-005**: The schedule MUST record who turned the left-out setting on and when. Every
  scheduled run MUST record its left-out count in its result event.

#### Schedule record

- **FR-006**: A schedule MUST be a record separate from the configuration, and MUST name one
  configuration and one version of it. Registering a newer version MUST NOT change what the
  schedule runs. A schedule MUST have a name that the operator chooses, unique among the
  schedules that are not deleted.
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
- **FR-013**: When the owner's actor no longer exists on the Sync API, the schedule MUST stop
  starting runs and MUST report that state and its reason. A resume of a stopped schedule MUST
  succeed when the reason no longer holds, and MUST be refused when it still holds.
- **FR-026**: An administrator MUST be able to take over a schedule, and then becomes its owner.
  A schedule that stopped because its owner's actor no longer exists MUST become active.

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
- **FR-022**: A scheduled run whose plan has no change MUST leave no branch and no proposed
  change in Infrahub when it ends.
- **FR-027**: A request for a schedule with branch review MUST be refused when the configuration
  version declares its own destination branch, because a run of that version writes to the
  declared branch. A schedule that writes to the default branch MUST write to the branch that a
  run of a person writes to for that version.
- **FR-028**: Requests to verify, apply or resume a scheduled run with branch review MUST be
  refused. Its plan and its results MUST stay readable.

#### Events

- **FR-023**: Scheduling MUST record these events in the event record that Infrahub Sync already
  keeps, each with the schedule's identifier:
    - a schedule was created, changed, paused, resumed or deleted
    - a start was skipped because of the overlap policy
    - a start was missed, and whether a late run started
    - a start failed because no run could be created
    - a schedule stopped, with the reason, and a schedule was taken over
    - a proposed change was opened, replaced an earlier one, was left in place because a person
      approved, merged or closed it, or could not be opened

  Every event of a scheduled run MUST also name its schedule.
- **FR-025**: No event, schedule record, message or log line of scheduling MUST hold a credential
  or source data.

#### Requirements for each user story

| User story | Requirements |
| --- | --- |
| 1 (P1) | FR-001, FR-002, FR-006, FR-008 to FR-015, FR-020 to FR-025, FR-027, FR-028 |
| 2 (P2) | FR-003, FR-004, FR-005 |
| 3 (P3) | FR-007, FR-016 to FR-019, FR-026 |

In user story 1, FR-014 covers the `reject` policy only, and a missed start is skipped and
recorded with no late run. The `queue` policy and the late run arrive with user story 3. Until
then, a start that the worker can begin only more than five minutes late does not run, and the
schedule does not show a "start late by at most" duration.

### Key Entities *(include if feature involves data)*

- **Schedule**: new. Has a name, and names one configuration, one version of it and one owner.
  Holds the cadence, the time zone, the write target, the left-out setting with who turned it on
  and when, the overlap policy, the "start late by at most" duration, and its state: active,
  paused, stopped or deleted. A stopped schedule holds the reason: the owner's actor no longer
  exists, or the Sync API now requires approval for its write target.
- **Run**: existing. Gains a trigger type and, for a scheduled run, the schedule's identifier
  and the start time of the cadence that it serves.
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
  produces a run, a recorded skipped start or a recorded failed start. None is missing from the
  history, and no start produces two runs.
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
  in Infrahub from the default branch, and the run plans, verifies and writes on it. The
  capability also deletes a branch that it created.
- **Open a proposed change for a run.** Given a run that finished its write to such a branch,
  Infrahub Sync opens a proposed change from that branch to the default branch, and returns its
  identifier. The capability also reads whether a person approved a proposed change, and closes a
  proposed change with a comment.

Who delivers these two capabilities is not decided, and neither is their order relative to the
recovery of interrupted runs (INFP-650), which is the current priority.

User stories 2 and 3 do not need the prerequisite, except acceptance scenarios 5 and 6 of user
story 2.

## Assumptions

- **Delivery of events to the outside is a separate specification.** An event system will deliver
  the events of Infrahub Sync, by webhook or another channel, for successful and failed runs,
  whoever started them. Until it exists, a team reads a schedule's events with `GET /events` or
  `infrahub-sync events`. Whether that event system is delivered before user story 1 is not
  decided.
- **Telemetry is a separate specification.** Scheduling adds nothing to it.
- **Constitution principle I.** Principle I asks for a person's approval of every `sync`, because
  a `sync` changes a destination system.
    - User story 1 meets its purpose: nothing reaches the default branch before a person merges
      in Infrahub. Its text does not say that a write to a review branch is allowed without a
      person's approval, and a branch is part of the destination system. This specification
      assumes that the principle will be amended to say so.
    - User story 2 does not meet it: a person gives the instruction once, when they create the
      schedule. This specification assumes that the principle will be amended to state that
      creating a schedule with an explicit choice of the default branch is that deliberate act.

  The product owner has not confirmed either amendment. User story 2 must not be delivered
  before they confirm the second.
- **An actor name is not a fixed identity.** The Sync API maps actor names to tokens in one
  setting. A changed token under the same name keeps the schedule running. A name that is
  removed and later given to another person makes that person the owner of the schedule.
- **An approved proposed change that is not merged.** The schedule keeps starting runs, and the
  next proposed change holds the same changes again. Whether the schedule should wait instead is
  a question for the product owner.
- **A new decision record is needed** for the rule that a write to a branch Infrahub Sync created
  for a run needs no Sync approval. The rule amends ADR 0037.
- **Branch name pattern.** The pattern is fixed and follows the one Infrahub MCP uses for its
  session branches: a fixed prefix, the schedule's name, the date and a short suffix. A schedule
  cannot set its own pattern.
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
