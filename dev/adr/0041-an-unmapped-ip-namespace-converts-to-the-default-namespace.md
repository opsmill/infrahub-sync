# 41. An IP namespace that a uniqueness constraint needs and the file does not map converts to the `default` namespace

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/006-v2-configuration-conversion/spec.md` (FR-002, FR-031),
`dev/specs/archive/006-v2-configuration-conversion/opsmill-implement-report.md` (sections 8 and 9)

## Context

v2 NetBox configurations did not map the IP namespace. They identified prefixes by
`(prefix, vrf)` and addresses by `(address, vrf)`, so Infrahub put every prefix and address v2
wrote into its built-in `default` namespace. Infrahub's uniqueness constraint for these kinds
includes the IP namespace, and V3 does not plan a kind unless the configuration maps every field
of that constraint. The first V3 plan of a converted v2 NetBox configuration failed with
`UnwritableConvergenceIdentityError`.

The first decision was that the team edits the converted package by hand. The two edits tried
first did not plan (see Alternatives considered), and no working edit was known. The edit that
works needs the fields of the uniqueness constraint, the concrete namespace kind and its name
attribute, which only the destination schema gives.

Infrahub declares `ip_namespace` optional and fills it when it writes the node. A rule based on
the mandatory flag would not find it.

## Decision

`configs convert --live-schema` reads the destination schema without a token: the `token`
setting of the destination is removed from a copy of the package before the read, so the
command still reads no credential. For each mapped kind, and for each relationship of
cardinality one that is not read-only, that a uniqueness constraint names, that points to an IP
namespace and that the file does not map, the command:

- writes the relationship as `static: default` with a `reference` to the one concrete namespace
  kind, for example `{name: ip_namespace, static: default, reference: IpamNamespace}`;
- adds a mapping of that namespace kind with no source endpoint, identified by its name
  attribute, unless the file already maps the kind;
- makes the identifiers of the kind the fields of the uniqueness constraint, for example
  `[prefix, ip_namespace]`, when the file maps all of them. Otherwise it leaves the identifiers
  as they are and adds a note that names the fields still unmapped.

The command does not fill the relationship, and adds a note, when the namespace generic has more
than one concrete kind or the namespace kind has no human-friendly ID made of one attribute.
After a fill, it runs the connection-free registration checks again and refuses when the
filled package fails them. When the schema read fails, the report says so and the package is
written unchanged.

No change to the plan engine is needed. The planner already records a peer that exists only in
Infrahub by its name (`infrahub_sync/plan/destination_only_peer.py`), when the peer kind is mapped
and identified by the one attribute that is its human-friendly ID.

## Consequences

- The first V3 plan of a converted v2 NetBox configuration plans prefixes and addresses in the
  `default` namespace, where v2 wrote them.
- The plan records one delete of `IpamNamespace name=default`, because the namespace mapping
  loads nothing from the source. Apply never executes a delete
  ([ADR 0004](0004-deletes-are-recorded-but-never-executed.md)).
- `--live-schema` needs the destination Infrahub to allow a schema read without a token. Without
  the flag, or when the read fails, the team makes the same edit by hand.
- The rule is limited to IP namespaces. Another peer that exists only in the destination and
  that a uniqueness constraint names is not filled.

## Alternatives considered

- **A bare `static: default` with no `reference`.** Rejected after a trial: the Infrahub side
  dropped the value, because the namespace kind was not mapped, and the plan failed.
- **Keeping `vrf` in the identifiers.** Rejected after a trial: a NetBox address with no VRF is
  refused with `identifier field 'vrf' cannot be None`.
- **One namespace per NetBox VRF, as the V3 NetBox example does.** Rejected: it puts prefixes and
  addresses in other namespaces than v2 did, so the first plan would not match the data v2
  wrote.
- **Asking the team to edit the package by hand.** Decided first, then replaced: the working
  edit depends on the destination schema, and it is the same for every v2 NetBox configuration,
  so the command makes it. A team without `--live-schema` can still make it by hand.
- **Using the `optional` flag of the relationship.** Rejected: Infrahub declares `ip_namespace`
  optional.
