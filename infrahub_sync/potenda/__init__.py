from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

from diffsync.enum import DiffSyncFlags
from tqdm import tqdm

from infrahub_sync import IncrementalConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    from diffsync import Adapter
    from diffsync.diff import Diff

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import PlanManifest


class Potenda:
    def __init__(
        self,
        source: Adapter,
        destination: Adapter,
        config: SyncInstance,
        top_level: list[str],
        partition=None,
        show_progress: bool | None = None,
        verbosity: int | None = None,
        tiers: list[set[str]] | None = None,
        run_dir: Path | None = None,
        run_id: str | None = None,
        cache_root: Path | None = None,
        schema_subhash: str = "",
        continue_on_error: bool = False,
        concurrent_load: bool = True,
    ):
        self.top_level = top_level
        self.tiers: list[set[str]] | None = tiers
        self.continue_on_error = continue_on_error
        self.concurrent_load = concurrent_load
        if self.tiers:
            for idx, tier in enumerate(self.tiers):
                logger.info("Potenda tier %d (%d): %s", idx, len(tier), sorted(tier))
        # Cache/run identity — passed at construction so the object is fully
        # valid on return rather than mutated into shape by the caller.
        self.run_dir: Path | None = run_dir
        self.run_id: str | None = run_id
        self.cache_root: Path | None = cache_root
        self._schema_subhash: str = schema_subhash
        self._counts: dict[str, int] = {}
        self._did_full_extract: bool = False
        # Per-side extraction mode, recorded alongside the OR-accumulated
        # `_did_full_extract` rather than in place of it. FR-015 derives deletes only
        # when the *destination* side ran a full extract, and the OR-accumulated flag
        # cannot answer for one side: it is deliberately true when either side ran
        # full, which is what `persist_baseline_counts` needs and what a per-side
        # question must not be answered with. Absent side means "did not load".
        self._side_full_extract: dict[str, bool] = {}
        self._side_extract_ts: dict[str, datetime] = {}
        self._prev_run_resolved: bool = False
        self._prev_run_cached: Path | None = None
        # Runtime toggle set per-command by the CLI just before load.
        self.force_full_extract: bool = False

        self.config = config

        self.source = source
        self.destination = destination

        # diffsync's `Adapter.top_level` is a ClassVar but the library supports per-instance overrides.
        self.source.top_level = top_level  # ty: ignore[invalid-attribute-access]
        self.destination.top_level = top_level  # ty: ignore[invalid-attribute-access]

        # Propagate continue_on_error so adapters can skip bad peers in-loop.
        # Adapters that don't read the attribute just ignore it.
        self.source.continue_on_error = continue_on_error  # ty: ignore[unresolved-attribute]
        self.destination.continue_on_error = continue_on_error  # ty: ignore[unresolved-attribute]

        self.partition = partition
        self.progress_bar = None
        self.show_progress = show_progress if show_progress is not None else sys.stderr.isatty()

        if verbosity is not None:
            logging.getLogger("diffsync").setLevel(verbosity)

        # Combine DiffSyncFlags from the configuration. `config` is typed as
        # SyncInstance but tests pass None — guard explicitly.
        self.flags: DiffSyncFlags = DiffSyncFlags.NONE
        if self.config is not None:
            for flag in self.config.diffsync_flags or []:
                self.flags |= flag if isinstance(flag, DiffSyncFlags) else DiffSyncFlags[flag]

        # Fallback to `SKIP_UNMATCHED_DST` if nothing is define
        if self.flags == DiffSyncFlags.NONE:
            self.flags = DiffSyncFlags.SKIP_UNMATCHED_DST

    def _print_callback(self, stage: str, elements_processed: int, total_models: int):
        """Callback for DiffSync progress tracking."""
        if self.show_progress:
            if self.progress_bar is None:
                self.progress_bar = tqdm(total=total_models, desc=stage, unit="models")

            self.progress_bar.n = elements_processed
            self.progress_bar.refresh()

            if elements_processed == total_models:
                self.progress_bar.close()
                self.progress_bar = None
        elif elements_processed == total_models:
            logger.info("%s: %d/%d models processed", stage, elements_processed, total_models)

    def _previous_run(self) -> Path | None:
        """Cached lookup of the most recent successful run dir.

        Called once per side load — recompute is wasteful since both sides
        share the same cache_root and the answer is invariant within a run.
        """
        if not self._prev_run_resolved:
            from infrahub_sync.cache.incremental import previous_successful_run_dir

            self._prev_run_cached = previous_successful_run_dir(self.cache_root) if self.cache_root else None
            self._prev_run_resolved = True
        return self._prev_run_cached

    def _write_side_snapshot(self, side: str, adapter: Adapter) -> None:
        if not self.run_dir:
            return
        from datetime import datetime, timezone

        from infrahub_sync.cache.parquet_io import write_resource_side

        extract_ts = datetime.now(timezone.utc)
        # Remember when this side started loading so persist_cursors_for_run
        # can anchor a cursor for resources whose snapshot is empty (e.g. the
        # destination on a fresh Infrahub — nothing exists pre-sync, but the
        # next warm run still needs a cursor to query `_updated_at__gte` from).
        self._side_extract_ts[side] = extract_ts
        for kind in adapter.top_level:
            records = list(adapter.get_all(kind))
            # Include both identifiers AND attributes so hydrate_from_parquet
            # can reconstruct a complete payload — without identifiers, replaying
            # a row through `model_cls(**payload)` fails pydantic validation for
            # any required identifier field. `get_identifiers` is guarded for
            # adapter stubs that don't implement it; falling back to just
            # attributes is what the pre-fix behavior did.
            rows = [
                {
                    **(r.get_identifiers() if hasattr(r, "get_identifiers") else {}),
                    **r.get_attrs(),
                }
                for r in records
            ]
            source_ids = [r.get_unique_id() for r in records]
            if side == "A":
                self._counts[kind] = len(records)
            write_resource_side(
                run_dir=self.run_dir,
                side=side,
                resource=kind,
                rows=rows,
                source_ids=source_ids,
                extract_ts=extract_ts,
            )

    def load_one_side(self, *, side: str, adapter: Adapter) -> None:
        """Load one side, choosing incremental vs full based on cursors.

        Falls back to the legacy full-extract path (``adapter.load()``) when
        there is no prior successful run, the schema-subhash mismatches, or
        the caller asked for ``--full-extract``.
        """
        from infrahub_sync.cache.cursors import CursorTier
        from infrahub_sync.cache.incremental import (
            hydrate_from_parquet,
            load_cursors,
            should_use_incremental,
        )
        from infrahub_sync.cache.sidecars import RunCounterFile

        cache_root = self.cache_root
        prev_run = self._previous_run()

        inc_config = self.config.incremental if self.config else None
        cadence = inc_config.full_resync_every if inc_config else IncrementalConfig().full_resync_every

        runs_since_full = 0
        if cache_root is not None:
            counter = RunCounterFile.load_or_default(cache_root / "run-counter.json")
            runs_since_full = counter.runs_since_full

        use_inc = should_use_incremental(
            prev_run_dir=prev_run,
            current_subhash=self._schema_subhash,
            force_full=self.force_full_extract,
            runs_since_full=runs_since_full,
            cadence=cadence,
        )

        # OR-accumulate so the second side cannot silently overwrite the
        # first side's True (persist_baseline_counts resets the run-counter
        # only when no side ran the incremental path).
        self._did_full_extract = self._did_full_extract or (not use_inc)
        # Per-side answer for FR-015. `should_use_incremental` already returns False
        # when there is no prior run, so `not use_inc` is exactly "this side ran a
        # full extract" for both arms of the branch below.
        self._side_full_extract[side] = not use_inc

        if not use_inc or prev_run is None:
            adapter.load()
            return

        def _add(model_name: str, payload: dict, _adapter: Adapter = adapter) -> None:
            model_cls = getattr(_adapter, model_name)
            _adapter.add(model_cls(**payload))

        cursors = load_cursors(prev_run / "cursors.json", side=side)
        for resource in adapter.top_level:
            tier_supported = adapter.cursor_tier_for(resource)  # ty: ignore[unresolved-attribute]
            cursor = cursors.get(resource)
            model_cls = getattr(adapter, resource, None)
            if model_cls is None:
                continue
            if cursor is None or tier_supported is CursorTier.NONE:
                adapter.model_loader(model_name=resource, model=model_cls)  # ty: ignore[unresolved-attribute]
                continue

            hydrate_from_parquet(
                run_dir=prev_run,
                side=side,
                resource=resource,
                add_row=_add,
            )
            for row in adapter.list_changed_since(resource, cursor):  # ty: ignore[unresolved-attribute]
                adapter.add(model_cls(**row))

    def source_load(self):
        try:
            logger.info("Load: Importing data from %s", self.source)
            self.load_one_side(side="A", adapter=self.source)
            self._write_side_snapshot("A", self.source)
        except Exception as exc:
            msg = f"An error occurred while loading {self.source}: {exc!s}"
            raise ValueError(msg) from exc

    def destination_load(self):
        try:
            logger.info("Load: Importing data from %s", self.destination)
            self.load_one_side(side="B", adapter=self.destination)
            self._write_side_snapshot("B", self.destination)
        except Exception as exc:
            msg = f"An error occurred while loading {self.destination}: {exc!s}"
            raise ValueError(msg) from exc

    def load_both_sides(self) -> None:
        """Load source and destination.

        When ``self.concurrent_load`` is True (the default), the two loads
        run on a 2-thread ``ThreadPoolExecutor`` since they hit independent
        services, write to independent ``DiffSyncStore``s, and write to
        disjoint cache subdirectories. Roughly halves wall-clock time on
        real APIs.

        When ``self.concurrent_load`` is False, falls back to sequential
        execution (``source_load`` then ``destination_load``) — useful when
        a custom adapter isn't thread-safe.

        Exceptions from either side are surfaced: the first failure to
        complete is re-raised, just like the sequential path would do.
        """
        if not self.concurrent_load:
            self.source_load()
            self.destination_load()
            return

        from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="potenda-load") as pool:
            src_fut = pool.submit(self.source_load)
            dst_fut = pool.submit(self.destination_load)
            wait([src_fut, dst_fut], return_when=FIRST_EXCEPTION)
            # Surface the failure (if any). Both futures have run or are
            # being cancelled by the pool's shutdown. ``.result()`` re-raises.
            for fut in (src_fut, dst_fut):
                fut.result()

    def load(self):
        try:
            self.load_both_sides()
        except Exception as exc:
            msg = f"An error occurred while loading the sync: {exc!s}"
            raise ValueError(msg) from exc

    def diff(self) -> Diff:
        logger.info("Diff: Comparing data from %s to %s", self.source, self.destination)
        self.progress_bar = None
        return self.destination.diff_from(self.source, flags=self.flags, callback=self._print_callback)

    def sync(self, diff: Diff | None = None):
        logger.info("Sync: Importing data from %s to %s based on Diff", self.source, self.destination)
        self.progress_bar = None
        return self.destination.sync_from(self.source, diff=diff, flags=self.flags, callback=self._print_callback)

    def _diff_to_rows(self, diff: Any) -> list[dict[str, str]]:
        """Materialize a diffsync.Diff into plan-row dicts (one per change).

        Pulled out so sync_in_tiers can accumulate rows across per-tier
        diffs before writing a single plan.parquet for the whole run.
        """
        import json

        rows: list[dict[str, str]] = []
        children = getattr(diff, "children", None) or {}
        for resource, elements_by_name in children.items():
            # `elements_by_name` is the diffsync `{name: DiffElement}` mapping.
            for element in elements_by_name.values():
                action = getattr(element, "action", None) or ""
                if not action:
                    # Skip elements with no actionable change (no-op).
                    continue
                attrs_diffs = element.get_attrs_diffs() if hasattr(element, "get_attrs_diffs") else {}
                old_attrs = attrs_diffs.get("-") or {}
                new_attrs = attrs_diffs.get("+") or {}
                rows.append(
                    {
                        "action": action,
                        "resource": resource,
                        "source_id": getattr(element, "name", "") or "",
                        "dest_id": "",
                        "attribute": "",
                        "old_value": json.dumps(old_attrs, sort_keys=True, default=str) if old_attrs else "",
                        "new_value": json.dumps(new_attrs, sort_keys=True, default=str) if new_attrs else "",
                        "owner": "",
                        "skip_reason": "",
                        "conflict_class": "",
                    }
                )
        return rows

    def write_plan(self, diff: Any) -> None:
        """Write both plan representations for a single-diff run.

        `plan.parquet` is written exactly as before (V23) — it is still the row set
        `apply` and operators read today, and the new artifact never replaces it. The
        saved plan artifact is written alongside it, because this method is the one call
        site common to every non-tier path that produces a plan: the `diff` command
        (`infrahub_sync/cli.py:152`), the serial `sync` command (`:271`) and
        `sync_in_tiers`' no-tiers branch — and on all three it runs before any
        destination write, which is what FR-001 requires. The tier branch of
        `sync_in_tiers` writes the artifact itself, from every tier's retained diff.
        """
        if not self.run_dir:
            return
        from infrahub_sync.cache.parquet_io import write_plan

        write_plan(run_dir=self.run_dir, rows=self._diff_to_rows(diff))
        self.write_plan_artifact([diff])

    def write_plan_artifact(self, diffs: Sequence[Any]) -> PlanManifest | None:
        """Derive and write `<run_dir>/plan/` for `diffs`, before any destination write.

        Composes the derivation (creates and updates from every diff handed in, then the
        derived deletes, then the convergence-key warning) with the source-snapshot
        binding and the configuration version, and hands the result to the artifact
        writer. Returns the manifest that was written, or `None` when this run has no
        cache identity to write into.

        `diffs` is a sequence rather than a single diff because the tier path retains one
        diff per tier and the artifact records the whole change set, once.

        A derivation or write failure propagates: it fails the command on `diff` exactly
        as on `sync` (FR-030, AD047).
        """
        if not self.run_dir or not self.run_id or self.config is None:
            # No cache identity or no parsed configuration — the latter only happens in
            # tests, which construct Potenda with `config=None`.
            logger.debug("Plan artifact: skipped, this run has no run_dir/run_id/config")
            return None

        from functools import partial

        from infrahub_sync.plan.checksum import source_snapshot_records
        from infrahub_sync.plan.config_version import default_config_version
        from infrahub_sync.plan.derive import (
            derive_deletes,
            operations_from_diff,
            tier_of,
            warn_missing_convergence_key,
        )
        from infrahub_sync.plan.models import SourceSnapshotRecord
        from infrahub_sync.plan.writer import write_plan_artifact as write_artifact

        resolve_tier = partial(tier_of, tiers=self.tiers, top_level=self.top_level)
        operations = []
        for diff in diffs:
            operations.extend(
                operations_from_diff(
                    diff,
                    config=self.config,
                    tier_of=resolve_tier,
                    source_adapter=self.source,
                )
            )

        # FR-015: deletes are derived only where the destination side holds a complete
        # picture, and the manifest records which of the two happened.
        deletes_computed = self._side_full_extract.get("B", False)
        operations.extend(
            derive_deletes(
                kinds=list(self.top_level),
                source_adapter=self.source,
                destination_adapter=self.destination,
                config=self.config,
                tier_of=resolve_tier,
                destination_full_extract=deletes_computed,
            )
        )

        warn_missing_convergence_key(destination=self.destination, operations=operations)

        manifest = write_artifact(
            run_dir=self.run_dir,
            run_id=self.run_id,
            config_version=default_config_version(self.config),
            source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(self.run_dir)],
            deletes_computed=deletes_computed,
            operations=operations,
        )
        logger.info(
            "Plan artifact: wrote %d operation(s) to %s (deletes computed: %s)",
            manifest.operations_count,
            self.run_dir / "plan",
            deletes_computed,
        )
        return manifest

    def apply_plan(self) -> None:
        """Dispatch each row in plan.parquet to the destination adapter.

        The destination's `apply_cached_row(*, resource, action, source_id,
        attribute, new_value)` method is expected to perform the actual
        write. Adapters that don't implement it yet will raise
        AttributeError; the operator is told to fall back to `sync`.
        """
        from infrahub_sync.cache.parquet_io import read_plan

        if not self.run_dir:
            msg = "Potenda.apply_plan requires run_dir to be set."
            raise ValueError(msg)
        if not hasattr(self.destination, "apply_cached_row"):
            msg = (
                f"Destination adapter {type(self.destination).__name__} does "
                "not implement apply_cached_row. Use `infrahub-sync sync` "
                "until the adapter is upgraded."
            )
            raise NotImplementedError(msg)
        apply_cached_row = getattr(self.destination, "apply_cached_row")
        table = read_plan(run_dir=self.run_dir)
        for i in range(table.num_rows):
            apply_cached_row(
                resource=table.column("resource")[i].as_py(),
                action=table.column("action")[i].as_py(),
                source_id=table.column("source_id")[i].as_py(),
                attribute=table.column("attribute")[i].as_py(),
                new_value=table.column("new_value")[i].as_py(),
            )

    def persist_cursors_for_run(self, *, side: str) -> None:
        """Walk the run_dir snapshot files for `side`, compute per-resource
        cursors (max `_extract_ts`), and persist into `<run_dir>/cursors.json`.
        """
        if not self.run_dir:
            return
        import pyarrow.compute as pc

        from infrahub_sync.cache.cursors import CursorState, CursorTier
        from infrahub_sync.cache.incremental import persist_cursors
        from infrahub_sync.cache.parquet_io import read_table

        side_dir = self.run_dir / side
        if not side_dir.exists():
            return

        adapter = self.source if side == "A" else self.destination
        fallback_ts = self._side_extract_ts.get(side)
        cursors: dict[str, CursorState] = {}
        for parquet_path in side_dir.glob("*.parquet"):
            resource = parquet_path.stem
            tier = adapter.cursor_tier_for(resource)  # ty: ignore[unresolved-attribute]
            if tier is CursorTier.NONE:
                continue
            table = read_table(str(parquet_path))
            if table.num_rows == 0:
                # Empty snapshot (e.g. destination on a fresh Infrahub).
                # Anchor the cursor to when this side started loading so the
                # next warm run's `_updated_at__gte=<cursor>` picks up
                # whatever this run wrote afterwards.
                if fallback_ts is not None:
                    cursors[resource] = CursorState(tier=tier, value=fallback_ts.isoformat())
                continue
            max_ts = pc.max(table.column("_extract_ts")).as_py()  # ty: ignore[unresolved-attribute]
            cursors[resource] = CursorState(tier=tier, value=max_ts.isoformat())

        if cursors:
            persist_cursors(self.run_dir / "cursors.json", side=side, cursors=cursors)

    def persist_baseline_counts(self) -> None:
        """Write the source-side row counts to the canonical baseline file.

        Called only after a successful sync — a failed run must not poison
        the baseline. Also bumps run-counter.json toward the cadence
        threshold (or resets it to zero if this run was a full extract).
        """
        if not self.run_dir:
            return
        from infrahub_sync.cache.paths import cache_root_for
        from infrahub_sync.cache.sidecars import RowcountsFile, RunCounterFile

        root = cache_root_for(self.config.name if self.config else "_unknown")
        counts_file = RowcountsFile.load_or_default(root / "last-successful-rowcounts.json")
        for k, v in self._counts.items():
            counts_file.set(k, v)
        counts_file.save()

        counter = RunCounterFile.load_or_default(root / "run-counter.json")
        if self._did_full_extract:
            counter.runs_since_full = 0
        else:
            counter.runs_since_full += 1
        counter.save()

    def check_rowcount_guardrail(self, *, allow_drop: bool) -> None:
        if not self.run_dir or not self.config:
            return
        from infrahub_sync.cache.guardrails import RowcountGuardrail
        from infrahub_sync.cache.paths import cache_root_for
        from infrahub_sync.cache.sidecars import RowcountsFile

        root = cache_root_for(self.config.name)
        baseline = RowcountsFile.load_or_default(root / "last-successful-rowcounts.json")
        guard = RowcountGuardrail(previous=baseline.counts, allow_drop=allow_drop)
        for resource, current in self._counts.items():
            guard.check(resource, current=current)

    def sync_in_tiers(self, *, parallel: bool = False, allow_rowcount_drop: bool = False) -> None:
        """Run diff+sync one tier at a time.

        When `parallel=False`, falls back to the existing serial pathway.
        When `parallel=True`, computes **every** tier's diff first, writes the plan
        artifact, and only then executes the retained diffs tier by tier. The two loops
        are what makes FR-001's "the artifact exists before anything is written" true in
        tier mode: interleaving diff and sync — as this branch used to — writes the first
        tier before the second tier has even been compared, so no complete artifact can
        precede the first write, and `sync --parallel` is the default.

        The `top_level` narrowing stays in the **compute** loop, restored afterwards. It
        governs diff computation, not execution: only the comparison differ reads it
        (`.venv/…/diffsync/helpers.py:79-88`), while the synchronizer walks the children of
        whatever `Diff` it is handed. Narrowing in the execution loop instead would compute
        six identical full-destination diffs rather than six disjoint per-tier ones, and the
        artifact would record every operation once per tier (AD039, PD-009).

        Aggregates per-tier diff rows into a single plan.parquet, unchanged, so `apply` and
        operators can review the whole change set.
        """
        if not self.tiers:
            self.load_both_sides()
            self.check_rowcount_guardrail(allow_drop=allow_rowcount_drop)
            diff = self.diff()
            self.write_plan(diff)
            if diff.has_diffs():
                self.sync(diff=diff)
                # Re-snapshot destination AFTER writes so the next warm run
                # hydrates from real post-sync state rather than the pre-sync
                # (often empty) snapshot. Source state was already final.
                self._write_side_snapshot("B", self.destination)
            self.persist_baseline_counts()
            self.persist_cursors_for_run(side="A")
            self.persist_cursors_for_run(side="B")
            return

        self.load_both_sides()
        self.check_rowcount_guardrail(allow_drop=allow_rowcount_drop)
        saved_top = self.destination.top_level

        # Compute loop: every tier's diff, each computed against its own narrowed
        # destination top_level, retained for the execution loop below.
        retained: list[tuple[list[str], Any]] = []
        try:
            for idx, tier in enumerate(self.tiers):
                tier_list = sorted(tier)
                logger.info("Diff tier %d (%d): %s", idx, len(tier), tier_list)
                self.destination.top_level = tier_list  # ty: ignore[invalid-attribute-access]
                retained.append((tier_list, self.diff()))
        finally:
            self.destination.top_level = saved_top  # ty: ignore[invalid-attribute-access]

        aggregated_rows: list[dict[str, str]] = []
        for _tier_list, diff in retained:
            aggregated_rows.extend(self._diff_to_rows(diff))

        # Before the first destination write, and after top_level is restored so the
        # derived deletes cover every kind rather than the last tier's (FR-001, AD039).
        self.write_plan_artifact([diff for _tier_list, diff in retained])

        # Execution loop: replay the retained diffs in tier order. `top_level` is
        # irrelevant here — the synchronizer walks the Diff it is handed.
        any_writes = False
        for idx, (tier_list, diff) in enumerate(retained):
            logger.info("Sync tier %d (%d): %s", idx, len(tier_list), tier_list)
            if diff.has_diffs():
                self.sync(diff=diff)
                any_writes = True

        if any_writes:
            # Same reasoning as the no-tiers branch — capture post-sync
            # destination state for the next warm run's hydrate path.
            self._write_side_snapshot("B", self.destination)
        if self.run_dir:
            from infrahub_sync.cache.parquet_io import write_plan as _write_plan_file

            _write_plan_file(run_dir=self.run_dir, rows=aggregated_rows)
        self.persist_baseline_counts()
        self.persist_cursors_for_run(side="A")
        self.persist_cursors_for_run(side="B")
        _ = parallel  # reserved for diffsync v3 thread fan-out; see backport doc
