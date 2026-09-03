# Knowledge

How infrahub-sync works — descriptive reference, loaded on demand. These documents
explain the moving parts so you can reason about a change before making it. For
prescriptive rules see [`dev/guidelines/`](../guidelines/README.md); for step-by-step
procedures see [`dev/guides/`](../guides/README.md).

## Adapters

- [Sync architecture](sync-architecture.md) — how a sync runs end to end: the DiffSync
  core, source and destination adapters, the Potenda engine, and the code-generation path.
- [Adapter anatomy](adapter-anatomy.md) — the two classes every adapter provides, the
  `DiffSyncMixin` / `DiffSyncModelMixin` contract, and what you implement versus what you
  get for free.
- [Schema mapping](schema-mapping.md) — how `config.yml` maps source resources to
  destination models: fields, identifiers, references, filters, and transforms.
- [Incremental sync and cache](incremental-and-cache.md) — cursors, tiers, plans, and
  row-count guardrails, and what an adapter implements to participate.

## Plans and applying them

- [The saved plan artifact](plan-artifact.md) — the manifest and operations a run records
  before it writes: layout, canonical encoding, operation identifiers, the checksum, and how
  a stored plan is read and verified.
- [Planned writes and apply](planned-write-and-apply.md) — the second write path: the
  destination write surface and what its type does and does not enforce, apply-time peer
  resolution, replace-set flush semantics, and how deletes are recorded but not executed.
- [The configuration write guard](apply-guard.md) — the PostgreSQL session advisory lock
  that serializes one configuration's writes across processes: its direct-connection
  requirement, key derivation, deadline bounds, ownership proof, and failure sanitizing.

## Running a sync from something other than the CLI

- [Configuration foundation](configuration-foundation.md) — declared package identity,
  runtime credential references, and the connection-free adapter capability declaration.
- [The shared execution surface](execution-surface.md) — the typed entry point to one run:
  `RunResult`, the failure classes, the pipeline lock, and the plan fingerprint.
- [Prefect orchestration](orchestration-prefect.md) — the packaged flow and serve
  entrypoint, the log bridge, the remote API surface, and Prefect's traps.

## Repository workflow

- [Quality gates](quality-gates.md) — what `invoke lint` and `invoke format` actually run,
  the inherited pylint baseline, and how to measure a no-regression claim.

## Related

- [Adapter guidelines](../guidelines/README.md) — rules that apply to this code.
- [Adapter guides](../guides/README.md) — adding and testing an adapter.
- [Decision records](../adr/README.md) — why the architecture is shaped the way it is.
- [Constitution](../constitution.md) — project principles these documents serve.
