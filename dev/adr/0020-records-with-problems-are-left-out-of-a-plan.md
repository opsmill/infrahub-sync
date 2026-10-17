# 20. Records with problems are left out of a plan

**Status**: Proposed, needs product sign-off (spec 009 FR-029)
**Date**: 2026-10-15
**Source**: `dev/specs/009-plan-problem-reporting` (research R11)

This record holds the engineering side of an amendment to product decision AD-12. The text of
the amendment is in `dev/specs/009-plan-problem-reporting/ad-12-amendment.md`.

## Context

A plan stops at the first source record that cannot be planned (#369). AD-12 says that failed
dependencies are visible and block dependent work, and leaves the policy for single records
open. The comment at `infrahub_sync/plan/derive.py:36-39` (AD047) states that a derivation
failure fails the command and that warning and dropping a record would produce a plan that is
incomplete without saying so. Two adapters, Nautobot and Infrahub, already skip records that
fail validation, without counting them, when `continue_on_error` is set.

## Decision

- At the plan stage, a record with one of nine named problems is left out, and so is every
  record that depends on it through a required relationship. A record that refers to it only
  through an optional relationship is kept, without that relationship. A reference to a peer
  that is not in the source is one of the nine problems; through an optional relationship, the
  referring record is kept without that relationship.
- DiffSync compares the stores as loaded. A record that cannot be built is not loaded. Every
  other record is loaded as the source holds it, references included. The adapter base class
  owns the loop over records and is the one place that catches a record that cannot be built.
- Problems are found after derivation, by a step that reads the stores and the operations. One
  selection step applies the left-out and kept decisions to the plan's operations. No step
  applies them to the stores or to the diff.
- The plan lists each problem, left-out record and kept record and counts them, and its checksum
  covers the list (ADR 0022). Apply needs the person to confirm the records left out. A
  `sync` run whose plan leaves out records stops after the plan.
- Every other plan failure still fails the plan. During apply, an Infrahub rejection still stops
  the apply. The `continue_on_error` parameter is removed.

The nine problem types are `dropdown_value_not_in_choices`, `duplicate_source_identity`,
`destination_identity_collision`, `wrong_value_type`, `required_peer_excluded_by_filter`,
`identities_merge_in_destination`, `peer_not_found_in_source`,
`destination_identity_ambiguous` and `transform_failed`. `destination_identity_ambiguous` is
found while the plan reads Infrahub: two or more Infrahub objects of one kind have the same
configured identity. The source record with that identity is left out, and no operation targets
that identity, a delete included. `transform_failed` is a configured transform that fails for
one source record, for example a mapping of interface modes that has no entry for `q-in-q`. The
loader records the record and does not load it, and the problem names the field and the
transform's position in the mapping entry, not the source value or the error text (ADR 0021).

## Consequences

- The rule of AD047 changes for these nine problem types only. A transform that fails no longer
  fails the plan; it leaves out each record it fails for.
- A left-out record that exists in Infrahub is not updated and not deleted.
- An adapter that overrides `model_loader` does not get the shared record admission step.
- The policy for runs that nobody watches stays open for 3.0.

## Alternatives considered

- Fail at the first problem (the behaviour before this decision): a team needs one plan for
  each problem.
- Remove left-out records from the stores before the diff: DiffSync would compute a diff that
  is not true of the source.
- Leave out a whole kind when one of its records has a problem: leaves out valid records the
  team could import.
