# Feature Specification: Plan problem reporting

**Feature Branch**: `019-plan-problem-reporting`

**Created**: 2026-11-11

**Status**: Draft

**Release**: the V3 testing release (named "MVP" on the release scope page). Work this
specification names as out of scope belongs to 3.0 or 3.x.

**Input**: rebuild of old specification 009 "Plan problem reporting" on `feature/v3-develop` at
`ea83a248`. A customer team that runs a first import from its own NetBox must be able to:

- view every bad source record in one plan, instead of the plan stopping at the first one
- apply the rest after confirming that they reviewed the records left out
- view why a run failed
- plan again after a stopped apply
- check a configuration against the destination schema, the worker credentials and the
  account's permissions before a run (`configs validate`)

Credential values stay out of failure causes and plans.

Sources:

- Old specification 009 and its AD-12 amendment note (`tmp-sync-specs/009-plan-problem-reporting/`:
  `spec.md`, `ad-12-amendment.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`).
  It is the source of the requirements below, unless a decision in "Decisions that change old
  specification 009" changes them.
- The gap report of 2026-11-10, which compares old 009 with `feature/v3-develop` at `ea83a248`.
- The V3 release scope page (009 section and its testing-release blockers).
- Merged specifications this one builds on:
  [014](../014-import-lineage/spec.md),
  [015](../015-run-discovery-api/spec.md),
  [016](../archive/016-prefect-run-observability/spec.md),
  [017](../017-sync-run-events-logs/spec.md),
  [018](../018-plan-read-progress/spec.md), and the decision records in [`dev/adr/`](../../adr/README.md).

## Background

A customer team that plans a first import from a real NetBox into an Infrahub branch cannot
review its bad source data in one pass:

