---
title: "Adapter anatomy"
---

## Adapter anatomy

> Part of: Develop > Knowledge | Related: [Sync architecture](sync-architecture.md), [Schema mapping](schema-mapping.md), [Adding an adapter](../guides/adding-an-adapter.md)

An adapter is a single module under `infrahub_sync/adapters/<name>.py` that defines two
classes: an **adapter class** that loads and writes objects, and a **model class** that the
generated models inherit. `infrahub_sync/adapters/netbox.py` is the reference example;
`examples/custom_adapter/custom_adapter_src/custom_adapter.py` is a minimal from-scratch one.

The registered Sync service runs only the adapters in `infrahub_sync/adapters/`, because only
they have a capability declaration. A module outside the package can be loaded during local
development, but a package that names it is refused at registration; see
[How the class is found](#how-the-class-is-found).

### The two classes

```python
from diffsync import Adapter, DiffSyncModel
from infrahub_sync import DiffSyncMixin, DiffSyncModelMixin

class MyAdapter(DiffSyncMixin, Adapter):
    type = "MySystem"

    def __init__(self, target, adapter, config, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = target          # "source" or "destination"
        self.config = config          # the SyncConfig for this run
        self.client = self._make_client(adapter.settings or {})

    def list_records(self, model_name, element): ...
    def record_to_data(self, record, element, model): ...

class MyModel(DiffSyncModelMixin, DiffSyncModel):
    pass
```

`DiffSyncMixin` and `DiffSyncModelMixin` live in `infrahub_sync/__init__.py`. The order of
base classes matters: the mixin comes first so its methods take precedence over the
DiffSync base.

### The adapter contract (`DiffSyncMixin`)

The mixin defines the surface Potenda calls. Each method is one of three kinds — provided
(use as-is), must-implement (raises `NotImplementedError` until you override), or optional.

| Method | Kind | Purpose |
|--------|------|---------|
| `load()` | Provided | Iterates `top_level`; calls `load_<name>()` if defined, otherwise `model_loader(name, model)`. Do not override. |
| `list_records(model_name, element)` | Must implement | Return the raw records of one mapping entry from the system, before any filter. |
| `record_to_data(record, element, model)` | Must implement | Convert one raw record to the model's field data, with `local_id` set to the record's source ID. Resolve each reference with `peer_unique_id`. |
| `record_source_id(record)` | Optional | Return the source ID of a raw record. The default reads `id` from a mapping or an attribute. |
| `model_loader(model_name, model)` | Provided | The per-record load loop: for each mapping entry it calls `list_records` and passes each record to `load_record`. On the source side it first applies the configured filters and transforms, and keeps the IDs that a filter removed. |
| `load_record(model, element, record)` | Provided | The admission step: builds one model, adds it to the store and, on the source side, records a record that cannot be built in `load_report`. |
| `peer_unique_id(peer_kind, peer_source_id, *, owner, field, required)` | Provided | Resolves a reference to the peer's `unique_id`. |
| `cursor_tier_for(model_name)` | Optional | Strongest incremental tier the source supports for this model. Defaults to `CursorTier.NONE` (always full extract). |
| `list_changed_since(model_name, cursor)` | Conditional | Required only if `cursor_tier_for` returns a non-`NONE` tier. Yields records changed since the cursor, as model field data (the shape `record_to_data` returns). |
| `list_existing_ids(model_name)` | Optional | Yields current `unique_id` strings for delete detection between incremental runs. |
| `apply_planned_operation(*, operation, peers)` | Optional | Executes one operation from a **saved plan** and returns the destination node id. The apply stage of `sync` or `apply` calls it. Without it, both commands refuse before writing — see [The planned-write surface](#the-planned-write-surface). |
| `new_peer_resolver()` | Conditional | Required only alongside `apply_planned_operation` — the engine builds the per-apply peer resolver through it. The two together are the planned-write surface; an adapter with only one of them is refused like an adapter with neither. |

A read-only-capable adapter that only ever does full extracts needs only `list_records` and
`record_to_data`.
Incremental support is additive — see [Incremental sync and cache](incremental-and-cache.md).

### The planned-write surface

Registered `sync` plans, verifies and applies. The apply step replays a saved plan without
loading either side, so it has no model instances to call. It dispatches to one method on the
**adapter**:

```python
def apply_planned_operation(self, *, operation: PlannedOperation, peers: PeerResolver) -> str:
    """Execute one planned operation convergently. Returns the destination node id."""
```

It is one of **two** members of that surface — `PlannedWriteDestination` in
`infrahub_sync/plan/write_surface.py` — the other being `new_peer_resolver()`, which builds the
per-apply peer resolver the method above is handed:

```python
def new_peer_resolver(self) -> PeerResolver:
    """Build the peer resolver for one apply, bound to this adapter."""
```

The surface is **optional**, and not defining it is a supported position rather than a gap. An
adapter that lacks either member makes `sync` or `apply` fail its pre-write verification gate.
The refusal names the adapter class before any write reaches the destination. The gate is an
`isinstance` check against the protocol, so it verifies that both members are **present**, never
that their signatures match: a wrong signature surfaces at the first operation instead.
Other commands, including plan review, are unaffected. `infrahub` is the only adapter in this
repository that implements the surface today.

An implementation must execute the single recorded operation convergently (a re-apply must not
duplicate), return the destination node id, resolve every relationship peer through the
supplied `peers` resolver rather than through any loaded store, and **decline a `delete`** by
raising `SkippedDeleteOperation` instead of executing it. That last obligation is defensive:
the engine filters deletes out of its own apply loop and never dispatches one to the write
surface, so the raise guards against a caller that is not the engine. Under the apply loop the
rest of the plan is still applied and the run still ends `applied`, with the skipped
identifiers recorded — but that accounting is the loop's, not your method's. Dispatched
directly, your method raises and that is all that happens: nothing is recorded, and there is no
run to complete.

It must also **write only the fields the operation maps**. The payload is authoritative for those
fields and for nothing else: an unmapped destination field must come out of the apply untouched.
That rule can break by accident on the relationship path, because an SDK that re-renders a
whole node it considers existing emits `<rel>: null` for every optional cardinality-one
relationship left uninitialized. If your destination client re-renders whole objects on write,
check what it does with the fields you did not set.

The full contract lives in
[the destination write surface contract](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/specs/archive/001-plan-artifact-saved-apply/contracts/destination-write-surface.md);
`infrahub_sync/adapters/infrahub.py` is the reference implementation.

### The model contract (`DiffSyncModelMixin`)

The model mixin gives every model helpers used during loading.

Provided for you (the base class `model_loader` calls the first two):

- `filter_records(records, schema_mapping)` — drop records that fail the mapping's filters.
- `transform_records(records, schema_mapping)` — apply the mapping's Jinja2 transforms.
- `apply_filters` / `apply_transforms` / `is_list` — lower-level loading helpers.

Models inherit `create`, `update` and `delete` from DiffSync. V3 does not call them.
Destinations write through the adapter's `apply_planned_operation` method.

### From upstream object to DiffSync model

`record_to_data` converts each raw record to the field shape the generated model expects.
It walks the mapping's `fields` and, for each:

- copies a `static` literal, or
- reads `field.mapping` from the record (dot notation, via `get_value`), or
- resolves a `reference` to another model's `unique_id` (single or list) with `peer_unique_id`.

Every record also carries a `local_id` — the source-side primary key — so references can be
resolved across models. See [Schema mapping](schema-mapping.md) for the field semantics.

### The load loop and the admission step

The base class owns the per-record load loop, so every adapter loads records the same way.
`model_loader` fetches the records of each mapping entry with `list_records`, applies the
configured filters and transforms on the source side, and passes each record to `load_record`. An adapter does not call
`self.add(model(**data))` itself.

`load_record` converts the record with `record_to_data`, builds the model and adds it to the
store. On the source side, it records a record that cannot be built in the adapter's
`load_report` and continues with the next record:

- the model refuses a value (`wrong_value_type`);
- the store already holds a record with the same identity (`duplicate_source_identity`);
- a required reference points to a peer that a configured filter removed, that was not
  loaded, or that the source does not return.

Any other failure while building a source record is raised as `RecordLoadError` and fails
the load. On the destination side, `load_record` catches nothing. The Infrahub adapter stores
each destination record through `DestinationIdentityAmbiguities.store`
(`infrahub_sync/plan/destination_ambiguity.py`): two Infrahub nodes with one identity keep one
model in the store, and their node IDs are recorded for the problem
`destination_identity_ambiguous`.

On the source side, `model_loader` passes `record_transform_failure` to the model's
`transform_records`. A record whose transform fails is recorded in the load report as
`transform_failed`, with the field and the transform's position (`transforms[0]`), and is not
loaded; the other records are transformed and loaded. A model that overrides `transform_records`
without the `on_failure` parameter keeps its own behaviour: a failed transform fails the load.

`peer_unique_id` resolves a reference. It looks up the peer among the loaded records, then in
the load report, then among the IDs that a configured filter removed. A reference on an
optional relationship to a peer that the source does not return keeps the record and is
listed as `peer_not_found_in_source`.

An adapter that overrides `model_loader` and calls `self.add(model(**data))` itself opts out
of the admission step: its records that cannot be built fail the load and are not listed.
The Infrahub adapter keeps its own `model_loader`, because it also fills the SDK store, and
calls `load_record` itself for each record, with an `after_add` hook. The engine also builds
the incremental snapshot rows and changed rows through `load_record`, with the
`identity_record_data` conversion.

### How the class is found

A configuration selects the adapter in one of these ways:

- `name: netbox` — a built-in under `infrahub_sync/adapters/`.
- `adapter: ./path/to/file.py:MyAdapter` — a filesystem path and class name.
- `adapter: my_pkg.adapters:MyAdapter` — a dotted import path.
- an installed package exposing an `infrahub_sync.adapters` entry point.

`plugin_loader.py` resolves these in order.

A registered package is stricter. Registration looks up the source and destination `name` in
`BUILTIN_ADAPTER_CAPABILITIES` (`infrahub_sync/configuration/capabilities.py`), which lists
only the bundled adapters, and refuses any other name. It also refuses a filesystem `adapter`
target, the `adapters_path` field, and any `adapter` key on the destination
(`infrahub_sync/configuration/models.py`). A source `adapter` that names a dotted import path or
an entry point passes that check, but the package is still refused unless its `name` is a
bundled adapter. Filesystem paths and `adapters_path` apply only to local development loading;
see [Local adapters](../../adapters/local-adapters.mdx) and
[Adding an adapter](../guides/adding-an-adapter.md#the-current-boundary-for-adapters-outside-the-distribution).

### See also

- [Sync architecture](sync-architecture.md) — where the adapter sits in a run.
- [Writing an adapter](../guidelines/writing-an-adapter.md) — the rules to follow.
- [Adding an adapter](../guides/adding-an-adapter.md) — the step-by-step procedure.
