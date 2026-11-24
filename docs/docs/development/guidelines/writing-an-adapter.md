---
title: "Writing an adapter"
---

## Writing an adapter

> Part of: Develop > Guidelines | Related: [Adapter anatomy](../knowledge/adapter-anatomy.md), [Adding an adapter](../guides/adding-an-adapter.md)

Rules for writing an adapter connector. They assume you understand the
[adapter anatomy](../knowledge/adapter-anatomy.md); this document is about *how* to write the
code, not what the pieces are. The repository-wide standards in `AGENTS.md` still apply —
this narrows them to adapters.

### Inherit the mixins, in order

**Always declare the mixin before the DiffSync base:**

```python
# ✅ Good — mixin first, so its methods win
class MyAdapter(DiffSyncMixin, Adapter): ...
class MyModel(DiffSyncModelMixin, DiffSyncModel): ...

# ❌ Bad — DiffSync base shadows the mixin contract
class MyAdapter(Adapter, DiffSyncMixin): ...
```

Set a `type` class attribute (for example `type = "NetBox"`); it is used in log lines and to
decide whether the adapter is acting as the source.

### Reuse the read loop

**Always implement `list_records` and `record_to_data`, and keep the base-class `model_loader`:**

```python
# ✅ Good — the base-class read loop filters, transforms and admits each record
def list_records(self, model_name, element):
    return self.client.get(element.mapping)

# ❌ Bad — re-implements filtering, diverges from every other adapter
def list_records(self, model_name, element):
    return [r for r in self.client.get(element.mapping) if r["status"] == "active"]
```

On a source read, the base-class `model_loader` applies each mapping entry's filters of
`config.yml`, and `load_record` applies its transforms and records each source record it cannot
build in `load_report`. A destination read applies neither. Filtering,
transforming or catching errors in an adapter means filters and transforms behave differently
per system, and the plan does not list the records the adapter skipped. For a
REST source, subclass `GenericrestapiAdapter` instead of writing HTTP handling from scratch —
override only the settings defaults you need (see `infrahub_sync/adapters/peeringmanager.py`).

### Type and document the public surface

**Always type new code and give public classes and methods a concise docstring:**

The codebase is clean under `ty` with no `[[tool.ty.overrides]]` blocks. Do not add overrides
to mask errors — fix the type, or use a targeted `# ty: ignore[<rule>]` with a short reason at
the call site. New code must be Ruff-clean and pass `uv run invoke lint`.

### Raise specific exceptions

**Always raise a specific exception with a clear message; never swallow errors broadly:**

```python
# ✅ Good
if not schema_element:
    msg = f"Schema mapping for model '{reference}' not found."
    raise ValueError(msg)

# ❌ Bad
try:
    ...
except Exception:
    pass
```

Handle the failure modes that matter for a connector explicitly: authentication (401 / 403),
timeouts, empty pages, and pagination. Let unexpected errors surface rather than hiding them.

### Log with structlog, never secrets

**Always use `structlog`; never `print`, and never log credentials:**

```python
import structlog

logger = structlog.get_logger(__name__)

logger.info("devices read", endpoint="dcim/devices", count=len(devices))
```

Create the logger with `structlog.get_logger(__name__)`, not `logging.getLogger`. The event
reaches the standard `logging` handlers with the key-value pairs in the message text, here
`devices read endpoint=dcim/devices count=3`. A `"%s"`-style message with positional
arguments is also accepted.

Include useful context — endpoint, model name, object counts — but never tokens, passwords,
or full auth headers. The same rule applies to exception messages and tracebacks.

> Some example adapters under `examples/` use `print` for illustration. Production adapters in
> `infrahub_sync/adapters/` use structured logging.

### Handle optional dependencies and credentials

**Always treat the upstream SDK as an optional dependency and read secrets from the
environment:**

```python
import pynetbox  # ty: ignore[unresolved-import]  # optional dep, see pyproject extras

url = os.environ.get("NETBOX_ADDRESS") or settings.get("url")
token = os.environ.get("NETBOX_TOKEN") or settings.get("token")
```

- The SDK (`pynetbox`, `pynautobot`, …) is not a core dependency. Import it at module top with
  the `# ty: ignore[unresolved-import]` comment, and document the install extra.
- Resolve credentials from environment variables first, falling back to `settings`. Never
  hardcode, print, commit, or log a secret. Keep example configs sanitized.
