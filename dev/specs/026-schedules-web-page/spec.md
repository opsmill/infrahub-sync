# Feature Specification: Manage schedules from the web interface

**Feature Branch**: `026-schedules-web-page`

**Created**: 2026-11-19

**Status**: Draft

**Input**: The maintainer's request on 2026-11-19, after finding no schedules in the web interface of
the development stack: "let's build the spec to have the scheduler page in the UI as well". The
brief given with it: operators list schedules, view one schedule, create, change, pause, resume,
delete and take over a schedule from the web interface, with the permissions that the Sync API
enforces, and follow from a schedule to the runs it started. No change to the scheduling engine
or to the Sync API, unless a gap blocks the page.

Sources:

- [Spec 021](../archive/021-scheduled-runs/spec.md), scheduled runs. Lines 556 and 574 put the web
  interface for schedules out of scope.
- [Spec 021, HTTP contract](../archive/021-scheduled-runs/contracts/http-api.md): the schedule
  resource and the schedule routes.
- [`infrahub_sync/service/schedules.py`](../../../infrahub_sync/service/schedules.py) and
  [`infrahub_sync/service/schedule_routes.py`](../../../infrahub_sync/service/schedule_routes.py):
  the schedule routes and their refusals.
- [`infrahub_sync/cli.py`](../../../infrahub_sync/cli.py), `_STOPPED_ACTIONS`: the action that each
  reason of a stopped schedule needs, as the CLI states it.
- [`infrahub_sync/service/presentation.py`](../../../infrahub_sync/service/presentation.py): the
  Sync API answers `owner` and `left_out_set_by` with account names, not account identifiers.
- [`frontend/src/api/types.generated.ts`](../../../frontend/src/api/types.generated.ts): the API
  types of the web interface already include the schedule routes and the run fields
  `trigger_type`, `schedule_id` and `scheduled_start_at`.
- [`frontend/src/routes/__root.tsx`](../../../frontend/src/routes/__root.tsx): the navigation has
  the entries Configurations, Runs, Accounts (administrators only) and Account.
- [`frontend/src/features/runs/RunPage.tsx`](../../../frontend/src/features/runs/RunPage.tsx): the
  pattern for an action that only the account that started a run or an administrator may take.
- [`AGENTS.md`](../../../AGENTS.md), section "Sync frontend": name the account that acted, with the
  name the Sync API returns; never show an account identifier.

## Background

### What a team can do today

- An operator creates a schedule with `infrahub-sync schedules create`. A schedule starts a `sync`
  run of one configuration version on a cron cadence, in a named time zone.
- A schedule writes each run either to a new Infrahub branch with a proposed change (write target
  "branch review", the default) or to the default branch. On a Sync API that requires approval, a
  schedule must use branch review.
- The CLI lists schedules, shows one, changes, pauses, resumes and deletes one, and lets an
  administrator take one over. Only the owner of a schedule or an administrator may change,
  pause, resume or delete it.
- A schedule stops by itself for one of three reasons. Each reason needs a different action:

  | Reason | Action the CLI states |
  | --- | --- |
  | `owner-not-found` | An administrator takes the schedule over, or enables the owner's account and resumes the schedule. |
  | `approval-required` | Change the write target to branch review; the schedule is then active again. |
  | `repeated-failures` | Check the last runs of the schedule, then resume it. |

- Runs started by a schedule carry the trigger type `scheduled`, the schedule's identifier and the
  time the schedule meant to start them.

### What a team cannot do today

- **View schedules in the web interface.** The web interface has no page and no navigation entry
  for schedules. A person who uses only the web interface does not know that a configuration runs
  on a schedule.
- **Tell a scheduled run from a manual run in the web interface.** The runs list and the run page
  do not show the trigger type or the schedule.
- **Find out why a schedule stopped without a terminal.** The stop reason and the action it needs
  are shown only by `infrahub-sync schedules show`.

## Decisions

These choices were made while writing this specification, without a discussion with the
maintainer. Each one can be changed during review.

1. **A "Schedules" entry in the main navigation**, next to "Runs", with a list page and a page for
   each schedule. The configuration page also shows the configuration's schedule, because a
   configuration has at most one schedule that is not deleted.
2. **Every signed-in account sees the actions.** An action the account may not take is shown as
   unavailable with the reason, as the run page already does for run actions. The Sync API stays
   the authority: a refusal it returns is shown as it is.
