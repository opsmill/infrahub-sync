# 17. Sync runs on Infrahub's task manager

**Status**: Accepted
**Date**: 2026-10-02
**Decision-makers**: Benoit Kohler
**Source**: dev/specs/008-infrahub-platform-alignment/research.md (R1–R3, R8); spec.md clarifications
**Narrows**: AD-2 in infrahub-sync-process `product/decisions.md`

## Context

The V3 deployment ran its own Prefect server and its own PostgreSQL server next to Infrahub,
which already ships both: its task manager is a Prefect server, backed by the `task-manager-db`
PostgreSQL server. Sync pinned Prefect 3.8.1 while Infrahub 1.11.3 and later ship 3.8.6, so the
two Prefect installations on one site drifted apart, and operators ran and upgraded two Prefect
servers and two PostgreSQL servers for one product. The owner set Infrahub's task manager as the
reference Sync must stay aligned with. Infrahub's task manager has no API credential setting
(#342).

## Decision

Sync runs its flows on Infrahub's task manager and keeps its own database on Infrahub's
task-manager PostgreSQL server.

- **Prefect version.** Pinned exactly to the version the newest supported Infrahub ships:
  3.8.6, so Infrahub 1.11.3 or later. A CI check fails when the two drift.
- **Pool and deployment.** Sync keeps its own work pool `infrahub-sync`, its own worker and its
  own deployment. It does not run on Infrahub's `infrahub-worker` pool.
- **Database.** Sync's bootstrap creates the `infrahub_sync` database and its owner role on
  `task-manager-db`. The database holds run state, idempotency receipts, write admissions, audit
  events and the advisory write locks of ADR 0010.
- **One Sync per task manager.** The service's Prefect deployment name is fixed.
- **Task manager access.** Sync sends a Prefect credential only when the operator sets one.
  Otherwise network isolation is the only protection, and the API and worker log which mode
  they run in.
- **Migration.** Existing V3 candidate deployments are reinstalled; no data is carried over.

## Consequences

- **One of each per site.** A site runs one Prefect server and one PostgreSQL server.
  Client/server skew between Sync and the task manager is ruled out by the pin.
- **Visible in Infrahub.** Sync's flow runs carry Infrahub's task tags, so they appear in
  Infrahub's task views.
- **Release coupling.** Sync's Prefect pin follows Infrahub's releases. A Prefect bump in
  Infrahub needs a Sync release, and the Infrahub floor is 1.11.3.
- **Private Prefect internals.** The worker reimplements the configured-command start path with
  Prefect 3.8.6's private `FlowRunExecutorContext` and `EngineCommandStarter`. Each Prefect bump
  needs that path re-verified.
- **Unauthenticated task manager.** Anyone who can reach the task manager can start Sync's flows
  unless the network is isolated or a credential is set.
- **Shared PostgreSQL server.** Sync's load and backups ride with Infrahub's task manager, and a
  full reset of Sync uses the server's administrator credential to drop its database and role.
- **One Sync per Infrahub.** A second Sync, for example for staging, needs its own task manager
  until the deployment name becomes configurable.
- **Prefect retention.** Deleting an Infrahub branch, or Infrahub's 30-day flush, purges Sync's
  flow runs and their logs from Prefect. Sync's durable run records are unaffected.

## Alternatives Considered

- **Keep Sync's own Prefect server and PostgreSQL server.** Rejected: the two Prefect
  installations drift, and operators run every service twice.
- **Run Sync's flows on Infrahub's `infrahub-worker` pool.** Rejected: Sync's worker enforces
  deployment admission, and Infrahub's workers would need Sync's code and credentials.
- **Pin a Prefect range such as `>=3.8,<3.9`.** Rejected: a range does not prove the pair was
  tested, and it drifts again at the next Infrahub bump.
