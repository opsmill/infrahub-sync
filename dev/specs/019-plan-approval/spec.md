# Feature Specification: Approve a plan without applying it, and a `verify` that checks the destination

**Feature Branch**: `verify-apply-approval`

**Created**: 2026-11-10

**Status**: Draft

**Input**: The idea brief "Approve a plan without applying it, and a `verify` that checks the destination",
grilled with the user on 2026-11-10. Every decision in the brief was confirmed by the user. The brief is compared
with this specification in [`alignment-check.md`](./alignment-check.md).

## Background

A customer who tested V3 expected a "validate, then apply" flow after `plan`. What they found:

- `verify` checks only the files that the plan run saved: the run binding, the plan checksum, the saved source
  snapshot and the configuration version. It contacts neither the source nor the destination, and it is optional.
- `apply` runs the same checks again before its first write. It also checks that the destination URL and branch
  match the plan, that the destination adapter can apply a saved plan, that every planned field still exists in the
  destination schema and can be written, and that the plan matches the `--expected-checksum` the operator gives.
- The only way to accept a plan is to apply it.

The same pain point is recorded for V3 under INFP-653 and INFP-647: "An operator cannot verify a saved plan from the
CLI without applying it."

### Terms used in this specification

- **Plan run**: a Sync run whose plan stage succeeded and saved a plan with its checksum.
- **Approval**: a recorded decision on one run that names the actor who approved it, the time, and the plan
  checksum the actor reviewed.
- **Approval-required setting**: a service-wide setting of the Sync API, off by default. When it is on, only an
  approved plan can be applied, and `sync` is refused.
- **Pre-write checks**: every check `apply` runs before its first destination write.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Approve a plan without applying it (Priority: P1)

An operator reviews a plan with `runs plan` and runs `approve RUN_ID --expected-checksum CHECKSUM`. The run records
who approved it, when, and which checksum. Nothing is written to the destination. Later, the operator or an
administrator applies the plan with `apply`, as today.

**Why this priority**: It removes the pain the customer reported: accepting a plan no longer means writing it.

**Independent Test**: Plan a run, approve it with its checksum, read the run, and check that the destination has
not changed.

**Acceptance Scenarios**:

1. **Given** a plan run that has not been applied, **When** the operator approves it with the plan's checksum,
   **Then** `runs show` and the Sync API return the approval with the actor, the time and the checksum, and no
   destination write happened.
2. **Given** a plan run, **When** the operator approves it with a checksum that is not the plan's checksum,
   **Then** the approval is refused, nothing is recorded, and the error shows the expected and the given checksum.
3. **Given** an approved run, **When** the operator approves it again, **Then** the second approval is refused and
   the error shows the existing approval.
4. **Given** a run whose plan stage failed, or a run that was applied, **When** the operator approves it, **Then**
   the approval is refused and the error says why.
5. **Given** a run started by another actor, **When** a non-administrator approves it, **Then** the approval is
   refused as for any other stage on that run.

---

### User Story 2 - Check that the plan would still apply (Priority: P2)

An operator runs `verify RUN_ID` before applying, for example just before a maintenance window. `verify` runs every
pre-write check against the current destination and writes nothing. When a check fails, `verify` names it.

**Why this priority**: It gives the operator the "validate" step the customer expected. It depends on nothing in
User Story 1.

**Independent Test**: Plan a run, change the destination so that a pre-write check fails, run `verify`, and check
that it fails on that check and that the destination did not change.

**Acceptance Scenarios**:

1. **Given** a plan run whose destination is unchanged, **When** the operator runs `verify`, **Then** it succeeds
   and reports each check it ran.
2. **Given** a plan run whose destination schema has since lost a planned field, **When** the operator runs
   `verify`, **Then** it fails and names the kind and the field.
3. **Given** a plan run whose configuration now points at another destination URL or branch, **When** the operator
   runs `verify`, **Then** it fails on the destination binding check and shows the recorded and the current
   destination.