3. **The cadence is entered as a cron expression.** The page shows the next start that the Sync API
   returns after a save. The web interface does not compute start times itself, so it can never
   show a time the Sync API disagrees with.
4. **The time zone defaults to the viewer's browser time zone.** The CLI defaults to UTC. A person
   who sets "every day at 02:00" in a web form expects their own time zone.
5. **Schedule history beyond runs is not shown.** Skipped starts, missed starts and stop events are
   in the event record of the Sync API. Showing them is not part of the brief and is left out.
6. **No change to the Sync API.** Every action uses an existing route. If the plan finds a gap that
   blocks a page, the gap is added to this specification before any API change.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View schedules and their state (Priority: P1)

An operator opens the web interface to learn which configurations run on a schedule, whether each
schedule is running, and when the next run starts. They open one schedule to view its full
settings, its owner, its last run and, for a stopped schedule, why it stopped and what to do.

**Why this priority**: Viewing comes before any change. It also answers the question that started
this work: "is anything scheduled, and is it working?"

**Independent Test**: Create schedules with the CLI in the active, paused and stopped states, then
open the web interface. Each schedule appears in the list with its state and next start, and each
schedule page shows the fields of the schedule resource.

**Acceptance Scenarios**:

1. **Given** three schedules exist, **When** the operator opens "Schedules", **Then** the list
   shows each schedule with its name, configuration and version, state, cadence, time zone, next
   start and owner, newest first.
2. **Given** a deleted schedule exists, **When** the operator opens the list, **Then** the deleted
   schedule is not shown, **and When** the operator filters by the state "deleted", **Then** it is
   shown.
3. **Given** a schedule stopped with the reason `repeated-failures`, **When** the operator opens it,
   **Then** the page states the reason in words, the count of failed runs in a row, and the action
   it needs: "Check the last runs of the schedule, then resume it."
4. **Given** a schedule whose configuration has a newer registered version, **When** the operator
   opens it, **Then** the page states that a newer version exists and which one.
5. **Given** a schedule that the task manager has not received yet (`prefect_synced` is false),
   **When** the operator opens it, **Then** the page warns that the schedule starts no run until
   the Sync API hands it to the task manager.
6. **Given** a schedule with an open review branch, **When** the operator opens it, **Then** the
   page shows the branch name and the run that created it.
7. **Given** the owner's account was deleted, **When** the operator views the schedule, **Then**
   the owner is shown as the name the Sync API returns, such as `Deleted account 3f2a9c`.

---

### User Story 2 - Follow a schedule to the runs it started (Priority: P1)

An operator who sees a stopped or failing schedule opens the runs it started, to read why they
failed. From a run, they go back to the schedule that started it.

**Why this priority**: The action that `repeated-failures` needs is "check the last runs". Without
this link, the operator cannot carry out that action in the web interface.

**Independent Test**: Let a schedule start two runs, start one manual run of the same
configuration, then open the schedule page and the runs list.

**Acceptance Scenarios**:

1. **Given** a schedule that started runs, **When** the operator opens the schedule page, **Then**
   the page links to its last run and to the list of all its runs.
2. **Given** the operator follows the link to all runs, **Then** the runs list shows only the runs
   of that schedule, and states that it is filtered by that schedule.
3. **Given** the runs list without a filter, **When** it shows a run that a schedule started,
   **Then** the run is marked as scheduled, and the mark links to the schedule.
4. **Given** the page of a scheduled run, **Then** it shows the schedule's name with a link, and the
   time the schedule meant to start the run.
5. **Given** a run started by a schedule that was later deleted, **When** the operator follows the
   link, **Then** the schedule page opens and shows the state "deleted".

---

### User Story 3 - Pause, resume and delete a schedule (Priority: P2)

An operator pauses a schedule before maintenance on the source, resumes it afterwards, resumes a
schedule stopped after repeated failures once the cause is fixed, or deletes a schedule that is no
longer needed.

**Why this priority**: These are the actions an operator takes most often on an existing schedule,
and the actions that a stopped schedule needs.

**Independent Test**: As the owner, pause, resume and delete a schedule; as another account that
is not an administrator, check that the actions are unavailable with a reason.

**Acceptance Scenarios**:

1. **Given** an active schedule owned by the signed-in account, **When** they pause it with an
   optional reason, **Then** the page shows the state "paused" and no next start.
