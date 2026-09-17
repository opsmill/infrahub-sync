---
title: "Adding an adapter"
---

## Adding an adapter

> Part of: Develop > Guides | Related: [Adapter anatomy](../knowledge/adapter-anatomy.md), [Writing an adapter](../guidelines/writing-an-adapter.md), [Repository tour](../knowledge/repository-tour.md)

**Verified 2026-09-16 against source revision `61b6a1b`.** The command forms were read from
`uv run infrahub-sync … --help` at that revision; the capability, validation and
registration behavior from
[`infrahub_sync/configuration/capabilities.py`](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/infrahub_sync/configuration/capabilities.py),
[`infrahub_sync/configuration/validation.py`](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/infrahub_sync/configuration/validation.py)
and
[`infrahub_sync/product_store/configs.py`](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/infrahub_sync/product_store/configs.py).
The refusal recorded in [The current boundary](#the-current-boundary-for-adapters-outside-the-distribution)
was reproduced in-process against the shipped example package, read-only. The worked
register-to-apply flow was **not** replayed against a live service for this revision.

The end-to-end procedure for connecting a new system to infrahub-sync as a source or a
destination. This is the canonical procedure; `AGENTS.md` links here.

### What this guide teaches, and what it does not

The supported V3 route is **an adapter that ships in this repository**. A package names its
adapter by a short configuration name, and the service resolves that name against a closed
registry of capability declarations. Adding an adapter therefore means adding two things
together: the connector, and its entry in that registry.

Writing a connector that lives outside the distribution is a different situation with a real
limit at this revision. [The current boundary](#the-current-boundary-for-adapters-outside-the-distribution)
below records exactly what refuses it and why. Read that section before you start if your
adapter is not going to live in this repository.

### When to add an adapter

Add one when you need to read from, or write to, a system that has no connector yet. The
bundled connectors live in `infrahub_sync/adapters/`: `aci`, `genericrestapi`, `infrahub`,
`ipfabricsync`, `nautobot`, `netbox`, `peeringmanager`, `prometheus` and `slurpitsync`.

Before writing one, check whether you can avoid it:

- If the system has a REST API, subclass `GenericrestapiAdapter` and configure it rather than
  writing a connector from scratch.
- If a bundled adapter already covers the system, you may only need a new configuration.

### Prerequisites

- A working development environment — `uv sync --extra dev --extra prefect --extra service`;
  see [Contributing](../../contributing.mdx).
- An understanding of [adapter anatomy](../knowledge/adapter-anatomy.md) and
  [schema mapping](../knowledge/schema-mapping.md).
- Read access to the source system, or write access to the destination.
- The destination schema — for an Infrahub destination, the node kinds you will map to.

### Steps

#### Step 1: Take the shape from the worked example

`examples/custom_adapter/` holds a complete adapter and model pair driven by a mock source, and
it is the shortest way to see the shape:

| File | What it shows |
|---|---|
| `custom_adapter_src/custom_adapter.py` | `MockdbAdapter` and `MockdbModel` — the two classes, a client, `model_loader` and a full `obj_to_diffsync` |
| `custom_adapter_src/mock_db.json` | The five-device fixture the adapter reads |
| `config.yml` | The declared configuration |
| `package.yml` | The registry envelope that wraps it |

Read it for shape, then follow the steps below for the in-repository route. The example's own
adapter target is a repository-local path, which is development material rather than an
identity a deployed worker can register — see
[The current boundary](#the-current-boundary-for-adapters-outside-the-distribution).

Then pick a starting point:

| Situation | Start from |
|-----------|------------|
| REST or JSON API | Subclass `GenericrestapiAdapter` (`infrahub_sync/adapters/peeringmanager.py`) |
| Bespoke SDK or protocol | A fresh `DiffSyncMixin` adapter (`infrahub_sync/adapters/netbox.py`) |
| Learning the shape | `examples/custom_adapter/custom_adapter_src/custom_adapter.py` |

#### Step 2: Create the adapter module

Create `infrahub_sync/adapters/<name>.py` and define the two classes and a client:

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
environment and never from an inline setting value.

#### Step 3: implement `model_loader`

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
`reference` resolved to a peer `unique_id` — and always set `local_id`.
`examples/custom_adapter/custom_adapter_src/custom_adapter.py` has a complete version,
including the single and list reference cases.

#### Step 4: Implement write methods (destination only)

If the adapter can be a destination, implement `create`, `update` and `delete` on the model to
mutate the target system. A source-only adapter leaves these deferring to the base.

That covers `infrahub-sync sync`, the live compare-and-write path. Applying a **saved plan**
(`infrahub-sync apply`) goes through a separate surface — see Step 5.

#### Step 5: Implement the planned-write surface (optional, destination only)

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

Nothing else about the adapter degrades: `diff`, `sync` and plan review (`runs plan RUN_ID`)
all work unchanged. Only `apply` is unavailable. `infrahub` is the only one of the nine
adapters shipped in this repository that implements the surface today; the other eight refuse
an `apply` exactly as described above.

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
  existing.
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
[the destination write surface contract](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/specs/archive/001-plan-artifact-saved-apply/contracts/destination-write-surface.md).
`infrahub_sync/adapters/infrahub.py` is the reference implementation, and
[Planned writes and apply](../knowledge/planned-write-and-apply.md) is the knowledge page.

#### Step 6: Declare the adapter's capabilities

**This is the step that makes a package using your adapter admissible.** Configuration
validation does not import your module or inspect your class; it resolves the package's
`source.name` and `destination.name` against `BUILTIN_ADAPTER_CAPABILITIES` in
`infrahub_sync/configuration/capabilities.py`. An adapter with no entry there has no declared
configuration boundary, and every package naming it is refused.

Add one `AdapterConfigurationCapabilities` entry, keyed by the exact lowercase configuration
name:

```python
"mysystem": AdapterConfigurationCapabilities(
    adapter_name="mysystem",
    roles=_SOURCE_ONLY,                                   # or _BOTH for a destination
    allowed_settings=frozenset({"token", "url", "verify_ssl"}),
    credential_setting_paths=("token",),
    incremental_extraction=False,
),
```

The declaration is connection-free: it says what a package may declare, not what the adapter
can reach. Its own `__post_init__` enforces the rules, so a mistake fails at import:

- `adapter_name` must be a lowercase configuration name, and the lookup is exact rather than
  case-folded.
- At least one role, drawn from `source` and `destination`.
- A source-only adapter cannot declare destination write operations.
- Every credential path must sit inside `allowed_settings`, and paths must be unique.
- `destination_schema_validation` and `destination_schema_accessor` are one fact: declare both
  or neither.
- Set `incremental_extraction` only if the adapter really implements the cursor methods; the
  conformance tests hold this flag to the runtime overrides.

[Configuration foundation](../knowledge/configuration-foundation.md) explains the declaration
in full.

#### Step 7: Update the frozen expected set and satisfy setting conformance

Two product tests guard that registry, and both must be updated or satisfied in the same
change:

| Test | What it holds |
|---|---|
| `tests/configuration/test_contracts.py` | The frozen set of adapter names that must have a static declaration, plus the contract version and the destination write operations |
| `tests/configuration/test_adapter_setting_conformance.py` | That the settings your adapter module actually reads match the settings its declaration allows |

For the first, add your name to the expected set in
`test_all_bundled_adapter_modules_have_static_declarations`. For the second, make the module's
`self.settings` reads and the declared `allowed_settings` agree. A runtime knob you
deliberately keep out of registered packages goes in that test's refusal map, with a comment
saying why, rather than being quietly widened into `allowed_settings`.

Run them directly while iterating:

```bash
uv run pytest -q tests/configuration/test_contracts.py \
  tests/configuration/test_adapter_setting_conformance.py
```

#### Step 8: Write the schema mapping and the configuration

Create an example project directory with a `config.yml` that selects the adapter and maps
resources to destination models:

```yaml
---
name: mysystem-example

source:
  name: mysystem
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

Omit `order` — it is computed from `reference` edges by
`infrahub_sync/dependency_graph.py`. See [schema mapping](../knowledge/schema-mapping.md) for
fields, filters and transforms.

#### Step 9: Wrap it in a registry envelope

A registered package is the envelope, not the bare configuration. Add `package.yml` beside
`config.yml`, with the configuration nested under `configuration:`:

```yaml
format_version: 1
configuration:
  name: mysystem-example
  source:
    name: mysystem
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

The two files must agree: a product test parses every `examples/*/package.yml` and asserts its
`configuration` block equals the sibling `config.yml`. See
[Configuration package](../../configuration-package.mdx) for the envelope's full reference.

#### Step 10: Add incremental support (optional)

If the source can filter by change, override `cursor_tier_for` to return the right `CursorTier`
and implement `list_changed_since`, and optionally `list_existing_ids`. Set
`incremental_extraction=True` in the capability entry at the same time. See
[incremental sync and cache](../knowledge/incremental-and-cache.md). Skip this and the adapter
performs full extracts.

#### Step 11: Add an example and document the environment variables

Put the directory under `examples/<system>_to_infrahub/` — or `infrahub_to_<system>/` — with
its `config.yml` and `package.yml`, and document the required environment variables and the
install extra for the optional SDK.

#### Step 12: add tests

Write unit tests under `tests/adapters/` that mock the client. See
[Testing an adapter](testing-an-adapter.md) and the rules in
[Testing adapters](../guidelines/testing-adapters.md). The offline default must stay green:

```bash
uv run invoke tests.tests-unit
```

[Testing tiers](../guidelines/testing-tiers.md) explains which further suite, if any, your
change needs.

#### Step 13: Add a documentation page

Create a page under `docs/docs/adapters/` covering the overview, configuration keys,
environment variables, an example configuration and the common errors. Add it to
`docs/sidebars.ts`, and lint it:

```bash
uv run rumdl check docs/docs/adapters/
```

### The worked flow: register to convergence

Run the local gates first:

```bash
uv run invoke format
uv run invoke lint
uv run invoke tests.tests-unit
```

Then drive the package through the Sync API. Every command below is a client of that service:
the CLI reads no source system and no local plan file, so `INFRAHUB_SYNC_API_URL` and a token
must be set.

```bash
export INFRAHUB_SYNC_API_URL=https://sync.example.com
export INFRAHUB_SYNC_API_TOKEN=<token>
```

**Register the package.** The path argument is positional, and it is the envelope:

```bash
uv run infrahub-sync configs register examples/mysystem_to_infrahub/package.yml \
  --reason "register the mysystem example"
```

Registration prints the service-issued configuration identity and its first version. Both are
positional arguments from here on.

**Validate the registered version.** This re-checks the stored version against the *current*
capability declarations, so it can report a finding on a package that registered cleanly
earlier:

```bash
uv run infrahub-sync configs validate CONFIG_ID 1
```

**Create a plan run.** `diff` takes its identity as options, and an audit reason is required:

```bash
uv run infrahub-sync diff --config-id CONFIG_ID --version 1 \
  --reason "review the new adapter"
```

**Review the saved plan**, and copy the `plan_checksum` from the output:

```bash
uv run infrahub-sync runs plan RUN_ID --detail
```

Against an **empty destination that matches the mapping**, the worked example produces five
`InfraDevice` creates — one per record in `mock_db.json`. A destination that already holds
those devices produces fewer operations, or none. Do not read a different count as a failure
without checking what the destination held first.

**Apply the reviewed plan.** The run identity is positional; the checksum you just reviewed and
an audit reason are required options. The service verifies the checksum before any worker
executes:

```bash
uv run infrahub-sync apply RUN_ID \
  --expected-checksum PLAN_CHECKSUM \
  --reason "apply the reviewed plan"
```

**Verify convergence.** Create a new plan over the same registered version. It should report
zero creates, zero updates and zero deletes. If it does not, inspect the worker's installed
adapter, the destination schema and the source records before applying anything else.

`configs register`, `configs validate` and `runs plan` write nothing to the destination; `diff`
plans against both sides and writes nothing. Run `sync` or `apply` only with explicit approval
against a known-safe target. [Run a sync](../../running-a-sync.mdx) covers wait, idempotency,
delete and failure behavior.

### The current boundary for adapters outside the distribution

**At this revision, an adapter installed outside the distribution has no admitted execution
path.** A package naming it is refused at registration, before anything is persisted. This is a
confirmed product limitation, recorded here so you do not discover it after writing a
connector. It is not a configuration mistake you can work around.

What the evidence shows, reproduced read-only and in-process against the shipped
`examples/custom_adapter/package.yml` at revision `61b6a1b`:

1. `BUILTIN_ADAPTER_CAPABILITIES` in `infrahub_sync/configuration/capabilities.py` is a
   read-only mapping with exactly nine keys — the nine bundled adapters. There is no
   registration hook, entry-point scan or plugin path that adds a tenth.
2. Validation resolves capabilities from the package's **`source.name`** — the short
   configuration name — and never from `source.adapter`. So an installed dotted import target
   or entry point does not make the package admissible on its own; the missing piece is the
   capability declaration, and only a bundled adapter has one.
3. The refusal is produced in `_accumulate` in `infrahub_sync/configuration/validation.py`,
   which emits one finding for the unresolved role and judges nothing deeper inside it:

   ```text
   severity=error  code=missing-adapter  location=/configuration/source
   adapter 'mockdb' has no configuration capability declaration
   ```

4. **Registration is the refusing surface.** `configs.register`
   (`infrahub_sync/product_store/configs.py`) calls `create_configuration` on the durable
   projection, which runs `validate_package_credentials` *before* it writes any row. That
   raises on the first error, and `register` converts it into the public validation error
   carrying the finding above. Over HTTP the route answers `422` with the `validation` reason
   and marks the refusal as proven to have had no effect. `configs validate` reports the same
   `missing-adapter` code for an already-stored version.

So: `examples/custom_adapter/package.yml` **does not run register-to-apply as shipped**. It is
a shape reference and a source fixture. The repository-local adapter target it declares is
development material; it is not an installed worker identity, and installing the adapter
somewhere a worker can import it does not change the outcome, because the refusal is about the
missing capability declaration rather than about whether the class can be imported.

The supported route today is the in-repository one this guide teaches: add the adapter under
`infrahub_sync/adapters/`, add its capability entry, and update the two conformance tests. That
is what Steps 6 and 7 are for.

For the development-time plugin loader and what it is and is not, see
[Local adapters](../../adapters/local-adapters.mdx).

### Quality checklist

- [ ] Adapter inherits `DiffSyncMixin` / `DiffSyncModelMixin`, mixin first, with a `type`.
- [ ] `model_loader` filters and transforms through the model mixin; `obj_to_diffsync` sets `local_id`.
- [ ] Decided whether the adapter implements the planned-write surface — **both** `new_peer_resolver` and `apply_planned_operation`; if it does not, confirmed that `apply` refuses cleanly and that `sync` is the documented path for it.
- [ ] An `AdapterConfigurationCapabilities` entry added to `BUILTIN_ADAPTER_CAPABILITIES`, with roles, allowed settings and credential paths that match the module.
- [ ] `tests/configuration/test_contracts.py` expected set updated, and `tests/configuration/test_adapter_setting_conformance.py` satisfied.
- [ ] Optional SDK imported with `# ty: ignore[unresolved-import]`; credentials from environment references; no secrets logged or committed.
- [ ] `uv run invoke format` and `uv run invoke lint` are clean; `uv run ty check .` exits 0.
- [ ] `configs register`, `configs validate` and `diff` succeed for the example, and the plan reviews as expected against a known destination state.
- [ ] Unit tests added under `tests/adapters/`; `uv run invoke tests.tests-unit` passes offline.
- [ ] Example added under `examples/` with both `config.yml` and `package.yml`; environment variables documented.
- [ ] Documentation page added under `docs/docs/adapters/` and listed in `docs/sidebars.ts`.

### Related resources

- [Adapter anatomy](../knowledge/adapter-anatomy.md) — the classes and the contract.
- [Writing an adapter](../guidelines/writing-an-adapter.md) — the rules.
- [Testing an adapter](testing-an-adapter.md) — the tests to add.
- [Testing tiers](../guidelines/testing-tiers.md) — which suite proves what.
- [Configuration foundation](../knowledge/configuration-foundation.md) — the capability declaration.
- [Repository tour](../knowledge/repository-tour.md) — where each of these modules lives.
