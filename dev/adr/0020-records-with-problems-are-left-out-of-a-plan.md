# 20. Records with problems are left out of a plan, counted, and confirmed at apply

**Status**: Proposed
**Date**: 2026-11-11
**Source**: `dev/specs/019-plan-problem-reporting/spec.md` (FR-001 to FR-012, FR-024),
`dev/specs/019-plan-problem-reporting/research.md` (R3, R6, R10, R11, R14, R21)

## Context

Before this decision, a plan stopped at the first source record it could not use. One record
with a value of the wrong type, a Dropdown value the destination schema does not allow, or a
reference to a peer the source does not return ended the whole plan, and the team saw only
that first record. Two source records that Infrahub would store as one node made the plan
refuse with `DestinationIdentityCollisionError`. The team could fix the records one at a time
and plan again for each of them.

## Decision

A plan finds every problem in one pass and plans the rest of the data:

- The source adapter reads every record through one loop (`DiffSyncMixin.model_loader` and
  `load_record`) that records each record it cannot build in a load report, then continues
  with the next record.
- After the operations are derived, a problem pass (`infrahub_sync/plan/problems.py`,
  `run_problem_pass`) reads the load report, the source store, the destination schema and the
  derived operations. It returns the problems of nine types, the records it leaves out and the
  records it keeps without one optional relationship.
- A record with a problem is left out. A record that needs a left-out record through a required
  relationship is left out too, at any depth, and names the record it depends on. A record that
  refers to a left-out record, or to a peer the source does not return, through an optional
  relationship is kept and written without that relationship.
- Every record of an identity that two source records share in Infrahub is left out, with one
  problem for the group (`destination_identity_collision` or `identities_merge_in_destination`).
  The plan no longer refuses these groups.
- A pure selection step removes the operations of the left-out records and the relationship
  values of the kept records. Neither store and not the DiffSync comparison are changed after
  the read, so a plan of valid data keeps its bytes.
- The plan computes no delete for a kind that holds a left-out record without a known identity
  or an identity that two or more Infrahub nodes hold, because such a node could look absent
  from the source. The plan summary counts these records in `deletes_withheld_by_kind`.
- The plan summary counts the left-out records (with a problem, and depending on one), the
  problems and the kept records. The Sync API and the CLI list them, page by page.
- An apply of a plan that leaves out records requires `confirm_left_out`. A `sync` run whose
  plan leaves out records stops after the plan and gives back its write admission, so the team
  can review the plan and apply that same run with the confirmation.

## Consequences

- The team sees every problem of a source in one plan, and the plan writes the records that
  have none.
- A left-out record produces no create, update or delete. The next plan checks it again.
- The plan report becomes part of the saved plan, which needs plan format 4
  ([ADR 0022](0022-saved-plan-format-4.md)).
- The store rule "a run that admitted its one write never admits another stage" gets one
  exception: a `sync` run that stopped after its plan, before any dispatch, gives back its write
  admission (research R11). The product owner is asked to confirm this exception.
- Deletes are withheld for a whole kind when one of its left-out records has no known identity.
  A source with many such records computes fewer deletes until the records are fixed.
