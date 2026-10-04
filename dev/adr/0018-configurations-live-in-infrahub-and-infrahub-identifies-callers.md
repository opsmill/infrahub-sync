# 18. Configurations live in Infrahub, and Infrahub identifies Sync's callers

**Status**: Accepted
**Date**: 2026-10-03
**Decision-makers**: Benoit Kohler
**Source**: dev/specs/008-infrahub-platform-alignment/research.md (R4–R6, R8–R10); spec.md clarifications
**Narrows**: AD-2 and AD-14 in infrahub-sync-process `product/decisions.md`

## Context

V3 kept configuration packages and their versions in Sync's PostgreSQL database. Administrators
registered them through the Sync API, and Sync authenticated callers against its own static
bearer-token list. A sync configuration decides what is written into Infrahub, but it was edited
outside Infrahub, versioned by a separate registry, and authorized by a separate identity list,
while the data it writes is reviewed in Infrahub with branches and proposed changes. The owner
judged the PostgreSQL package store overkill and asked for configurations and runs to be part of
Infrahub and its task system.

## Decision

Configurations and their versions are Infrahub data, and Infrahub identifies and authorizes
every caller of the Sync API.

- **Schema extension.** `schema/sync.yml` (namespace `Sync`) adds `SyncConfiguration`, which
  holds the whole package as one document, `SyncConfigurationVersion`, which is an immutable copy
  of a document, `SyncRun` and `SyncApproval`. The operator loads it with Infrahub's tools. Sync
  checks it at startup and never loads a schema itself.
- **Editing.** Users create and change configurations in Infrahub, on branches.
- **Versions.** A version is recorded when a run starts on content that no version holds yet,
  matched by checksum, on the default branch, under an advisory lock.
- **Validation.** Any branch's document can be validated on demand through the Sync API.
  Merging is not blocked; instead, a run refuses invalid content.
- **Identity.** Callers present their own Infrahub API token, and Sync asks Infrahub who they
  are. Rights are Infrahub object permissions on Sync's kinds:
    - `view` to read;
    - `create` on `SyncRun` to plan;
    - `create` on `SyncApproval` to apply or sync;
    - `update` on `SyncRun` to cancel.

  The permission alone decides; who started a run does not. Sync keeps no principals of its own
  and writes its records with a dedicated service account.
- **Run state (option B).** Sync's PostgreSQL database stays the authority for run state. Each
  run is mirrored to a `SyncRun` node, best effort, and each apply's approval to a `SyncApproval`.
  Flow runs are tagged with the run's nodes so they appear in those nodes' Tasks tab.
- **Entry point.** The Sync HTTP API stays the only way to start runs (AD-16 unchanged).

## Consequences

- **Infrahub's workflow.** Configuration changes get Infrahub's branches, diffs, proposed
  changes, permissions and history, and access is governed by one identity and permission
  system.
- **Infrahub dependency.** Sync needs a reachable Infrahub with the schema loaded. When Infrahub
  is down, every authenticated call answers 503 `identity-unavailable`, where the static tokens
  kept working.
- **Per-request cost.** Each authenticated request costs one Infrahub identity and permission
  query.
- **Two copies of run state.** A `SyncRun` mirror can lag or miss a write. PostgreSQL wins and
  the mirror can be rebuilt from it.
- **A versioned contract with operators.** The schema extension is one. A Sync upgrade that
  changes it needs a schema load, and the startup check is structural only, because Infrahub
  stores no extension version.
- **Opaque document.** A configuration is one document field, so Infrahub cannot validate its
  structure or diff it per mapping; Sync validates it.
- **Lazy versions.** Content that no run ever uses never becomes a version.
- **Two configuration stores until option A.** A Sync without Infrahub keeps the PostgreSQL
  configuration tables and registration routes; with Infrahub, those routes answer 410.

## Alternatives Considered

- **Keep configurations in Sync's PostgreSQL database with bearer tokens.** Rejected: a separate
  store the owner judged overkill, and configuration changes bypass Infrahub's review and
  permissions.
- **Keep packages as files in the object store, which every worker can read.** Raised first by
  the owner, then set aside for Infrahub: it removes the database registry but leaves editing,
  versioning and authorization outside Infrahub.
- **Structured mapping nodes instead of one document.** Rejected in the spec clarifications: a
  configuration is one object holding the whole package, and a version is a copy of it.
- **Move run state fully into Infrahub now (option A).** Deferred. Run state drives the worker
  claim, liveness, replay and the write guard, all built on PostgreSQL transactions, and Infrahub
  nodes are not written in one transaction. Mirroring gives visibility now and keeps option A for
  a later version.

## More information

Revisit when option A is taken up: run state moves into Infrahub, the mirror and the PostgreSQL
configuration tables go, and this record is superseded.
