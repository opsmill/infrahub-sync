from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, cast

from diffsync.enum import DiffSyncFlags

# The destination SDK's error base, imported for the apply path's exception boundary below and
# for nothing else. It is the one module-level import here that is not cheap, and it is
# unavoidable: the boundary has to *name* the library whose rejections are operational, and
# every path that reaches `apply_plan` constructs an SDK-backed destination anyway.
from infrahub_sdk.exceptions import (
    AuthenticationError,
    GraphQLError,
    ServerNotResponsiveError,
)
from infrahub_sdk.exceptions import (
    Error as InfrahubSDKError,
)
from tqdm import tqdm

from infrahub_sync import IncrementalConfig

# Imported at module level, unlike this module's other `infrahub_sync` imports: these four
# pull nothing beyond pydantic and the standard library — the write-surface protocol pulls
# nothing at all at runtime — while the artifact reader and the verifier reach
# `cache/parquet_io` and therefore `pyarrow`, which this module defers on purpose so
# importing the engine stays cheap.
from infrahub_sync.plan.config_version import resolve_config_version, validate_config_version
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    OperationApplyFailedError,
    PlanArtifactError,
    PlanVerificationError,
    SkippedDeleteOperation,
)
from infrahub_sync.plan.models import ACTIONS, ApplyRecord
from infrahub_sync.plan.write_surface import PlannedWriteDestination

# Justified once here rather than per site. Nearly every `infrahub_sync`
# import in this module is deliberately deferred into the function that needs it: the cache
# layer and the plan reader, verifier and writer all reach `pyarrow`, which costs hundreds of
# milliseconds to import and is not needed to construct an engine, list runs, or fail on a
# configuration error. Hoisting them would trade that for tidiness in a module whose import
# cost every CLI invocation pays. The four cheap plan imports that *are* at module level say
# so where they sit; anything reaching pyarrow stays local to its caller.
# pylint: disable=import-outside-toplevel

logger = logging.getLogger(__name__)

