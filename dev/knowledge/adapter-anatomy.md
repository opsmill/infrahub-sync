# Adapter anatomy

> Part of: `dev/knowledge/` | Related: [Sync architecture](sync-architecture.md), [Schema mapping](schema-mapping.md), [Adding an adapter](../guides/adding-an-adapter.md)

An adapter is a single module under `infrahub_sync/adapters/<name>.py` (or a custom module
outside the package) that defines two classes: an **adapter class** that loads and writes
objects, and a **model class** that the generated models inherit. `infrahub_sync/adapters/netbox.py`
is the reference example; `examples/custom_adapter/custom_adapter_src/custom_adapter.py` is a
minimal from-scratch one.

## The two classes

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

    def model_loader(self, model_name, model): ...

class MyModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(cls, adapter, ids, attrs): ...
    def update(self, attrs): ...
```

`DiffSyncMixin` and `DiffSyncModelMixin` live in `infrahub_sync/__init__.py`. The order of
base classes matters: the mixin comes first so its methods take precedence over the
DiffSync base.

## The adapter contract (`DiffSyncMixin`)

The mixin defines the surface Potenda calls. Each method is one of three kinds — provided
(use as-is), must-implement (raises `NotImplementedError` until you override), or optional.

| Method | Kind | Purpose |
|--------|------|---------|
| `load()` | Provided | Iterates `top_level`; calls `load_<name>()` if defined, otherwise `model_loader(name, model)`. Do not override. |
| `model_loader(model_name, model)` | Must implement | Fetch records from the system, filter and transform them, build each into the DiffSync shape, and `self.add(model(**data))`. |
| `cursor_tier_for(model_name)` | Optional | Strongest incremental tier the source supports for this model. Defaults to `CursorTier.NONE` (always full extract). |
| `list_changed_since(model_name, cursor)` | Conditional | Required only if `cursor_tier_for` returns a non-`NONE` tier. Yields records changed since the cursor, in the same shape `model_loader` produces. |
| `list_existing_ids(model_name)` | Optional | Yields current `unique_id` strings for delete detection between incremental runs. |

A read-only-capable adapter that only ever does full extracts needs just `model_loader`.
Incremental support is additive — see [Incremental sync and cache](incremental-and-cache.md).

## The model contract (`DiffSyncModelMixin`)

The model mixin gives every model the helpers used during loading and the hooks used during
writing.

Provided for you (used inside `model_loader`):

- `filter_records(records, schema_mapping)` — drop records that fail the mapping's filters.
- `transform_records(records, schema_mapping)` — apply the mapping's Jinja2 transforms.
- `apply_filters` / `apply_transforms` / `is_list` / `get_resource_name` — the lower-level
  building blocks the two above are built from.

You implement on the model (used when it is the destination):

- `create(cls, adapter, ids, attrs)` — create the object in the destination, return the
  instance.
- `update(self, attrs)` — apply changed attributes.
- `delete(self)` — inherited from DiffSync; override only if deletion needs custom logic.

If an adapter is only ever a source, its model's `create` / `update` / `delete` are never
called and can defer to the base implementation.

## From upstream object to DiffSync model

Inside `model_loader`, each raw record is converted to the field shape the generated model
expects. By convention this is a helper named `<name>_obj_to_diffsync` (or `obj_to_diffsync`
on the REST base). It walks the mapping's `fields` and, for each:

- copies a `static` literal, or
- reads `field.mapping` from the record (dot notation, via `get_value`), or
- resolves a `reference` to another model's `unique_id` (single or list) using the store.

Every record also carries a `local_id` — the source-side primary key — so references can be
resolved across models. See [Schema mapping](schema-mapping.md) for the field semantics.

## How the class is found

`config.yml` selects the adapter:

- `name: netbox` — a built-in under `infrahub_sync/adapters/`.
- `adapter: ./path/to/file.py:MyAdapter` — a filesystem path and class name.
- `adapter: my_pkg.adapters:MyAdapter` — a dotted import path.
- an installed package exposing an `infrahub_sync.adapters` entry point.

`plugin_loader.py` resolves these in order. Custom adapters do not need to live inside the
package — point at them with `adapter` and, if needed, `adapters_path`.

## See also

- [Sync architecture](sync-architecture.md) — where the adapter sits in a run.
- [Writing an adapter](../guidelines/writing-an-adapter.md) — the rules to follow.
- [Adding an adapter](../guides/adding-an-adapter.md) — the step-by-step procedure.
