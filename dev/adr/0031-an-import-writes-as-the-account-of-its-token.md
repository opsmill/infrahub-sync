# 31. An import writes as the account of its token, and sets an owner only on values it creates

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/009-import-lineage/research.md` (R-3, R-4, R-6),
`dev/specs/archive/009-import-lineage/spec.md` (Clarifications, D-3, D-4, D-8)

## Context

Infrahub records three properties on each attribute and each relationship edge: a source, an
owner and a protection flag. A team that imports data into Infrahub uses them to view where a
value came from and who is responsible for it.

Before this decision, an import to Infrahub:

- looked up a `CoreAccount` named after the source system and recorded it as the source and the
  owner of every value;
- wrote the value with an empty source or owner, and only logged a warning, when that account or
  a group the package named did not exist;
- marked every value it wrote as protected;
- wrote an update through the same call as a create, so it sent the owner on values that already
  existed. Infrahub replaces the owner of a value when an owner is sent.

So the team could not tell which import wrote a value when the account did not exist, the next
import replaced an owner that a person set in Infrahub, and the team could not turn protection
off. A reviewed plan also did not say which account would write it.

## Decision

- **The import account is the account that owns the API token.** At the start of each plan and
  each apply, the Infrahub adapter reads it with one `AccountProfile` query, which needs no
  permission on accounts (`InfrahubAdapter.resolve_lineage`). The lookup of an account named
  after the source system is removed.
- **The source is the import account, unless the package names one.** `source` and `owner` each
  name one node as `{kind, name}`. The kind is `CoreAccount`, `CoreAccountGroup`,
  `CoreRepository` or `CoreReadOnlyRepository`. Registration refuses a bare name. The source goes
  on every attribute and relationship edge the import writes.
- **A named node that does not exist stops the run.** The plan looks up each named node before
  it reads the source system. The apply repeats the lookup before its first write. There is no
  fallback to another node.
- **The plan records the import account, and the apply compares it.** The manifest's `lineage`
  field holds the import account, the source, the owner and `protect`, and the plan checksum
  covers it. An apply reads its own import account again and refuses, before any write, a plan
  made with another account (`ImportAccountMismatchError`, which names both accounts) and a plan
  that records no account (`PlanLineageMissingError`).
- **There is no default owner, and an existing owner is never changed.** When the package names
  an owner, it goes on every value of a node the run creates, and on each relationship edge an
  update adds. An update sends no owner on an attribute or on an edge the node already has, so
  Infrahub keeps the owner it holds, including an empty one.
- **To find the edges an update adds, the apply reads the node's current peers.** Infrahub
  replaces the owner of an existing edge whenever an owner is sent, and has no option to set an
  owner on new edges only. So, when an owner is named, the apply makes one read of the node
  before each update that writes relationships, and puts the owner only on the peers the node
  does not have yet.
- **Protection is off by default.** With `protect: true` the import marks every value it creates
  or changes as protected. Without it, the import sends no protection flag, so a value that is
  already protected stays protected.
- **An edge that Infrahub allocates from a resource pool carries none of the three properties**,
  so the pool stays its source.

## Consequences

- The team creates one Infrahub account for each import and gives its token only to that import.
  With an administrator token, the administrator becomes the source of every value.
- A plan saved before this decision cannot be applied. The team creates a new plan.
- A plan made with one token cannot be applied with a token of another account. The team plans
  again with the token the apply uses.
- When an owner is named, an apply makes one more read for each update that writes relationships.
  When no owner is named, it makes none.
- If a person adds the same edge between that read and the write, the edge gets the named owner.
- A sync does not change the owner of data that already exists in Infrahub. The team sets it in
  Infrahub itself.
- The import does not rewrite the properties of values written before this decision. A value keeps
  its owner and its protection flag; it gets the new source the next time the import writes it.
- An update sends every source attribute of the record, not only the changed ones
  (`docs/docs/development/knowledge/plan-artifact.md`, "The last-change file"), so an unchanged
  value also gets the import's source.
- The import account needs `view` on the kind of a named `source` or `owner`, and on the peer
  kinds the apply reads. `docs/docs/adapters/infrahub.mdx` lists the permissions.

## Alternatives considered

- **Keep the lookup of an account named after the source system.** Rejected by the
  specification (FR-005). That lookup also needs `view` on `CoreAccount`, which the import account
  otherwise does not need (research R-7).
- **Name the import account in the package.** Rejected: the token already identifies it, and a
  second setting could name a different account from the one that writes.
- **Send the owner on every edge of an update.** Rejected: Infrahub then replaces the owner of
  edges the node already has.
- **Record the added peers in the plan.** Rejected: a new field on a planned operation needs a new
  plan format version, and the destination can change between the plan and the apply.
- **Take the added peers from the last-change file of the plan.** Rejected: that file is display
  data, and an apply must not depend on data the reviewer does not approve as writes.