2. **Given** a paused schedule, **When** the owner resumes it, **Then** the page shows the state
   "active" and the next start that the Sync API returns.
3. **Given** a schedule stopped with `repeated-failures`, **When** the owner resumes it, **Then** it
   is active and the count of failed runs in a row is zero.
4. **Given** a stopped schedule whose reason still holds, **When** the owner resumes it, **Then**
   the page shows the refusal of the Sync API, which names the reason and the action it needs,
   and the page shows the schedule as the Sync API stored it.
5. **Given** a schedule, **When** the owner chooses to delete it, **Then** the page asks for
   confirmation and states that a run in progress is not cancelled and that an open review branch
   and its proposed change stay in Infrahub. **When** they confirm, **Then** the schedule shows
   the state "deleted" and no action except opening its runs.
6. **Given** a schedule owned by another account, **When** a signed-in account that is not an
   administrator opens it, **Then** pause, resume, change and delete are unavailable, with the
   reason "Only the owner of this schedule or an administrator can do this."
7. **Given** an administrator who does not own the schedule, **Then** every action is available.

---

### User Story 4 - Create a schedule (Priority: P2)

An operator who has registered a configuration creates a schedule for it from the web interface,
starting from the configuration page or from the schedules list.

**Why this priority**: Creating a schedule is less frequent than acting on one. The CLI already
covers it, and every other story depends on a schedule existing.

**Independent Test**: Create a schedule for a configuration that has none, then try to create a
second one for the same configuration.

**Acceptance Scenarios**:

1. **Given** a configuration with no schedule, **When** the operator opens its page, **Then** the
   page offers "Create schedule", with the configuration and its latest version filled in.
