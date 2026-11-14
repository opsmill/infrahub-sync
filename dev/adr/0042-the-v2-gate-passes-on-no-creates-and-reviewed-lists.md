# 42. The v2 gate passes on no creates and no updates, and on reviewed lists for everything else

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/012-v2-configuration-conversion/spec.md` (FR-022 to FR-026),
`dev/specs/archive/012-v2-configuration-conversion/opsmill-implement-report.md` (sections 8, 10,
11 and 12)

## Context

A team that moves from v2 to V3 must be able to trust that its first V3 plan does not rewrite
the data v2 wrote. The release needs a check that proves this on real data and fails when it
stops being true.

NetBox identifies racks, VLANs and devices by more fields than Infrahub does. On the pinned
NetBox `demo` dataset, 12 racks named `Comms closet` (one at each of 12 sites) map to one
Infrahub rack, 24 VLANs (Admin, Data, Voice and Wireless, each in six VLAN groups) map to four
Infrahub VLANs, and 3 devices named `PP:MDF` map to one Infrahub device. v2 wrote them all, and
each later write replaced the earlier one without a report. V3 leaves such records out of the
plan and reports a problem for each group
([ADR 0020](0020-records-with-problems-are-left-out-of-a-plan.md)). The Infrahub nodes v2 wrote
for the devices in the merged racks match no source record, so the plan records deletes of them.
This is a difference between the two schemas, not a defect.

On 2026-11-13 the latest v2 release on PyPI was `2.0.1`. The `2.0.2` tag existed, but
`infrahub-sync==2.0.2` was not on PyPI.

## Decision

The nightly end-to-end workflow runs the suite `v2-gate` of `.github/scripts/nightly_e2e.py`
on the development stack:

1. It loads the pinned `demo` dataset into a local NetBox, starts the development stack, loads
   the schema snapshot and creates the `v2-gate` branch.
2. It installs `infrahub-sync==2.0.1` from PyPI in its own environment and runs v2 `generate`
   and `sync` with the NetBox example of the `2.0.2` tag
   (`tests/data/v2_examples/netbox_to_infrahub/config.yml`).
3. It converts the same `config.yml` with `configs convert --live-schema --yes` against the host
   addresses, then sets the package's addresses to those the containerized Sync worker reaches.
4. It registers the package and runs `plan` on the same branch, with no NetBox change between
   the v2 sync and the plan.

The gate passes only when all of these hold:

- The plan holds 0 creates and 0 updates.
- Every record left out because of a problem, and every problem, is on
  `tests/data/v2_gate/expected_left_out.yml`.
- Every record left out because it depends on another record depends on an accepted record,
  directly or through other dependent records.
- Every delete is on `tests/data/v2_gate/expected_deletes.yml`, or is of a kind that has an
  accepted record left out.
- The plan detail lists as many operations as the plan summary's total, and the plan computed
  deletes.

A failure of the gate fails the nightly job.

The gate pins v2 `2.0.1`, by the product owner's decision of 2026-11-13. There is no plan to
move to `2.0.2`. It runs the NetBox example of the `2.0.2` tag, because the `2.0.1` example maps
kinds that the loaded schema does not have.

The reviewed lists are maintained as follows:

- An entry is added only in a pull request where a reviewer accepts its reason.
- An entry of `expected_left_out.yml` gives its list (`left_out` or `problem`), its type, its
  kind, its identity and its reason. An entry of `expected_deletes.yml` gives its kind, its
  identity and its reason.
- The lists hold only root records: records that the destination schema cannot represent, and
  the delete that the conversion causes. Records that depend on a listed record, and deletes of
  the kinds of the listed records, are accepted without entries.
- The lists were written from the first complete gate run of 2026-11-13: 50 entries for the 39
  records left out because of a problem and the 11 problems, and one delete, of
  `IpamNamespace name=default`.
- The lists must be reviewed when the pinned `demo` dataset, the NetBox example, or the way a
  plan reports records left out and problems changes.

## Consequences

- Every create or update in the first V3 plan, and every record left out, problem or delete
  that the lists do not account for, is a defect in V3 or in the conversion, and it fails the
  nightly job.
- The first complete run recorded 0 creates, 0 updates, 867 records left out (39 because of a
  problem, 828 because they depend on one), 11 problems and 829 deletes (828 of accepted kinds,
  and the `default` namespace). None of the deletes is executed
  ([ADR 0004](0004-deletes-are-recorded-but-never-executed.md)).
- The gate depends on PyPI keeping `infrahub-sync==2.0.1`, and on the local NetBox and the
  development stack. It runs nightly, not in the default test tier.
- The specification requires that a failure of the gate blocks the testing release (FR-026).
  The gate fails the nightly job. No workflow in this repository reads the result of the nightly
  job, and how a failure blocks the release is not known.
- The migration page tells a team to read its own first V3 plan before it applies it, because
  its data can differ from the `demo` dataset.

## Alternatives considered

- **Zero records left out and zero problems, as first specified.** Rejected: the racks, VLANs
  and devices above cannot be represented in the destination schema, so the gate could never
  pass, although no defect exists.
- **Listing every record left out and every delete.** Rejected by the product owner on
  2026-11-13: The 828 dependent records and 828 deletes follow from the 39 listed records, so
  the gate accepts them through the listed records.
- **Installing v2 from the `2.0.2` Git tag.** Rejected: v1 and v2 were distributed only on
  PyPI, so the gate installs v2 as a customer did.
- **A pytest integration test.** Rejected: the gate needs the same NetBox and stack as the
  `from-netbox` check, which is already a nightly suite.
