# Testing an adapter

> Part of: `dev/guides/` | Related: [Testing adapters](../guidelines/testing-adapters.md), [Adding an adapter](adding-an-adapter.md)

Step-by-step guide for writing and running an adapter's tests. It covers the mechanics —
mocking, fixtures, commands — while [Testing adapters](../guidelines/testing-adapters.md)
defines what coverage is required.

## When to test

Every new or changed adapter needs tests before it is merged. Write them alongside the
adapter (Step 8 of [Adding an adapter](adding-an-adapter.md)), not afterward. The default
suite must run offline, so tests mock the upstream system rather than calling it.

## Prerequisites

- A working dev environment (`uv sync`).
- The adapter under test, with its `model_loader` and any incremental methods.
- Familiarity with `unittest.mock` and pytest fixtures.
- The existing tests as templates: `tests/adapters/test_netbox_incremental.py` and
  `tests/test_diffsync_mixin_contract.py`.

## Steps

### Step 1: Create the test module

Add `tests/adapters/test_<adapter>_*.py` — for example `test_mysystem_incremental.py` for the
cursor methods and `test_mysystem_loader.py` for conversion.

### Step 2: Guard the optional dependency

If the adapter hard-imports an optional SDK, skip the whole module when it is absent so test
collection stays green:

```python
import pytest

pytest.importorskip("pynetbox")  # at module top, before importing the adapter
```

### Step 3: Build a fake client

Stub the upstream client with `MagicMock` and feed it sample records shaped like the real API
response. Construct the adapter, then replace its client with the mock:

```python
from unittest.mock import MagicMock
from infrahub_sync.adapters.mysystem import MysystemAdapter

def make_adapter(config):
    adapter = MysystemAdapter(target="source", adapter=config.source, config=config)
    adapter.client = MagicMock()
    return adapter
```

Keep the sample records and a minimal `SyncConfig` (with a `schema_mapping`) in fixtures so
several tests share them. `tests/adapters/test_netbox_incremental.py` shows the concrete
construction for a real adapter — mirror it.

### Step 4: Test the conversion path

Drive `model_loader` (or `obj_to_diffsync`) with the fake records and assert the resulting
models. Check that fields map to the right names, that `local_id` and `identifiers` are set,
and that `reference` fields resolve to peer `unique_id`s:

```python
def test_loader_maps_fields(adapter, device_model):
    adapter.client.get.return_value = [{"name": "rtr1", "device_type": "qfx"}]
    adapter.model_loader("InfraDevice", device_model)
    obj = adapter.get(device_model, "rtr1")
    assert obj.type == "qfx"
```

Add cases for filters (kept vs dropped) and transforms (native-typed output).

### Step 5: Test the incremental contract

If the adapter declares cursor support, assert each method:

```python
from infrahub_sync.cache.cursors import CursorState, CursorTier

def test_cursor_tier(adapter):
    assert adapter.cursor_tier_for("InfraDevice") == CursorTier.TIMESTAMP
    assert adapter.cursor_tier_for("Unmapped") == CursorTier.NONE

def test_list_changed_since_uses_change_filter(adapter):
    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2024-01-01T00:00:00Z")
    list(adapter.list_changed_since("InfraDevice", cursor))
    # assert the client was queried with the change filter, e.g. last_updated__gte
```

Also assert that declaring a non-`NONE` tier without implementing `list_changed_since` raises
`NotImplementedError` — the mixin defaults are covered in `tests/test_diffsync_mixin_contract.py`.

### Step 6: Test the edge cases

Cover empty result sets, pagination, and that authentication failures (401 / 403), timeouts,
and unknown model names raise clear errors rather than passing silently.

### Step 7: Run the suite

```bash
uv run pytest -q tests/adapters/test_mysystem_loader.py
uv run pytest -q                # full offline suite
```

## Integration tests

Tests that talk to a live system go under `tests/integration/`, marked so they are opt-in:

```python
import pytest

@pytest.mark.integration
def test_live_load(): ...
```

Run them explicitly and only when credentials are available:

```bash
uv run pytest -m integration
```

Keep them out of the default run — `uv run pytest -q` must pass with no network and no secrets.

## Verification

- `uv run pytest -q` passes offline, with no live system and no credentials.
- The module skips cleanly (not errors) when the optional SDK is not installed.
- `uv run invoke lint` is clean on the new test files.

## Quality checklist

- [ ] Upstream client mocked; no network in the default suite.
- [ ] Conversion covered: field mapping, `local_id`, identifiers, references (single and list).
- [ ] Filters and transforms covered.
- [ ] Cursor methods covered (tier, change filter, existing ids) if the adapter is incremental.
- [ ] Edge cases covered: empty, pagination, 401/403, timeout, unknown model.
- [ ] `pytest.importorskip` guards an optional-dependency import.
- [ ] Live tests marked `@pytest.mark.integration` under `tests/integration/`.

## Related resources

- [Testing adapters](../guidelines/testing-adapters.md) — the required coverage and conventions.
- [Adding an adapter](adding-an-adapter.md) — where testing fits in the full procedure.
- [Incremental sync and cache](../knowledge/incremental-and-cache.md) — the behavior to test.
