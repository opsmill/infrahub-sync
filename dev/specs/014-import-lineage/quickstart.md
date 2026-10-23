# Quickstart: Validate the lineage of an import

**Feature**: [spec.md](./spec.md) | **Contracts**: [contracts/](./contracts/)

## Prerequisites

- Full development profile: `uv sync --extra dev --extra prefect --extra service`.
- The development stack with the pinned Infrahub 1.10.6 (`development/docker-compose.infrahub.yml`)
  and the Sync service, as in `docs/docs/development-stack.mdx`.
- In Infrahub:
  - an account `netbox` with exactly the permission set recorded in research.md R-7, which gives no
    permission to read accounts, and an API token for it;
  - a group `network-team` (`CoreAccountGroup`) that `netbox` can view;
  - a second account that a person uses to change values by hand.
- A configuration package from `examples/` whose destination is Infrahub, with the `netbox` token
  given to the worker through the environment.

## Offline checks

```bash
uv run invoke tests.tests-unit
```

Covers: registration refusals ([contracts/infrahub-destination-settings.md](./contracts/infrahub-destination-settings.md)),
the payload rules for source, owner and protection ([data-model.md](./data-model.md), "State
changes on written values"), the error classification ([contracts/run-errors.md](./contracts/run-errors.md)),
and the plan files and checksum ([contracts/plan-last-changes.md](./contracts/plan-last-changes.md)).

## Scenario 1: an account used only by this import (User Story 1, SC-001, SC-002)

1. Register the package with no `source`, `owner` or `protect`.
2. Run `infrahub-sync sync` into a new branch.
3. Expected: the run completes; every attribute and edge it created has source `netbox`, no owner
   and no protection; every node it created has `created_by` `netbox`.
4. Run `diff`, then apply the plan with the token of another account. Expected: apply refuses
   before any write, and the cause names `netbox` and the other account (FR-012).

## Scenario 2: owner and a missing owner (User Story 2, SC-003, SC-004)

1. Add `owner: {kind: CoreAccountGroup, name: network-team}` and run `infrahub-sync diff`.
2. Expected: the plan's summary says the owner setting applies only to values the run creates.
3. Run `sync`. Expected: created values have owner `network-team`.
4. Change a source value and run `sync` again. Expected: owners of the updated values are unchanged;
   a relationship edge added to an existing node has owner `network-team`.
5. Rename the group in Infrahub and run `diff`. Expected: the run stops before reading the source
   system; the cause names `owner`, `CoreAccountGroup` and `network-team`.
6. Restore the group, run `diff`, rename the group again, then apply the plan. Expected: apply
   stops before any write, with the same cause (FR-011).
7. Register `owner: network-team`. Expected: registration is refused.

## Scenario 3: who last changed a value (User Story 3, SC-005)

1. As the second account, change the description of a device the import wrote.
2. Run `diff`, then `infrahub-sync runs plan <run id>`.
3. Expected: the proposed update to `description` shows the second account and the time of the
   change. A field with no recorded change shows "not recorded". A proposed create shows no
   last-change record.

## Scenario 4: protection (User Story 4)

1. Set `protect: true`, no owner; run `sync`. Expected: written attributes are protected.
2. Set `protect: false`; change a source value; run `sync`. Expected: the import sends no
   protection flag, and the attributes protected in step 1 stay protected.

## Scenario 5: missing permission (FR-008, SC-006)

1. Remove the `netbox` account's permission to view `CoreAccountGroup`, keep the owner setting, and
   run `diff`.
2. Expected: the run records a missing permission and quotes the permission Infrahub names; it does
   not report a rejected token.

## Measurement (research R-2)

Run the NetBox benchmark (`docs/docs/development/guides/netbox-benchmark-runner.md`) with and
without metadata reads, on the default branch and on another branch, and record the destination
read time for about 10,000 nodes in the plan's notes. The result is information only.

The measurement was made on 2026-10-22 with a direct SDK read, not with the runner; the result is
in research.md R-2.

## Quickstart results, for the pull request description

Run on 2026-10-23 from 00:00 to 00:09 UTC (task T057), with the code of commit `99b579eb`.

### What ran

- The development stack, `development/docker-compose.dev.yml`, as a separate Compose project
  with its own image tag and host ports: Postgres, MinIO, the Prefect server, the Sync API and one
  Sync worker. The worker joined the network of a separate Infrahub 1.10.6 Compose project
  (`development/docker-compose.infrahub.yml`, anonymous access off).
- Every step below ran through the CLI (`configs register`, `configs version`, `diff`, `sync`,
  `apply`, `runs plan --detail`, `runs results`), the Sync API and the worker.
- Differences from the prerequisites:
  - The source system was not NetBox. No NetBox of this run's own was available, and the
    scenarios change source values. The source was the Infrahub adapter reading a branch
    `qs-source` of the same Infrahub with the admin token. The destination kinds were a
    throwaway `TestQsTag` and `TestQsDevice` (`name`, `serial`, `description`, and a
    cardinality-many `tags`).
  - The import account was named `netbox` and held the research R-7 permission set for a branch
    other than the default branch, plus `view` on `CoreAccountGroup`. The second account,
    `operator`, held every permission.
  - Each scenario wrote to a destination branch created for it. No step wrote to `main`.
- Values were read back with an admin GraphQL query: `source`, `owner` and `is_protected` of each
  attribute and each `tags` edge, and `node_metadata.created_by` of each device.

### Scenario 1: an account used only by this import

- Passed. `sync` completed. Every attribute and edge the run created had source `netbox` and no
  owner; attributes were not protected, and `tags` edges had `is_protected` null. The device had
  `created_by` `netbox`.
- Passed. After a `diff` made with the `netbox` token, the worker was started with the
  `operator` token and `apply` was refused before any write: the run ended `apply-failed`, the
  branch held no device, and the worker log recorded "The saved plan … was made with the import
  account 'netbox' (id …), and this apply runs with the import account 'operator' (id …). Nothing
  was written to the destination."
- The CLI printed only `apply failed: ImportAccountMismatchError`; the names of the two accounts
  were in the worker log only.

### Scenario 2: owner and a missing owner

- Steps 1 to 4 passed. The `diff` summary printed "The owner setting applies only to values this
  run creates." Created values had owner `network-team`. On a branch where the device was first
  created without an owner, a `sync` with the owner setting changed the serial and added a
  `tags` edge: the updated values kept no owner, and the added edge had owner `network-team`.
- Step 5 passed on a branch created after the group was renamed: the plan stopped before the
  source system was read, with "The setting `owner` names `CoreAccountGroup` `network-team`,
  which does not exist."
- Step 5 failed on a branch created before the rename: the plan completed. On Infrahub 1.10.6, a
  filter by name on such a branch still matches the group by its earlier name. A direct read
  showed the same: on `main` the filter by the earlier name returned nothing; on a branch created
  before the rename it returned the renamed group.
- Step 6 failed for the same reason: the branch was created and planned while the group had its
  name, then the group was renamed, and `apply` wrote every value, with the renamed group as
  owner. The quickstart's step 6 always creates the branch before the rename, so the check that
  apply makes before its first write (FR-011) does not stop it on a branch other than the default
  branch. Not run on the default branch, because the `netbox` account holds no permission there.
- Step 7 passed: `configs register` and `configs version` with `owner: network-team` were refused
  with HTTP 422, code `configs-validation`, and the CLI exited with status 1.

### Scenario 3: who last changed a value

- Passed. After `operator` changed the description of a device the import wrote, `runs plan
  --detail` showed for the proposed update `description (attribute): updated_by=Operator,
  updated_at=2026-10-23T00:06:49…` and `serial (attribute): updated_by=Netbox, …`.
- Passed. An update that adds the first edge of an empty `tags` relationship showed
  `tags (relationship): updated_by=not recorded, updated_at=not recorded`. A proposed create
  showed no last-change line.
- On 1.10.6 every attribute has a record, so "not recorded" shows only for a relationship with no
  edge (research R-2).

### Scenario 4: protection

- Passed. With `protect: true` and no owner, every attribute and edge the run wrote was
  protected. A `description` the source holds as null was not written and was not protected.
- Passed. With `protect: false`, a changed serial and an edge added to another device: the
  values protected before stayed protected, and the added edge had `is_protected` null.

### Scenario 5: missing permission

- Partly passed. With `view` on `CoreAccountGroup` removed and the owner setting kept, the plan
  failed (`plan-failed`) and was not reported as a rejected token. The worker log quoted the
  permission Infrahub names: "You do not have one of the following permissions:
  object:Core:AccountGroup:view:allow_other".
- Not met through the service: the run's recorded results (`runs results`) held only
  `error_type: AuthenticationError` and `cause_type: HTTPStatusError`, not "missing permission"
  and not the permission. The classification of contracts/run-errors.md is applied to apply
  failures (`infrahub_sync/potenda/__init__.py:225`) and to the destination schema read
  (`infrahub_sync/configuration/capabilities.py:366-408`), not to a plan failure of this kind.

### Covered only by the opt-in integration tests

`tests/integration/test_infrahub_lineage_integration.py`, 10 tests, passed on 2026-10-22 at
23:37 UTC against the same Infrahub 1.10.6. They drive the destination adapter and
`Potenda.apply_plan` directly, not the CLI, and cover:

- a missing permission on a written kind, and its classification as a missing permission;
- values written before this release keep their owner and protection and get the import source;
- the missing-owner stop at plan and at apply, with a group created after the branch, which does
  not reach the case found in scenario 2.
