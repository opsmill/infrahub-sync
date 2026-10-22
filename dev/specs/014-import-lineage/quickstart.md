# Quickstart: Validate the lineage of an import

**Feature**: [spec.md](./spec.md) | **Contracts**: [contracts/](./contracts/)

## Prerequisites

- Full development profile: `uv sync --extra dev --extra prefect --extra service`.
- The development stack with the pinned Infrahub 1.10.6 (`development/docker-compose.infrahub.yml`)
  and the Sync service, as in `docs/docs/development-stack.mdx`.
- In Infrahub:
  - an account `netbox` with the documented import permissions and no permission to read accounts,
    and an API token for it;
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

## Scenario 1: dedicated account (User Story 1, SC-001, SC-002)

1. Register the package with no `source`, `owner` or `protect`.
2. Run `infrahub-sync sync` into a new branch.
3. Expected: the run completes; every attribute and edge it created has source `netbox`, no owner
   and no protection; every node it created has `created_by` `netbox`.

## Scenario 2: owner and a missing owner (User Story 2, SC-003, SC-004)

1. Add `owner: {kind: CoreAccountGroup, name: network-team}` and run `infrahub-sync diff`.
2. Expected: the plan's summary says the owner setting applies only to values the run creates.
3. Run `sync`. Expected: created values have owner `network-team`.
4. Change a source value and run `sync` again. Expected: owners of the updated values are unchanged;
   a relationship edge added to an existing node has owner `network-team`.
5. Rename the group in Infrahub and run `diff`. Expected: the run stops before reading the source
   system; the cause names `owner`, `CoreAccountGroup` and `network-team`.
6. Register `owner: network-team`. Expected: registration is refused.

## Scenario 3: who last changed a value (User Story 3, SC-005)

1. As the second account, change the description of a device the import wrote.
2. Run `diff`, then `infrahub-sync runs plan <run id>`.
3. Expected: the proposed update to `description` shows the second account and the time of the
   change. A field with no recorded change shows "not recorded".

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
read time for about 10,000 nodes in the plan's notes.
