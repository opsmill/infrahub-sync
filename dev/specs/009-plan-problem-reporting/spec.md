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

Not decided yet: whether a record that refers to a left-out record through an optional
relationship is also left out, and whether a `sync` run (plan and apply in one request) may write
when records are left out. Both are marked in the requirements below.

This specification covers requirements FR-001 to FR-004, journey P1 scenarios 1 and 2, and
success criteria SC-003 and SC-005 of the V3 idea brief (2026-10-15). The UI that consumes the
information described here is specified separately (spec 011).

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
six problem types and a set of valid records. The plan finishes, lists six problems, leaves out
those records and their dependents, and writes nothing to Infrahub.

**Acceptance Scenarios**:

1. **Given** a source that holds one record of each of the six problem types listed in FR-002,
   **When** the team starts a plan, **Then** the plan finishes and its problem list contains one
   entry for each of the six records, each with its kind, its source record identity, the problem
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

---

### User Story 3 - View why a run failed or stopped (Priority: P1)

A plan, a verification or an apply fails or stops. Without access to the worker container log or
the orchestration server, the team views the cause in the CLI output and in the run record that
the Sync API returns: Infrahub's rejection code, the node kind and attribute it rejected and its
message; or the system that could not be reached; or the system that rejected a token.

**Why this priority**: a run that fails without a reason forces the team to ask OpsMill to read
container logs, which blocks a customer test (idea brief, SC-005).

**Independent Test**: one test per failure type (Infrahub rejection, source unreachable,
destination unreachable, source token rejected, destination token rejected, plan failure that is
not a record problem) checks that the CLI output and the Sync API run record contain the cause
text.

**Acceptance Scenarios**:

1. **Given** an apply in which Infrahub rejects a write with an error code, **When** the run
   stops, **Then** the CLI prints, and the Sync API run record holds, Infrahub's error code, the
   node kind, the attribute name when Infrahub reports one, and Infrahub's message with any
   secret replaced.
2. **Given** a NetBox or Infrahub address that cannot be reached, **When** a plan or an apply
   runs, **Then** the run fails and its cause names the system (source or destination, and its
   adapter name) and states that it could not be reached or did not answer in time.
3. **Given** a token that NetBox or Infrahub rejects, **When** a plan or an apply runs, **Then**
   the run fails and its cause names the system and states that it rejected the token, without
   showing the token.
4. **Given** a plan that fails for a reason that is not a record problem, for example a
   transform error, **When** the team views the run, **Then** the CLI and the Sync API show the
   cause text, with any secret replaced.
5. **Given** an Infrahub version that does not return an error code, **When** Infrahub rejects a
   write, **Then** the cause shows that Infrahub rejected the write and Infrahub's message with
   any secret replaced.

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
- The cause text from Infrahub or from a source holds a value that matches a collected secret:
  the secret is replaced before the text is stored, returned or printed.
- The problem list is very long, for example several thousand problems at tier M: the list is
  returned in pages, under the same rule as the plan operations (idea brief FR-007, another
  specification).

## Requirements *(mandatory)*

### Functional Requirements

#### Problems found during a plan (brief FR-001)

- **FR-001**: A plan MUST check every source record and MUST NOT stop at the first record that
  has a problem.
- **FR-002**: A plan MUST detect and list these six problem types:
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
- **FR-003**: Each listed problem MUST hold the kind, the source record identity, the problem
  type, and a reason that names the attribute or relationship and the value or peer involved.
  Problems of the identity types MUST also name the other records involved.
- **FR-004**: A plan MUST NOT write to Infrahub, whether or not it finds problems.

#### Records left out of a plan (brief FR-002, AD-12 plan stage)

- **FR-005**: A plan MUST leave out each record that has a problem, and every record that
  depends on it through a required relationship, at any depth.
  [NEEDS CLARIFICATION: is a record that refers to a left-out record only through an optional
  relationship also left out, or is it kept without that relationship? Leaving it out is safer,
  because keeping it writes a node that the next plan changes again, but it leaves out more
  records.]
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
  [NEEDS CLARIFICATION: stop after the plan and wait for the team to review and apply it, or
  write the remaining operations and report the left-out count afterwards? Stopping keeps the
  rule that a person confirms the count before a write.]
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
- **FR-018**: Every other failure of a plan, verification or apply MUST record the failure type
  and its message.
