# Feature Specification: Operator UI for the testing release, and the run list and paged plan endpoints it needs

**Feature Branch**: `011-operator-ui`

**Created**: 2026-10-15

**Status**: Draft

**Release**: MVP (the testing release). Every requirement in this specification belongs to the MVP.
Work it names as out of scope belongs to 3.0 or 3.x, as listed in
[V3 release scope](../v3-release-scope.md).

**Input**: User description: "A basic operator UI for the V3 testing release, plus the API endpoints it needs. The UI is standalone, served by the sync-api container on the same origin as the Sync HTTP API, and uses only the public API (so anything it does a script can do). It is not part of the Infrahub UI. A customer team can: list configurations with their versions; list runs with status, counts and duration; open one run's plan and view its summary by kind, its operations filterable by kind, its problems and its left-out records (from spec 009); start a plan; approve and apply a plan, including confirming the left-out count; view why a run failed or stopped. It does not edit configurations (YAML stays in the CLI or git), manage schedules, manage users (it uses the same API token as the CLI), or draw a graph of the plan. New API: GET /runs (list, with filters and paging) and plan detail returned in pages so the 16 MiB client response limit no longer blocks plan review; at tier M (10,207 mapped objects) a first-sync plan opens in the UI. Success: a customer team completes the P1 journey (first import from their NetBox, reviewed, applied) in the UI alone, without OpsMill running a command for them. This moves the UI (INFP-664, AD-5 said 'after recovery and operations') into the testing release; AD-5 needs an amendment. Scope = brief FR-006, FR-007, SC-002 (UI part), SC-006."

## Summary

Problem:

- A customer team that tests V3 can review a plan, apply it and read a run only through the CLI
  or direct calls to the Sync HTTP API. Testing customers are network or automation teams that
  run Sync v1 or v2. The gain V3 offers them over v2 is a reviewed plan, a list of bad source
  records before any write, and a recorded history of runs (idea brief, "Users and value").
  Without a UI, they view that gain only as JSON or terminal output.
- The Sync HTTP API has no route that lists runs. A team that does not keep its run IDs cannot
  find an earlier run.
- `GET /runs/{run_id}/plan` returns the whole plan in one response, and the client refuses any
  JSON response above 16 MiB (`_MAX_JSON_BYTES` in `infrahub_sync/client/client.py`). The idea
  brief states that this limit stops the review of a full plan at tier M (10,207 mapped objects).

What is needed:

- A standalone operator UI, served by the `sync-api` container on the same origin as the Sync
  HTTP API. It uses only the public Sync HTTP API, so a script can do anything the UI does
  (product decisions AD-5 and AD-14). It is not part of the Infrahub UI.
- Two additions to the Sync HTTP API: a run list with filters and paging, and plan detail
  returned in pages.
- With both, a customer team completes journey P1 (first import from their NetBox, reviewed,
  applied) in the UI, without OpsMill running a command for them.

Decided by the product owner on 2026-10-15:

- The UI can cancel a plan or an apply in progress (FR-024).
- In the MVP, the UI is reachable from the `sync-api` host only, on loopback. A person on another
  computer uses a tunnel they set up themselves. Access from other computers over TLS, through a
  reverse proxy, belongs to 3.0 (INFP-663) (FR-031).

This specification also owns these MVP blockers from
`infrahub-sync-process/product/path-to-3.0.md`:

- each peer's identity is printed twice per operation, so a full import cannot be reviewed
  ([#373](https://github.com/opsmill/infrahub-sync/issues/373), SYNC-259)
- the CLI has no command to list runs or to cancel a run (SYNC-211, SYNC-254)

Not decided yet: product sign-off on the UI in the MVP. Product decision AD-5 places the UI
"after recovery and operations". This specification moves a basic UI into the testing release,
so AD-5 needs an amendment before this work ships (FR-037).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review a first-import plan and its problems in the UI (Priority: P1)

The customer team has registered a NetBox configuration with the CLI. In the UI, they open the
list of configurations, select the configuration and version, enter the Infrahub branch and a
reason, and start a plan. When the plan finishes, they open it and view the counts by action and
by kind, the records left out, the records kept without a relationship, and the problems. They
filter the operations by kind and read
the operations of one kind, page by page.

**Why this priority**: reviewing the plan before anything is written is the main gain of V3 over
v2 (idea brief, "Users and value"). Every other story depends on a plan the team can open.

**Independent Test**: register the NetBox example configuration with planted bad records, start
a plan from the UI, and check that the UI shows the summary, every planted problem and every
left-out record that the Sync API returns for that plan.

**Acceptance Scenarios**:

1. **Given** a registered configuration, **When** the team starts a plan from the UI with a
   branch and a reason, **Then** the UI shows the new run with its phase, and updates the
   phase and outcome until the plan finishes or fails.
2. **Given** a finished plan, **When** the team opens it, **Then** the UI shows the counts of
   creates, updates, deletes not executed and records left out, in total and by kind.
3. **Given** a finished plan that holds problems (spec 009), **When** the team opens its problem
   list, **Then** each problem shows the kind, the source record identity, the problem type and
   the reason, and the team can filter the list by problem type and by kind.
4. **Given** a finished plan that leaves out records, **When** the team opens the left-out list,
   **Then** each entry shows the kind, the source record identity, and either its own problems
   or the record it depends on.
6. **Given** a finished plan that keeps records without an optional relationship (spec 009
   FR-005), **When** the team opens the kept list, **Then** each entry shows the kind, the source
   record identity, the relationship and the left-out record it names.
7. **Given** an update or a create with relationships, **When** the team reads the operation,
   **Then** each peer's identity appears once.
5. **Given** a first-sync plan at tier M (10,207 mapped objects), **When** the team opens it in
   the UI, **Then** the summary appears, and the team can read every operation of any kind page
   by page, without an error about the response size.

---

### User Story 2 - Approve and apply the reviewed plan, confirming the records left out (Priority: P1)

After reviewing the plan, the team approves it in the UI. The UI shows the checksum of the plan
they reviewed, the target branch, the count of operations to write and, when the plan leaves out
records, that count. The team enters a reason, ticks the checkbox "I have reviewed the N records
left out" when the plan leaves out records, and applies. The UI follows the apply until it finishes and then shows what was written.

**Why this priority**: journey P1 ends with an apply. Without it, the team must switch to the CLI
to finish the journey, and SC-006 of the idea brief fails.

**Independent Test**: apply a plan that leaves out records from the UI, and check that the apply
is refused until the team ticks the left-out checkbox, and that only the valid records are
written.

**Acceptance Scenarios**:

1. **Given** a reviewed plan with no records left out, **When** the team approves and applies it
   with a reason, **Then** the UI sends the checksum of the plan it showed, and shows the run
   until the apply finishes.
2. **Given** a reviewed plan that leaves out 12 records, **When** the team approves it, **Then**
   the UI states that 12 records are left out and will not be written, and requires the team to
   tick the checkbox "I have reviewed the 12 records left out" before the apply request is sent.
   The request carries `confirm_left_out: true`.
3. **Given** a plan whose stored checksum no longer matches the plan the team reviewed, **When**
   the team applies, **Then** the Sync API refuses the apply and the UI shows the reason and
   that a new plan is needed.
4. **Given** a completed apply, **When** the team opens the run, **Then** the UI shows the count
   of operations written by action and kind.

---

### User Story 3 - View why a run failed or stopped (Priority: P1)

A plan or an apply fails, for example because Infrahub rejects a write or the NetBox token is
rejected. The team opens the run in the UI and views the stage that failed, the failure type,
the system involved, Infrahub's error code when there is one, the node kind and attribute, and
the message. For a stopped apply, they view the operations written and the operation that
failed, and the UI states that a new plan is needed.

**Why this priority**: today the cause of a failed run appears only in the worker container
logs (idea brief FR-003). Brief SC-005 requires the UI to show the cause for 100% of failed runs.

**Independent Test**: for each failure type that spec 009 defines, cause the failure, open the
run in the UI and check that each part of the cause appears without reading container logs.

**Acceptance Scenarios**:

1. **Given** an apply stopped by an Infrahub rejection, **When** the team opens the run,
   **Then** the UI shows Infrahub's message and code, the operation that failed with its kind
   and source record identity, the count of operations written, and that a new plan is needed.
2. **Given** a plan that failed because NetBox rejected the token, **When** the team opens the
   run, **Then** the UI shows the stage (plan), the system (NetBox) and the failure type, and no
   part of the token.
3. **Given** a run that ended as `ambiguous` or needs reconciliation, **When** the team opens it,
   **Then** the UI shows that state and the reconciliation evidence the Sync API returns.

---

### User Story 4 - Find earlier runs and configurations (Priority: P2)

The team opens the run list to find an earlier plan or apply. They view each run's
configuration and version, operation, phase and outcome, counts, start time and duration. They
filter the list by configuration, operation, phase and outcome, and move through it page by
page. They also open
the list of configurations and view each configuration's registered versions.

**Why this priority**: a recorded run history is one of the four gains of V3 over v2. It is P2
because a team can complete journey P1 by following one run from start to end.

**Independent Test**: create 120 runs over two configurations, then check that the UI lists all
of them newest first, that each filter returns only matching runs, and that every run appears
on exactly one page.

**Acceptance Scenarios**:

1. **Given** runs for two configurations, **When** the team filters the run list by one
   configuration, **Then** the UI shows only that configuration's runs, newest first.
2. **Given** more runs than one page holds, **When** the team moves to the next page, **Then**
   the UI shows the following runs with no run repeated or missing, even if new runs started
   in between.
3. **Given** a run in progress, **When** the team views the run list, **Then** the run shows its
   current phase and the time elapsed so far.
4. **Given** registered configurations, **When** the team opens the configuration list, **Then**
   the UI shows each configuration with its versions, and for each version its number,
   registration time and package checksum.

---

### User Story 5 - Read runs and plans through the API in pages, from a script or the CLI (Priority: P2)

An automation engineer on the customer team reads the run list and a large plan through the
Sync HTTP API from a script, with the same token as the CLI. The CLI command `runs plan --detail`
also reads the plan in pages, so it no longer fails on a plan above the client's 16 MiB limit.

**Why this priority**: the UI uses only the public API (AD-5, AD-14). The endpoints must exist,
be documented and work for a script before the UI depends on them.

**Independent Test**: read every page of a tier M first-sync plan through the API and through
`infrahub-sync runs plan --detail`, and check that the operations read equal the operations of
the saved plan.

**Acceptance Scenarios**:

1. **Given** a tier M first-sync plan, **When** a script reads its operations page by page,
   **Then** no response exceeds the page size limit and the pages together hold every operation
   once.
2. **Given** a script that has read page 2 of a plan's operations, **When** it reads page 3,
   **Then** both pages name the same plan checksum, and no operation appears on both.
3. **Given** a request for a page size above the maximum, **When** the Sync API receives it,
   **Then** it refuses the request with a clear error that states the maximum.

---

### Edge Cases

- The token is missing, wrong, or removed from the Sync API configuration while the UI is open:
  the UI shows that the token is rejected and asks for it again. It does not retry with the
  rejected token.
- The Sync API cannot be reached: the UI shows that the Sync API cannot be reached and keeps the
  last data it showed, marked as possibly out of date.
- A plan with zero operations: the UI states that the branch already matches the source and
  offers no apply.
- A plan that is still running when the team opens it: the UI shows the run phase and offers
  the plan review only when the plan is saved.
- The team applies, and the request result is lost (network failure): the UI sends the retry with
  the same idempotency key, so the Sync API does not create a second apply.
- Two people apply the same plan at the same time: the Sync API accepts one apply and refuses the
  other, and the UI shows the refusal reason.
- A run started from the CLI or a script: it appears in the UI run list like any other run.
- A source value in a problem reason or an operation contains markup, for example
  `<script>` in a NetBox description: the UI shows it as text and never runs it (FR-030).
- A configuration whose registered version no longer passes adapter validation: the Sync API
  refuses the plan, and the UI shows the reason.
- A plan with more than 10,207 mapped objects: not covered by the testing release (idea brief,
  "Edge cases"). Paging has no fixed upper bound on plan size, but the response times of SC-003
  are measured only up to tier M.
- Deletes in a plan: the UI shows them as "would delete, not executed", with their count.

## Requirements *(mandatory)*

### Functional Requirements

#### Where the UI runs and what it calls (brief FR-006)

- **FR-001**: The `sync-api` container MUST serve the UI from the same origin as the Sync HTTP
  API, under a path that no API route uses. No other container or service is added for the UI.
- **FR-002**: The UI MUST read and change data only through public, documented Sync HTTP API
  routes, with the same token and the same checks as the CLI. The Sync HTTP API MUST NOT have a
  route that only the UI uses.
- **FR-003**: The UI MUST NOT be built into, or depend on, the Infrahub UI.
- **FR-004**: The UI files themselves MUST hold no run, plan, configuration or credential data,
  so serving them without a token reveals nothing. Every piece of data the UI shows MUST come
  from an authenticated Sync API request.

#### Configurations

- **FR-005**: The UI MUST list the registered configurations, and for each one its registered
  versions with the version number, the registration time and the package checksum, from the
  existing `GET /configs` and `GET /configs/{config_id}/versions` routes.
- **FR-006**: The UI MUST NOT register, change or delete a configuration or a version.
  Configuration files stay in the CLI or in git.

#### Run list (brief FR-007, `GET /runs`)

- **FR-007**: The Sync HTTP API MUST provide `GET /runs`, which lists runs newest first. Each
  entry MUST hold the run ID, the configuration ID and version, the operation (`plan`, `verify`,
  `apply` or `sync`, as in `infrahub_sync/client/models.py:11`), the `phase` and `outcome` fields
  that the run record already uses (`infrahub_sync/client/models.py:186-187`), the target branch,
  the actor who started it, the start and finish times, and the plan counts of spec 009 FR-007
  (creates, updates, deletes not executed, records left out) when the run has a plan.
- **FR-008**: `GET /runs` MUST accept filters by configuration ID, configuration version,
  operation, phase and outcome, and MUST combine filters so that a run is listed only when it
  matches all of them.
- **FR-009**: `GET /runs` MUST return results in pages, with a default and a maximum page size,
  and a reference to the next page. Moving through the pages MUST list every run that existed
  at the first request exactly once, even when new runs start between page requests.
- **FR-010**: The UI MUST show the run list with each run's configuration and version,
  operation, phase and outcome, counts, start time and duration. For a run in progress, the
  duration is the time elapsed so far.
- **FR-011**: The UI MUST update the phase and outcome of runs in progress, in the run list and
  in the run view, at least every 10 seconds while the page is open, without the team reloading
  the page.

#### Plan detail in pages (brief FR-007, brief SC-002 UI part)

- **FR-012**: The Sync HTTP API MUST return a plan's summary separately from its operations. The
  summary holds the checksum, the checksum verification state, the target branch, the schema
  fingerprint, the counts by action and by kind, and the counts of spec 009 FR-007.
- **FR-013**: The Sync HTTP API MUST return a plan's operations in pages, filterable by kind and
  by action. Each page MUST stay below the client's response size limit at the maximum page
  size.
- **FR-014**: The Sync HTTP API MUST return a plan's problem list, left-out list and kept list
  (spec 009 FR-023) in pages, the problem list filterable by problem type and by kind.
- **FR-015**: Every page of a plan's operations, problems, left-out records or kept records
  MUST name the checksum of the plan it comes from. A page request MUST be refused when the plan
  it refers to is no longer the plan stored for that run.
- **FR-016**: The order of operations, problems, left-out records and kept records MUST be
  stable across page requests for the same plan, so that the pages together hold every entry
  exactly once.
- **FR-017**: `infrahub-sync runs plan --detail` MUST read the operations through the paged
  routes, so that the CLI shows a tier M first-sync plan without reaching the 16 MiB limit.
- **FR-018**: The existing `GET /runs/{run_id}/plan` route MAY remain for plans that fit in one
  response. The UI and the CLI MUST NOT depend on it to show a plan's operations.

#### Plan review in the UI

- **FR-019**: The UI MUST show a plan's summary: the counts of creates, updates, deletes not
  executed and records left out, in total and by kind.
- **FR-020**: The UI MUST list a plan's operations, filterable by kind and by action, page by
  page. For each operation it MUST show the action, the kind, the identity, and the values and
  relationships the operation writes, as the Sync API returns them. For an update, it MUST show
  the destination node the update names.
- **FR-021**: The UI MUST list a plan's problems, its left-out records and its kept records
  with the fields of spec 009 FR-003, FR-006 and FR-023, the problems filterable by problem type
  and by kind.
- **FR-022**: The UI MUST NOT draw a graph of the plan or of the dependencies between records.

#### Starting a plan and applying it

- **FR-023**: The UI MUST let the team start a plan for a chosen configuration and version, with
  a target branch and a reason, through `POST /runs` with the operation `plan`. The UI MUST NOT
  start a `sync` run (plan and apply in one request).
- **FR-024**: The UI MUST
  let the team cancel a plan or an apply in progress through the existing
  `POST /runs/{run_id}/cancel` route.
- **FR-025**: The UI MUST let the team approve and apply a reviewed plan through
  `POST /runs/{run_id}/apply`. The request MUST carry the checksum of the plan the UI showed,
  the confirmation of writes, the target branch and a reason the team enters.
- **FR-026**: When the plan leaves out records, the UI MUST show the left-out count before the
  apply, and MUST require the team to tick a checkbox "I have reviewed the N records left out",
  where N is that count. The apply request MUST carry `confirm_left_out: true` only when the
  checkbox is ticked (spec 009 FR-026). When the plan leaves out no records, the UI MUST NOT
  show the checkbox (spec 009 FR-011). Decided on 2026-10-17: the confirmation is a yes or no
  flag, not a typed count, because the plan checksum the request carries already binds the
  apply to the reviewed plan.
- **FR-027**: Every request from the UI that changes state MUST carry an idempotency key that
  the UI generates once per action and reuses when it retries that action.
- **FR-028**: The UI MUST NOT offer an apply for a plan that cannot be applied: a plan with zero
  operations, a plan that was already applied, or the plan of a stopped apply (spec 009 FR-022).
  It MUST state why.

#### Why a run failed or stopped (brief FR-003, SC-005)

- **FR-029**: For every run that failed, stopped, was cancelled or ended as `ambiguous`, the UI
  MUST show each part of the structured cause that the Sync API returns (spec 009 FR-025): the
  stage (`plan`, `verify`, `apply` or `sync`), the failure type, the system involved, Infrahub's
  error code, the node kind and attribute, the message, the operations written and the operation
  that failed. It MUST also show whether the run needs reconciliation. The UI MUST read the
  cause of a failed or stopped run from `run.failure` of `GET /runs/{run_id}`. A failed verify
  does not change the run's phase, so `run.failure` stays `null` for it; the UI MUST read the
  cause of a failed verify from `verify_failure.failure` of `GET /runs/{run_id}/results`. The UI
  MUST show a `failure_type` that it does not know by its name, because a later Sync release can
  add a type.

#### Token handling and browser security

- **FR-030**: The UI MUST display every value it receives from the Sync API, including source
  record values, problem reasons and Infrahub messages, as plain text. It MUST never interpret
  such a value as markup or code. The UI MUST load no script, style or font from another origin.
  The `sync-api` container MUST send a content security policy with the UI that allows scripts
  only from its own origin.
- **FR-031**: In the MVP, the UI MUST be reachable from the `sync-api` host only: the API and
  the UI stay published on loopback, and the Compose deployment adds no service or port for the
  UI (spec 013 FR-007 and FR-017). The user documentation MUST state that a person on another
  computer reaches the UI through a tunnel they set up themselves, such as an SSH port
  forward. Access from other computers over TLS, through a reverse proxy, belongs to 3.0
  (INFP-663).
- **FR-032**: The UI MUST ask for the Sync API token when it has none, and MUST keep the token
  only in the browser tab's session storage. It MUST NOT store the token in a cookie, in
  persistent browser storage, in a URL or in a log. Closing the tab, or a "sign out" action in
  the UI, MUST remove the token.
- **FR-033**: The UI MUST send the token only to the Sync HTTP API on its own origin, in the
  `Authorization` header. When the Sync API rejects the token, the UI MUST remove it and ask for
  it again.
- **FR-034**: The UI MUST NOT show which principal or role the token belongs to beyond the actor
  name the Sync API returns on runs. It does not create, change or list tokens or principals.

#### Plan step timings in the run view (spec 010 FR-013)

- **FR-038**: For a plan, `sync` run or failed plan, the UI MUST show the plan step timings that
  the run record returns (spec 010 FR-009 and FR-010), in the run view, labelled as values that
  may change between releases. It MUST NOT show them under the name "phase", which the UI uses
  for the run's state.

#### Peer identities in plan detail (#373, SYNC-259)

- **FR-039**: The paged plan routes, the UI and `infrahub-sync runs plan --detail` MUST show
  each peer of an operation's relationships once, with its identity
  ([#373](https://github.com/opsmill/infrahub-sync/issues/373): today the CLI prints each
  peer's full identity twice per operation, so a full import cannot be reviewed).

#### CLI commands for runs (SYNC-211, SYNC-254)

- **FR-040**: The CLI MUST provide `infrahub-sync runs list`, which reads `GET /runs` with the
  same filters and paging as FR-008 and FR-009 and prints each run's ID, configuration and
  version, operation, phase, outcome, start time and duration.
- **FR-041**: The CLI MUST provide `infrahub-sync runs cancel <run_id>`, which calls the existing
  `POST /runs/{run_id}/cancel` route with an idempotency key, as the other CLI commands that
  change state do (`infrahub_sync/cli.py:266`).

#### Documentation and decision records

- **FR-035**: The Sync HTTP API reference MUST document `GET /runs`, the paged plan routes, their
  filters, page sizes and errors, so that a script can do what the UI does.
- **FR-036**: The user documentation MUST describe journey P1 in the UI, from a registered
  configuration to a completed apply, and how to open the UI on the supported deployment.
- **FR-037**: Product decision AD-5 MUST be amended to place the basic UI in the testing
  release. The amendment keeps the rule that the UI uses only the public API.

### Key Entities

- **Run list entry**: one run as the run list shows it: run ID, configuration ID and version,
  operation, phase, outcome, branch, actor, start and finish times, and plan counts.
- **Run list page**: an ordered set of run list entries, with a reference to the next page that
  keeps the order stable while new runs start.
- **Plan summary**: the checksum, checksum verification state, target branch, schema
  fingerprint, and counts by action, by kind, of deletes not executed and of records left out.
- **Plan page**: an ordered set of operations, problems, left-out records or kept records of
  one plan, with the checksum of that plan and a reference to the next page.
- **Operator session**: the browser tab in which a person entered the Sync API token. It holds
  the token until the tab closes or the person signs out.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (brief SC-006): during at least one customer test, a customer team completes
  journey P1 in the UI, from a registered configuration to a completed apply, including the
  review of problems and the confirmation of the records left out, without OpsMill running a
  command for them.
- **SC-002** (brief SC-002, UI part): at tier M (10,207 mapped objects), a first-sync plan opens
  in the UI, and every operation of the plan can be read in the UI page by page, with no limit
  changed in the code.
- **SC-003**: at tier M, on the benchmark host, the plan summary appears in the UI within 5
  seconds of opening a finished plan, and each page of operations, problems, left-out records or
  kept records appears within 2 seconds.
- **SC-004**: for 100% of the failure types in the spec 009 test suite, the UI shows the cause
  without the team reading container logs or the orchestration server.
- **SC-005**: an automated check confirms that every request the UI sends goes to a documented
  public Sync API route, and that no Sync API route exists only for the UI.
- **SC-006**: no test fixture value that contains markup or script runs as code in the UI, and
  no response, URL, browser storage other than the tab's session storage, or log in the test
  suite holds the Sync API token.
- **SC-007**: reading every page of a tier M first-sync plan through the API returns the same
  operations, in count and checksum, as the saved plan.
- **SC-008**: in the plan detail of the NetBox example first sync, read through the UI, the paged
  routes and `runs plan --detail`, no peer identity appears twice in one operation.
- **SC-009**: `infrahub-sync runs list` lists every run that `GET /runs` returns for the same
  filters, and `infrahub-sync runs cancel` cancels a plan in progress, in 100% of CLI tests.

## Assumptions

- Journey P1 in the UI starts from a registered configuration. Registering a configuration,
  registering a new version after fixing the mapping, and fixing source data in NetBox happen
  outside the UI: in the CLI, in git and in NetBox. The idea brief keeps configuration files in
  the CLI or git (FR-006), so SC-001 counts the CLI registration that the customer team runs
  themselves, and fails only when OpsMill runs a command for them.
- The UI uses the same bearer tokens as the CLI (`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`). Any
  configured token can plan and apply, as with the CLI. The testing release has no separate
  approver role: "approve" means the confirmations the apply request carries. Delete approval
  by a separate step is part of 3.0, not this specification.
- The token lives in the tab's session storage rather than only in memory, so that reloading the
  page does not ask for it again. Any script that runs on the UI's origin can read session
  storage, so FR-030 (text-only display, no scripts from other origins, a content security
  policy) is what protects the token. Because the UI sends the token in a header and not in a
  cookie, another site cannot make the browser send authenticated requests to the Sync API.
- Teams are advised to give the UI a token without the administrator flag, because the UI needs
  no administrator route.
- The supported deployment remains the single-host compose file. The `sync-api` port binds to
  loopback by default, and the compose file states that external exposure and TLS termination
  are outside the supported single-host shape. FR-031 keeps that shape for the UI in the MVP.
- The UI supports the current versions of Chrome, Firefox, Edge and Safari on a desktop or
  laptop screen. Phone layouts are not required.
- The UI shows times in the browser's time zone and durations in hours, minutes and seconds.
- The structured failure cause, the problem list, the left-out list, the kept list, the plan
  counts and the left-out confirmation (`confirm_left_out`) in the apply request come from spec
  009. This
  specification adds only the paging of those lists. If spec 009 is not delivered, User Story 3
  and FR-021, FR-026 and FR-029 cannot be met. The plan step timings come from spec 010
  (FR-038).
- Spec 010 delivers a tier M first sync with no size limit changed (spec 010 FR-018). This
  specification covers opening that plan in the UI. The 64 MiB bundle limit stays as released;
  removing it is part of 3.0 (INFP-659).
- The size of a tier M plan is not known. `product/metrics.md` records a 66.5 MiB plan at tier L
  only; scaled by object count, a tier M plan would be about 7.7 MiB, below the 16 MiB client
  limit, but that is an estimate. The Sync API also parses the whole retained plan on each
  request today (`infrahub_sync/service/service.py:861-870`). Paging stays in scope. The plan
  for this specification MUST use the tier M plan size that spec 010 FR-019 records before it
  chooses how plan pages are stored and read, and MUST record that choice in a decision record
  when it changes the retained plan artifact (ADR 0001, ADR 0011).
- The run list includes runs started from the CLI, scripts and the UI alike. Runs are listed as
  long as the product store keeps them; this specification adds no retention rule.
- Verification (`POST /runs/{run_id}/verify`) stays available in the CLI and the API. The UI does
  not need a separate verify action, because apply checks the reviewed checksum.
- Schedules, user management, configuration editing, a graph of the plan, resume of a stopped
  apply, executed deletes and a UI inside Infrahub are not part of the testing release.
- The Sync API stays REST (AD-4). AD-4 names GraphQL as an option if large-plan browsing needs
  it; paging plan detail meets that need for the testing release.
