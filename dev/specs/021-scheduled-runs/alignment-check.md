# Alignment check: Scheduled runs that write without a person present

**Date**: 2026-11-17

## Source

The idea brief of 2026-11-17, agreed with the product owner in an interview in the session that
produced this specification. It is not stored in this repository. `spec.md` was compared with it
section by section, at commit `8561749c`.

The preparation was started with no text of its own, so the brief is the only source.

## Verdict

⚠️ MINOR DRIFT (proceeding)

- Every requirement (FR-001 to FR-023) and every success criterion (SC-001 to SC-006) of the
  brief is in the specification under the same number.
- Every item that the brief puts out of scope is out of scope in the specification.
- The order of the three user stories is the order of the brief.
- Five requirements were added, and four were changed in meaning. Each change follows from a
  fact of the code that the critique found, or states something that the brief implies. None
  adds a capability that the brief excludes.

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-022: a run whose plan has no change "MUST create no branch" | FR-022: "MUST leave no branch … when it ends" | The plan must be made on the branch it is applied to, so the branch exists for the time of the plan and is then deleted (research R11, critique E4). What the team views in Infrahub after the run is the same. |
| Minor | changed | FR-013: "when the owner's token is no longer valid" | FR-013: "when the owner's actor no longer exists" | The Sync API can detect a removed actor name, not a changed token under the same name (critique E8). The assumptions state this. |
| Minor | changed | FR-013 belongs to the third journey | FR-013 is in user story 1; the take-over is FR-026 in user story 3 | A schedule can stop as soon as the loop exists, so the stop and the resume cannot wait for user story 3 (critique P5). The take-over stays where the brief put it. |
| Minor | changed | Constitution alignment: the first journey "fits" principle I | Assumptions: the text of principle I needs an amendment for a write to a review branch too | The purpose of the principle is met. Its text asks for a person's approval of every `sync` (critique E13). The specification is more cautious than the brief. |
| Minor | changed | SC-002: every start produces "a run or a recorded skip" | SC-002: "a run, a recorded skipped start or a recorded failed start", and no start produces two runs | A start for which no run can be created needs its own record (critique P6). |
| Minor | added | The first journey names pause, resume, change and delete | FR-024 | States as a requirement what the journey of the brief names, and adds list and view. |
| Minor | added | Not in the brief | FR-025: no credential or source data in a schedule record, event, message or log line | The project's rule on secrets (constitution principle VI), stated for the new records. |
| Minor | added | Not in the brief | FR-027: branch review is refused for a configuration version that declares its own destination branch | Without it, such a schedule would write to the declared branch with no review, against FR-002 and SC-003 (critique E3). |
| Minor | added | Not in the brief | FR-028: a person cannot verify, apply or resume a run with branch review | The branch steps exist only in the scheduled `sync`, so an `apply` by a person would write to a branch that no longer exists (critique P3). |
| Minor | added | FR-004: the setting applies to branch review too | FR-004: with branch review, a run that stops after the plan leaves no branch | Says what the brief's rule means for a branch. |
| Minor | added | FR-023 event list | FR-023: also a failed start, a proposed change left in place, and one that could not be opened; every event of a scheduled run names its schedule | Events for outcomes that the brief did not list. |
| Minor | added | A stopped schedule: owner's token removed | A second reason: the Sync API now requires approval for the schedule's write target | Covers a restart that turns required approval on. |
| Minor | added | Branch name pattern uses "the schedule's name" | FR-006: a schedule has a name, unique among schedules that are not deleted | The brief implies a name and does not require it. |
| Minor | added | Not in the brief | Edge case: more than 500 missed starts of one schedule are recorded in one event for the oldest | A bound on one transaction. FR-018 ("every missed start") holds up to 500. |
| Minor | missing | "Governance Gates Crossed": database, API and authentication changes | Not a section of the specification | The plan covers them: the data model, the contracts, and the decision record that amends ADR 0037. |

## Open points carried from the brief

These are not drift. The brief left them open and the specification records them as assumptions
and prerequisites:

- Who delivers the two capabilities of INFP-655, and when.
- Whether the event system is delivered before user story 1.
- Whether constitution principle I is amended. The product owner did not answer this in the
  interview, so user story 2 is gated on it.

One point is new: whether a schedule should start a run while one of its proposed changes is
approved and not merged (critique P2).

## Action

Proceed. No phase is run again. The product owner reviews the changed and added requirements
above with the three questions of the critique report.
