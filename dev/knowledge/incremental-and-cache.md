# Incremental sync and cache

> Part of: `dev/knowledge/` | Related: [Adapter anatomy](adapter-anatomy.md), [Sync architecture](sync-architecture.md)

A full sync extracts every object from both sides on every run. For large systems that is
slow and wasteful, so infrahub-sync can run *incrementally*: extract only what changed since
the last run, and reuse a cached diff plan. Incremental support is opt-in per adapter and
per model — an adapter that does nothing still works, it just always does a full extract.

## Cursor tiers

`cursor_tier_for(model_name)` declares the strongest extraction strategy the source supports
for a model. It returns a `CursorTier` (an `IntEnum`, defined in
`infrahub_sync/cache/cursors.py`):

| Tier | Value | Meaning |
|------|-------|---------|
| `NONE` | 0 | The source cannot filter by change; always full extract. The default. |
| `ID` | 1 | The source exposes a stable id set; changes are detected by comparing ids. |
| `TIMESTAMP` | 2 | The source can filter by modification time (for example NetBox / Nautobot `last_updated__gte`); extract only changed-since records. |

Higher tiers extract less data. NetBox returns `TIMESTAMP` for mapped kinds and `NONE`
otherwise; an adapter with no incremental support inherits the `NONE` default from
`DiffSyncMixin`.

## What an adapter implements

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

## Full re-extraction cadence

`IncrementalConfig.full_resync_every` (default `10`) is declared under `incremental` in
`config.yml` and currently governs nothing: the run counter it compared against was written
only by a code path that had no production caller, and that path is gone. Every current
caller extracts in full, so no cadence decision is reachable. The key is retained for the
incremental-extraction work that will supply the counter durably.

## The diff plan and the cache

Potenda can separate computing a diff from applying it:

- `write_plan(diff)` serializes the diff to a Parquet **plan** on disk.
- `apply_plan()` reads that plan back and syncs without re-extracting either side.

This lets you review a plan before applying it, or compute on one host and apply on another.
Cached side snapshots (also Parquet) and cursor state live alongside the plan.

The cache root defaults to `<cwd>/.infrahub-sync-cache/<sync_name>/`, with each run under its
own `<run_id>/`. Set `INFRAHUB_SYNC_CACHE_DIR` to relocate it (for example to a shared volume);
the path may not contain `..` traversal segments. Cursor state is written by
`persist_cursors_for_run()` at the end of a successful run and read at the start of the next.

## The row-count baseline

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

## See also

- [Adapter anatomy](adapter-anatomy.md) — where these methods sit in the contract.
- [Sync architecture](sync-architecture.md) — how Potenda drives load, diff, and sync.
- [Testing an adapter](../guides/testing-an-adapter.md) — testing the incremental methods.
