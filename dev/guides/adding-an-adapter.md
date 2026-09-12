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

That covers `infrahub-sync sync`, the live compare-and-write path. Applying a **saved plan**
(`infrahub-sync apply --run-id <id>`) goes through a separate surface — see Step 4b.

### Step 4b: Implement the planned-write surface (optional, destination only)

**Infrahub-only in v1.** Read this step as documentation of the Infrahub destination's write
surface, not as a general extension point. Both members are typed with `PeerResolver`, which is
the Infrahub adapter's concrete resolver class, so a non-Infrahub destination cannot conform to
the protocol statically without importing the Infrahub adapter. Making the resolver type
adapter-neutral is a tracked follow-up: raise it before writing a second implementation rather
than working around the Infrahub import.

`infrahub-sync apply` replays a plan artifact that a previous `diff` saved. It does not load
either side and does not re-compare, so it cannot go through the model's `create` / `update`.
It goes through a surface on the **adapter** instead — `PlannedWriteDestination` in
`infrahub_sync/plan/write_surface.py`, which has **two** members:

```python
def new_peer_resolver(self) -> PeerResolver:
    """Build the peer resolver for one apply, bound to this adapter."""

def apply_planned_operation(self, *, operation: PlannedOperation, peers: PeerResolver) -> str:
    """Execute one planned operation convergently. Returns the destination node id."""
```

Both are required: the engine builds the per-apply resolver through the factory rather than
constructing one itself, so an adapter offering only the write method is not a planned-write
destination and is refused with the rest.

**Not implementing the surface is a supported position, not a break.** An adapter without it
makes `apply` refuse in its pre-write verification gate — **before any write reaches the
destination** — with an error naming the adapter class and directing the operator to `sync`:

```text
The destination adapter 'MysystemAdapter' cannot apply a saved plan. Use `infrahub-sync sync`
for this destination, or apply against a destination whose adapter implements the
planned-write surface.
```

Nothing else about the adapter degrades: `diff`, `sync` and plan *review*
(`runs plan RUN_ID`) all work unchanged. Only `apply` is unavailable. `infrahub` is
the only one of the nine adapters shipped in this repository that implements the surface
today; the other eight refuse an `apply` exactly as described above.

The gate is an `isinstance` check against the protocol, which verifies that both members are
**present** and not that their signatures match. Get a signature wrong and the refusal will not
catch it — the apply will fail at the first operation instead. Type-check your adapter
(`uv run ty check .`) rather than relying on that gate.

If you do implement it, the method must:

- **Execute exactly one operation, convergently** — re-applying the same plan must not
  duplicate the object. Take `operation.payload` and `operation.identity` as recorded; do not
  recompute either, and do not read the destination to decide what to write.
- **Return the destination node id** as a string. The engine feeds it back to the resolver so
  later operations in the same plan can refer to this object.
- **Touch no destination field the operation did not map.** The payload is authoritative for the
  fields it carries and for nothing else. Watch the relationship path in particular: a client that
  re-renders a whole object on write may send explicit nulls for the fields you never set — the
  Infrahub SDK does exactly that for optional cardinality-one relationships on a node it considers
  existing, which is why the cardinality-many replace-set there is flushed by a **targeted write**
  naming the id plus only the fields being replaced, rather than a whole-node update.
- **Resolve relationship peers through the supplied resolver**, never through a loaded store.
  Call `peers.resolve(peer_kind=..., identity=..., referring_operation_id=...)` for each peer
  in each `operation.relationships` entry; it returns one node id per identity, and
  cardinality is your concern, not its.
- **Decline a `delete` rather than executing one** — raise `SkippedDeleteOperation`
  (`infrahub_sync.plan.errors`) and touch nothing. Applying deletes is not supported and
  remains outside the planned-write contract. In practice your method will not see one:
  the engine recognizes a `delete` in its
  own apply loop, records its identifier and never dispatches it to the write surface. Raise
  it anyway — it is the defensive half of the contract, for any caller that is not the
  engine. Either way the run applies every non-delete in the same plan and ends `applied`
  with the skipped count recorded.

The full contract — the convergent upsert sequence, the keyedness gate, relationship
replace-set reconciliation and the error taxonomy — is in
[the destination write surface contract](../specs/archive/001-plan-artifact-saved-apply/contracts/destination-write-surface.md).
`infrahub_sync/adapters/infrahub.py` is the reference implementation.

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
uv run rumdl check docs/docs/adapters/
```

## Verification

Validate read-only paths before ever running `sync`:

```bash
uv run invoke format
uv run invoke lint

uv run infrahub-sync configs register --file examples/mysystem_to_infrahub/config.yml --reason "add mysystem example"
uv run infrahub-sync configs validate --config-id mysystem-example --version 1
uv run infrahub-sync diff --config-id mysystem-example --version 1 --reason "verify the new adapter"
```

`configs register` and `configs validate` read no source; `diff` plans against both sides but
writes nothing. Run `sync` only with explicit approval against a known-safe target.

## Quality checklist

- [ ] Adapter inherits `DiffSyncMixin` / `DiffSyncModelMixin`, mixin first, with a `type`.
- [ ] `model_loader` filters and transforms through the model mixin; `obj_to_diffsync` sets `local_id`.
- [ ] Decided whether the adapter implements the planned-write surface — **both** `new_peer_resolver` and `apply_planned_operation`; if it does not, confirmed that `apply` refuses cleanly and that `sync` is the documented path for it.
- [ ] Optional SDK imported with `# ty: ignore[unresolved-import]`; credentials from env vars; no secrets logged or committed.
- [ ] `uv run invoke format` and `uv run invoke lint` are clean; `uv run ty check .` exits 0.
- [ ] `configs register` / `configs validate` / `diff` succeed for the example.
- [ ] Unit tests added under `tests/adapters/`; `uv run pytest -q` passes offline.
- [ ] Example added under `examples/`; env vars documented.
- [ ] Documentation page added under `docs/docs/adapters/` and in the sidebar.

## Related resources

- [Adapter anatomy](../knowledge/adapter-anatomy.md) — the classes and contract.
- [Writing an adapter](../guidelines/writing-an-adapter.md) — the rules.
- [Testing an adapter](testing-an-adapter.md) — the tests to add.
