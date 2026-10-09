# 19. Prefect is an internal component of Sync

**Status**: Accepted
**Date**: 2026-10-05
**Decision-makers**: Damien (CTO), Benoit Kohler

## Context

Sync runs its flows on a Prefect server backed by PostgreSQL. Infrahub ships both as well: its
task manager is a Prefect server on the `task-manager-db` PostgreSQL server. A site that runs
Sync next to Infrahub therefore ran two Prefect servers and two PostgreSQL servers, and Sync's
Prefect pin (3.8.1) had drifted from Infrahub's (3.8.6 in Infrahub 1.11.3 and 1.11.4). Pull
request #362 proposed running Sync on Infrahub's task manager and keeping Sync's database on
`task-manager-db` (draft ADR 0017, not merged). Pull request #357 moves Sync's own Prefect
server to 3.8.6 and keeps the two deployments apart.

## Decision

Prefect is an internal component of Sync, not an external dependency that Sync shares.

- **Own Prefect server.** Sync's deployment runs its own Prefect server. Sync never registers
  work pools, deployments or flow runs on Infrahub's task manager.
- **Own PostgreSQL server.** Sync's Prefect database and its `infrahub_sync` database live on
  Sync's own PostgreSQL server, not on `task-manager-db`.
- **No dependency on Infrahub's deployment.** Sync's Compose file does not join Infrahub's
  Compose network to reach the task manager or its database. Sync reaches Infrahub only through
  Infrahub's API.
- **Prefect version.** Sync chooses its Prefect version for itself, and ships client and server
  together. It may differ from Infrahub's.

## Consequences

- **Sync owns its Prefect configuration.** Work pools, deployments, retention, concurrency and
  the API credential are set by Sync alone. For Prefect to work properly, each project has to
  manage its whole configuration, which is already hard with one project on an instance.
- **No release coupling.** A Prefect upgrade in Infrahub needs no Sync release, and no minimum
  Infrahub version follows from Prefect.
- **Several Sync deployments per site.** The fixed deployment name only has to be unique on
  Sync's own Prefect server.
- **Two of each per site.** A site runs two Prefect servers and two PostgreSQL servers, with two
  sets of upgrades, backups and credentials. The resource overhead was judged negligible
  compared with sharing a Prefect instance. The duplicated maintenance remains a cost.
- **Not in Infrahub's task views.** Sync's flow runs are not in Infrahub's `prefect` database,
  so Infrahub's task list does not show them. #362's `infrahub.app` tags are dropped.
- **Spec 008 user story 1 is reversed.** #362 is not merged. #363 is rebased off it, and its
  node tags no longer place runs in Infrahub's task views.
- **Prefect internals.** The worker sets Prefect's private
  `ProcessJobConfiguration._command_configured` on each admitted run, so that Prefect starts
  the run with its private `EngineCommandStarter` (#357). Each Prefect upgrade has to
  re-verify both.

## Alternatives Considered

- **Run Sync on Infrahub's task manager, with its database on `task-manager-db` (#362).**
  Rejected. Sharing one Prefect instance between projects means sharing its configuration, which
  neither project then fully manages.
- **Run Sync's own Prefect server, with its databases on `task-manager-db`.** Rejected. It still
  depends on Infrahub's deployment for Sync's PostgreSQL server.