4. **Given** a plan run whose destination is unreachable, **When** the operator runs `verify`, **Then** it fails,
   says that the destination could not be reached, and does not report the plan as valid.

---

### User Story 3 - The site requires approval (Priority: P3)

The person who operates the Sync API turns on the approval-required setting. From then on, only an approved plan can
be applied, and `sync`, which plans and writes in one run, is refused.

**Why this priority**: Approval is optional by default. This story lets a site make it mandatory without changing
the commands its operators use.

**Independent Test**: Start the Sync API with the setting on, then try to apply an unapproved plan, apply an
approved plan, and run `sync`.

**Acceptance Scenarios**:

1. **Given** the setting is on and a plan run has no approval, **When** the operator applies it with the right
   checksum, **Then** the Sync API refuses before any write and tells the operator to approve the plan first.
2. **Given** the setting is on and a plan run is approved, **When** the operator applies it with the approved
   checksum, **Then** the apply proceeds as today.
3. **Given** the setting is on, **When** the operator runs `sync`, **Then** the Sync API refuses before the run
   starts and tells the operator to run `plan`, `approve` and `apply` instead.
4. **Given** the setting is off, **When** the operator applies an unapproved plan or runs `sync`, **Then** the
   result is the same as before this feature.

---

### Edge Cases

- **Approval with the wrong checksum**: refused, nothing is recorded.
- **A second approval**: refused, and the error shows the existing approval. A retry with the same idempotency key
  returns the original response, as for other commands.
- **Approving a run that cannot be approved**: a run with no successful plan, a run that was applied, or a run whose
  plan stage is still running is refused with the reason.
- **Withdrawing an approval**: there is no withdrawal in v1. `runs cancel` cancels only an execution in progress, so
  it cannot withdraw an approval of a finished plan. An operator who no longer wants an approved plan applied does not
  apply it and creates a new plan run.
- **The setting is turned on while runs exist**: approved runs can be applied; unapproved runs must be approved
  first. The setting is read when each `apply` or `sync` request arrives.
- **The setting is turned off after approvals exist**: approvals stay recorded and visible; `apply` ignores them.
- **`verify` cannot reach the destination**: `verify` fails with a reachability error. It never reports success
  for a check it could not run.
- **`verify` while an `apply` of the same configuration is writing**: `verify` writes nothing and does not wait for
  the write guard. Its result describes the destination at the time it read it.
- **An approved plan whose destination changed**: approval does not run the pre-write checks against the
  destination. `apply` still runs them and refuses as today; `verify` reports the failure beforehand.

## Requirements *(mandatory)*

### Functional Requirements

#### Approval

- **FR-001**: Operators MUST be able to approve a plan run by naming the run and the plan checksum. Approving MUST
  NOT write to the destination.
- **FR-002**: The system MUST record the approval on the run with the approving actor, the time and the checksum,
  and MUST return it when the run is read through the Sync API and in `runs show`.
- **FR-003**: Approving MUST refuse a checksum that differs from the saved plan's checksum, and MUST record nothing
  in that case.
- **FR-004**: The system MUST check, before recording an approval, that the saved plan files are unchanged (the
  checks `verify` runs today). A failed check MUST refuse the approval and name the check.
- **FR-005**: Only a run whose plan stage succeeded, that is not being planned, and that has not been applied MAY be
  approved. Approving any other run MUST be refused with the reason.
- **FR-006**: A run MUST have at most one approval. A second approval MUST be refused and MUST show the existing
  approval.
- **FR-007**: Only the actor who started the run, or an administrator, MAY approve it, as for the other stages of a
  run today.
- **FR-008**: Approving MUST accept an optional audit reason and an idempotency key, as the other run commands do.
  The system MUST record each approval request in the run's audit history, accepted or refused.

#### `verify` checks the destination

- **FR-009**: `verify` MUST run every pre-write check that `apply` runs, without writing to the destination. The
  checks are:
  - the saved plan files are unchanged (run binding, plan checksum, source snapshot, configuration version);
  - the destination URL and branch match the ones the plan recorded;
  - the destination adapter can apply a saved plan;
  - every planned field still exists in the current destination schema and can still be written.
