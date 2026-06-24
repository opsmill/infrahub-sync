# Adding an adapter

> Part of: `dev/guides/` | Related: [Adapter anatomy](../knowledge/adapter-anatomy.md), [Writing an adapter](../guidelines/writing-an-adapter.md)

Step-by-step guide for connecting a new system to infrahub-sync as a source or destination.
This is the canonical procedure; `AGENTS.md` links here.

## When to add an adapter

Add an adapter when you need to read from, or write to, a system that has no connector yet.
The built-ins live in `infrahub_sync/adapters/` (`netbox`, `nautobot`, `infrahub`, `aci`,
`prometheus`, `peeringmanager`, `ipfabricsync`, `slurpitsync`, `genericrestapi`).

Before writing one, check whether you can avoid it:

- If the system has a REST API, subclass `GenericrestapiAdapter` and configure it rather than
  writing a connector from scratch.
- If a built-in already covers the system, you may only need a new `config.yml`.

## Prerequisites

- A working dev environment (`uv sync`) — see `AGENTS.md`.
- An understanding of the [adapter anatomy](../knowledge/adapter-anatomy.md) and
  [schema mapping](../knowledge/schema-mapping.md).
- Read access to the source system (URL, token) or write access to the destination.
- The destination schema (for an Infrahub destination, the node kinds you will map to).

## Steps

### Step 1: Choose the role and a starting point

Decide whether the new system is a **source** (read-only) or a **destination** (written to),
and pick a base:

| Situation | Start from |
|-----------|------------|
| REST/JSON API | Subclass `GenericrestapiAdapter` (`infrahub_sync/adapters/peeringmanager.py`) |
| Bespoke SDK or protocol | A fresh `DiffSyncMixin` adapter (`infrahub_sync/adapters/netbox.py`) |
| Learning the shape | Copy `examples/custom_adapter/custom_adapter_src/custom_adapter.py` |

### Step 2: Create the adapter module

Create `infrahub_sync/adapters/<name>.py` (or a custom module outside the package). Define the
two classes and a client:

```python
from diffsync import Adapter, DiffSyncModel
from infrahub_sync import DiffSyncMixin, DiffSyncModelMixin, SchemaMappingModel, SyncAdapter, SyncConfig

class MysystemAdapter(DiffSyncMixin, Adapter):
    type = "MySystem"

    def __init__(self, target, adapter, config, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = target
        self.config = config
        self.settings = adapter.settings or {}
        self.client = self._create_client(self.settings)

    def model_loader(self, model_name, model): ...

class MysystemModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(cls, adapter, ids, attrs): ...
    def update(self, attrs): ...
```

Follow [Writing an adapter](../guidelines/writing-an-adapter.md): mixin first, `structlog`,
optional-dependency import with `# ty: ignore[unresolved-import]`, credentials from the
environment.

### Step 3: Implement `model_loader`

For each model, find its schema-mapping entry, fetch the source records, filter and transform
them through the model mixin, convert each to the DiffSync shape, and add it:

```python
def model_loader(self, model_name, model):
    element = next(e for e in self.config.schema_mapping if e.name == model_name)
    records = self.client.get(element.mapping)
    if self.config.source.name.title() == self.type.title():
        records = model.filter_records(records=records, schema_mapping=element)
        records = model.transform_records(records=records, schema_mapping=element)
    for obj in records:
        self.add(model(**self.obj_to_diffsync(obj=obj, mapping=element, model=model)))
```

Write the `obj_to_diffsync` helper to walk `element.fields` — `static`, plain `mapping`, and
`reference` (resolved to a peer `unique_id`) — and always set `local_id`. See
`examples/custom_adapter/custom_adapter_src/custom_adapter.py` for a complete version.

### Step 4: Implement write methods (destination only)

If the adapter can be a destination, implement `create`, `update`, and `delete` on the model
to mutate the target system. A source-only adapter can leave these deferring to the base.

### Step 5: Write the schema mapping and `config.yml`

Create an example project directory with a `config.yml` that selects the adapter and maps
resources to destination models:

```yaml
---
name: mysystem-example

source:
  name: mysystem
  # built-in name above, OR a path/dotted-path to a custom class:
  adapter: ./path/to/my_adapter.py:MysystemAdapter
  settings:
    url: "https://mysystem.example.com"

destination:
  name: infrahub
  settings:
    url: "http://localhost:8000"

schema_mapping:
  - name: InfraDevice
    mapping: devices
    identifiers: ["name"]
    fields:
      - name: name
        mapping: name
```

Omit `order` — it is computed from `reference` edges. See
[schema mapping](../knowledge/schema-mapping.md) for fields, filters, and transforms.

### Step 6: Add incremental support (optional)

If the source can filter by change, override `cursor_tier_for` to return the right
`CursorTier` and implement `list_changed_since` (and optionally `list_existing_ids`). See
[incremental sync and cache](../knowledge/incremental-and-cache.md). Skip this and the adapter
simply does full extracts.

### Step 7: Add an example and document env vars

Add a directory under `examples/<system>_to_infrahub/` (or `infrahub_to_<system>/`) with the
`config.yml`, and document the required environment variables and the install extra for the
optional SDK.

### Step 8: Add tests

Write unit tests under `tests/adapters/` that mock the client. See
[Testing an adapter](testing-an-adapter.md) and the rules in
[Testing adapters](../guidelines/testing-adapters.md).

### Step 9: Add a documentation page

Create a page under `docs/docs/adapters/` (overview, config keys, env vars, example YAML,
common errors), add it to the sidebar, and lint it:

```bash
markdownlint-cli2 "docs/docs/adapters/**/*.{md,mdx}"
```

## Verification

Validate read-only paths before ever running `sync`:

```bash
uv run invoke format
uv run invoke lint

uv run infrahub-sync list --directory examples/
uv run infrahub-sync generate --name mysystem-example --directory examples/mysystem_to_infrahub/
uv run infrahub-sync diff --name mysystem-example --directory examples/mysystem_to_infrahub/
```

`list` and `generate` need no live source; `diff` reads both sides but writes nothing. Run
`sync` only with explicit approval against a known-safe target.

## Quality checklist

- [ ] Adapter inherits `DiffSyncMixin` / `DiffSyncModelMixin`, mixin first, with a `type`.
- [ ] `model_loader` filters and transforms through the model mixin; `obj_to_diffsync` sets `local_id`.
- [ ] Optional SDK imported with `# ty: ignore[unresolved-import]`; credentials from env vars; no secrets logged or committed.
- [ ] `uv run invoke format` and `uv run invoke lint` are clean; `uv run ty check .` exits 0.
- [ ] `list` / `generate` / `diff` succeed for the example.
- [ ] Unit tests added under `tests/adapters/`; `uv run pytest -q` passes offline.
- [ ] Example added under `examples/`; env vars documented.
- [ ] Documentation page added under `docs/docs/adapters/` and in the sidebar.

## Related resources

- [Adapter anatomy](../knowledge/adapter-anatomy.md) — the classes and contract.
- [Writing an adapter](../guidelines/writing-an-adapter.md) — the rules.
- [Testing an adapter](testing-an-adapter.md) — the tests to add.
