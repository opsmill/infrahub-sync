# Feature Specification: Plan problem reporting

**Feature Branch**: `009-plan-problem-reporting`

**Created**: 2026-10-15

**Status**: Draft

**Release**: MVP (the testing release). Every requirement in this specification belongs to the MVP.
Work it names as out of scope belongs to 3.0 or 3.x, as listed in
[V3 release scope](../v3-release-scope.md).

**Input**: User description: "Plan problem reporting for the V3 testing release. A plan checks every source record and lists every problem (Dropdown value outside its choices, duplicate source records, two source objects with the same destination HFID, wrong value type, required peer excluded by a filter, same-named objects that would silently merge) with the record, kind and reason, instead of stopping at the first one. Records with problems and every record that depends on them are left out of the plan and counted (isolate policy, AD-12 plan stage); apply requires the person to confirm the left-out count in CLI and API. Every failed or stopped run shows its cause (Infrahub rejection message and code, unreachable system, rejected token) in the CLI and the API. A stopped apply shows the operations written and the one that failed, and a new plan contains only the remaining operations (no resume). Scope = brief FR-001 to FR-004, journeys P1 scenarios 1-2, SC-003, SC-005. The UI is a separate spec (011); state the API/CLI contract the UI will consume."

## Summary

Problem: a customer team that plans a first import from a real NetBox into an Infrahub branch
cannot review its bad source data in one pass. Today:

