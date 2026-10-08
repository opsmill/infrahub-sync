---
title: "Knowledge"
---

## Knowledge

Understand the service components, execution stages and Python modules before changing
infrahub-sync. A **registered run** is one the Sync HTTP API creates against a
configuration package registered in PostgreSQL, as opposed to a legacy run resolved from a
local configuration directory; see [Sync architecture](sync-architecture.md) for the
lifecycle. Start there, or with the repository tour for a module to edit. For development
rules see [Guidelines](../guidelines/index.md); for step-by-step procedures see
[Developer guides](../guides/index.md).

### Orientation

- [Sync architecture](sync-architecture.md) — service components, the registered-run
  lifecycle, durable records and deployment limits.
- [Repository tour](repository-tour.md) — the modules for CLI requests, service execution,
  plans, storage and adapters, with a trace from the HTTP client to worker operations.

### Adapters

- [Adapter anatomy](adapter-anatomy.md) — the two classes every adapter provides, the
  `DiffSyncMixin` (the adapter base an adapter's source and destination classes implement) /
  `DiffSyncModelMixin` (the model base their record types implement) contract, and what you
  implement versus what you get for free.
- [Schema mapping](schema-mapping.md) — how `config.yml` maps source resources to
  destination models: fields, identifiers, references, filters, and transforms.
- [Incremental sync and cache](incremental-and-cache.md) — cursors (per-adapter bookmarks of
  what was already read), the cursor tiers an adapter declares for each model, plans, and
  row-count guardrails (a comparison that would refuse a per-resource count drop
  against a previous baseline; no product path calls it today), and what an adapter
  implements to participate.

### Plans and applying them

- [The saved plan artifact](plan-artifact.md) — the manifest and operations a run records
  before it writes: layout, canonical encoding (the exact byte serialization a plan's
  checksum is computed over), operation identifiers, the checksum, and how a stored plan is
  read and verified.
- [Planned writes and apply](planned-write-and-apply.md) — the destination write surface for saved
  operations, apply-time peer resolution (matching a planned reference to the destination
  object it names before writing), replace-set semantics, and why recorded deletes
  are not executed.
- [The configuration write guard](apply-guard.md) — the PostgreSQL session advisory lock (a
  database-held mutual-exclusion lock, not a table row) that serializes one configuration's
  writes across processes: its direct-connection requirement, key derivation, deadline
  bounds, ownership proof, and failure sanitizing.

### Configuration and execution {#running-a-sync-from-something-other-than-the-cli}

- [Configuration foundation](configuration-foundation.md) — declared package identity,
  runtime credential references, and the connection-free adapter capability declaration.
- [The shared execution surface](execution-surface.md) — service and direct Python
  callers, plan/verify/apply inputs and return types, failure handling and filesystem locks.
- [Prefect orchestration](orchestration-prefect.md) — registered service execution and
  direct read-only planning, with their inputs, results and optional dependencies.

### Repository workflow

- [MVP acceptance contract](mvp-acceptance-contract.md) — the normative release boundary,
  required criterion grammar, owning specifications, and explicit post-MVP deferrals. Tooling can
  download its generated [qualification manifest JSON Schema]
  (/schemas/infrahub-sync-mvp-qualification-manifest-v1.schema.json).
- [Quality gates](quality-gates.md) — what `invoke lint` and `invoke format` actually run,
  the inherited pylint baseline, and how to measure a no-regression claim.
- [Testing tiers](../guidelines/testing-tiers.md) — which test command covers which tier, what
  each one needs, and what a skip means.

### Related

- [Guidelines](../guidelines/index.md) — rules that apply to this code.
- [Developer guides](../guides/index.md) — step-by-step procedures for adapters, the development environment, and release candidates.
- [Decision records](../adr-index.mdx) — why the architecture is shaped the way it is.
- [Constitution](../constitution.md) — project principles these documents serve.
