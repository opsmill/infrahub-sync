# Testing adapters

> Part of: `dev/guidelines/` | Related: [Testing an adapter](../guides/testing-an-adapter.md), [Adapter anatomy](../knowledge/adapter-anatomy.md)

Rules for what an adapter's tests must cover and how they are written. For the mechanics of
writing and running them — fixtures, mocking, commands — see
[Testing an adapter](../guides/testing-an-adapter.md).

## Mock the upstream; never require a live server

**Always stub the upstream client so the default suite runs offline:**

```python
# ✅ Good — controlled responses, deterministic, fast
api = MagicMock()
api.dcim.devices.filter.return_value = [ ... ]

# ❌ Bad — needs a real NetBox, flaky, slow, leaks credentials
api = pynetbox.api(url=os.environ["NETBOX_URL"], token=...)
```

The unit suite must pass with no network access and no secrets. Tests that need a live system
are integration tests (see below).

## Cover the conversion path

**Always test that records become correct DiffSync models:**

- `model_loader` (or the `obj_to_diffsync` helper) maps fields to the right destination names.
- Filters keep and drop the right records.
- Transforms produce native-typed values.
- `identifiers` and `local_id` are populated, and `reference` fields resolve to peer
  `unique_id`s — including the list-reference case.

## Cover the incremental contract

**Always test the cursor methods an adapter declares:**

- `cursor_tier_for` returns the expected tier for mapped kinds and `CursorTier.NONE` for
  unmapped ones.
- `list_changed_since` issues the correct change filter (for example `last_updated__gte`) and
  yields records in `model_loader` shape.
- `list_existing_ids` yields the current `unique_id`s.
- An adapter that declares a non-`NONE` tier but omits `list_changed_since` must fail — assert
  the `NotImplementedError`. `tests/test_diffsync_mixin_contract.py` covers the mixin defaults;
  per-adapter tests cover the overrides.

## Cover the error and edge cases

**Always test the failure modes a connector actually hits:**

- Empty result sets and pagination across multiple pages.
- Authentication failures (401 / 403) and timeouts surface as clear errors, not silent passes.
- Unknown model names raise rather than returning empty.

## Skip cleanly when the optional dependency is absent

**Always guard a module that hard-imports an optional SDK:**

```python
import pytest

pytest.importorskip("pynetbox")  # module-level: skip, don't error, when the dep is missing
```

This keeps collection green in environments that did not install that adapter's extra.

## Keep tests atomic and integration tests opt-in

**Always isolate one behavior per test and mark live tests:**

- One assertion target per test; parametrize config-parsing and mapping cases instead of
  looping inside a test.
- Place unit tests under `tests/adapters/` named `test_<adapter>_*.py`.
- Mark anything that talks to a real system `@pytest.mark.integration` and keep it under
  `tests/integration/` so the default `uv run pytest -q` stays offline.

## See also

- [Testing an adapter](../guides/testing-an-adapter.md) — fixtures, mocking, and commands.
- [Writing an adapter](writing-an-adapter.md) — the code these tests exercise.
- [Incremental sync and cache](../knowledge/incremental-and-cache.md) — the cursor behavior to test.