- A plan stops at the first bad record. On 2026-10-05, five configuration versions were needed to
  find five problems in the NetBox demo data, one plan at a time (GitHub
  [#369](https://github.com/opsmill/infrahub-sync/issues/369)).
- A failed run prints only its state and an error class name. The cause, which names the record,
  is written only to the worker container log and to the orchestration state message (#369).
- When Infrahub rejects a write during apply, the run reports "a destination GraphQL rejection"
  without Infrahub's error code or the attribute it rejected
  ([#370](https://github.com/opsmill/infrahub-sync/issues/370)).
- A Dropdown value outside the attribute's choices is found only when apply writes it, after
  hundreds of operations are already written
  ([#371](https://github.com/opsmill/infrahub-sync/issues/371)).
- Two source objects whose mapping identities differ, but whose Infrahub identity is the same,
  are reported with a warning that does not stop them from merging into one Infrahub node
  ([#166](https://github.com/opsmill/infrahub-sync/issues/166)).

What is needed: one plan lists every problem with the record, the kind and the reason. The plan
leaves out the records with problems and every record that depends on them, and counts them.
The person confirms that count when they approve the apply. Every failed or stopped run shows
its cause in the CLI and in the Sync API. A stopped apply shows what it wrote and which operation
failed, and a new plan contains only the operations that remain.

Decided by the product owner on 2026-10-15: a record that refers to a left-out record only
through an optional relationship is kept and written without that relationship (FR-005). A
`sync` run (plan and apply in one request) whose plan leaves out records stops after the plan,
and the MVP has no option that skips that stop (FR-012).

Decided by the product owner on 2026-10-15, second review: credential values are kept out of
failure causes, problem reasons and plan files by how these are built, not by replacing values
in text afterwards. A cause is built only from named fields, never from the text of an error
raised by an adapter or an HTTP library (FR-019, FR-027, FR-028). The existing replacement of
collected values stays as it is in the testing release.

Decided by the product owner on 2026-10-15, third review:

- A source record that refers to a peer that is not in the source at all is a plan problem, the
  seventh type. It does not fail the plan. The record is left out when the relationship is
  required, and kept without that relationship when it is optional (FR-002, FR-005).
- Removing the existing replacement of values from published plans, which can make a plan
  invalid JSON (SYNC-262), is follow-up work after the MVP. The replacement stays unchanged in
  the MVP (FR-028).

Not decided yet: product sign-off on the two decision record changes this specification needs
(FR-029 and FR-030).

This specification covers requirements FR-001 to FR-004, journey P1 scenarios 1 and 2, and
success criteria SC-003 and SC-005 of the V3 idea brief (2026-10-15). It also owns these MVP
blockers from `infrahub-sync-process/product/path-to-3.0.md`:

- credential values that can still appear in errors and plans (SYNC-82)
- the NetBox example package that fails on data real NetBox instances hold
  ([#374](https://github.com/opsmill/infrahub-sync/issues/374))
- `configs validate`, which cannot reach the destination schema check or the credential check
  (INFP-651, INFP-652, SYNC-107, SYNC-147)

The replacement of values that can make a published plan invalid JSON (SYNC-262) is not an MVP
blocker of this specification. It is follow-up work after the MVP (FR-028).

The UI that consumes the information described here is specified separately (spec 011).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review every bad source record in one plan (Priority: P1)

A customer network or automation team plans a first import from their NetBox into an Infrahub
branch. Their NetBox holds duplicate IP addresses, two devices with the same name, and a device
status that is not one of the choices of the Infrahub Dropdown attribute. They start one plan.
The plan finishes. In the CLI or through the Sync API, they view the plan summary, which shows
the creates, the updates and "N records left out", and the problem list, which names each bad
record with its kind and the reason. They fix the data in NetBox or the mapping in the
configuration, and plan again.

**Why this priority**: a first import from real data is the first thing a testing customer does.
Finding every bad record in one plan, before anything is written, is the gain V3 offers over v2
for that step (idea brief, "Users and value").

**Independent Test**: run one plan against a source fixture that holds one record of each of the
seven problem types and a set of valid records. The plan finishes, lists seven problems, leaves out
those records and their dependents, and writes nothing to Infrahub.

**Acceptance Scenarios**:

1. **Given** a source that holds one record of each of the seven problem types listed in FR-002,
   **When** the team starts a plan, **Then** the plan finishes and its problem list contains one
   entry for each of the seven records, each with its kind, its source record identity, the problem
   type and the reason.
2. **Given** a source where a device has a problem and five interfaces belong to that device,
   **When** the team starts a plan, **Then** the plan leaves out the device and its five
   interfaces, the left-out list names each interface with the device it depends on, and the
   summary counts six records left out.
3. **Given** a plan with records left out, **When** the team views the plan summary in the CLI
   or through the Sync API, **Then** the summary shows the count of records left out next to the
   counts of creates and updates.
4. **Given** a plan with problems, **When** the plan finishes, **Then** no write has taken place
   in Infrahub.
5. **Given** the team fixed the source data after a plan with records left out, **When** they
   start a new plan, **Then** the records that were left out and are now valid appear in the new
   plan as creates or updates.
6. **Given** a source with no problems, **When** the team starts a plan, **Then** the summary
   shows zero records left out and the problem list is empty.
7. **Given** a source where a device has a problem and a valid IP address refers to that device
   only through an optional relationship, **When** the team starts a plan, **Then** the plan
   keeps the IP address without that relationship, and the kept list names the IP address, the
   relationship and the left-out device.
8. **Given** a source where a device refers to a site that the source does not return, through a
   required relationship, and an IP address refers to a VRF that the source does not return,
   through an optional relationship, **When** the team starts a plan, **Then** the plan
   finishes, the problem list names the device and the IP address with the kind, the
   relationship and the peer ID that is missing, the plan leaves out the device, and it keeps
   the IP address without that relationship.

---

### User Story 2 - Confirm the left-out count and apply the rest (Priority: P1)

The team reviewed a plan that leaves out records. They approve it with its checksum and confirm
the count of records left out. Apply writes the remaining operations to the Infrahub branch, and
the run shows what was written.

**Why this priority**: without this step the team can review problems but cannot import the
valid part of their data, which completes journey P1.

**Independent Test**: apply the plan from User Story 1 once without the confirmation and once
with it. The first attempt is refused before any write. The second writes only the operations in
the plan.

**Acceptance Scenarios**:

1. **Given** a plan that leaves out 12 records, **When** the team approves the apply in the CLI
   without confirming the count, **Then** the apply is refused before any write and the CLI
   states the count that must be confirmed.
2. **Given** the same plan, **When** an API caller requests the apply without the count or with
   a different count, **Then** the Sync API refuses the request before any write and its response
   states the expected count.
3. **Given** the same plan, **When** the team confirms 12, **Then** apply writes the operations
   of the plan, and none for the records left out.
4. **Given** a plan that leaves out no records, **When** the team approves the apply, **Then**
   no confirmation of a left-out count is asked for.
5. **Given** a completed apply, **When** the team views the run, **Then** the run shows the
   count of operations written and the count of records the plan left out.
6. **Given** a `sync` run whose plan leaves out records, **When** the plan finishes, **Then** the
   run stops before any write with the plan saved, and the team applies it only by approving it
   with the confirmed count.

---

### User Story 3 - View why a run failed or stopped (Priority: P1)

A `plan`, `verify`, `apply` or `sync` run fails or stops. Without access to the worker container
log or the orchestration server, the team views the cause in the CLI output and in the run record
that the Sync API returns: Infrahub's rejection code, the node kind and attribute it rejected and
its message; or the system that could not be reached; or the system that rejected a token.

**Why this priority**: a run that fails without a reason forces the team to ask OpsMill to read
container logs, which blocks a customer test (idea brief, SC-005).

**Independent Test**: one test per failure type (Infrahub rejection, source unreachable,
destination unreachable, source token rejected, destination token rejected, plan failure that is
not a record problem) checks that the CLI output and the Sync API run record contain the cause
fields of FR-019 and no other error text.

**Acceptance Scenarios**:

1. **Given** an apply in which Infrahub rejects a write with an error code, **When** the run
   stops, **Then** the CLI prints, and the Sync API run record holds, Infrahub's error code, the
   node kind, the attribute name when Infrahub reports one, and Infrahub's message.
2. **Given** a NetBox or Infrahub address that cannot be reached, **When** a plan or an apply
   runs, **Then** the run fails and its cause names the system (source or destination, and its
   adapter name) and states that it could not be reached or did not answer in time.
3. **Given** a token that NetBox or Infrahub rejects, **When** a plan or an apply runs, **Then**
   the run fails and its cause names the system and states that it rejected the token, without
   showing the token.
4. **Given** a plan that fails for a reason that is not a record problem, for example a
   transform that raises an error, **When** the team views the run, **Then** the CLI and the
   Sync API show the failure type, and the kind, the source record identity and the field
   involved when the failure names them.
5. **Given** an Infrahub version that does not return an error code, **When** Infrahub rejects a
   write, **Then** the cause shows that Infrahub rejected the write and Infrahub's message.
6. **Given** a NetBox address that holds a user name and password, or an error from an HTTP
   library that repeats a request header, **When** a plan fails because NetBox cannot be
   reached or rejects the token, **Then** the cause holds no part of the address, the header
   or the error text, only the system, the adapter and the failure type.

---

### User Story 4 - Recover a stopped apply by planning again (Priority: P1)

An apply stops partway because Infrahub rejected a write. The team views which operations were
written and which operation failed. They fix the cause, start a new plan, and view a plan that
contains only the operations that were not written. They review it and apply it.

**Why this priority**: there is no resume in the testing release. Planning again is the only way
to finish an import, so the new plan must not repeat what was already written (idea brief,
journey P1 scenario 2).

**Independent Test**: stop an apply of a plan with a known number of operations at a known
operation, then plan again against the same source and branch. The new plan's operation count
equals the original count minus the operations written.

**Acceptance Scenarios**:

1. **Given** an apply of 2,291 operations that Infrahub stops at operation 693, **When** the team
   views the run, **Then** the run shows 692 operations written, names the operation that failed
   with its kind and source record identity, and states whether that operation may have written
   part of its change.
2. **Given** that stopped apply, **When** the team plans again without changing the source,
   **Then** the new plan contains no operation for the 692 nodes already written and contains
   the failed operation and the 1,598 operations that were not attempted, minus any that the
   destination now already matches.
3. **Given** that stopped apply, **When** the team tries to apply the stopped run's plan again,
   **Then** the CLI and the Sync API state that the team must create and review a new plan.

---

### Edge Cases

- A record has more than one problem: each problem is listed, and the record is counted once in
  the left-out count.
- Both records of a duplicate pair, and both source objects that share one Infrahub identity, are
  listed and left out, because the plan cannot tell which one is correct.
- A record depends on another left-out record that itself depends on a record with a problem:
  every record in the chain is left out, and each names the record it depends on.
- A left-out record already exists in Infrahub from an earlier run: the plan proposes no update
  and no delete for it, and the Infrahub node stays as it is.
- Every source record is left out: the plan finishes with zero operations, and the summary shows
  the left-out count. There is nothing to apply.
- The source or Infrahub cannot be reached, or rejects a token, during a plan: the plan fails
  with that cause. This is a run failure, not a record problem, and no partial plan is saved.
- Infrahub rejects a write during apply for a value the plan did not check: the apply stops, as
  it does today. Records are not left out during apply.
- The text of an error raised by a source adapter or an HTTP library holds an address with a
  password, a request header or a traceback: none of that text enters the cause, whatever the
  length of the credential (FR-019, FR-027).
- The problem list is very long, for example several thousand problems at tier M: the list is
  returned in pages, under the same rule as the plan operations (spec 011 FR-014).
- A credential value is shorter than six characters, or holds characters that JSON escapes: it
  does not appear in any cause, problem reason or plan file, because none of them is built from
  a credential or from error text (FR-027). A source record value that happens to be equal to a
  credential value is source data and appears like any other value.

## Requirements *(mandatory)*

### Functional Requirements

#### Problems found during a plan (brief FR-001)

- **FR-001**: A plan MUST check every source record and MUST NOT stop at the first record that
  has a problem.
- **FR-002**: A plan MUST detect and list these seven problem types:
  - a value outside the choices of a Dropdown attribute in the destination schema the plan read
    (#371)
  - two or more source records with the same source identity (for example duplicate IP
    addresses in NetBox, which today stop the source load with "already present")
  - two or more source objects that would create Infrahub nodes with the same human-friendly ID
    (today the whole plan is refused with a destination identity collision)
  - a value of the wrong type for the destination attribute (for example a rack position of 2.5
    for an integer attribute)
  - a required relationship whose peer a configured filter excluded (today the source load stops
    with "Configured filter excluded all peers for required relationship")
  - two or more source objects with different mapping identities that Infrahub would store as
    one node, because the destination identity uses fewer fields than the mapping (#166; today a
    warning only)
  - a reference to a peer that is not in the source at all, for example a device that refers
    to a site the source does not return (today a NetBox, ACI or generic REST source fails the
    whole plan, and a Nautobot source drops the reference with a warning)
- **FR-003**: Each listed problem MUST hold the kind, the source record identity, the problem
  type, and a reason that names the attribute or relationship and the value or peer involved.
  Problems of the identity types MUST also name the other records involved. A problem of a
  peer that is not in the source MUST also name the relationship and the peer's source ID.
- **FR-004**: A plan MUST NOT write to Infrahub, whether or not it finds problems.

#### Records left out of a plan (brief FR-002, AD-12 plan stage)

- **FR-005**: A plan MUST leave out each record that has a problem, and every record that
  depends on it through a required relationship, at any depth.
  One exception: a record whose only problem is a reference to a peer that is not in the
  source, through an optional relationship, MUST be kept and written without that
  relationship. The plan MUST list it both as a problem and as kept without that relationship.
  A record that refers to a left-out record only through an optional relationship MUST be
  kept and written without that relationship. The plan MUST list it as kept without that
  relationship and name the left-out record. Once the left-out record is fixed, the next plan
  sets the relationship.
- **FR-006**: Each left-out record that has no problem of its own MUST name the record it
  depends on.
- **FR-007**: The plan summary MUST show the count of records left out next to the counts of
  creates, updates and deletes not executed, and MUST show how many of them have a problem and
  how many depend on one.
- **FR-008**: A left-out record MUST produce no create, update or delete in the plan.
- **FR-009**: Records left out MUST be checked again by the next plan, with no action from the
  team, and appear in it when they no longer have a problem.
- **FR-010**: An apply of a plan that leaves out records MUST require the person to confirm the
  left-out count, in the CLI and in the Sync API. An apply request without the count, or with a
  count different from the plan's, MUST be refused before any write, and the refusal MUST state
  the expected count.
- **FR-011**: An apply of a plan that leaves out no records MUST NOT ask for that confirmation.
- **FR-012**: A `sync` run (plan and apply in one request) whose plan leaves out records MUST
  stop after the plan, with the plan saved, and wait for the team to review it and apply it with
  the confirmed count of FR-010. The MVP offers no option that skips that stop. A policy for runs
  that nobody watches, including any such option, belongs to 3.0
  ([V3 release scope](../v3-release-scope.md), "Recovery").
- **FR-013**: Infrahub rejecting a write during apply MUST still stop the apply. The plan's
  left-out policy does not apply during apply.

#### Cause of a failed or stopped run (brief FR-003)

- **FR-014**: Every run that fails or stops MUST record its cause, and the CLI MUST print that
  cause when a `diff`, `sync` or `apply` command ends with a failed or stopped run, and when the
  team views the run.
- **FR-015**: The Sync API run record MUST return the cause, so that a caller does not need the
  worker log or the orchestration server.
- **FR-016**: When Infrahub rejects a write, the cause MUST hold Infrahub's error code when
  Infrahub returns one, the node kind and attribute name when Infrahub returns them, and
  Infrahub's message.
- **FR-017**: When the source or Infrahub cannot be reached, does not answer in time, or rejects
  a token, the cause MUST name the system (source or destination, and the adapter) and the type
  of failure.
- **FR-018**: Every other failure of a `plan`, `verify`, `apply` or `sync` run MUST record the
  failure type, and the kind, the source record identity and the field when the failure names
  them.
- **FR-019**: A cause MUST be built only from these fields:
  - the stage and the failure type
  - the system involved: source or destination, and the adapter name
  - Infrahub's error code, the node kind and the attribute name that Infrahub returns
  - Infrahub's own error message, for a write that Infrahub rejected
  - the kind, the source record identity and the field involved
  - the operations written and the operation that failed

  A cause MUST NOT hold the text of an error raised by an adapter, an HTTP library or a
  configuration parser, such as an address, a request header or a traceback. Each field MUST
  be limited in length.

#### A stopped apply and the next plan (brief FR-004)

- **FR-020**: A stopped apply MUST show the count of operations written, the identifiers of
  those operations, the operation that failed with its kind and source record identity, and
  whether that operation may have written part of its change.
- **FR-021**: A new plan after a stopped apply MUST contain only the operations that the
  destination does not already match. It MUST NOT contain operations for nodes that the stopped
  apply wrote and that still match the source.
- **FR-022**: The plan of a stopped apply MUST NOT be applied again. The CLI and the Sync API
  MUST state that a new plan is needed. The testing release has no resume (INFP-650 comes
  later).

#### Information the Sync API returns for the UI (spec 011)

- **FR-023**: The Sync API MUST return, for a plan run:
  - the plan summary with the counts of FR-007
  - the problem list of FR-003
  - the left-out list of FR-006
  - the kept list of FR-005: each record kept without an optional relationship, with its kind,
    its source record identity, the relationship and the left-out record it names

  It MUST return them with the same authentication as the existing run and plan information,
  so that the UI of spec 011 uses only the public Sync API.
- **FR-024**: Each problem type MUST have a stable name that does not change between releases,
  so that the CLI, the UI and scripts can filter problems by type.
- **FR-025**: The Sync API run record MUST return the cause of FR-014 to FR-020 in a structured
  form, with separate fields for:
  - the stage that failed, with the values the Sync API already uses for a run's operation:
    `plan`, `verify`, `apply` or `sync` (`infrahub_sync/client/models.py:11`)
  - the failure type
  - the system involved
  - Infrahub's error code
  - the node kind and attribute
  - the message
  - the operations written and the operation that failed

  The UI can then show each part without parsing text.
- **FR-026**: The apply request in the Sync API MUST carry the confirmed left-out count of
  FR-010, next to the reviewed checksum it already carries.

#### Credential values in causes, problem lists and plan files (SYNC-82)

`path-to-3.0.md` lists SYNC-82 and SYNC-262 as needed for INFP-648, INFP-649 and INFP-653:
short credentials and credentials that contain JSON separators can still appear in errors and
plans, or break a published plan. The product owner decided on 2026-10-15 to prevent credential
values from entering this information, not to replace them afterwards. In a later review the
same day, the product owner moved SYNC-262 out of the MVP.

- **FR-027**: A credential value, whatever its length or its characters, MUST NOT enter a cause,
  a problem reason, a left-out or kept entry, a plan file or a Sync API response. Sync uses a
  credential value only to connect to the system it belongs to. No cause, problem reason or plan
  field is built from connection settings or from the text of an error (FR-019).
- **FR-028**: The protection of FR-027 MUST NOT depend on replacing values in text. This
  specification adds no replacement of values. The existing replacement of collected values
  in published artifacts, run records and the worker log stays as it is in the testing release.
  Removing it from published plans, where it can make a plan invalid JSON (SYNC-262), is not
  part of the MVP. It is follow-up work after the MVP. Because plan files no longer receive
  credential values under FR-027, the replacement on plan files can then be removed without
  exposing a credential.

#### Decision records this specification changes (product sign-off needed)

- **FR-029**: Product decision AD-12 in `infrahub-sync-process/product/decisions.md` MUST be
  amended before this work ships: at the plan stage, records with problems and the records that
  depend on them are left out and counted, and apply needs the confirmed count. The policy for
  runs that nobody watches stays open for 3.0.
- **FR-030**: A decision record in `dev/adr/` MUST replace the rule that a run's failure record
  holds an error class name and never a message
  ([#369](https://github.com/opsmill/infrahub-sync/issues/369); the rule is stated in
  `infrahub_sync/service/flow.py:722-725`, and ADR 0005 places failure translation at the remote
  boundary). The new rule: the failure record holds the structured cause of FR-019, which
  includes Infrahub's own error message and never the text of other errors. The code comment
  and the
  [secret redaction guideline](../../../docs/docs/development/guidelines/secret-redaction.md)
  MUST be updated to match. FR-014 to FR-019 MUST NOT ship before the product owner signs off
  this change.

#### The NetBox example package on data real NetBox instances hold (#374)

- **FR-031**: The shipped NetBox example package (`examples/netbox_to_infrahub/package.yml`)
  MUST plan the pinned NetBox `demo` dataset with no run failure caused by its own mapping. The
  three cases in [#374](https://github.com/opsmill/infrahub-sync/issues/374) MUST be handled:
  - a device status outside the five choices of the destination Dropdown, such as `planned`
  - a rack position that is not an integer, such as `2.5`
  - two devices with the same name at two sites

  Each case MUST either be written by a changed mapping or schema, or be reported as a plan
  problem of FR-002. The plan chooses which, and the example documentation states it.

#### Problems found before a plan, with `configs validate` (INFP-651, INFP-652)

`path-to-3.0.md` states that `configs validate` cannot reach the destination schema check, and
that a credential missing from the worker is found only when the run fails in the worker.

- **FR-032**: `infrahub-sync configs validate` and
  `POST /configs/{config_id}/versions/{registry_version}/validate` MUST run the destination
  schema check against the Infrahub branch the team names: every mapped kind, attribute and
  relationship exists, and every Dropdown value that the mapping sets literally is one of the
  attribute's choices.
- **FR-033**: The same check MUST report each `$credential` reference whose variable is not set
  in the worker environment, by name, without reading or showing its value.
- **FR-034**: The check and the plan MUST agree on whether a kind that has no writable
  human-friendly ID can be created (SYNC-147). A kind that validation accepts MUST NOT be
  refused by the plan for that reason, and the reverse.
- **FR-035**: The response to `configs register` MUST NOT show redacted package content next to
  the checksum of the original content (SYNC-107). It MUST show either the checksum alone or
  content and checksum that match.

### Key Entities

- **Plan problem**: one problem found in one source record. It holds the kind, the source
  record identity, the problem type, the reason, for identity problems the other records
  involved, and for a peer that is not in the source the relationship and the peer's source ID.
- **Left-out record**: a source record that the plan leaves out. It holds the kind, the source
  record identity, and either its own problems or the record it depends on.
- **Kept record**: a source record that the plan keeps without an optional relationship,
  because the peer of that relationship is left out or is not in the source. It holds the kind,
  the source record identity, the relationship, and the left-out record or the peer's source ID.
- **Plan summary**: the counts of creates, updates, deletes not executed and records left out,
  by action and by kind.
- **Run failure cause**: the stage (`plan`, `verify`, `apply` or `sync`), the failure type, the
  system involved, Infrahub's error code, the node kind and attribute, Infrahub's message for a
  rejected write, and the record and field involved. It never holds the text of other errors.
- **Apply progress**: for a stopped apply, the operations written, the operation that failed,
  and whether that operation may have written part of its change.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (brief SC-003): on the NetBox example data with planted bad records, one plan run
  lists every planted problem. On 2026-10-05 the same problems took five configuration versions.
- **SC-002**: a fixture with one record of each of the seven problem types gives one plan that
  lists all seven, and its apply writes only the valid records after the left-out count is
  confirmed.
- **SC-003** (brief SC-005): for 100% of failed or stopped runs in the test suite, the CLI and
  the Sync API show the cause without reading container logs or the orchestration server.
- **SC-004**: after an apply stopped partway, the new plan's operation count equals the stopped
  plan's operation count minus the operations written, adjusted only for changes made in the
  source between the two plans.
- **SC-005**: no cause, problem reason or left-out entry in the test suite contains a
  credential value or the text of an error raised by an adapter or an HTTP library.
- **SC-006**: 100% of apply attempts on a plan with records left out are refused when the
  left-out count is missing or wrong.
- **SC-007**: in tests with a referenced credential of five characters and a referenced
  credential that holds `"`, `\` and `,`, where each failure type is raised with error text
  that holds both values (for example an address with a user name and password), neither
  value appears in a cause, a problem reason, a plan file or a Sync API response.
- **SC-008**: the NetBox example package plans the pinned NetBox `demo` dataset with no run
  failure, and each of the three #374 cases is either written or listed as a plan problem.
- **SC-009**: on a configuration that maps an attribute missing from the destination schema and
  references an unset credential, `configs validate` reports both before any run is started.
- **SC-010**: for each bundled source adapter that resolves references while loading, a source
  record that refers to a peer that is not in the source gives one listed problem, and the plan
  finishes.

## Assumptions

- Duplicate records and identity collisions leave out every record involved, not all but one,
  because the plan cannot tell which record is correct (idea brief, journey P1 scenario 1).
- A problem in a record that already exists in Infrahub leaves the Infrahub node as it is. The
  plan proposes no update and no delete for it.
- Source values may appear in problem reasons and in Infrahub's message, as they already do in
  the plan operations the team reviews. Credential values do not appear, under FR-027. This
  replaces today's rule that a run's failure record holds an error class name and never a
  message (#369). FR-030 requires the decision record and the product owner's sign-off.
- Infrahub's error message describes the write Infrahub received, which holds source data and
  no Sync credential. Infrahub does not receive the source credentials.
- The worker container log and the orchestration log keep the full error text for OpsMill
  engineers, protected only by the existing replacement of collected values, which skips
  values shorter than six characters. A short credential can therefore still appear in those
  two logs (the part of SYNC-82 that remains) until those logs record the structured cause
  instead of the error text, which is follow-up work after the MVP. They are not returned by
  the Sync API or printed by the CLI.
- Infrahub 1.11 and later return an error code, the node kind and the attribute name with a
  rejection (#370). For older versions, the cause shows Infrahub's message without a code.
- The Dropdown check needs the attribute choices in the record of the destination schema that a
  plan is bound to. Adding them changes that record, so plans saved by an earlier release must be
  planned again before they are applied (#371). This is accepted for the testing release.
- A plan failure that is not tied to one record, such as an unreachable system, a rejected token
  or an invalid transform, still fails the whole plan. Only the seven problem types of FR-002 leave
  records out.
- The problem list, the left-out list and the kept list follow the paging rule for plan
  details that spec 011 defines (FR-013 to FR-016 there; idea brief FR-007).
- The testing release supports only the adapters that Sync bundles, and up to about 10,000
  mapped objects (idea brief, "Assumptions"). The seven problem types are checked for every bundled
  source adapter, not only NetBox.
- The UI that shows this information is out of scope here (spec 011). This specification
  defines what the CLI shows and what the Sync API returns.
- Resume of a stopped apply, deletes executed, and the AD-12 policy for runs nobody watches
  (including any option that applies a plan with records left out without review) are not part
  of the testing release.
- These two follow-ups come after the MVP: removing the replacement of values from published
  plans (SYNC-262), and recording the structured cause instead of the error text in the worker
  and orchestration logs, so that a credential shorter than six characters no longer appears
  there (the rest of SYNC-82).
