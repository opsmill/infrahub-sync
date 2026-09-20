---
title: "Knowledge"
---

## Knowledge

Understand the service components, execution stages and Python modules before changing
infrahub-sync. Start with the architecture for a registered run or the repository tour
for a module to edit. For development rules see [Guidelines](../guidelines/index.md);
for step-by-step procedures see [Guides](../guides/index.md).

### Orientation

- [Sync architecture](sync-architecture.md) — service components, the registered-run
  lifecycle, durable records and deployment limits.
- [Repository tour](repository-tour.md) — the modules for CLI requests, service execution,
  plans, storage and adapters, with a trace from the HTTP client to worker operations.

### Adapters

- [Adapter anatomy](adapter-anatomy.md) — the two classes every adapter provides, the
  `DiffSyncMixin` / `DiffSyncModelMixin` contract, and what you implement versus what you
  get for free.
- [Schema mapping](schema-mapping.md) — how `config.yml` maps source resources to
  destination models: fields, identifiers, references, filters, and transforms.
- [Incremental sync and cache](incremental-and-cache.md) — cursors, tiers, plans, and
  row-count guardrails, and what an adapter implements to participate.

### Plans and applying them

- [The saved plan artifact](plan-artifact.md) — the manifest and operations a run records
  before it writes: layout, canonical encoding, operation identifiers, the checksum, and how
  a stored plan is read and verified.
- [Planned writes and apply](planned-write-and-apply.md) — the destination write surface for saved
  operations, apply-time peer resolution, replace-set semantics, and why recorded deletes
  are not executed.
- [The configuration write guard](apply-guard.md) — the PostgreSQL session advisory lock
  that serializes one configuration's writes across processes: its direct-connection
  requirement, key derivation, deadline bounds, ownership proof, and failure sanitizing.

### Configuration and execution {#running-a-sync-from-something-other-than-the-cli}

- [Configuration foundation](configuration-foundation.md) — declared package identity,
  runtime credential references, and the connection-free adapter capability declaration.
- [The shared execution surface](execution-surface.md) — service and direct Python
  callers, plan/verify/apply inputs and return types, failure handling and filesystem locks.
- [Prefect orchestration](orchestration-prefect.md) — registered service execution and
  direct read-only planning, with their inputs, results and optional dependencies.

### Repository workflow

- [Quality gates](quality-gates.md) — what `invoke lint` and `invoke format` actually run,
  the inherited pylint baseline, and how to measure a no-regression claim.
- [Testing tiers](../guidelines/testing-tiers.md) — which test command covers which tier, what
  each one needs, and what a skip means.

### Related

- [Guidelines](../guidelines/index.md) — rules that apply to this code.
- [Guides](../guides/index.md) — adding and testing an adapter.
- [Decision records](../adr-index.mdx) — why the architecture is shaped the way it is.
- [Constitution](../constitution.md) — project principles these documents serve.
