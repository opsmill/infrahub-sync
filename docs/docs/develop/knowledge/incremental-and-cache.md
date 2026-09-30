---
title: "Incremental sync and cache"
---

## Incremental sync and cache

> Part of: Develop > Knowledge | Related: [Adapter anatomy](adapter-anatomy.md), [Sync architecture](sync-architecture.md)

A full sync extracts every object from both sides on every run. For large systems that is
slow and wasteful, so infrahub-sync can run *incrementally*: extract only what changed since
the last run, and reuse a cached diff plan. Incremental support is opt-in per adapter and
per model — an adapter that does nothing still works, and always does a full extract.

### Cursor tiers

`cursor_tier_for(model_name)` declares the strongest extraction strategy the source supports
for a model. It returns a `CursorTier` (an `IntEnum`, defined in
`infrahub_sync/cache/cursors.py`):

| Tier | Value | Meaning |
|------|-------|---------|
| `NONE` | 0 | The source cannot filter by change; always full extract. The default. |
| `PAGE_TOKEN` | 1 | Intended for a source that paginates with an opaque `?next=` token, so a crashed extraction could resume mid-page instead of restarting. No product adapter returns this tier yet, and no product entry point persists a cursor across a restart, so the resume behavior is not currently reachable. |
| `TIMESTAMP` | 2 | The source can filter by modification time (for example NetBox / Nautobot `last_updated__gte`); extract only changed-since records. |
| `INFRAHUB_DIFF` | 3 | Intended for the Infrahub destination's diff API to return the changed records directly. No adapter returns this tier yet; the Infrahub adapter returns `TIMESTAMP` for kinds present in its schema and `NONE` otherwise. |

`TIMESTAMP` and `INFRAHUB_DIFF` would extract less data than `NONE` by filtering at the source
or destination — but only on the per-resource incremental branch described below, which no
current entry point reaches, so every current `plan` and `sync` run still extracts every resource in full.
`PAGE_TOKEN` is about resuming a paginated extraction, not reducing what it reads, so it does
not fit that ordering either. NetBox returns `TIMESTAMP` for mapped kinds and `NONE` otherwise;
an adapter with no incremental support inherits the `NONE` default from `DiffSyncMixin`.

### What an adapter implements

Three methods, layered on top of `model_loader`:

- `cursor_tier_for(model_name)` — return the tier. This is the switch that turns incremental
  on for a model.
- `list_changed_since(model_name, cursor)` — **required when the tier is not `NONE`.** Yield
  the raw records changed since `cursor`, in the same shape `model_loader` feeds to
  `self.add(...)`. `DiffSyncMixin` raises `NotImplementedError` until you override it.
- `list_existing_ids(model_name)` — optional. Yield the current `unique_id` strings present
  in the source so deletions can be detected between runs. Without it, a warm run cannot tell
  that an object disappeared.

`CursorState` (also in `cache/cursors.py`) carries the tier and the saved value (a timestamp
or id watermark) from the previous run.

### Full re-extraction cadence

`IncrementalConfig.full_resync_every` (default `10`) is declared under `incremental` in
`config.yml` and currently governs nothing: the run counter it compared against was written
only by a code path that had no production caller, and that path is gone. Every current
caller extracts in full, so no cadence decision is reachable. The key is retained for the
incremental-extraction work that will supply the counter durably.

### The diff plan and the cache

Potenda separates computing a diff from applying it:

- `write_plan(diff)` writes two files. `plan.parquet` is a row format kept for operators to
  query. The [saved plan artifact](plan-artifact.md) under `<run_dir>/plan/` is what apply uses.
- `apply_plan()` applies the saved plan artifact without extracting either side. It never reads
  `plan.parquet`, and it refuses a run directory that contains only that file.

This separation is what lets you review a saved plan before you apply it. Cached side
snapshots (also Parquet) and cursor state are stored in the same run directory.

The cache root defaults to `<cwd>/.infrahub-sync-cache/<sync_name>/`, with each run under its
own `<run_id>/`. Set `INFRAHUB_SYNC_CACHE_DIR` to relocate it (for example to a shared volume);
the path may not contain `..` traversal segments.

`source_load()` and `destination_load()` call `load_one_side()` on every `plan` and `sync`
extract (`potenda/__init__.py:482,491`), so the method itself always runs. Every current entry
point runs with `full_extract=True`. The `execute_run()` default is `True` (`execution.py:1095`),
and the managed service's `execute_run` call (`service/flow.py:201`) does not override it. The
`BaselineWriteback` at `service/flow.py:288` only records that fact in metadata. So on every
current path `load_one_side()` calls `adapter.load()` and returns without reading any cursor
(`potenda/__init__.py:438-440`).

Only a direct `execute_run(full_extract=False)` call, made with a prior successful run present
and a matching schema sub-hash, reaches the per-resource branch instead
(`potenda/__init__.py:446-477`). There, `load_cursors()` returns `{}` (`cache/incremental.py:99-114`)
because no product code has ever written `cursors.json`: `persist_cursors_for_run()`
(`potenda/__init__.py:954`) is the direct engine method that would write it, and nothing calls
it. With no saved cursor, that branch still falls to `model_loader` for every resource, one
resource at a time; `hydrate_from_parquet()` plus `list_changed_since()` run only for a
resource that does have a saved cursor, which does not happen on any current path.

`verify` and `apply` do not extract at all: verify reads the saved plan back, and apply opens
and applies it through `PlanApplier.open_existing`.

### The row-count baseline

A buggy source or an auth failure can return far fewer objects than reality, which would make
a later sync delete most of the destination. The durable input for detecting that is the
configuration baseline: a successful managed apply or sync records the source row counts its
plan was computed against, in the same PostgreSQL transaction that stores the run's success.
A failed or ambiguous run leaves the previous baseline standing, and a read stage never
advances it.

Nothing reads the baseline yet. There is no row-count refusal in the managed write path, no
lookup before dispatch, no operator override, and no lockout rule; adding one needs a fixed
placement, a missing-baseline rule, a failure class, and an operator contract. The
`RowcountGuardrail` comparison in `cache/guardrails.py` is that future feature's primitive
and has no caller.

### See also

- [Adapter anatomy](adapter-anatomy.md) — where these methods sit in the contract.
- [Sync architecture](sync-architecture.md) — how Potenda drives load, diff, and sync.
- [Testing an adapter](../guides/testing-an-adapter.md) — testing the incremental methods.
