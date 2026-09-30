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
| `TIMESTAMP` | 2 | The source can filter by modification time (for example NetBox / Nautobot `last_updated__gte`). A safe bound is required before using this filter. |
| `INFRAHUB_DIFF` | 3 | Intended for the Infrahub destination's diff API to return the changed records directly. No adapter returns this tier yet; the Infrahub adapter returns `TIMESTAMP` for kinds present in its schema and `NONE` otherwise. |

`TIMESTAMP` and `INFRAHUB_DIFF` would extract less data than `NONE` by filtering at the source
or destination — but only on the per-resource incremental branch described below, which no
current entry point reaches, so every current `plan` and `sync` run still extracts every resource in full.
`PAGE_TOKEN` is about resuming a paginated extraction, not reducing what it reads, so it does
not fit that ordering either. NetBox returns `TIMESTAMP` for mapped kinds and `NONE` otherwise;
an adapter with no incremental support inherits the `NONE` default from `DiffSyncMixin`.

### What an adapter implements

The extraction methods are layered on top of `model_loader`:

- `cursor_tier_for(model_name)` — declare eligibility for a model. A tier alone does not
  establish a safe cursor.
- `list_changed_since(model_name, cursor)` — **required when the tier is not `NONE`.** Yield
  the raw records changed since `cursor`, in the same shape `model_loader` feeds to
  `self.add(...)`. `DiffSyncMixin` raises `NotImplementedError` until you override it.
- `list_existing_ids(model_name)` — optional. Yield the current `unique_id` strings present
  in the source so deletions can be detected between runs. Without it, a warm run cannot tell
  that an object disappeared.

`CursorState` (also in `cache/cursors.py`) carries the tier and the saved value (a timestamp
or opaque watermark) from the previous run. Its `safe` flag defaults to `False`.

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

### Cursor safety and current reachability

Current v3 service and Prefect entry points use full extraction. The service's `_plan`
(`service/flow.py`) calls `execute_run` (`execution.py`) with its `full_extract=True` default.
The Prefect flow calls `run_remote_request`, which uses the same default. Both reach
`_run_plan_lifecycle`, `Potenda.load_both_sides`, and `source_load` / `destination_load`.
Neither calls `Potenda.persist_cursors_for_run()`. Verify and apply do not extract data.

A direct Python caller must enable incremental extraction, retain a matching schema hash
and a successful run record, and explicitly persist cursors to use the warm path. This
safety contract does not enable incremental extraction or add cursor persistence to
current product flows.

An adapter can opt in with `safe_cursor_before_load(model_name) -> CursorState | None`.
Potenda calls this hook for every resource before it queries the first resource on that
side. Return `CursorState(tier=..., value=..., safe=True)` only when the source guarantees
that the next changed-since query includes every change that can commit during or after
this extraction. Return `None` when that guarantee cannot be established. The cursor tier
must match `cursor_tier_for(model_name)`.

For a timestamp cursor, the guarantee must cover the source's clock, timestamp resolution,
transaction visibility and the filter's boundary semantics. A documented overlap can
establish this guarantee only if it bounds clock differences and precision. NetBox and
supported Nautobot endpoints use inclusive `last_updated__gte`; Infrahub uses
`node_metadata__updated_at__after`, which must be treated as exclusive. A source with
one-second resolution and an exclusive filter must provide a bound before the entire
start-time second, rather than rounding the start time down to that second. An HTTP Date
header or a maximum timestamp from returned rows does not establish this guarantee.

The bundled NetBox, Nautobot and Infrahub adapters currently provide neither a safe source
watermark nor a bounded overlap. Potenda therefore extracts those resources in full,
even if a direct caller requests incremental extraction. It also extracts in full if the
saved cursor is unqualified, the tier changed, or the current source cannot guarantee a
safe next bound. Forced full extraction does not request the hook.

After a successful load, an explicit `persist_cursors_for_run()` call saves the captured
bound for each resource, including empty snapshots. The `safe-v1:` prefix in `cursors.json`
marks bounds established under this contract; older cursors require full extraction.
Parquet `_extract_ts` values remain diagnostic host timestamps and never become query
bounds. Capture failure or load failure cannot advance the candidate cursor.

Safe bounds can re-read records already in the cached snapshot. Potenda uses DiffSync's
in-memory `update_or_add_model_instance` for these rows: later yielded attributes replace
earlier values for the same identifier, without invoking destination writes. Destination
`local_id` values supplied by the delta are retained; an omitted value preserves the cached
id. This prevents the same-identifier warm update failure for deliberate overlap reads.

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
