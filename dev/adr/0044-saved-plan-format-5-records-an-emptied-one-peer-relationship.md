# 44. Saved plan format 5 records an emptied one-peer relationship

**Status**: Accepted
**Date**: 2026-11-16
**Source**: `dev/specs/019-second-plan-real-changes/spec.md` (FR-005 to FR-013a, User Story 2),
`dev/specs/019-second-plan-real-changes/research.md` (R4 to R8),
`dev/specs/019-second-plan-real-changes/contracts/plan-format-5.md`

## Context

When an interface leaves its LAG in the source, the source record holds no peer for the
optional one-peer relationship of the LAG, and the destination node still holds one. Plan
format 4 could not record that change: a one-peer reference named exactly one peer, and an
absent reference means that the apply leaves the relationship as it is
([ADR 0001](0001-saved-plan-artifact-format.md),
[ADR 0022](0022-saved-plan-format-4.md)). Derivation therefore left the field out of the update
and logged a warning. The destination kept the old peer, and every following plan showed the
same difference again, so a second plan was never empty after such a change.

A many relationship already had a way to say "no peer": a reference with an empty peer list
replaces the set with nothing ([ADR 0012](0012-the-convergent-upsert-is-the-replace-set-write.md)).

A live check on Infrahub 1.11.9 with SDK 1.23.2 showed that an Upsert whose input holds
`<field>: null` removes the edge of that one-peer relationship.

## Decision

A one-peer reference with an empty peer list on an update means that the relationship becomes
empty. Plans that hold it are saved in format 5.

- `RelationshipReference(cardinality="one", peers=[])` is accepted from format 5 on. The data
  model refuses it on a create, on a field that is part of the operation identity, and in a plan
  of format 4 or earlier. No new field is added to the operation.
- Every plan is saved in format 5, whether or not it holds an emptied relationship. Format 4
  plans are still read, verified, approved, applied and resumed. The checksum covers
  `operations.jsonl` in full, as in format 4.
- Derivation adds the empty reference to an update when the field is a one-peer reference field
  of the mapping and not an identifier, the source value is `None`, the destination node holds a
  peer, the source mapping model marks the field optional, and no configured filter removed the
  peer. The shared reference helper records a peer that a filter removes, so that derivation
  leaves that relationship alone. The warning of the previous behavior is removed.
- When the source mapping model marks the field required and the source record holds no peer, the
  source read reports a problem of the new type `required_relationship_empty_in_source`. The
  record is left out of the plan and its destination node is kept, as for
  `required_peer_excluded_by_filter` ([ADR 0020](0020-records-with-problems-are-left-out-of-a-plan.md)).
- Apply writes the emptied relationship as `<field>: null` in the one Upsert of the operation. The
  write is refused before it is sent when the rendered input holds anything else for that field,
  or renders a relationship that the operation does not name.
- The pre-write checks compare the empty planned peer set with the destination peer set like any
  other relationship. `runs plan --detail` prints `<field>: becomes empty`.

## Consequences

- An interface that leaves its LAG gives one update, the apply removes the peer, and the next plan
  on unchanged data is empty.
- A release earlier than this one refuses every plan of this release, with a message that names
  version 5 and the versions it supports. The Sync API and the worker are upgraded together.
- Saved plans in format 4 are not converted. Applying one leaves an emptied relationship as it is,
  as before.
- The operation stays one Upsert, so the resume position does not move.
- A field that the source mapping model marks optional but the destination schema requires gets no
  reference and no problem.
- A record with no source ID whose peer a configured filter removed is not recorded as filtered,
  so the plan empties that relationship.

## Alternatives considered

- **A `None` value for the relationship in the payload.** Rejected: the payload is reviewed as
  attributes, and the SDK renders `None` as `{"id": "None"}`.
- **A new list of emptied fields on the operation.** Rejected: it adds a field that every reader
  must handle, where the empty peer list reuses the meaning it already has for a many
  relationship.
- **Writing format 4 when a plan holds no emptied relationship.** Rejected: it gives two rules
  for the format. The deployment runs one release of the Sync API and the worker at a time.
- **A second SDK `update()` call that removes the peer.** Rejected: it adds a write to the
  operation and moves the resume position
  ([ADR 0027](0027-an-interrupted-apply-resumes-from-its-saved-position.md)).