2. **Given** the create form, **Then** it asks for a name, the version, a cron expression, a time
   zone (the browser's time zone filled in), the write target (branch review filled in), the
   overlap policy ("reject" filled in), how late a start may be ("1 hour" filled in) and an
   optional reason.
3. **Given** a valid form, **When** the operator saves, **Then** the schedule page opens and shows
   the next start that the Sync API returned.
4. **Given** an invalid cron expression, **When** the operator saves, **Then** the form keeps the
   values entered and shows the message of the Sync API next to the cadence.
5. **Given** a Sync API that requires approval, **Then** the write target "default branch" is
   unavailable, with the reason that this Sync API requires an approved plan before every write to
   the default branch.
6. **Given** the write target "branch review", **Then** the setting "write when records are left
   out" is unavailable, with the reason that a run with branch review always writes the remaining
   records and its proposed change states the count left out.
7. **Given** the configuration already has a schedule, **When** the operator saves, **Then** the
   form shows the refusal, which names the existing schedule, with a link to it.
8. **Given** the operator saves and the network fails before the answer, **When** they save again,
   **Then** only one schedule exists.

---

### User Story 5 - Change a schedule's settings (Priority: P3)

An operator moves a schedule to a newer configuration version, changes its cadence or time zone,
or changes its write target.

**Why this priority**: Changes are rarer than pausing and resuming. Moving to a newer version is
the most common change: registering a version does not change what a schedule runs.

**Independent Test**: Register a newer version of a scheduled configuration, then move the
schedule to it from the schedule page.

**Acceptance Scenarios**:

1. **Given** a schedule with a newer version available, **When** the owner chooses to change the
   version, **Then** the form offers the registered versions and fills in the newest one.
2. **Given** the change form, **Then** it shows the current values, and the name and the
   configuration are shown but cannot be changed.
3. **Given** the owner saves a change, **Then** the page shows the new values and the new next
   start.
4. **Given** the owner saves the form with no value changed, **Then** the form states that nothing
   changed and sends no request.
5. **Given** a schedule stopped with `approval-required`, **When** the owner changes the write target
   to branch review, **Then** the schedule is active again.
6. **Given** a change of the write target to the default branch while a review branch is open,
   **When** the owner saves, **Then** the page states that the open branch and its proposed change
   stay in Infrahub.
7. **Given** the owner turns on "write when records are left out", **Then** the page shows who
   turned it on and when, as the Sync API returns them.
8. **Given** another account changed the schedule after the form opened, **When** the Sync API
   refuses with `schedule-changed`, **Then** the form states that the schedule changed and offers
   to reload it.

---

### User Story 6 - Take over a schedule (Priority: P3)

An administrator takes over a schedule whose owner left the team, or whose owner's account was
deleted, so that it runs again under an account that exists.

**Why this priority**: It is needed only when an owner's account goes away. It is the only way
out of the stop reason `owner-not-found` without restoring the account.

**Independent Test**: Delete the account that owns a schedule, check that the schedule stops with
`owner-not-found`, then take it over as an administrator.

**Acceptance Scenarios**:

1. **Given** a schedule stopped with `owner-not-found`, **When** an administrator opens it, **Then**
   the page states the reason and offers "Take over".
2. **Given** the administrator takes it over with a reason, **Then** they are the owner, the schedule
   is active, and its next runs record them as the account that started them. The confirmation
   states that last point before the take-over.
3. **Given** the schedule writes to the default branch and the Sync API requires approval, **When**
   the administrator takes it over, **Then** they are the owner, and the page shows the schedule
   stopped with the reason `approval-required` and the action it needs.
4. **Given** an account that is not an administrator, **Then** "Take over" is not offered.
5. **Given** an active schedule owned by another account, **Then** an administrator can take it
   over as well.

---

### Edge Cases

- **No schedule exists.** The list states that no schedule exists and offers "Create schedule".
  It also states that a configuration is needed first when none is registered.
- **The schedule identifier in the address does not exist.** The page states that the schedule was
  not found and links to the list.
- **More schedules than one page.** The list loads further pages as the runs list does.
- **An action and a refresh cross.** The page refreshes the list and the open schedule on the same
  interval as the runs pages. After an action, the page shows the schedule resource that the
  action returned, so a refresh that started earlier does not show the old state.
- **The signed-in account's session ends.** The page asks the person to sign in again, as every
  other page does.
- **A pause of a paused schedule or a resume of an active schedule** (two tabs, or two people).
  The Sync API answers success and changes nothing; the page shows the current state.
- **A refused write target.** `schedule-branch-declared` (the version declares its own branch) and
  `approval-required-for-schedule` are shown next to the write target, with the message of the
  Sync API.
- **The task manager is unreachable on save.** The Sync API stores the schedule and answers
  `orchestration-unavailable`. The page opens the stored schedule and shows the warning of User
  Story 1, scenario 5.
- **A credential in a field.** The Sync API refuses with `secret-parameter-refused`. The form shows
  the refusal and does not repeat the value of the field in the message.
- **A deleted schedule.** It shows no action that changes it. Its runs stay reachable.

## Requirements *(mandatory)*

### Functional Requirements

#### Viewing (User Stories 1 and 2)

- **FR-001**: The web interface MUST have a "Schedules" entry in its main navigation for every
  signed-in account.
- **FR-002**: The schedules list MUST show, for each schedule: name, configuration name, version,
  state, cron expression, time zone, next start in the viewer's local time, and owner. It MUST
  list schedules newest first and load further pages on demand.
- **FR-003**: The schedules list MUST leave out deleted schedules by default, and MUST let the
  viewer filter by state, including "deleted", and by configuration.
- **FR-004**: The schedule page MUST show every field of the schedule resource that a person can
  act on: name, configuration and version, newer version, cron expression, time zone, write
  target, "write when records are left out" with who set it and when, overlap policy, how late a
  start may be, state, stop reason, owner, next start, waiting start, last run, count of failed
  runs in a row, open review branch, created and updated times.
- **FR-005**: For a stopped schedule, the schedule page MUST state the reason in words and the
  action it needs, with the same meaning as the CLI's text for each reason. A reason the web
  interface does not know MUST be shown as the Sync API returns it, with no action.
- **FR-006**: The schedule page MUST warn when `prefect_synced` is false that the schedule starts no
  run until the Sync API hands it to the task manager.
- **FR-007**: Every account name MUST be shown as the Sync API returns it, including
  `Deleted account <short identifier>`. The web interface MUST NOT show an account identifier.
- **FR-008**: The schedule page MUST link to the schedule's last run and to the runs list filtered
  to the schedule's runs.
- **FR-009**: The runs list MUST accept a filter by schedule, MUST state when it is active, and
  MUST let the viewer remove it.
- **FR-010**: The runs list and the run page MUST mark a run that a schedule started, and MUST link
  to the schedule. The run page MUST show the time the schedule meant to start the run.
- **FR-011**: The configuration page MUST show the configuration's schedule that is not deleted,
  with its state and next start and a link to it, or offer to create one.

#### Acting (User Stories 3 to 6)

- **FR-012**: Every signed-in account MUST be able to create a schedule.
- **FR-013**: Change, pause, resume and delete MUST be available to the schedule's owner and to an
  administrator. For any other account they MUST be shown as unavailable with the reason.
  Whether the signed-in account is the owner MUST be decided by comparing its name with the
  schedule's owner name.
- **FR-014**: "Take over" MUST be offered only to an administrator, and only for a schedule that is
  not deleted and that the administrator does not own.
- **FR-015**: Every action that changes a schedule MUST accept an optional reason and send it to the
  Sync API.
- **FR-016**: Delete and take-over MUST ask for confirmation. The delete confirmation MUST state
  that a run in progress is not cancelled and that an open review branch and its proposed change
  stay in Infrahub. The take-over confirmation MUST state that the schedule's next runs record
  the administrator as the account that started them.
- **FR-017**: The create form MUST offer every setting of `POST /schedules`, with these values
  filled in: the configuration's latest version, the browser's time zone, branch review, overlap
  policy "reject", a late start of at most one hour, and "write when records are left out" off.
- **FR-018**: The change form MUST offer every setting that `POST /schedules/{schedule_id}/change`
  accepts, filled in with the current values, and MUST send only the settings the person changed.
  A form with no change MUST NOT send a request.
- **FR-019**: The default-branch write target MUST be unavailable, with the reason, when the Sync
  API requires approval. "Write when records are left out" MUST be unavailable, with the reason,
  when the write target is branch review.
- **FR-020**: Every refusal of the Sync API MUST be shown with its message, next to the field it
  concerns when it concerns one field (cron expression, time zone, name, write target). The form
  MUST keep the values the person entered.
- **FR-021**: Retrying a create or an action after a network failure MUST NOT create two schedules
  or apply the action twice. The web interface MUST reuse the retry key of the attempt that failed.
- **FR-022**: After an action, the page MUST show the schedule resource that the action returned,
  including the new next start.
- **FR-023**: A refusal with `schedule-changed` MUST offer to reload the schedule. Reloading MUST
  discard the values entered in the form only after the person confirms.

#### Documentation

- **FR-024**: The user documentation for schedules MUST describe where schedules are found in the
  web interface and which actions it offers.

### Key Entities

- **Schedule**: a record that starts a `sync` run of one configuration version on a cron cadence.
  It has a name, an owner, a state (active, paused, stopped, deleted), an optional stop reason,
  and the settings listed in FR-004. A configuration has at most one schedule that is not
  deleted.
- **Scheduled run**: a run with the trigger type `scheduled`, the identifier of the schedule that
  started it and the time the schedule meant to start it.
- **Signed-in account**: the account name and administrator flag that the Sync API returns for the
  person's session or token. It decides which actions are available.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator who uses only the web interface can answer "which configurations run on a
  schedule, and when does each one run next?" from one page.
- **SC-002**: For each of the three stop reasons, an operator can carry out the action it needs
  from the web interface, with no terminal.
- **SC-003**: An operator can go from a stopped schedule to the run that failed last in two
  selections or fewer.
- **SC-004**: Every action that the CLI's `schedules` commands offer is available in the web
  interface, with the same permission rules.
- **SC-005**: Retrying any schedule action after a network failure never creates two schedules and
  never applies one action twice.
- **SC-006**: No page of this feature shows an account identifier.

## Out of Scope

- Changes to the scheduling engine or the Sync API. If the plan finds a gap that blocks a page, the
  gap is added to this specification first.
- The event history of a schedule: skipped starts, missed starts and stop events.
- A preview of the next start times before saving, and a cadence entered with fields such as a time
  of day and a list of days.
- Links into Infrahub for the open review branch and its proposed change. Whether the web
  interface knows the address of the destination Infrahub is not known.
- A notification when a schedule stops.

## Assumptions

- The Sync API answers `owner` and `left_out_set_by` with account names, so the web interface can
  compare the signed-in account's name with the owner name. Account names are unique among
  accounts that are not deleted.
- The schedule routes and the run fields that this feature uses are already in the generated API
  types of the web interface.
- The web interface already refreshes lists on an interval and loads further pages of a list. The
  schedules pages use the same behavior.
- The browser offers the list of named time zones, and the Sync API accepts those names. A time
  zone the Sync API does not know is refused with `schedule-timezone-invalid`, and FR-020 covers
  it.