# The apply path's **operational** exception boundary. An exception from
# the write surface is reported as `OperationApplyFailedError` — a designed destination refusal,
# whose remedy is to repair the destination and re-plan — only if it is one of these:
#
#   * `PlanArtifactError` — the plan taxonomy the write surface raises deliberately: a peer that
#     matches nothing or matches many, an unaccounted identity component, an unkeyed render.
#   * `SkippedDeleteOperation` — the surface's defensive delete refusal. Unreachable on this
#     loop's own path, which filters deletes before dispatch, and a designed limitation rather
#     than a defect when some other caller provokes it.
#   * `InfrahubSDKError` — the destination library's own base, and therefore its transport,
#     authentication, GraphQL and object-validation rejections.
#
# Everything else — `TypeError`, `AttributeError`, `KeyError` after an SDK shape change, a bare
# `AssertionError` — is a **defect**, and a defect wrapped in this taxonomy advises an operator
# to repair a destination that is working while hiding the traceback that would diagnose it. So
# it escapes unchanged, carrying the partial record. Deliberately narrow: an `httpx` error the
# SDK failed to translate escapes as a defect rather than being wrapped on suspicion — the run
# still records what was written, and mislabelling a defect as a refusal is the worse failure.
OPERATIONAL_APPLY_FAILURES: tuple[type[Exception], ...] = (
    PlanArtifactError,
    SkippedDeleteOperation,
    InfrahubSDKError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from pathlib import Path

    from diffsync import Adapter
    from diffsync.diff import Diff

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import PlanManifest, VerificationFailure
    from infrahub_sync.plan.reader import RawPlanArtifact


def _plan_refusal(failures: Sequence[VerificationFailure], *, run_id: str) -> PlanVerificationError:
    """Build the refusal for a non-empty set of pre-apply failures, naming every one of them.

    One refusal for the whole gate rather than one per check, so an operator learns
    everything that is wrong from a single attempt (AD036), and each entry carries its own
    next action (AD059).
    """
    detail = "\n".join(
        f"  - {failure.check}: expected {failure.expected}, found {failure.found}. {failure.next_action}"
        for failure in failures
    )
    msg = (
        f"The plan artifact of run {run_id!r} cannot be applied: {len(failures)} pre-apply check(s) "
        f"failed and nothing was written to the destination.\n{detail}"
    )
    return PlanVerificationError(msg)


def _operational_failure_summary(exc: Exception) -> str:
    """Return stable operator context without rendering untrusted SDK/server text.

    SDK exceptions can embed a complete GraphQL request or response body in
    ``str(exc)``.  The exception remains chained for an explicitly requested
    developer traceback, while normal CLI output receives only its category.
    """
    if isinstance(exc, AuthenticationError):
        return "an authentication failure (AuthenticationError)"
    if isinstance(exc, ServerNotResponsiveError):
        return "a destination timeout (ServerNotResponsiveError)"
    if isinstance(exc, GraphQLError):
        return "a destination GraphQL rejection (GraphQLError)"
    if isinstance(exc, InfrahubSDKError):
        return f"a destination SDK failure ({type(exc).__name__})"
    # PlanArtifactError and SkippedDeleteOperation are in-tree, purpose-built
    # operator errors. Their detail identifies the affected peer or plan field
    # and is not SDK/server response text.
    return str(exc)


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
        self._last_plan_action_counts: dict[str, int] | None = None
        self._last_applied_plan_action_counts: dict[str, int] | None = None
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

    @property
    def last_applied_plan_action_counts(self) -> dict[str, int] | None:
        """Return a copy of the most recent applied plan's action counts."""
        if self._last_applied_plan_action_counts is None:
            return None
        return dict(self._last_applied_plan_action_counts)

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

    def write_plan(self, diff: Any) -> dict[str, int] | None:
        """Write both plan representations for a single-diff run.

        `plan.parquet` is written exactly as before (V23) — it is retained for operators
        to query, and the new artifact never replaces it. It is **not** what `apply`
        reads: `apply_plan` loads `<run_dir>/plan/` and refuses a run that holds only the
        parquet. The saved plan artifact is written alongside it, because this method is
        the one call site common to every non-tier path that produces a plan: the `diff`
        command, the serial `sync` command and `sync_in_tiers`' no-tiers branch — and on all
        three it runs before any destination write, which is what FR-001 requires. The tier branch of
        `sync_in_tiers` writes the artifact itself, from every tier's retained diff.

        Returns the saved artifact's in-memory per-action counts, or `None` when no
        saved artifact can be written. For `operation="plan"`, the shared execution
        surface uses those counts instead of the narrower legacy parquet rows; legacy
        behavioral engines that return nothing retain the row fallback. Serial-sync
        results instead report their live diffsync rows.
        """
        if not self.run_dir:
            return None
        from infrahub_sync.cache.parquet_io import write_plan

        write_plan(run_dir=self.run_dir, rows=self._diff_to_rows(diff))
        self._last_plan_action_counts = None
        self.write_plan_artifact([diff])
        return self._last_plan_action_counts

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
        written = self._write_plan_artifact(diffs)
        return None if written is None else written[0]

    def _write_plan_artifact(self, diffs: Sequence[Any]) -> tuple[PlanManifest, dict[str, int]] | None:
        """Write the artifact once and retain its authoritative in-memory action counts."""
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
        action_counts: dict[str, int] = dict.fromkeys(ACTIONS, 0)
        for operation in operations:
            action_counts[operation.action] += 1

        warn_missing_convergence_key(destination=self.destination, operations=operations)

        manifest = write_artifact(
            run_dir=self.run_dir,
            run_id=self.run_id,
            config_version=default_config_version(self.config),
            source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(self.run_dir)],
            deletes_computed=deletes_computed,
            operations=operations,
            # The resolved destination identity, when the adapter captured one; `None`
            # writes the manifest shape older plans carry.
            destination_binding=getattr(self.destination, "destination_binding", None),
        )
        logger.info(
            "Plan artifact: wrote %d operation(s) to %s (deletes computed: %s)",
            manifest.operations_count,
            self.run_dir / "plan",
            deletes_computed,
        )
        self._last_plan_action_counts = action_counts
        return manifest, action_counts

    def _apply_config_version(self, supplied: str | None) -> str:
        """The configuration version the apply compares the artifact's against (FR-011, AD013).

        Recomputed by the default rule from the parsed configuration on the CLI path, or
        taken **verbatim** — validated as non-empty printable ASCII, never parsed — when an
        in-process caller supplies one. A caller with neither is asking for a comparison
        that cannot be made, so it is refused here rather than silently skipped.
        """
        if self.config is not None:
            return resolve_config_version(self.config, supplied)
        if supplied is None:
            msg = (
                "Potenda.apply_plan needs a configuration version to compare the plan artifact "
                "against: construct Potenda with a parsed configuration, or pass `config_version`."
            )
            raise ValueError(msg)
        return validate_config_version(supplied)

    def apply_plan(
        self,
        *,
        config_version: str | None = None,
        artifact: RawPlanArtifact | None = None,
        expected_checksum: str | None = None,
    ) -> ApplyRecord:
        """Apply this run's saved plan artifact to the destination, and return what it did.

        Neither side is loaded, nothing is re-compared and nothing is re-derived: the stored
        operations are executed in **stored order**, exactly as recorded (FR-012, SC-001).

        The step order is load-bearing: **read once, verify those bytes, then parse them.**
        FR-019's `plan/`-directory verdict is settled first, on its own. The artifact is then
        read from disk exactly once — or supplied by a caller that has already read it, so a
        caller with its own pre-apply check answers it about these bytes rather than a second
        read — and verification, the approval comparison, parsing and the loop below all
        consume that one `RawPlanArtifact`, so the bytes verified and approved are the bytes
        applied (DBR-006, DBA-004). Verification is one gate over those raw bytes, the write-surface check
        included, and it **precedes** the parse: FR-009 requires the format-version gate to
        report that the remaining checks were not evaluated, and a co-occurring tear to be
        reported alongside every other failure, neither of which survives the parser's
        single-condition refusal. The parse still precedes the loop, so an unrecognized
        `action` is refused before any destination write (FR-017, AD055).

        A recorded `delete` is **collected, never dispatched**: a delete-bearing plan ends
        `applied` with the skipped identifiers, their count, and one `WARNING` naming that
        count — pinned at that level because `--quiet` floors the package logger there
        (FR-016, FR-017, AD055, AD089).

        This method **writes no run file** (AD069). It returns the record; the CLI is the
        single writer and merges `as_summary_keys()` into `run_file.summary` before saving.

        Args:
            config_version: The comparison value for the configuration-version check,
                compared for equality and never parsed. `None` recomputes it from the
                parsed configuration by the default rule (FR-011, AD013).
            artifact: The plan artifact's bytes, when the caller has already read them.
                `None` reads them here. Either way it is read once per apply.
            expected_checksum: An approved plan checksum the artifact must hash to. The
                **authoritative** answer to `--expected-checksum`, because it is given about
                the bytes this call applies; a caller's earlier check is a fast path, not a
                substitute. `None` asks for no approval comparison at all.

        Returns:
            The `ApplyRecord`: the ordered applied identifiers (whose final element is
            FR-025's last-applied pointer), the skipped deletes in stored order, and their
            count. A completed apply records `failed_operation` as `None`.

        Raises:
            ValueError: this run has no `run_dir`, or no configuration version can be formed.
            PlanFormatV1Error: this run holds no `plan/` directory (FR-019).
            PlanVerificationError: a pre-apply check failed, or the artifact does not hash to
                `expected_checksum`; nothing was written.
            PlanArtifactTornError: an operations record fails validation for a reason the
                line count cannot see (FR-010).
            UnsupportedOperationActionError: an operation's `action` is outside `ACTIONS`.
            OperationApplyFailedError: an operation failed inside the operational boundary
                (`OPERATIONAL_APPLY_FAILURES`), carrying the partial record.
            ApplyRecordInvariantError: a completed apply's record does not account for every
                operation in the plan (AD062). Carries the record it is complaining about.
            BaseException: anything outside that boundary — an interrupt, or a defect such as
                a `TypeError` from an SDK shape change — propagates **unchanged** with the
                partial record attached as `apply_record`.
        """
        from infrahub_sync.plan.reader import parse_plan_artifact, read_plan_artifact_bytes, require_plan_directory
        from infrahub_sync.plan.review import expected_checksum_refusal
        from infrahub_sync.plan.verify import verify_plan

        if not self.run_dir:
            msg = "Potenda.apply_plan requires run_dir to be set."
            raise ValueError(msg)
        run_id = self.run_id or self.run_dir.name
        comparison_version = self._apply_config_version(config_version)

        # FR-019's verdict first, and on its own: a run that never reached the writer holds
        # the pre-existing row format, which is a different condition with a different remedy
        # from an artifact whose manifest the gate below cannot parse. The CLI already reaches
        # this before it constructs anything (AD026); an in-process caller reaches it here.
        require_plan_directory(self.run_dir)

        # The one read — the caller's, when it has already read the artifact for a check of its
        # own. Verification, the approval comparison, parsing and the loop below all consume this
        # object, so the bytes verified and approved are the bytes applied and nothing re-reads
        # the disk between the gate and the writes (DBR-006, DBA-004).
        raw = read_plan_artifact_bytes(self.run_dir) if artifact is None else artifact
        destination = self.destination
        # FR-023's write-surface check joins the same pre-write gate as the five verification
        # checks, so one attempt tells the operator everything that is wrong (AD036). The
        # adapter's **name** goes in, not a boolean, because the failure it drives names the
        # adapter (AD058). On what the protocol check does and does not verify, see
        # `infrahub_sync.plan.write_surface`.
        failures = verify_plan(
            artifact=raw,
            run_id=run_id,
            config_version=comparison_version,
            write_surface_missing_on=(
                None if isinstance(destination, PlannedWriteDestination) else type(destination).__name__
            ),
        )
        if failures:
            raise _plan_refusal(failures, run_id=run_id)

        # The operator's approval, decided about the bytes above and therefore the bytes applied.
        # **After** the gate so FR-009's evaluate-all disclosure still reaches an operator whose
        # artifact is torn as well as unapproved, and **before** the parse and the loop so a plan
        # that is not the approved one is refused with nothing written. A caller's earlier check
        # is a fast path that spares building a destination; this one is what decides.
        if expected_checksum is not None:
            refusal = expected_checksum_refusal(
                artifact=raw, run_id=run_id, expected=expected_checksum, destination_contacted=True
            )
            if refusal is not None:
                raise PlanVerificationError(refusal.reason, next_action=refusal.next_action)

        # The gate evaluated the write-surface check, so an empty failure list proves the
        # surface is present — the cast narrows for the type checker; it is not a second gate.
        destination = cast("PlannedWriteDestination", destination)

        # Parse after the gate and before the loop — the same bytes the gate verified.
        # Everything the gate can see is already reported; what remains for the parser is
        # per-record validity — an unrecognized `action` above all — which is still refused
        # before the first destination write.
        loaded = parse_plan_artifact(raw, run_id=run_id)
        self._last_applied_plan_action_counts = {
            action: sum(operation.action == action for operation in loaded.operations) for action in ACTIONS
        }

        # One memo for the whole apply, discarded with it — the same lifetime as the run. The
        # destination supplies it, so the engine builds a resolver for a destination it does
        # not have to name (AD086).
        peers = destination.new_peer_resolver()

        applied: list[str] = []
        skipped_deletes: list[str] = []
        for operation in loaded.operations:
            if operation.action == "delete":
                skipped_deletes.append(operation.operation_id)
                continue
            try:
                destination.apply_planned_operation(operation=operation, peers=peers)
            except BaseException as exc:
                # The partial record travels on the error so the CLI can merge what was
                # written before it records `failed` (AD069). Re-raising bare would lose it.
                partial = ApplyRecord(
                    applied_operations=tuple(applied),
                    skipped_delete_operations=tuple(skipped_deletes),
                    # Named on the record because applying one operation is not one write: the
                    # base upsert precedes the relationship flush, so this operation may have
                    # changed the destination while belonging to neither recorded set.
                    failed_operation=operation.operation_id,
                )
                if not isinstance(exc, OPERATIONAL_APPLY_FAILURES):
                    # An interrupt or a defect: it propagates as itself, with its own
                    # traceback, carrying the record so what was written stays readable from the
                    # run (AD062). See `OPERATIONAL_APPLY_FAILURES` for the boundary.
                    # The suppression is not masking a defect — no annotation can declare an
                    # attribute on an exception type this module does not own.
                    exc.apply_record = partial  # ty: ignore[unresolved-attribute]
                    raise
                msg = (
                    f"Applying operation {operation.operation_id!r} of run {run_id!r} to the destination "
                    f"failed with {_operational_failure_summary(exc)}. The {len(applied)} operation(s) applied before it stay written, and "
                    f"this operation may itself have written part of its change before failing — "
                    f"re-applying the plan converges it."
                )
                raise OperationApplyFailedError(msg, apply_record=partial) from exc
            applied.append(operation.operation_id)

        completed = ApplyRecord(
            applied_operations=tuple(applied),
            skipped_delete_operations=tuple(skipped_deletes),
        )

        # AFTER the loop and off the rejection path (AD069): a partial apply breaks both
        # clauses by construction, so checking unconditionally would replace a clear
        # destination-rejection message with an invariant error, and checking inside the
        # loop would fail on the first iteration.
        planned_ids = {operation.operation_id for operation in loaded.operations}
        recorded_ids = set(applied) | set(skipped_deletes)
        if recorded_ids != planned_ids or len(applied) + len(skipped_deletes) != loaded.manifest.operations_count:
            msg = (
                f"The apply of run {run_id!r} completed but its record does not account for the plan: "
                f"{len(applied)} applied and {len(skipped_deletes)} delete(s) skipped against "
                f"{loaded.manifest.operations_count} recorded operation(s); "
                f"{sorted(planned_ids - recorded_ids)} are in neither set and "
                f"{sorted(recorded_ids - planned_ids)} are in no plan."
            )
            # The **real** record travels with it: every non-delete operation above was
            # written before this check ran, so an empty one would misreport a fully applied
            # run as having applied nothing.
            raise ApplyRecordInvariantError(msg, apply_record=completed)

        if skipped_deletes:
            logger.warning(
                "Apply of run %s: %d recorded delete operation(s) were not executed. Applying deletes "
                "is not supported; their identifiers are recorded on the run under "
                "'skipped_delete_operations'.",
                run_id,
                len(skipped_deletes),
            )

        return completed

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

    def sync_in_tiers(
        self,
        *,
        parallel: bool = False,
        allow_rowcount_drop: bool = False,
        plan_committed: Callable[[], None] | None = None,
    ) -> dict[str, int]:
        """Run diff+sync one tier at a time.

        When `parallel=False`, falls back to the existing serial pathway.
        When `parallel=True`, computes **every** tier's diff first, writes the plan
        artifact, and only then executes the retained diffs tier by tier. The two loops
        are what makes FR-001's "the artifact exists before anything is written" true in
        tier mode: interleaving diff and sync writes the first
        tier before the second tier has even been compared, so no complete artifact can
        precede the first write, and `sync --parallel` is the default.

        The `top_level` narrowing stays in the **compute** loop, restored afterwards. It
        governs diff computation, not execution: only the comparison differ reads it, while
        the synchronizer walks the children of whatever `Diff` it is handed. Narrowing in the execution loop instead would compute
        six identical full-destination diffs rather than six disjoint per-tier ones, and the
        artifact would record every operation once per tier (AD039, PD-009).

        Aggregates per-tier diff rows into a single plan.parquet, unchanged, so operators can
        query the whole change set. `apply` reads the saved plan artifact instead, which this
        branch writes from every tier's retained diff.
        """
        if not self.tiers:
            self.load_both_sides()
            self.check_rowcount_guardrail(allow_drop=allow_rowcount_drop)
            diff = self.diff()
            self.write_plan(diff)
            if plan_committed is not None:
                plan_committed()
            if diff.has_diffs():
                self.sync(diff=diff)
                # Re-snapshot destination AFTER writes so the next warm run
                # hydrates from real post-sync state rather than the pre-sync
                # (often empty) snapshot. Source state was already final.
                self._write_side_snapshot("B", self.destination)
            self.persist_baseline_counts()
            self.persist_cursors_for_run(side="A")
            self.persist_cursors_for_run(side="B")
            rows = self._diff_to_rows(diff)
            return {action: sum(row["action"] == action for row in rows) for action in ACTIONS}

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
        if plan_committed is not None:
            plan_committed()

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
        return {action: sum(row["action"] == action for row in aggregated_rows) for action in ACTIONS}