- A plan stops at the first bad record. On 2026-10-05, five configuration versions were needed
  to find five problems in the NetBox demo data, one plan at a time
  ([#369](https://github.com/opsmill/infrahub-sync/issues/369)).
- When Infrahub rejects a write during apply, the run records class names
  (`OperationApplyFailedError` and its cause class), not Infrahub's error code, the node kind,
  the attribute or Infrahub's message ([#370](https://github.com/opsmill/infrahub-sync/issues/370)).
- A Dropdown value outside the attribute's choices is found only when apply writes it, after
  hundreds of operations are already written
  ([#371](https://github.com/opsmill/infrahub-sync/issues/371)).
- Two source objects whose mapping identities differ, but whose Infrahub identity is the same,
  only produce a warning and merge into one Infrahub node
  ([#166](https://github.com/opsmill/infrahub-sync/issues/166)).
- The NetBox example package fails on data that real NetBox instances hold
  ([#374](https://github.com/opsmill/infrahub-sync/issues/374)).
- `configs validate` cannot reach the destination schema check, cannot report a credential that
  the worker does not hold, and does not check the import account's permissions (INFP-651,
  INFP-652, SYNC-107, SYNC-147; spec 014 decision D-1 assigns the permission check to this work).

### What `feature/v3-develop` already does

These parts of old 009 are delivered, or delivered in another shape, by merged work. This
specification keeps them and builds on them. They are not new requirements.

| Old 009 item | Current behavior | Where |
| --- | --- | --- |
| FR-004: a plan does not write to Infrahub | Writes happen only through the apply of a saved plan | [ADR 0014](../../adr/0014-v3-writes-through-saved-plan-apply.md) |
| FR-013: an Infrahub rejection during apply stops the apply | `OperationApplyFailedError` stops the apply and carries the partial apply record | `infrahub_sync/plan/errors.py:480`, `infrahub_sync/plan/models.py:600-664` |
| FR-014, FR-015: a failed run records a cause that the CLI and the run record show | Spec 017 FR-001: each failed stage stores `results.<stage>_failure` with `error_type`, `cause_type`, `cause`, `next_action` and, for a 401 or 403, `infrahub_error`. The CLI prints `<stage> failed: <ErrorType>`, then `cause:` and `hint:` | `infrahub_sync/service/flow.py:805-847`, `infrahub_sync/service/failure_cause.py`, `infrahub_sync/cli.py:832-866` |
| FR-019: a cause is built from fixed templates | Lineage refusals, destination schema read failures and 401/403 classifications are recorded from fixed templates over recorded identifiers. Other errors record only class names | `infrahub_sync/service/failure_cause.py:30-83` |
| FR-022: the plan of a stopped apply is not applied again | A run that admitted a write never admits another stage: HTTP 409 `run-execution-conflict`, "review the run and create a new plan or sync run" | `infrahub_sync/product_store/store.py:310-314`, `:2166-2177` |
| Part of FR-020: a stopped apply shows what it wrote | The run summary records `applied_operations`, `failed_operation` (an operation ID), `failed_operation_wrote` and `may_have_partially_written` | `infrahub_sync/plan/models.py:649-664`, `infrahub_sync/service/flow.py:947-963` |
| Part of FR-021: a new plan holds only what the destination does not match | The plan compares the source with the destination, so written nodes drop out ([ADR 0012](../../adr/0012-the-convergent-upsert-is-the-replace-set-write.md)). No test covers a stopped apply | Hivemind note of 2026-11-05: a second plan after a full apply held only one recorded delete |
| The hook for a `sync` that stops after its plan | `result_next_action` reads `result["failure"]["next_action"]` from a successful stage result, and the last log line and the CLI use it. Nothing writes that key today | `infrahub_sync/service/flow.py:888-897`, `docs/docs/development/knowledge/orchestration-prefect.md:110-113` |

### Decisions that change old specification 009

The caller of this rebuild took these decisions on 2026-11-11. Each one deliberately changes
old 009.

1. **One failure record.** No new top-level `failure` field on the run. The existing
   `results.<stage>_failure` record from spec 017 gains the cause fields of old 009 (FR-014). A
   `sync` that stops after a plan with left-out records writes `result["failure"]`, the field
   `flow.py` already reads (FR-012).
2. **Error text rule.** A decision record replaces the rule "a class name, never a message"
   (#369), and the comment at `infrahub_sync/service/flow.py:834-836` and the secret redaction
   guideline are updated to the new rule (FR-020).
3. **Credentials.** Causes and plan files are built only from allowlisted fields, so credential
   values never enter them (old FR-027, FR-028). The masking pass that exists today stays
   unchanged: the product owner decided on 2026-10-15 that SYNC-262 and the short-credential log
   follow-ups are 3.0 work. Tests use a 5-character credential and a credential that holds `"`,
   `\` and `,` (FR-029, FR-030, SC-007).
4. **Refusal code.** A second apply on a run that admitted a write keeps the current code
   `run-execution-conflict`. Old 009's `new-plan-required` is dropped.
5. **Decision record numbers.** The next free numbers in `dev/adr/` are used. 0024 is taken.
   0017 and 0018 are not used, because ADR 0019 names a draft ADR 0017 that another pull request
   holds. This specification uses 0020, 0021 and 0022 (FR-032).
6. **Plan format.** A new plan format 4 on top of format 3, keeping every field that specs 014
   and 018 rely on (FR-031).
7. **Command names.** `plan`, not `diff`. `diff` is a hidden, deprecated alias (spec 015 FR-016).
8. **Prefect run artifact.** The run summary artifact that spec 016 rebuilt is extended
   (FR-028). Old 009's `runs problems`, `runs left-out` and `runs kept` commands are not
   re-added: the lists are read through options of `runs plan` and through Sync API routes
   (FR-027). The plan records the reasons.
9. **Permission check before a plan.** Part of `configs validate` (FR-036).
10. **AD-12 amendment.** The amendment lives in `opsmill/infrahub-sync-process` and is still
    "Open" on its main branch. This repository does not edit it. Merging it there is an external
    dependency (see "Dependencies").
11. **Unverified behavior.** How a plan handles today a duplicate source identity, a Dropdown
    value outside its choices and a value of the wrong type is not known for certain. The plan
    phase verifies each one in code and records it in `research.md`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review every bad source record in one plan (Priority: P1)

A customer network or automation team plans a first import from its NetBox into an Infrahub
branch. Their NetBox holds duplicate IP addresses, two devices with the same name, and a device
status that is not one of the choices of the Infrahub Dropdown attribute. They start one plan
with `infrahub-sync plan`. The plan finishes. In the CLI or through the Sync API, they view the
plan summary, which shows the creates, the updates and the records left out, and the problem
list, which names each bad record with its kind and the reason. They fix the data in NetBox or
the mapping in the configuration, and plan again.

**Why this priority**: a first import from real data is the first thing a testing customer
does. Finding every bad record in one plan, before anything is written, is what V3 offers over
v2 for that step.

**Independent Test**: run one plan against a source fixture that holds one record of each of
the nine problem types and a set of valid records. The plan finishes, lists nine problems,
leaves out those records and their dependents, and writes nothing to Infrahub.

**Acceptance Scenarios**:

1. **Given** a source that holds one record of each of the nine problem types of FR-002,
   **When** the team starts a plan, **Then** the plan finishes and its problem list holds one
   entry for each of the nine records, each with its kind, its source record identity, the
   problem type and the reason.
2. **Given** a source where a device has a problem and five interfaces belong to that device,
   **When** the team starts a plan, **Then** the plan leaves out the device and its five
   interfaces, the left-out list names each interface with the device it depends on, and the
   summary counts six records left out.
3. **Given** a plan with records left out, **When** the team views the plan summary with
   `infrahub-sync plan`, `infrahub-sync runs plan` or `GET /runs/{run_id}/plan`, **Then** the
   summary shows the count of records left out next to the counts of creates, updates and
   deletes not executed.
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
8. **Given** a source where a device refers through a required relationship to a site that the
   source does not return, and an IP address refers through an optional relationship to a VRF
   that the source does not return, **When** the team starts a plan, **Then** the plan finishes,
   the problem list names the device and the IP address with the kind, the relationship and the
   missing peer's source ID, the plan leaves out the device, and it keeps the IP address without
   that relationship.

---

### User Story 2 - Confirm the records left out and apply the rest (Priority: P1)

The team reviewed a plan that leaves out records. They approve it with its checksum and confirm
that they reviewed the records left out. Apply writes the remaining operations to the Infrahub
branch, and the run shows what was written.

**Why this priority**: without this step the team can review problems but cannot import the
valid part of their data.

**Independent Test**: apply the plan from User Story 1 once without the confirmation and once
with it. The first attempt is refused before any write. The second writes only the operations
in the plan.

**Acceptance Scenarios**:

1. **Given** a plan that leaves out 12 records, **When** the team runs `infrahub-sync apply`
   without `--confirm-left-out`, **Then** the apply is refused before any write, and the CLI
   states that the plan leaves out 12 records and names `--confirm-left-out`.
2. **Given** the same plan, **When** an API caller requests the apply without
   `confirm_left_out: true`, **Then** the Sync API refuses the request before any write, and the
   response states the count of records left out.
3. **Given** the same plan, **When** the team confirms the records left out, **Then** apply
   writes the operations of the plan, and none for the records left out.
4. **Given** a plan that leaves out no records, **When** the team approves the apply, **Then** no
   confirmation of records left out is asked for, and a confirmation sent anyway is accepted and
   ignored.
5. **Given** a completed apply, **When** the team views the run, **Then** the run shows the
   count of operations written and the count of records the plan left out.
6. **Given** a `sync` run whose plan leaves out records, **When** the plan finishes, **Then** the
   run stops before any write with the plan saved, the CLI and the run record show a next action
   that names the run ID, the plan checksum and `--confirm-left-out`, and the team applies it
   only by approving it and confirming the records left out.

---

### User Story 3 - View why a run failed (Priority: P1)

A `plan`, `verify`, `apply` or `sync` run fails or stops. Without access to the worker container
log or to Prefect, the team views the cause in the CLI output and in the run record that the
Sync API returns:

- for a rejected write: Infrahub's error code, the node kind and attribute it rejected, and
  Infrahub's message
- for a connection failure: the system that could not be reached or did not answer in time
- for a refused token: the system that refused it

**Why this priority**: a run that fails without a reason forces the team to ask OpsMill to read
container logs, which blocks a customer test.

**Independent Test**: one test per failure type (Infrahub rejection, source unreachable,
destination unreachable, source token refused, destination token refused, a plan failure that
is not a record problem) checks that the CLI output and `GET /runs/{run_id}` hold the cause
fields of FR-014 and no other error text.

**Acceptance Scenarios**:

1. **Given** an apply in which Infrahub rejects a write with an error code, **When** the run
   stops, **Then** the CLI prints, and the run's `apply_failure` record holds, Infrahub's error
   code, the node kind, the attribute name when Infrahub reports one, and Infrahub's message.
2. **Given** a NetBox or Infrahub address that cannot be reached, **When** a plan or an apply
   runs, **Then** the run fails and its cause names the system (source or destination, and its
   adapter name) and states that it could not be reached or did not answer in time.
3. **Given** a token that NetBox or Infrahub refuses, **When** a plan or an apply runs, **Then**
   the run fails and its cause names the system and states that it refused the token, without
   showing the token.
4. **Given** a plan that fails for a reason that is not a record problem, for example a record
   with no value for a required field, **When** the team views the run, **Then** the CLI and the
   Sync API show the failure type, and the kind, the source record identity and the field when
   the failure names them.
5. **Given** an Infrahub version that returns no error code, **When** Infrahub rejects a write,
   **Then** the cause shows that Infrahub rejected the write, and Infrahub's message.
6. **Given** a NetBox address that holds a user name and password, or an HTTP library error that
   repeats a request header, **When** a plan fails because NetBox cannot be reached or refuses
   the token, **Then** the cause holds no part of the address, the header or the error text,
   only the system, the adapter and the failure type.
7. **Given** a failure of an error class that has no cause template, **When** the team views
   the run, **Then** the failure type is `internal_error`, the cause names the stage and the
   error class, and the next step is `infrahub-sync runs show <run_id>`, as it is today.

---

### User Story 4 - Recover a stopped apply by planning again (Priority: P1)

An apply stops partway because Infrahub rejected a write. The team views which operations were
written and which operation failed. They fix the cause, start a new plan, and view a plan that
holds only the operations that were not written. They review it and apply it.

**Why this priority**: the testing release has no resume. Planning again is the only way to
finish an import, so the new plan must not repeat what was already written.

**Independent Test**: stop an apply of a plan with a known number of operations at a known
operation, then plan again against the same source and branch. The new plan's operation count
equals the original count minus the operations written.

**Acceptance Scenarios**:

1. **Given** an apply of 2,291 operations that Infrahub stops at operation 693, **When** the team
   views the run, **Then** the run shows 692 of 2,291 operations written, names the operation
   that failed with its kind and source record identity, and states whether that operation may
   have written part of its change.
2. **Given** that stopped apply, **When** the team plans again without changing the source,
   **Then** the new plan holds no operation for the 692 nodes already written, and holds the
   failed operation and the 1,598 operations that were not attempted, minus any that the
   destination now already matches.
3. **Given** that stopped apply, **When** the team tries to apply the stopped run's plan again,
   **Then** the Sync API refuses it with `run-execution-conflict`, and the CLI states that the
   team must create and review a new plan.

---

### User Story 5 - Check a configuration before a run (Priority: P2)

Before the first plan, the team runs `infrahub-sync configs validate` on the registered
configuration version and names the Infrahub branch it will import into. The check reports every
mapped kind, attribute or relationship missing from that branch's schema, every Dropdown value
the mapping sets literally that is not a choice, every `$credential` variable the worker does not
hold, and every permission the import account lacks on that branch. The team fixes these before
any run starts.

**Why this priority**: these problems are found today only when a run fails in the worker. They
are blockers on the release scope page (INFP-651, INFP-652, SYNC-107, SYNC-147), but each one has
a workaround: run a plan and read its failure.

**Independent Test**: validate a configuration that maps an attribute missing from the branch
schema, references an unset credential, and uses an account that cannot create one mapped kind.
The check reports all three, names each one, and starts no run.

**Acceptance Scenarios**:

1. **Given** a configuration that maps an attribute that the named branch's schema does not
   hold, **When** the team runs `configs validate --branch <name>`, **Then** the report names the
   kind and the attribute.
2. **Given** a mapping that sets a Dropdown attribute to a literal value that is not one of its
   choices, **When** the team validates against a branch, **Then** the report names the kind,
   the attribute, the value and the choices.
3. **Given** a `$credential` reference whose variable no live worker holds, **When** the team
   validates, **Then** the report names the variable, never a value.
4. **Given** an import account that cannot create or update a mapped kind on the branch,
   **When** the team validates against that branch, **Then** the report names the kind and the
   missing permission.
5. **Given** a kind without a writable human-friendly ID, **When** the team validates and then
   plans, **Then** validation and the plan give the same answer on whether the kind can be
   created.
6. **Given** a new configuration version, **When** the team registers it, **Then** the response
   shows the package checksum without redacted content next to it.

---

### User Story 6 - Import the NetBox example from real NetBox data (Priority: P2)

The team starts with the shipped NetBox example package against its own NetBox. A plan on the
pinned NetBox `demo` dataset finishes with no run failure that the example's own mapping causes.
Each of the three cases of #374 is either written by the mapping or listed as a plan problem.

**Why this priority**: the success criteria of User Story 1 run on this example. It depends on
User Story 1 for the cases that become plan problems.

**Independent Test**: plan the pinned `demo` dataset with the example package and check that the
plan finishes and that each #374 case is written or listed.

**Acceptance Scenarios**:

1. **Given** a device whose NetBox status is not one of the five choices of the destination
   Dropdown, such as `planned`, **When** the team plans, **Then** the device is written with a
   translated status or listed as `dropdown_value_not_in_choices`.
2. **Given** a rack position of `2.5`, **When** the team plans, **Then** the device is written or
   listed as `wrong_value_type`.
3. **Given** two devices with the same name at two sites, **When** the team plans, **Then** both
   are written or both are listed as an identity problem.

---

### Edge Cases

- A record has more than one problem: each problem is listed, and the record counts once in the
  left-out count.
- Both records of a duplicate pair, and both source objects that share one Infrahub identity,
  are listed and left out, because the plan cannot tell which one is correct.
- Infrahub holds two nodes with the identity of one source record: the source record is left
  out, and no operation targets either node. Infrahub holds two nodes with an identity that no
  source record has: the problem is listed, nothing is left out, and neither node is deleted.
- A record depends on a left-out record that itself depends on a record with a problem: every
  record in the chain is left out, and each names the record it depends on.
- A left-out record already exists in Infrahub from an earlier run: the plan proposes no update
  and no delete for it, and the Infrahub node stays as it is.
- Every source record is left out: the plan finishes with zero operations, and the summary shows
  the left-out count. There is nothing to apply.
- The source or Infrahub cannot be reached, or refuses a token, during a plan: the plan fails
  with that cause. This is a run failure, not a record problem, and no partial plan is saved.
- Infrahub rejects a write during apply for a value the plan did not check: the apply stops, as
  it does today. Records are not left out during apply.
- The text of an error raised by a source adapter or an HTTP library holds an address with a
  password, a request header or a traceback: none of that text enters the cause, whatever the
  length of the credential.
- The problem list is very long, for example several thousand problems at tier M (about 10,000
  mapped objects): the lists are returned in pages.
- A credential value is shorter than six characters, or holds characters that JSON escapes: it
  does not appear in any cause, problem reason or plan file, because none of them is built from
  a credential or from error text. A source value that happens to equal a credential value is
  source data and appears like any other value.
- A plan saved in format 3 before this feature: it can still be viewed. What its apply does is a
  question for the product owner (see "Open questions").
- A run made by a direct Prefect flow, with no destination schema snapshot: the Dropdown check
  cannot run, and the plan summary states that Dropdown values were not checked.

## Requirements *(mandatory)*

### Functional Requirements

#### Problems found during a plan

- **FR-001**: A plan MUST check every source record and MUST NOT stop at the first record that
  has a problem.
- **FR-002**: A plan MUST detect and list these nine problem types:
  - `dropdown_value_not_in_choices`: a value outside the choices of a Dropdown attribute in the
    destination schema the plan read (#371)
  - `duplicate_source_identity`: two or more source records with the same source identity, for
    example duplicate IP addresses in NetBox
  - `destination_identity_collision`: two or more source records that would create Infrahub
    nodes with the same human-friendly ID (today `DestinationIdentityCollisionError` refuses the
    whole plan)
  - `wrong_value_type`: a value of the wrong type for the destination attribute, for example a
    rack position of 2.5 for an integer attribute
  - `required_peer_excluded_by_filter`: a required relationship whose peer a configured filter
    excluded (today the NetBox adapter stops the source read)
  - `identities_merge_in_destination`: two or more source records with different mapping
    identities that Infrahub would store as one node, because the destination identity uses
    fewer fields than the mapping (#166; today a warning only)
  - `peer_not_found_in_source`: a reference to a peer that is not in the source at all, for
    example a device that refers to a site the source does not return (today
    `SourcePeerUnresolvedError` fails the plan)
  - `destination_identity_ambiguous`: two or more Infrahub nodes of one kind with the same
    configured identity (the mapping's `identifiers`), found while the plan reads Infrahub
  - `transform_failed`: a configured transform that fails for one source record, for example a
    mapping of interface modes with no entry for `q-in-q`. The problem names the field and the
    transform's position in the mapping entry (`transforms[0]`), never the source value or the
    error text
- **FR-003**: Each listed problem MUST hold the kind, the source record identity, the problem
  type, and a reason that names the attribute or relationship and the value or peer involved.
  - Problems of the identity types MUST also name the other records involved.
  - A `peer_not_found_in_source` problem MUST also name the relationship and the peer's source ID.
  - A `destination_identity_ambiguous` problem MUST name the kind, the identity and the Infrahub
    node IDs, and no other attribute value. The plan MUST leave out the source record with that
    identity and plan no create, update or delete for that identity. When no source record has
    that identity, the problem MUST still be listed, nothing is left out, and no delete is
    planned for those nodes.

#### Records left out of a plan

- **FR-005**: A plan MUST leave out each record that has a problem, and every record that
  depends on it through a required relationship, at any depth. Two exceptions keep a record:
  - A record whose only problem is `peer_not_found_in_source` through an optional relationship
    MUST be kept and written without that relationship. The plan lists it both as a problem and
    as kept.
  - A record that refers to a left-out record only through an optional relationship MUST be kept
    and written without that relationship. The plan lists it as kept and names the left-out
    record. Once the left-out record is fixed, the next plan sets the relationship.
- **FR-006**: Each left-out record that has no problem of its own MUST name the record it
  depends on and the relationship through which it depends.
- **FR-007**: The plan summary MUST show the count of records left out next to the existing
  counts by action and by kind and the count of deletes not executed. It MUST show how many
  left-out records have a problem and how many depend on one, the count of problems by type, and
  the count of kept records.
- **FR-008**: A left-out record MUST produce no create, update or delete in the plan.
- **FR-009**: The next plan MUST check left-out records again, with no action from the team, and
  include them when they no longer have a problem.
- **FR-010**: An apply of a plan that leaves out records MUST require the person to confirm that
  they reviewed the records left out: `--confirm-left-out` in the CLI (a flag without a value)
  and `confirm_left_out: true` in the Sync API apply request. An apply request without that
  confirmation MUST be refused before any write, and the refusal MUST state the count of records
  the plan leaves out. The confirmation does not repeat the count: the plan checksum already
  binds the apply to the reviewed plan.
- **FR-011**: An apply of a plan that leaves out no records MUST NOT ask for that confirmation.
  A confirmation sent with such an apply MUST be accepted and ignored.
- **FR-012**: A `sync` run whose plan leaves out records MUST stop after the plan, before any
  write, with the plan saved. The stage result MUST carry `failure` with the failure type
  `left_out_review_required` and a next action that names the run ID, the plan checksum and
  `--confirm-left-out`; the last log line, the Prefect run artifact and the CLI MUST show that
  next action. The team then applies the run as it applies a `plan` run, with the confirmation of
  FR-010. No option skips that stop in the testing release. A policy for runs that nobody
  watches belongs to 3.0.

#### Cause of a failed or stopped run

- **FR-014**: The `results.<stage>_failure` record that spec 017 defines MUST gain these fields,
  next to the fields it holds today (`stage`, `outcome`, `error_type`, `cause_type`, `cause`,
  `next_action`, `infrahub_error`, `recovery_action` and the apply record keys), which MUST keep
  their meaning:
  - `failure_type`: one stable name from this list: `destination_rejected_write`,
    `system_unreachable`, `system_timeout`, `token_rejected`, `permission_missing`,
    `tls_certificate_rejected`, `record_load_failed`, `transform_failed`, `credential_missing`,
    `verification_failed`, `apply_refused`, `left_out_review_required`,
    `destination_peer_missing`, `destination_peer_ambiguous`, `lineage_refused`,
    `destination_schema_unavailable`, `internal_error`
  - `system`: the role (`source` or `destination`) and the adapter name, when one system is
    involved
  - `error_code`, `node_kind` and `attribute`: as Infrahub returns them for a rejected write
  - `infrahub_message`: Infrahub's own message, for a write that Infrahub rejected
  - `record`: the kind, the source record identity and the field involved, when the failure
    names them
  - for a stopped apply: the total count of operations in the plan, and the failed operation's
    action, kind and source record identity, next to the operation ID recorded today

  Every failure that the worker records MUST carry a `failure_type`. A failure of an error class
  with no template is `internal_error`.
- **FR-015**: The CLI MUST print these fields when a `plan`, `verify`, `apply` or `sync` command
  ends with a failed or stopped run, and when the team runs `runs show`, after the existing
  `<stage> failed:` line and before `hint:`. The Sync API MUST return them in `GET /runs/{run_id}`
  and `GET /runs/{run_id}/results`, where the record is returned today.
- **FR-016**: When Infrahub rejects a write, the cause MUST hold Infrahub's error code when
  Infrahub returns one, the node kind and attribute name when Infrahub returns them, and
  Infrahub's message.
- **FR-017**: When the source or Infrahub cannot be reached, does not answer in time, or refuses
  a token, during a plan, a verify or an apply, the cause MUST name the system (source or
  destination, and the adapter) and the failure type.
- **FR-018**: Every other failure of a `plan`, `verify`, `apply` or `sync` run MUST record its
  failure type, and the kind, the source record identity and the field when the failure names
  them. A peer that an apply operation refers to and that Infrahub no longer holds MUST have the
  type `destination_peer_missing`, naming the destination, the peer kind and the operation. A
  peer that matches more than one Infrahub node MUST have the type `destination_peer_ambiguous`.
- **FR-019**: A cause MUST be built only from these fields:
  - the stage and the failure type
  - the system involved: source or destination, and the adapter name
  - Infrahub's error code, node kind and attribute name
  - Infrahub's own message, for a write that Infrahub rejected, and the permission or message
    Infrahub returns with a 403, as today
  - the kind, the source record identity and the field involved
  - the operations written and the operation that failed
  - text from a fixed template filled with the fields above, settings names, kind names, node
    names, account names, branch names and IDs (the lineage refusal and schema-read texts of
    today)

  A cause MUST NOT hold the text of an error raised by an adapter, an HTTP library or a
  configuration parser, such as an address, a request header or a traceback. Each field MUST be
  limited in length.
- **FR-020**: A decision record in `dev/adr/` MUST replace the rule that a run's failure record
  holds an error class name and never a message (#369, comment at
  `infrahub_sync/service/flow.py:834-836`; [ADR 0005](../../adr/0005-translate-run-failures-only-at-the-remote-boundary.md)
  places failure translation at the remote boundary). The new rule: the failure record holds
  the allowlisted fields of FR-019, which include Infrahub's own message, and never the text of
  other errors. The code comment and the
  [secret redaction guideline](../../../docs/docs/development/guidelines/secret-redaction.md) MUST
  be updated to the new rule.

#### A stopped apply and the next plan

- **FR-021**: A stopped apply MUST show the count of operations written out of the total, the
  IDs of the written operations (as `GET /runs/{run_id}/results` returns them today), the
  operation that failed with its kind and source record identity, and whether that operation may
  have written part of its change.
- **FR-022**: A new plan after a stopped apply MUST hold only the operations that the
  destination does not already match. It MUST NOT hold operations for nodes that the stopped
  apply wrote and that still match the source. A test MUST stop an apply partway and check this.
- **FR-023**: When the team applies the plan of a run that already admitted a write, the CLI
  MUST print the existing `run-execution-conflict` refusal with the next step: create and review
  a new plan. No new refusal code is added. A `sync` run that stopped under FR-012 dispatched no
  write; its one confirmed apply is admitted, and any apply after that is refused the same way.

#### What the Sync API returns and what the CLI prints

- **FR-024**: The Sync API MUST return, for a plan run, with the same authentication as the
  existing run and plan routes:
  - the plan summary with the counts of FR-007, in `GET /runs/{run_id}/plan`
  - the problem list of FR-003, filterable by problem type and kind
  - the left-out list of FR-006, filterable by kind
  - the kept list of FR-005: each kept record with its kind, its source record identity, the
    relationship, and the left-out record or the missing peer's source ID, filterable by kind

  The three lists MUST be returned in pages, in a stable order, and each page MUST name the plan
  checksum it comes from.
- **FR-025**: Each problem type and each failure type MUST have a stable name that does not
  change between releases. A later release may add a name; it never renames or removes one.
- **FR-026**: The Sync API apply request MUST carry `confirm_left_out` (a JSON boolean, false when
  absent), next to the `expected_checksum` and `confirm_writes` it carries today. A request that
  leaves it out keeps working when the plan leaves out no records.
- **FR-027**: The CLI MUST show:
  - after `infrahub-sync plan` and `infrahub-sync runs plan <run_id>`: the counts of FR-007, the
    first 20 problems, and the command that lists the rest
  - with `infrahub-sync runs plan <run_id>` and the options `--problems`, `--left-out` and
    `--kept`: every entry of that list, read page by page, one line per entry, with filters by
    problem type and kind

  No `runs problems`, `runs left-out` or `runs kept` command is added (decision 8).
- **FR-028**: The Prefect run summary artifact that spec 016 publishes for a stage MUST show, for
  a plan or `sync` stage, the count of records left out and the count of problems, and its next
  steps MUST name the `runs plan` options of FR-027 when the plan leaves out records.

#### Credential values

- **FR-029**: A credential value, whatever its length or characters, MUST NOT enter a cause, a
  problem reason, a left-out or kept entry, a plan file or a Sync API response. Sync uses a
  credential value only to connect to the system it belongs to. No cause, problem reason or plan
  field is built from connection settings or from the text of an error (FR-019).
- **FR-030**: The protection of FR-029 MUST NOT depend on replacing values in text. This
  specification adds no replacement of values and removes none: the masking pass that exists
  today stays as it is in the testing release (product owner decision of 2026-10-15).

#### Saved plan format and decision records

- **FR-031**: Plans with problems, left-out records or kept records MUST be saved in plan format
  4. Format 4 keeps every field of format 3, including the recorded destination IDs, the
  lineage record and the last-changes file of spec 014. It adds a report of the problems, the
  left-out records and the kept records, and their counts, all covered by the plan checksum.
  Every reader that applies a plan MUST refuse a format 4 plan whose report is missing or does
  not match its counts.
- **FR-032**: Three decision records MUST be written in `dev/adr/`:
  - 0020: records with problems, and the records that depend on them, are left out at the plan
    stage, counted, and confirmed at apply
  - 0021: a run's failure record holds a structured cause built from allowlisted fields (the
    record FR-020 requires)
  - 0022: saved plan format 4

#### The NetBox example package on real NetBox data

- **FR-033**: The shipped NetBox example package (`examples/netbox_to_infrahub/package.yml`) MUST
  plan the pinned NetBox `demo` dataset with no run failure caused by its own mapping. The three
  cases of #374 MUST be handled:
  - a device status outside the five choices of the destination Dropdown, such as `planned`
  - a rack position that is not an integer, such as `2.5`
  - two devices with the same name at two sites

  Each case MUST either be written by a changed mapping, or be listed as a plan problem of
  FR-002. The example documentation states which.

#### Problems found before a plan, with `configs validate`

- **FR-034**: `infrahub-sync configs validate --branch <name>` and
  `POST /configs/{config_id}/versions/{registry_version}/validate` with a branch MUST run the
  destination schema check against the Infrahub branch the team names: every mapped kind,
  attribute and relationship exists, and every Dropdown value that the mapping sets literally is
  one of the attribute's choices. Without a branch, validation stays as it is today.
- **FR-035**: The same check MUST report each `$credential` reference whose variable no live
  worker holds, by name, without reading or showing its value. When no live worker has reported
  which variables it holds, the check MUST say so as a warning.
- **FR-036**: The same check MUST report, for the import account on the named branch, each mapped
  kind that the account cannot view, create or update, and each `source` or `owner` lineage name
  that the account cannot look up or that matches no node or more than one node (spec 014
  decision D-1).
- **FR-037**: Validation and the plan MUST agree on whether a kind that has no writable
  human-friendly ID can be created (SYNC-147). A kind that validation accepts MUST NOT be refused
  by the plan for that reason, and the reverse. A test MUST check both paths on the same kinds.
- **FR-038**: The response to `configs register` and to a new version MUST NOT show redacted
  package content next to the checksum of the original content (SYNC-107). It MUST show either
  the checksum alone, or content and a checksum that match.

FR-004 and FR-013 of old 009 are not repeated: they describe behavior that `feature/v3-develop`
already has (see "What `feature/v3-develop` already does"). Their numbers are not reused, so
that the other numbers match old 009.

### Key Entities

- **Plan problem**: one problem found in one source record. It holds the kind, the source record
  identity, the problem type, the reason, and, by type: the other records involved, the
  relationship and the missing peer's source ID, the Infrahub node IDs, or the transform's
  position in the mapping entry.
- **Left-out record**: a source record that the plan leaves out. It holds the kind, the source
  record identity, and either its own problem types or the record and relationship it depends
  on.
- **Kept record**: a source record that the plan keeps without an optional relationship, because
  the peer is left out or is not in the source. It holds the kind, the source record identity,
  the relationship, and the left-out record or the peer's source ID.
- **Plan summary**: the existing counts by action and by kind and of deletes not executed, plus
  the counts of FR-007.
- **Plan report**: the problems, left-out records and kept records of one saved plan, covered by
  the plan checksum (format 4).
- **Stage failure record** (`results.<stage>_failure`, spec 017): extended with the cause fields
  of FR-014. It never holds the text of other errors.
- **Apply record**: for a stopped apply, the operations written, the total, the operation that
  failed with its kind and identity, and whether that operation may have written part of its
  change.
- **Validation finding**: an existing finding of `configs validate`, with new codes for a
  Dropdown literal, a credential not set in the worker, and a missing permission or lineage name.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the NetBox example data with planted bad records, one plan lists every planted
  problem. On 2026-10-05 the same problems took five configuration versions.
- **SC-002**: A fixture with one record of each of the nine problem types gives one plan that
  lists all nine, and its apply writes only the valid records after the records left out are
  confirmed.
- **SC-003**: For 100% of failed or stopped runs in the test suite, the CLI and the Sync API show
  a failure type and a cause without reading container logs or Prefect.
- **SC-004**: After an apply stopped partway, the new plan's operation count equals the stopped
  plan's operation count minus the operations written, adjusted only for changes made in the
  source between the two plans.
- **SC-005**: No cause, problem reason, left-out entry or kept entry in the test suite holds a
  credential value or the text of an error raised by an adapter or an HTTP library.
- **SC-006**: 100% of apply attempts on a plan with records left out are refused when they do not
  confirm the records left out, before any write.
- **SC-007**: In tests with a referenced credential of five characters and a referenced
  credential that holds `"`, `\` and `,`, where each failure type is raised with error text that
  holds both values (for example an address with a user name and password), neither value
  appears in a cause, a problem reason, a plan file or a Sync API response.
- **SC-008**: The NetBox example package plans the pinned NetBox `demo` dataset with no run
  failure, and each of the three #374 cases is either written or listed as a plan problem.
- **SC-009**: On a configuration that maps an attribute missing from the destination schema and
  references an unset credential, `configs validate` reports both before any run starts.
- **SC-010**: For each bundled source adapter that resolves references while it reads, a source
  record that refers to a peer that is not in the source gives one listed problem, and the plan
  finishes.
- **SC-011**: On an import account that lacks the permission to create one mapped kind on the
  named branch, `configs validate` reports that kind and permission before any run starts.

## Assumptions

- Duplicate records and identity collisions leave out every record involved, not all but one,
  because the plan cannot tell which record is correct.
- A problem in a record that already exists in Infrahub leaves the Infrahub node as it is. The
  plan proposes no update and no delete for it.
- Source values may appear in problem reasons and in Infrahub's message, as they already do in
  the plan operations the team reviews. Credential values do not appear (FR-029).
- Infrahub's error message describes the write Infrahub received, which holds source data and no
  Sync credential. Infrahub does not receive the source credentials.
- The worker container log and the Prefect log keep the full error text for OpsMill engineers,
  protected only by the masking pass of today, which skips values shorter than six characters
  (`infrahub_sync/execution.py:128`). A short credential can therefore still appear in those two
  logs until they record the structured cause instead of the error text. That is 3.0 work. Those
  logs are not returned by the Sync API or printed by the CLI; the run log that spec 017 returns
  holds only lines that Sync writes.
- Infrahub 1.11 and later return an error code, the node kind and the attribute name with a
  rejection (#370). Whether every rejection carries the kind and attribute is not known; the
  cause leaves them empty when Infrahub does not return them.
- The Dropdown check needs the attribute choices in the destination schema record that a plan is
  bound to. Adding them changes that record.
- A plan failure that is not tied to one record, such as an unreachable system or a refused
  token, still fails the whole plan. Only the nine problem types of FR-002 leave records out. A
  transform that fails, even for every record, leaves out each record it fails for.
- The testing release supports only the adapters that Sync bundles, and up to about 10,000
  mapped objects. Every problem type except `destination_identity_ambiguous` is checked for every
  bundled source adapter. `destination_identity_ambiguous` is checked only when the plan reads
  every node of a kind from Infrahub, not on an incremental read through the cache.
- The time the new checks add to a plan at tier M is not known. Spec 018 records each side's
  read; the plan phase decides how to measure the rest.
- Spec 012 task T031 (the v2 gate) waits for the left-out and problem counts of this
  specification.

## Dependencies

- **AD-12 amendment (external).** Product decision AD-12 in
  `opsmill/infrahub-sync-process`, `product/decisions.md`, is still "Open" on that repository's
  main branch. The amended row ("at the plan stage, records with problems and the records that
  depend on them through a required relationship are left out and counted; apply needs the
  person to confirm that they reviewed the records left out, and a `sync` run whose plan leaves
  out records stops after the plan; the policy for runs that nobody watches is open for 3.0")
  exists only on the unmerged branch `product/ad-12-plan-stage-policy` (commit `f0486ff`). It
  must be merged there before FR-005 to FR-012 ship. This repository does not edit that
  repository.
- **Product owner sign-off.** The product owner signed off ADR 0020 with the AD-12 amendment and
  ADR 0021 on 2026-10-17, for old 009. Whether that sign-off covers the rewritten records of this
  specification is not known (see "Open questions").

## Out of Scope

- Resume of a stopped apply (INFP-650), executed deletes, and the AD-12 policy for runs nobody
  watches, including any option that applies a plan with records left out without review.
- Removing the masking pass from published plans, where it can make a plan invalid JSON
  (SYNC-262), and recording the structured cause instead of error text in the worker and Prefect
  logs (the rest of SYNC-82). Both are 3.0 work.
- The operator UI (spec 011), which reads the contract this specification defines.
- Records removed by a configured filter are not counted as left out; only a required peer that
  a filter excluded is a problem.

## Traceability to old specification 009

| Old 009 | This specification | How |
| --- | --- | --- |
| FR-001 to FR-003, FR-005 to FR-012 | Same numbers | Kept. FR-012 uses `result["failure"]` (decision 1) |
| FR-004, FR-013 | "What `feature/v3-develop` already does" | Delivered |
| FR-014, FR-015 | FR-015 | Delivered by spec 017; extended with the new fields |
| FR-016 to FR-018 | FR-016 to FR-018 | Extensions of `<stage>_failure` |
| FR-019 | FR-019 | Kept; allowlist names today's template texts |
| FR-020 to FR-022 | FR-021 to FR-023 | Extended; FR-023 keeps `run-execution-conflict` (decision 4) |
| FR-023, FR-024, FR-026 | FR-024, FR-025, FR-026 | Kept; FR-025 also covers failure types |
| FR-025 | FR-014 | Changed by decision 1: fields on `<stage>_failure`; three failure types added for refusals that spec 014 and spec 017 introduced |
| FR-027, FR-028 | FR-029, FR-030 | Kept; masking unchanged (decision 3) |
| FR-029 | "Dependencies" | External (decision 10) |
| FR-030 | FR-020, FR-032 | Kept; numbers of decision 5 |
| FR-031 | FR-033 | Kept; `plan`, not `diff` (decision 7) |
| FR-032, FR-033 | FR-034, FR-035 | Kept |
| FR-034, FR-035 | FR-037, FR-038 | Kept |
| Contract: `runs problems`, `runs left-out`, `runs kept` | FR-027, FR-028 | Changed by decision 8 |
| Plan: format 4 | FR-031 | Decision 6 |
| Commit `a154b907` on the old branch | FR-036 | Decision 9 |
| SC-001 to SC-010 | SC-001 to SC-010 | Kept; SC-011 added for FR-036 |

## Open questions for the product owner

1. Does the sign-off of 2026-10-17 on ADR 0020 and ADR 0021 cover the rewritten records, which
   extend `<stage>_failure` instead of adding a `failure` field?
2. Apply of a plan saved in format 3 before this feature: refuse it with "plan again before
   applying", as old 009 did, or apply it as a plan with nothing left out? The plan phase
   proposes a default.