- **FR-010**: When a `verify` check fails, the result MUST name the check and show what was expected and what was
  found, in the same terms `apply` uses when it refuses on that check.
- **FR-011**: When `verify` cannot reach the destination, it MUST fail with a reachability error and MUST NOT report
  any destination check as passed.
- **FR-012**: `verify` MUST NOT change the run's phase and MUST NOT record an approval.

#### Approval-required setting

- **FR-013**: The Sync API MUST read a service-wide setting, `INFRAHUB_SYNC_REQUIRE_APPROVAL`, off by default.
- **FR-014**: When the setting is off, `apply` and `sync` MUST behave exactly as before this feature.
- **FR-015**: When the setting is on, `apply` MUST refuse, before any destination write, a run with no approval, or
  whose approval names a checksum different from the `--expected-checksum` given. The error MUST tell the operator
  to approve the plan.
- **FR-016**: When the setting is on, the Sync API MUST refuse `sync` before the run starts. The error MUST tell the
  operator to run `plan`, `approve` and `apply` instead.
- **FR-017**: Operators MUST be able to read whether the setting is on, so that a refusal can be predicted before it
  happens.

#### Documentation

- **FR-018**: The user documentation MUST describe `plan`, `verify`, `approve` and `apply` each with one purpose,
  and MUST say that `verify` does not approve a plan and that approval is optional unless the setting is on.

### Key Entities

- **Run** (existing): gains an optional approval.
- **Approval** (new, part of a run): approving actor, time, plan checksum, optional audit reason. At most one per
  run. Never changed after it is recorded.
- **Plan checksum** (existing): the checksum of the saved plan; the approval names it.
- **Actor and administrator** (existing): each Sync API token belongs to one actor, which may be an administrator.
- **Approval-required setting** (new): service-wide, off by default.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator goes from a reviewed plan to a recorded approval in one command, with zero writes to the
  destination.
- **SC-002**: With the approval-required setting on, every `apply` of an unapproved plan and every `sync` is
  refused before any write. A test covers each case.
- **SC-003**: With the setting off, every existing `apply` and `sync` command and HTTP request gives the same result
  as before this feature, and the existing tests for them pass unchanged.
- **SC-004**: For each pre-write check that `apply` refuses on, a `verify` run against the same destination fails
  on the same check and names it.
- **SC-005**: The "Running a sync" page describes `plan`, `verify`, `approve` and `apply` each with one purpose and
  answers "is `verify` an approval?".

## Assumptions

- `verify` can reach the destination with the same credentials and network access that `apply` uses, because both
  run on the Sync worker.
- Without a second reviewer, an approval records a decision. It does not prove that a second person reviewed the
  plan.
- Approval is an API-side decision, not a worker stage: it does not start a Prefect flow run.
- The run events and log of spec 017 (PR #402) are not on this branch's base. The approval is recorded in the run
  and its audit history. Once spec 017 is merged, the approval SHOULD also appear as a run event; that is a follow-up,
  not a requirement of this feature.

## Out of Scope

- Requiring a reviewer other than the actor who planned ("four-eyes"). This waits for single sign-on.
- Checking whether destination objects changed since the plan. That means reading the whole destination again.
- Withdrawing an approval.
- Approval expiry.
- Setting the approval requirement for each configuration.
- Approval in the web UI. It can follow once the API exists.
- Changing `apply` arguments: `apply` keeps requiring `--expected-checksum`.

## Open Questions

- The brief proposed that an operator withdraws an approval by cancelling the run with `runs cancel`. The code
  refuses to cancel a run that has no execution in progress (`no-active-execution`, `execution-terminal`), so that is
  not possible for a finished plan. This specification records no withdrawal in v1 (see Edge Cases). Confirm that this
  is acceptable, or decide to add a withdrawal in a later feature.