- An adapter that gains a credential setting needs a credential variable that the Compose
  worker receives, because Compose passes only the variables the file names. Use the name the
  adapter's documentation page and examples already use; otherwise `<ADAPTER>_<SETTING>`.
  Change these places in one commit:
    - the `sync-worker` environment of the root `docker-compose.yml`, as
      `INFRAHUB_SYNC_CREDENTIAL_<NAME>: "${INFRAHUB_SYNC_CREDENTIAL_<NAME>:-}"`. Two tests read
      it: `tests/configuration/test_conversion_worker_variables.py` compares its names and
      their order with the constant below, and
      `test_the_worker_holds_exactly_the_documented_credential_variables` in
      `tests/compose/test_compose_contract.py` compares its names with
      `ADAPTER_CREDENTIAL_NAMES` and with a fixed count, which changes too;
    - the same place in `infrahub_sync/testcontainers/docker-compose.yml`.
      `tests/testcontainers/test_packaging.py` fails when the copy is not byte-equal to the
      root file;
    - `COMPOSE_WORKER_CREDENTIAL_SUFFIXES` in `infrahub_sync/configuration/credentials.py`, in
      the root file's order;
    - `ADAPTER_CREDENTIAL_NAMES` in `tests/compose/test_compose_contract.py`.
      `test_the_list_names_every_credential_setting_of_every_bundled_adapter` fails when a
      credential setting of a bundled adapter has no name there;
    - the table "Credential variables the worker receives" in
      `docs/docs/compose-deployment.mdx`.
      `test_the_page_documents_every_variable_the_compose_file_reads` in
      `tests/compose/test_documentation.py` fails when the page does not name a variable that
      the root file reads; it does not check in which table the name appears;
    - the `sync-worker` environment of `development/docker-compose.yml`. No test compares this
      list with the root file's, so change it by hand.

  [Pin a code constant to the deployment file it mirrors](testing.md#pin-a-code-constant-to-the-deployment-file-it-mirrors)
  is the rule behind the first test.

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

### Implement the planned-write surface as a whole, or not at all

Applies to the Infrahub destination only in v1: both members are typed with Infrahub's concrete
`PeerResolver`, so another destination cannot conform statically without importing the Infrahub
adapter. An adapter-neutral resolver type is a tracked follow-up — raise it rather than working
around the import.

**Always provide both members if a destination adapter supports applying a saved plan:**

```python
# ✅ Good — both members, so the pre-write gate passes and `ty` checks the call sites
class MyAdapter(DiffSyncMixin, Adapter):
    def new_peer_resolver(self) -> PeerResolver: ...
    def apply_planned_operation(
        self, *, operation: PlannedOperation, peers: PeerResolver
    ) -> str: ...
```

`PlannedWriteDestination` is a `runtime_checkable` Protocol, so the engine's gate checks **member
presence only, never signatures**. An adapter with only one member fails the gate before writing.
If your adapter is not a planned-write destination, implement neither member and let the
gate refuse it by name. See
[Planned writes and apply](../knowledge/planned-write-and-apply.md).

Within `apply_planned_operation`:

- **Refuse; never silently drop.** An unresolvable or ambiguous peer raises and fails the run, naming the
  peer, the referring operation and the operator's next action. A dropped relationship makes the applied
  set differ from the reviewed set exactly as a dropped operation does.
- **Do not re-render a whole node to write part of it.** Rendering a node the SDK considers existing
  nulls unmapped optional cardinality-one relationships. Write only the fields you mean to change.
- **Do not read the comparison store.** A saved-plan apply has no comparison; resolve through the
  per-apply resolver, which the destination itself builds.
- **Never split a DiffSync `unique_id` to recover identifiers.** Split a schema path if you must; never a
  data value. Plan operations carry peer identities as nested `{peer_kind, identity}` pairs precisely so
  you do not have to.

### Keep model writes inherited

Models inherit `create`, `update` and `delete` from DiffSync. V3 does not call those methods.
The service composes `sync` from plan, verify and apply. During apply, the engine calls the
destination adapter's `apply_planned_operation`, with a resolver from `new_peer_resolver`.
If a destination does not implement both methods, the pre-write gate refuses `sync` and `apply`.

### Anti-patterns

| Anti-pattern | Do instead |
|--------------|------------|
| Implementing one of the two planned-write members | Implement both, or neither |
| Whole-node re-render to change one relationship | Write `id` plus only the fields being replaced |
| Splitting a `unique_id` on `__` to get identifiers | Read the nested `{peer_kind, identity}` pair |
| Silently dropping an unresolvable peer on the apply path | Raise, naming the peer and the next action |
| Implementing model `create` / `update` / `delete` for V3 writes | Implement the planned-write surface on the adapter |
| Inline filtering / transforming in the adapter, or an adapter-own `model_loader` | `list_records` and `record_to_data`, with the base-class read loop |
| Hand-rolled HTTP for a REST source | Subclass `GenericrestapiAdapter` |
| `except Exception: pass` | Catch the specific error; surface the rest |
| `print()` for diagnostics | `structlog` with context |
| Hardcoded URL or token | Environment variable, then `settings` |
| Declaring `cursor_tier_for` without `list_changed_since` | Implement both, or leave the tier `NONE` |

### See also

- [Adapter anatomy](../knowledge/adapter-anatomy.md) — the contract these rules apply to.
- [Testing adapters](testing-adapters.md) — what to test once it is written.
- [Adding an adapter](../guides/adding-an-adapter.md) — the end-to-end procedure.
- [Planned writes and apply](../knowledge/planned-write-and-apply.md) — the write surface these
  planned-write rules apply to.