- **FR-019**: Every cause text MUST have collected secrets replaced, and MUST be limited in
  length, before it is stored, returned by the Sync API, or printed. A token MUST never appear in
  a cause.

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

  It MUST return them with the same authentication as the existing run and plan information,
  so that the UI of spec 011 uses only the public Sync API.
- **FR-024**: Each problem type MUST have a stable name that does not change between releases,
  so that the CLI, the UI and scripts can filter problems by type.
- **FR-025**: The Sync API run record MUST return the cause of FR-014 to FR-020 in a structured
  form, with separate fields for:
  - the stage that failed (plan, verification or apply)
  - the failure type
  - the system involved
  - Infrahub's error code
  - the node kind and attribute
  - the message
  - the operations written and the operation that failed

  The UI can then show each part without parsing text.
- **FR-026**: The apply request in the Sync API MUST carry the confirmed left-out count of
  FR-010, next to the reviewed checksum it already carries.

### Key Entities

- **Plan problem**: one problem found in one source record. It holds the kind, the source
  record identity, the problem type, the reason, and for identity problems the other records
  involved.
- **Left-out record**: a source record that the plan leaves out. It holds the kind, the source
  record identity, and either its own problems or the record it depends on.
- **Plan summary**: the counts of creates, updates, deletes not executed and records left out,
  by action and by kind.
- **Run failure cause**: the stage, the failure type, the system involved, Infrahub's error code,
  the node kind and attribute, and the message with secrets replaced.
- **Apply progress**: for a stopped apply, the operations written, the operation that failed,
  and whether that operation may have written part of its change.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (brief SC-003): on the NetBox example data with planted bad records, one plan run
  lists every planted problem. On 2026-10-05 the same problems took five configuration versions.
- **SC-002**: a fixture with one record of each of the six problem types gives one plan that
  lists all six, and its apply writes only the valid records after the left-out count is
  confirmed.
- **SC-003** (brief SC-005): for 100% of failed or stopped runs in the test suite, the CLI and
  the Sync API show the cause without reading container logs or the orchestration server.
- **SC-004**: after an apply stopped partway, the new plan's operation count equals the stopped
  plan's operation count minus the operations written, adjusted only for changes made in the
  source between the two plans.
- **SC-005**: no cause text, problem reason or left-out entry in the test suite contains a
  collected secret.
- **SC-006**: 100% of apply attempts on a plan with records left out are refused when the
  left-out count is missing or wrong.

## Assumptions

- Duplicate records and identity collisions leave out every record involved, not all but one,
  because the plan cannot tell which record is correct (idea brief, journey P1 scenario 1).
- A problem in a record that already exists in Infrahub leaves the Infrahub node as it is. The
  plan proposes no update and no delete for it.
- Source values may appear in problem reasons and in Infrahub's message, as they already do in
  the plan operations the team reviews. Secrets do not appear, under the existing secret
  redaction rules. This replaces today's rule that a run's failure record holds an error class
  name and never a message (#369), so the change needs a decision record.
- Infrahub 1.11 and later return an error code, the node kind and the attribute name with a
  rejection (#370). For older versions, the cause shows Infrahub's message without a code.
- The Dropdown check needs the attribute choices in the record of the destination schema that a
  plan is bound to. Adding them changes that record, so plans saved by an earlier release must be
  planned again before they are applied (#371). This is accepted for the testing release.
- A plan failure that is not tied to one record, such as an unreachable system, a rejected token
  or an invalid transform, still fails the whole plan. Only the six problem types of FR-002 leave
  records out.
- The problem list and the left-out list follow the paging rule for plan details that another
  testing-release specification defines (idea brief FR-007).
- The testing release supports only the adapters that Sync bundles, and up to about 10,000
  mapped objects (idea brief, "Assumptions"). The six problem types are checked for every bundled
  source adapter, not only NetBox.
- The UI that shows this information is out of scope here (spec 011). This specification
  defines what the CLI shows and what the Sync API returns.
- Resume of a stopped apply, deletes executed, and the AD-12 policy for runs nobody watches are
  not part of the testing release.
