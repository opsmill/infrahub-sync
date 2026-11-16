# 43. A plan leaves out the deletes of nodes that Infrahub creates itself

**Status**: Accepted
**Date**: 2026-11-16
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
`__system__` and that no source record matches.

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
  on a list (FR-002).
- **A flag on a mapping entry that turns off its deletes.** Rejected: it depends on the
  configuration, and it would also hide deletes of nodes that an import wrote.
- **Leaving out the deletes of nodes that no import wrote.** Rejected: it would also leave out a
  node that an operator created by hand, which must stay a delete.
- **Filtering the delete after the plan is derived, or recording it as a kept record.**
  Rejected: it changes the plan report.
