# 43. A plan leaves out the deletes of nodes that Infrahub creates itself

**Status**: Accepted
**Date**: 2026-11-16, amended 2026-11-17 (see [Amendment, 2026-11-17](#amendment-2026-11-17-the-mark-also-needs-a-listed-kind))
**Source**: `dev/specs/019-second-plan-real-changes/spec.md` (FR-001 to FR-004, FR-024, SC-003),
`dev/specs/019-second-plan-real-changes/research.md` (R2, R3)

## Context

Infrahub creates some nodes itself when it starts. A mapping of their kind loads them from the
destination, and when no source record matches them the plan records a delete. The first such
node seen was the `IpamNamespace` named `default`: a converted v2 NetBox configuration maps the
namespace kind with no source endpoint
([ADR 0041](0041-an-unmapped-ip-namespace-converts-to-the-default-namespace.md)), so every plan
of that configuration recorded the delete of `default`. Apply never executes a delete
([ADR 0004](0004-deletes-are-recorded-but-never-executed.md)), but the delete kept every second
plan from being empty.

Three places accepted that delete by the name of the node:

- the benchmark runner, which counted it in a `builtin_deletes` record field and excluded it from
  the change comparison;
- the list of deletes that the v2 gate accepts, `tests/data/v2_gate/expected_deletes.yml`
  ([ADR 0042](0042-the-v2-gate-passes-on-no-creates-and-reviewed-lists.md));
- the NetBox example README and the benchmark runner guide, which described it as a limitation.

A rule by name holds only for the configurations and kinds that someone has listed. The rule
must hold for every configuration and every source adapter, without a list kept in the package.

A probe of a newly initialised Infrahub 1.11.9 on 2026-11-16, with the core schema only, read
every node of the kinds outside `Core*` and `Internal*` (`BuiltinTag` and `IpamNamespace`). The
output is `tests/data/infrahub_builtin_nodes/infrahub-1.11.9.json`. It found:

- one node that Infrahub creates itself: the `IpamNamespace` named `default`. Its node metadata
  names the creator `__system__` (display label `[Infrahub System]`), and its `default`
  attribute is `true`;
- a `BuiltinTag` that the API token created on the same branch, whose creator is `Admin`, the
  account of the token;
- in a separate check, a `BuiltinTag` created on a branch and merged into `main` keeps the
  creator `Admin` on `main`. A node that an import writes therefore does not take the mark when
  its branch is merged.

## Decision

A plan records no delete for a destination node whose node metadata names the creator
`__system__` and that no source record matches. The amendment of 2026-11-17 below also requires
the node's kind to be in a list of kinds that Infrahub creates nodes of.

- The Infrahub destination adapter collects the ids of those nodes while it reads node metadata,
  with no extra request. It exposes them through the optional `BuiltinNodeReadingDestination`
  protocol in `infrahub_sync/plan/write_surface.py`.
- `derive_deletes` in `infrahub_sync/plan/derive.py` skips such a node only when no source
  record matches it. It adds no entry to the plan report and logs one `info` line with the
  count.
- A node of that set that a source record matches is compared like any other node of its kind.
- The three places that accepted the `default` namespace delete by name no longer do:
    - the benchmark runner records every planned delete in `skipped_deletes`, and the
      `builtin_deletes` field is removed;
    - the list of deletes that the v2 gate accepts is empty, so the gate fails on a delete of the
      `default` namespace. The file and its loader stay for future reviewed entries;
    - the README and the guide no longer describe the delete.

## Consequences

- A plan of a converted v2 NetBox configuration no longer holds the delete of the `default`
  namespace, and a second plan with no source change can be empty.
- No file of the repository accepts or excludes a planned delete by the name of the node.
- The rule depends on node metadata. The adapter reads it from Infrahub 1.7.0. Against an older
  Infrahub the set is empty, and the delete of `default` comes back.
- A node that an operator creates by hand in Infrahub, and that no source record matches, still
  gets a delete, because its creator is the operator's account.
- Not known: whether Infrahub also names `__system__` as the creator of nodes it writes during a
  migration, a generator run or a resource pool allocation. The probe covered only a new
  instance. If it does, a plan leaves out the deletes of those nodes too.
- The statements of ADR 0041 and ADR 0042 about the delete of the `default` namespace describe
  the behavior before this decision.

## Alternatives considered

- **The read-only `default` attribute of `BuiltinIPNamespace`.** Rejected: it covers IP
  namespaces only. The node metadata covers every kind, and on the probed instance both marks
  agree on the one built-in node.
- **A list of built-in nodes by kind and name.** Rejected: the rule must not depend on a name or
  on a list (FR-002). The amendment below adds a list of kinds, not of names, beside the
  creator.
- **A flag on a mapping entry that turns off its deletes.** Rejected: it depends on the
  configuration, and it would also hide deletes of nodes that an import wrote.
- **Leaving out the deletes of nodes that no import wrote.** Rejected: it would also leave out a
  node that an operator created by hand, which must stay a delete.
- **Filtering the delete after the plan is derived, or recording it as a kept record.**
  Rejected: it changes the plan report.

## Amendment, 2026-11-17: the mark also needs a listed kind

The creator `__system__` alone does not mark a node that Infrahub creates itself. Infrahub 1.11.9
also records `__system__` as the creator of IPAM nodes that a sync imported: an import run from
NetBox found 274 `IpamPrefix`, `IpamIPAddress` and `IpamAggregate` nodes with that creator,
although their attributes have the source `netbox`
(`dev/specs/archive/009-import-lineage/opsmill-implement-report.md`). The sources do not say why.
Under the rule above, a plan would leave out the delete of such a node when NetBox deletes it,
with no entry in the plan report.

The rule is narrowed. A plan records no delete for a destination node that no source record
matches when both conditions hold:

- its node metadata names the creator `__system__`;
- its kind is in `INFRAHUB_BUILTIN_NODE_KINDS` in `infrahub_sync/adapters/infrahub.py`.

The list holds `IpamNamespace` only, the one kind of which the probe of a new branch found a
node. A kind is added only when a probe of a new branch finds a node of that kind.

Consequences of the amendment:

- An imported `IpamPrefix`, `IpamIPAddress` or `IpamAggregate` node that the source deletes gets
  its delete again, whatever its creator.
- The rule now depends on a list of kinds kept in the package. FR-002 of the spec first forbade
  any such list; it is amended to allow a list of kinds and to forbid only names. The creator
  still decides which node of a listed kind is built in.
  A node that Infrahub creates itself, of a kind not in the list, gets a delete again until the
  list names its kind.
- An `IpamNamespace` that a sync imported with the creator `__system__`, if Infrahub records one,
  is still left out. The import run above does not list namespaces among those nodes.
