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

## Related

- [Adapter guidelines](../guidelines/README.md) — rules an adapter must follow.
- [Adapter guides](../guides/README.md) — adding and testing an adapter.
- [Constitution](../constitution.md) — project principles these documents serve.
