# Feature Specification: Lineage of the data an import writes

**Feature Branch**: `014-import-lineage`

**Created**: 2026-10-22

**Status**: Draft

**Input**: User description: idea brief D, "lineage of the data an import writes". The brief's
decisions D-1 to D-8 are final product-owner decisions and are carried over below unchanged.

## Summary

Infrahub records a source, an owner, a protection flag and the account that last changed the
value on every attribute and relationship
([Data lineage and metadata](https://github.com/opsmill/infrahub/blob/stable/docs/docs/tutorials/getting-started/lineage-information.mdx)).
It also records on every node who created it, when, and who last changed it
([Object-level metadata](https://github.com/opsmill/infrahub/blob/stable/docs/docs/objects/metadata.mdx)).

Today an import into Infrahub records an account named after the source system as the source and
the owner of each value. When that account, or a group the package names, does not exist, the
import writes the value with an empty source or owner and only logs a warning. It also marks every
value it writes as protected (`infrahub_sync/adapters/infrahub.py:713-741` and `:1336`).

The team that runs Infrahub and reviews what an import writes needs to know, for each value, where
it came from, who is responsible for it, and who last changed it and when. This feature makes the
import record the account behind its token as the source, sets an owner only when the package
names one, stops the run when a named source or owner does not exist, makes protection an opt-in,
and shows in the plan who last changed each value the run proposes to change.

Not decided yet: the oldest Infrahub version an import supports. Planning decides it.

## Clarifications

### Session 2026-10-22

- Q: What does the plan show when Infrahub holds no `updated_by` or `updated_at` for a value? → A:
  The plan shows "not recorded" for the missing field and continues, on any Infrahub version.
- Q: What lineage does the import write on an edge allocated from a resource pool? → A: The import
  sends no source, owner or protection flag on that edge, and the pool stays its source.
- Q: Which values does the owner setting cover as "values the run creates"? → A: The attributes of
  nodes the run creates, and every relationship edge the run adds, including edges on nodes that
  already exist.
- Q: Which kinds can a package name as source or owner? → A: `CoreAccount`, `CoreAccountGroup`,
  `CoreRepository` and `CoreReadOnlyRepository`.

### Session 2026-10-22 (review of the plan and tasks)

- Q: Which proposed changes show who last changed the value? → A: Proposed updates to attributes
  and relationships of nodes that already exist. A create shows no last-change record, because
  there is no earlier value.
- Q: What does apply do when a named source or owner no longer exists? → A: Apply stops before any
  write, with the same cause as the plan.
- Q: What does apply do when the import account differs from the account the plan recorded? → A:
  Apply refuses before any write, with a cause that names both accounts.
- Q: What does apply do with a saved plan that records no import account, because it was made
  before this feature? → A: Apply refuses it before any write and asks for a new plan. V3 is not
  released, and plans saved before the upgrade are already refused for other reasons.
- Q: Which term names the account behind the token? → A: "Import account", as defined under Key
  Entities.

## Decisions carried over from the brief

- **D-1. Scope.** This specification covers six things:
  - the token's account as the source;
  - the owner set only when a value is created;
  - a stop when a named node is missing;
  - protection off by default;
  - an HTTP 403 reported as a missing permission;
  - no need for permission to read accounts.

  The permission check before the plan, and its findings in `configs validate`, belong to the
  `configs validate` brief (INFP-651, INFP-652).
- **D-2.** For each change it proposes, the plan shows the attribute's or relationship's
  `updated_by` and `updated_at`. The plan only displays them; it blocks nothing. Uses of
  `created_by`, and keeping values a person changed, belong to the 3.0 design.
- **D-3.** There is no default owner. An owner is set only when the package names one. The
  documentation recommends naming the team responsible for the data.
- **D-4.** `protect` is an opt-in for the whole configuration and is off by default.
  - On: the import marks the attributes and relationship edges it creates or changes as protected.
  - Off: the import sends no protection flag.
  - In 3.0, protection can be the import's own signal for deletes until the Advanced RBAC work
    ships.
- **D-5.** `protect` does not require an owner. Infrahub accepts the flag on its own
  (`../infrahub/backend/infrahub/core/attribute.py:729-731`). Without an owner, the flag has no
  effect in Infrahub (`../infrahub/frontend/app/src/shared/components/form/utils/isFieldDisabled.ts:36`).
- **D-6.** Relationship edges the import adds follow the attribute rules. Resource-pool allocations
  are the exception, and the documentation says so.
- **D-7.** A source or owner is named as `{kind, name}`, and a bare name is refused when the package
  is registered.
  - Allowed kinds: `CoreAccount`, `CoreAccountGroup` and the repository kinds (`CoreRepository`
    and `CoreReadOnlyRepository`, see Clarifications).
  - At the start of the plan, before the source system is read, the import looks up each named
    node.
- **D-8.** The import never changes an existing owner, including an empty one. The plan's summary
  says that the owner setting applies only to values the run creates.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Import with an account used only by this import (Priority: P1)

The operator creates an Infrahub account for one import, for example `netbox`, gives it the
documented permissions and no permission to read accounts, and gives its token to the worker. After
a `sync` into a branch, the operator views any value the import created and finds `netbox` as its
source, and finds `netbox` as the account that created each new node.

**Why this priority**: The source is the first answer a reviewer needs ("where did this value come
from?"). Today it names the source system rather than the account that wrote the value, and it is
empty when no account has that name.

**Independent Test**: Create the `netbox` account with the documented permissions only, run `sync`
into a branch with its token, then read the lineage of every attribute, relationship edge and node
the run created.

**Acceptance Scenarios**:

1. **Given** an account `netbox` that has the documented permissions and no permission to read
   accounts, and its token given to the worker, **When** the operator runs `sync` into a branch,
   **Then** every attribute and relationship edge the import creates has source `netbox`, no owner
   and no protection, and every node it creates has `created_by` set to `netbox`.
2. **Given** the same account, **When** the import runs, **Then** it completes without asking
   Infrahub for any account by the name of the source system.
3. **Given** the import account is refused a write because it lacks a permission (HTTP 403),
   **When** the run fails, **Then** the run records a missing permission and quotes the permission
   Infrahub names, and does not report a rejected token.
4. **Given** a plan made with the `netbox` token, **When** the operator applies it with the token
   of another account, **Then** apply refuses before any write, and the cause names `netbox` and
   the other account.

---

### User Story 2 - Make a team the owner (Priority: P1)

The operator names the team responsible for the data as the owner in the package. Values the import
creates carry that team as their owner. When the named team does not exist, the run stops before it
reads the source system and says which setting names the missing node.

**Why this priority**: Without an owner the reviewer cannot tell who is responsible for a value.
Today a missing group leaves the owner empty without stopping the run.

**Independent Test**: Register a package that names an existing `CoreAccountGroup` as owner and run
an import that creates a device; then rename the group so it no longer exists and plan again.

**Acceptance Scenarios**:

1. **Given** a package with `owner: {kind: CoreAccountGroup, name: network-team}`, and that group
   exists, **When** the import creates a device, **Then** the device's values have owner
   `network-team` and a source equal to the import account.
2. **Given** the same package, and the group does not exist, **When** the operator plans the run,
   **Then** the run stops before it reads the source system, and the cause names the setting
   (`owner`), the kind (`CoreAccountGroup`) and the name (`network-team`).
3. **Given** a value the import created earlier with owner `network-team`, **When** a later run
   updates that value, **Then** its owner does not change.
4. **Given** a package that sets `owner: network-team` as a bare name, or names a kind outside the
   allowed list, **When** the operator registers the package, **Then** the registration is refused.
5. **Given** a package that names an owner, **When** the operator views the plan's summary,
   **Then** the summary says that the owner setting applies only to values the run creates.
6. **Given** a package with `owner: {kind: CoreAccountGroup, name: network-team}` and a device that
   already exists, **When** the import adds a relationship edge to that device, **Then** the new
   edge has owner `network-team`, and the owners of the device's existing attributes and edges do
   not change.
7. **Given** a plan made while the group `network-team` existed, and the group was deleted
   afterwards, **When** the operator applies the plan, **Then** apply stops before any write, and
   the cause names the setting, the kind and the name.

---

### User Story 3 - View who changed a value before overwriting it (Priority: P2)

Before approving a run, the operator views the plan's detail and, for each proposed update to a
node that already exists, views which account last changed the value and when. This tells the operator that a person, not an
earlier import, changed the value.

**Why this priority**: It lets the operator spot a manual change the run is about to overwrite. The
plan only displays this information; it does not block the change (D-2).

**Independent Test**: Change a device description as another account after the last import, then
plan the next run and view the plan's detail.

**Acceptance Scenarios**:

1. **Given** a person changed a device description after the last import, **When** the operator
   views the plan's detail, **Then** the proposed update shows `updated_by` (the person's account)
   and `updated_at`.
2. **Given** a proposed update to a relationship of an existing node, **When** the operator views the plan's detail,
   **Then** the change shows the relationship's `updated_by` and `updated_at`.
3. **Given** a value whose `updated_by` Infrahub did not record, **When** the operator views the
   plan's detail, **Then** the proposed change shows "not recorded" for that field, and the plan
   continues.

---

### User Story 4 - Opt in to protection (Priority: P2)

The operator decides whether the values an import writes are protected. Protection is off unless the
package turns it on.

**Why this priority**: Today every value is protected, which the operator did not choose. With an
owner, protection stops other accounts from editing the value in Infrahub; without an owner, the
flag has no effect in Infrahub (D-5).

**Independent Test**: Run the same import with `protect` on and with `protect` off, and check the
protection flag of the values written and of a value protected earlier.

**Acceptance Scenarios**:

1. **Given** a package with `protect` on and no owner, **When** the import creates or changes
   attributes, **Then** those attributes are protected.
2. **Given** a package with `protect` off, **When** the import creates or changes attributes,
   **Then** the import sends no protection flag, and values protected earlier stay protected.

---

### Edge Cases

- **Administrator token**: the source is the administrator account. The documentation recommends
  one account for each import.
- **Values written before this release**: their lineage is not rewritten. Their source is updated
  the next time an import writes them. Their owner and protection stay as they are.
- **An update that changes nothing**: the import writes nothing, so the lineage does not change.
- **A person changes a value, and the next import overwrites it**: the plan shows the person and the
  time (D-2), and the source goes back to the import account.
- **Imports that share one token**: `updated_by` cannot tell them apart. The documentation says so.
- **A value with an empty owner**: the import does not set an owner on it, even when the package
  names one (D-8).
- **A relationship edge added to a node that already exists**: the edge is a value the run creates,
  so it gets the named owner. The owners of the node's existing values do not change.
- **Resource-pool allocations**: they do not follow the attribute rules (D-6). On an edge allocated
  from a resource pool, the import sends no source, owner or protection flag, and the pool stays
  the source that Infrahub records
  (`../infrahub/backend/infrahub/core/relationship/model.py:643`). The documentation states the
  exception.
- **No `updated_by` or `updated_at` recorded for a value**: this happens on Infrahub versions before
  1.7.0, and on 1.7.0 or later for a value last changed before the upgrade. The plan shows "not
  recorded" for the missing field and continues.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The import MUST record the import account as the source of every attribute and
  relationship edge it writes, unless the package names a source.
  - Test: import with the token of an account used only by this import, then read the source of the
    written values.
- **FR-002**: The import MUST set an owner only when the package names one, and only on values it
  creates: the attributes of nodes the run creates, and every relationship edge the run adds,
  including edges on nodes that already exist.
  - Test: create values with and without an owner setting; add an edge to an existing node with an
    owner setting present; then update the values with an owner setting present and check that the
    owners do not change.
- **FR-003**: Registration MUST refuse a `source` or `owner` given as a bare name, and a kind
  outside the allowed list: `CoreAccount`, `CoreAccountGroup`, `CoreRepository` and
  `CoreReadOnlyRepository`.
  - Test: register packages using each form.
- **FR-004**: The plan MUST stop before it reads the source system when a named source or owner does
  not exist. The cause MUST name the setting, the kind and the name. The plan MUST NOT fall back to
  another node.
  - Test: name a missing group.
- **FR-005**: The import MUST NOT look up an account by the name of the source system, and MUST NOT
  need permission to read accounts unless the package names a `CoreAccount`.
  - Test: import with an account that cannot read accounts.
- **FR-006**: The import MUST protect values only when `protect` is on. With `protect` off, it MUST
  NOT send the protection flag.
  - Test: check the inputs the import sends to Infrahub, and check that a value protected earlier
    keeps its flag.
- **FR-007**: The plan's detail MUST show `updated_by` and `updated_at` for each proposed update to
  an attribute or a relationship of a node that already exists. A proposed create shows no
  last-change record. When Infrahub holds no value for one of these fields, the plan MUST show
  "not recorded" for it and continue, on any Infrahub version. The plan MUST NOT show "not
  recorded" for a value Infrahub holds.
  - Test: change a value as another account, then plan; plan a change to a value with no recorded
    `updated_by`; plan a create and check it has no last-change record.
- **FR-008**: The run MUST record an HTTP 403 as a missing permission, quoting the permission
  Infrahub names. Only a refused credential MUST be recorded as a token problem.
  - Test: one test for each response code.
- **FR-009**: The plan's summary MUST say that the owner setting applies only to values the run
  creates, whenever the package names an owner.
- **FR-010**: The documentation MUST list:
  - the permissions an import account needs on each branch;
  - the read permission each allowed source or owner kind needs;
  - what `protect` does with an owner and without one;
  - that values written before this release keep their protection;
  - the resource-pool exception: on an allocated edge the import sends no source, owner or
    protection flag, and the pool stays the source;
  - how to set the owner of existing data.
- **FR-011**: Apply MUST stop before any write when a named source or owner does not exist at the
  start of the apply. The cause MUST name the setting, the kind and the name, as in FR-004.
  - Test: plan with an existing group, delete the group, then apply.
- **FR-012**: The plan MUST record its import account. Apply MUST refuse before any write when the
  import account differs from the account the plan recorded, whether or not the package names a
  source. The cause MUST name both accounts. Apply MUST also refuse, before any write, a saved
  plan that records no import account (a plan made before this feature), and the cause MUST ask
  for a new plan.
  - Test: plan with one token, then apply with the token of another account; apply a saved plan
    that has no recorded import account.

### Key Entities

- **Import account** (existing): the Infrahub account behind the token the worker uses for the
  import. It is the default source, and Infrahub records it as `created_by` and `updated_by`. The
  rest of this specification calls it "the import account".
- **Named source or owner** (existing setting, new form): `{kind, name}`, where the kind is
  `CoreAccount`, `CoreAccountGroup`, `CoreRepository` or `CoreReadOnlyRepository`.
- **Attribute and relationship lineage** (existing in Infrahub): source, owner, protection flag,
  `updated_by` and `updated_at`.
- **Node metadata** (existing in Infrahub): `created_at`, `created_by`, `updated_at` and
  `updated_by`. Infrahub sets them; a caller cannot
  ([Object-level metadata](https://github.com/opsmill/infrahub/blob/stable/docs/docs/objects/metadata.mdx)).
- **Protection setting** (new): `protect`, one setting for the whole configuration, off by default.

### Governance Gates Crossed

- **Configuration format**: the new `{kind, name}` form for `source` and `owner`, a bare name
  refused, and the new `protect` setting.
- **API and plan format**: the plan's detail carries `updated_by` and `updated_at`.
- **Permissions**: the documented permissions of the import account, and no permission to read
  accounts.
- **Database or product store schema**: the product store database does not change. The stored
  plan gains one file and two manifest fields, with no new plan format version (see
  [research.md](./research.md), R-4).
- **New dependency**: none. The pinned Infrahub SDK 1.23.2 has `include_metadata`, which arrived in
  1.18.0.
- **CI/CD**: none.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After an import with the token of an account used only by this import, 100% of the
  values it created name that account as source and as `updated_by`.
- **SC-002**: An import completes with only the documented permissions, without permission to read
  accounts.
- **SC-003**: In 100% of runs with a missing named node, the run stops before reading the source
  system, and no value is written with an empty or fallback lineage.
- **SC-004**: An owner set at creation is unchanged after any number of updates.
- **SC-005**: 100% of proposed updates to attributes and relationships of existing nodes in a
  plan's detail show who last changed the value and when, or "not recorded" when Infrahub holds no
  value.
- **SC-006**: No HTTP 403 is reported as a rejected token.
- **SC-007**: In 100% of applies with a missing named node, or with an import account that differs
  from the plan's, no value is written.

## Assumptions

- Infrahub returns the token's own account to that account without any permission on accounts. The
  brief records that this was checked on 2026-10-20.
- Every import is expected to run with its own account.
- The v2 conversion brief translates `source` and `owner` into `{kind: CoreAccountGroup, name}`.
  That brief is being worked on separately.
- Infrahub records `updated_by` and `updated_at` on attributes and relationships from version
  1.7.0, and does not backfill `updated_by`:
  - the GraphQL fields came in with commits 6092620bb6 (2025-11-19, "IFC-1940: Add metadata to
    graphql response"), c2e78ce106 and 134027ca4d; the first tag that contains them is
    `infrahub-v1.7.0b0`;
  - migration `m050_backfill_vertex_metadata`
    (`../infrahub/backend/infrahub/core/migrations/graph/m050_backfill_vertex_metadata.py`, added
    as `m049_backfill_vertex_metadata` in commit 480b930e0d, PR #7928) first ships in
    `infrahub-v1.7.0b0`; it backfills only `created_at` and `updated_at`, on the default and
    global branches;
  - the 1.7.0 release notes
    (`../infrahub/docs/docs/release-notes/infrahub/release-1_7_0.mdx`, lines 45-46) say that
    `created_by` and `updated_by` are set only for changes made after the release.
- The oldest Infrahub version an import supports is not known. Planning decides it.
- `CoreAccount`, `CoreAccountGroup`, `CoreRepository` and `CoreReadOnlyRepository` can be both a
  source and an owner in Infrahub
  (`../infrahub/backend/infrahub/core/schema/definitions/core/account.py:30`,
  `permission.py:172`, `repository.py:37` and `:246`).
- The `protect` setting and the `{kind, name}` form are set once for the whole configuration; a
  setting for each attribute is out of scope.

## Out of Scope

- The permission check before the plan, and `configs validate` findings: they belong to the
  `configs validate` brief (INFP-651, INFP-652).
- An owner or protection set for each attribute.
- Rewriting the lineage of values already written.

## Points Passed to the 3.0 Design

- Delete rules 1 and 3 rely on the owner. The 3.0 design decides between the owner and
  `created_by`.
- Protection as the import's own signal for deletes.
- Keeping a value a person changed, using `updated_by`.
- Taking ownership of existing values, and the rules for which import claims a value when several
  imports write it.
- Owners and sources stored on the node
  ([Advanced RBAC draft](https://app.notion.com/p/opsmill/Advanced-RBAC-ownership-based-permissions-and-source-metadata-draft-3f2228b8302581699421d37be2c6f094),
  sections 6.2 and 10).

## Sources

- [Data lineage and metadata](https://github.com/opsmill/infrahub/blob/stable/docs/docs/tutorials/getting-started/lineage-information.mdx)
- [Object-level metadata](https://github.com/opsmill/infrahub/blob/stable/docs/docs/objects/metadata.mdx)
- [Advanced RBAC draft](https://app.notion.com/p/opsmill/Advanced-RBAC-ownership-based-permissions-and-source-metadata-draft-3f2228b8302581699421d37be2c6f094)
- Current import behavior: `infrahub_sync/adapters/infrahub.py:713-741` (source and owner lookup
  with fallback) and `:1336` (every value created as protected).
- Infrahub accepts the protection flag without an owner: `../infrahub/backend/infrahub/core/attribute.py:729-731`.
- Infrahub ignores the protection flag without an owner when it decides whether a field can be
  edited: `../infrahub/frontend/app/src/shared/components/form/utils/isFieldDisabled.ts:36`.
