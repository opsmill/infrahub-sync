# Sync architecture

> Part of: `dev/knowledge/` | Related: [Adapter anatomy](adapter-anatomy.md), [Adding an adapter](../guides/adding-an-adapter.md)

infrahub-sync moves data from a *source* system of record to a *destination* system of
record. It is built on [DiffSync](https://github.com/networktocode/diffsync): each side
loads its objects into an in-memory store, the two stores are diffed, and the destination
is reconciled to match the source. An adapter is the connector for one system; the same
adapter class can act as either source or destination depending on where it appears in
`config.yml`.

## The DiffSync foundation

DiffSync represents every object as a model instance with a stable identity. Two concepts
do the work:

- **Adapter** — loads objects from one system into a store and knows how to `create`,
  `update`, and `delete` them there.
- **Model** — a typed record with `_identifiers` (the natural key) and `_attributes`
  (the comparable fields). Two objects with the same identifiers are "the same object" on
  both sides; their attributes are what the diff compares.

infrahub-sync layers two mixins on top of the DiffSync base classes: `DiffSyncMixin` for
adapters and `DiffSyncModelMixin` for models. See [Adapter anatomy](adapter-anatomy.md).

## Source and destination

A sync names exactly one `source` and one `destination` in `config.yml`. Each points at an
adapter by `name` (a built-in such as `netbox`, `nautobot`, or `infrahub`) or at a custom
class via `adapter`. The direction is fixed per run: data flows source → destination, and
only the destination is written to. The source is read-only.

The same adapter is therefore used in both roles across the codebase — `infrahub` is the
destination in `netbox_to_infrahub` and the source in `infrahub_to_peering-manager`. An
adapter must be able to *load* (read) for its source role and to *create / update / delete*
for its destination role.

## The Potenda engine

`Potenda` (in `infrahub_sync/potenda/`) orchestrates a run in three stages:

1. **Load** — `load_both_sides()` calls each adapter's `load()`, which iterates the
   adapter's `top_level` model list and calls `model_loader()` per model. The source and
   destination load concurrently by default.
2. **Diff** — `diff()` runs the DiffSync comparison and produces a structured set of
   create / update / delete actions.
3. **Sync** — `sync()` (or `sync_in_tiers()`) applies those actions against the
   destination in dependency order, calling the destination model's `create` / `update` /
   `delete`.

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

Between the diff and the first write, a run saves a **plan artifact** recording every
operation it intends to perform. This holds on the `sync` path as well as the `diff` path:
the tier branch computes and retains every tier's `Diff` first, writes the artifact, then
applies the retained diffs tier by tier, so a plan always exists before anything is written.
The narrowing of `top_level` to one tier governs *diff computation* rather than execution —
it is read only by the comparison engine's differ — so it wraps each `diff()` call in the
compute loop and is irrelevant in the execution loop. A saved artifact can be reviewed
afterwards and applied on its own, without recomputing either side. See
[The saved plan artifact](plan-artifact.md) and
[Planned writes and apply](planned-write-and-apply.md).

Potenda also owns the cross-cutting machinery — write order tiers, the incremental cursor
state, the Parquet diff plan, and the row-count guardrail. See
[Incremental sync and cache](incremental-and-cache.md). Adapters do not call Potenda;
Potenda calls adapters through the mixin contract.

## The code-generation path

Adapters do not hand-write a model class per object type. Instead:

1. You write the adapter module (the connector logic) and a `config.yml` whose
   `schema_mapping` describes which source resources map to which destination models.
2. `infrahub-sync generate` (in `infrahub_sync/generator/`) reads the config and the
   destination schema and renders DiffSync model classes from Jinja2 templates.
3. `infrahub_sync/plugin_loader.py` resolves the adapter class — built-in by `name`, a
   dotted import path, a filesystem path, or an installed entry point — and wires the
   generated models onto the adapter instance at run time.

So an adapter author supplies two things: the connector (how to talk to the system) and
the schema mapping (what to move). The model classes are generated. See
[Schema mapping](schema-mapping.md).

## See also

- [Adapter anatomy](adapter-anatomy.md) — the classes and methods you implement.
- [Adding an adapter](../guides/adding-an-adapter.md) — the end-to-end procedure.
- [Writing an adapter](../guidelines/writing-an-adapter.md) — the rules to follow.
- [The saved plan artifact](plan-artifact.md) — what a run records before it writes.
- [Planned writes and apply](planned-write-and-apply.md) — the second write path.
