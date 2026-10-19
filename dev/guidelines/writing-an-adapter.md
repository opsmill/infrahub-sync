# Writing an adapter

> Part of: `dev/guidelines/` | Related: [Adapter anatomy](../knowledge/adapter-anatomy.md), [Adding an adapter](../guides/adding-an-adapter.md)

Rules for writing an adapter connector. They assume you understand the
[adapter anatomy](../knowledge/adapter-anatomy.md); this document is about *how* to write the
code, not what the pieces are. The repository-wide standards in `AGENTS.md` still apply —
this narrows them to adapters.

## Inherit the mixins, in order

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

## Reuse the loading helpers

**Always filter and transform through the model mixin:**

```python
# ✅ Good — honors the schema mapping consistently
filtered = model.filter_records(records=records, schema_mapping=element)
records = model.transform_records(records=filtered, schema_mapping=element)

# ❌ Bad — re-implements filtering, diverges from every other adapter
records = [r for r in records if r["status"] == "active"]
```

Filtering and transforming are defined once on `DiffSyncModelMixin`. Re-implementing them in
an adapter means filters and transforms in `config.yml` behave differently per system. For a
REST source, subclass `GenericrestapiAdapter` instead of writing HTTP handling from scratch —
override only the settings defaults you need (see `infrahub_sync/adapters/peeringmanager.py`).

## Type and document the public surface

**Always type new code and give public classes and methods a concise docstring:**

The codebase is clean under `ty` with no `[[tool.ty.overrides]]` blocks. Do not add overrides
to mask errors — fix the type, or use a targeted `# ty: ignore[<rule>]` with a short reason at
the call site. New code must be Ruff-clean and pass `uv run invoke lint`.

## Raise specific exceptions

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

## Log with structlog, never secrets

**Always use `structlog`; never `print`, and never log credentials:**

Include useful context — endpoint, model name, object counts — but never tokens, passwords,
or full auth headers. The same rule applies to exception messages and tracebacks.

> Some example adapters under `examples/` use `print` for illustration. Production adapters in
> `infrahub_sync/adapters/` use structured logging.

## Handle optional dependencies and credentials

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

## Anti-patterns

| Anti-pattern | Do instead |
|--------------|------------|
| Inline filtering / transforming in the adapter | `model.filter_records` / `model.transform_records` |
| Hand-rolled HTTP for a REST source | Subclass `GenericrestapiAdapter` |
| `except Exception: pass` | Catch the specific error; surface the rest |
| `print()` for diagnostics | `structlog` with context |
| Hardcoded URL or token | Environment variable, then `settings` |
| Declaring `cursor_tier_for` without `list_changed_since` | Implement both, or leave the tier `NONE` |

## See also

- [Adapter anatomy](../knowledge/adapter-anatomy.md) — the contract these rules apply to.
- [Testing adapters](testing-adapters.md) — what to test once it is written.
- [Adding an adapter](../guides/adding-an-adapter.md) — the end-to-end procedure.
