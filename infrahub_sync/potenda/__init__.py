# pylint: disable=too-many-lines  # the engine owns load, compare, plan writing and apply
from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

# The one costly module-level import: apply's exception boundary must name the SDK's errors.
from infrahub_sdk.exceptions import Error as InfrahubSDKError
from infrahub_sdk.exceptions import GraphQLError, ServerNotResponsiveError
from tqdm import tqdm

from infrahub_sync import DiffSyncMixin, resolve_effective_diffsync_flags
from infrahub_sync.infrahub_errors import classify_infrahub_error
from infrahub_sync.load_report import source_record_id

# These plan imports are cheap; imports that reach pyarrow stay deferred below.
from infrahub_sync.plan.config_version import resolve_config_version, validate_config_version
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    LeftOutConfirmationRequiredError,
    OperationApplyFailedError,
    PlanArtifactError,
    PlanFormatApplyUnsupportedError,
    PlanVerificationError,
    SkippedDeleteOperation,
)
from infrahub_sync.plan.models import (
    ACTIONS,
    DESTINATION_ID_FORMAT_VERSION,
    REPORT_FORMAT_VERSION,
    ApplyRecord,
    ordered_checks,
)
from infrahub_sync.plan.write_surface import (
    LineageResolvingDestination,
    PlannedWriteDestination,
    destination_last_changes,
    destination_lineage_record,
    resolve_destination_lineage,
    resolve_destination_metadata_read,
)
from infrahub_sync.potenda.read_progress import SideReadReport

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
#     matches nothing or matches many, an unaccounted identity component, a create that cannot
#     be proven keyed, a stale recorded id.
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


class SideLoadError(ValueError):
    """Reading one side of the sync failed (spec 020 research R12).

    `role` is `source` or `destination`, and `adapter` the adapter name the sync configuration
    declares, else the adapter's type. The error that stopped the read is kept as `__cause__`.
    A subclass of `ValueError`, so every handler that matches `ValueError` still matches it.
    """

    def __init__(self, message: str, *, role: Side, adapter: str) -> None:
        super().__init__(message)
        self.role: Side = role
        self.adapter = adapter

    def __reduce__(self) -> tuple[Any, ...]:
        # Keep the named attributes when the error is pickled, as it is across a process boundary.
        return (_rebuild_side_load_error, (str(self), self.role, self.adapter))


def _rebuild_side_load_error(message: str, role: Side, adapter: str) -> SideLoadError:
    return SideLoadError(message, role=role, adapter=adapter)


def _admits_records(adapter: Adapter) -> bool:
    """Tell whether an adapter reads through the read loop of `DiffSyncMixin` (`load_record`)."""
    return isinstance(adapter, DiffSyncMixin)


def _side_adapter_name(config: SyncInstance | None, role: Side, adapter: Adapter) -> str:
    """The adapter name of one side: the configured name, else the adapter's type or class name."""
    declared = getattr(config, role, None) if config is not None else None
    name = getattr(declared, "name", None)
    if isinstance(name, str) and name:
        return name
    adapter_type = getattr(type(adapter), "type", None)
    return adapter_type if isinstance(adapter_type, str) and adapter_type else type(adapter).__name__


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from diffsync import Adapter, DiffSyncModel
    from diffsync.diff import Diff
    from diffsync.enum import DiffSyncFlags

    from infrahub_sync import SyncInstance
    from infrahub_sync.cache.cursors import CursorState
    from infrahub_sync.plan.models import PlanManifest, PlannedOperation, PreWriteCheck, VerificationFailure
    from infrahub_sync.plan.ownership import WriteOwnership
    from infrahub_sync.plan.reader import LoadedPlan, RawPlanArtifact
    from infrahub_sync.potenda.read_progress import Side, SideReadObserver


@dataclass(frozen=True)
class PlanCheck:
    """The outcome of the pre-write gate: the plan it parsed and the checks that passed, in order.

    `apply_plan` writes `loaded.operations`; `verify` reports `checks`. Both come from one
    `Potenda.check_plan` call, so the plan `verify` passed is checked exactly as `apply` checks it.
    """

    loaded: LoadedPlan
    checks: tuple[PreWriteCheck, ...]


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
    from infrahub_sync.plan.verify import failure_guidance

    # The next action names each failed check with its own next action, so the hint the CLI
    # prints says what to fix.
    return PlanVerificationError(
        msg, next_action=failure_guidance(failures), checks=[failure.check for failure in failures]
    )


# The destination's uniqueness refusal, as measured: this exact message grammar, and
# `extensions.http_status` 422. Both halves are required. The transport status is 200 even
# for a refused mutation, and `extensions.code` is the generic `UNDEFINED_ERROR`, so neither
# of those can discriminate and neither is consulted.
#
# The pattern is anchored at both ends and the name is bounded to a schema-identifier
# charset, because the capture is the **only** server-authored text this module ever
# renders. A destination can put anything after the quoted name — including something it was
# sent — so a message that is not exactly the measured shape is not parsed for a name at
# all; it takes the category-only summary instead and discloses nothing.
_UNIQUENESS_MESSAGE_PATTERN = re.compile(r"^Violates uniqueness constraint '([A-Za-z0-9_.\-]{1,64})'$")
_UNIQUENESS_HTTP_STATUS = 422


def _uniqueness_refusal_summary(exc: GraphQLError) -> str | None:
    """Name a uniqueness refusal, or return `None` for every other rejection.

    The single, deliberate widening of what server text this module discloses: the
    constraint name, because an operator cannot act on "a destination GraphQL rejection"
    when the cause is a duplicate on a kind with no human-friendly ID. Nothing else from
    the response — not the message body, not the query, not the variables — is rendered.

    Matched only on the measured signature, so a rejection that merely shares its status
    keeps the category-only summary rather than being described as something it is not. The
    constraint name is taken from the pattern's capture and nowhere else, so no text the
    destination appended can ride along with it.
    """
    for error in getattr(exc, "errors", ()) or ():
        if not isinstance(error, Mapping):
            continue
        extensions = error.get("extensions")
        message = error.get("message")
        if not isinstance(extensions, Mapping) or not isinstance(message, str):
            continue
        if extensions.get("http_status") != _UNIQUENESS_HTTP_STATUS:
            continue
        matched = _UNIQUENESS_MESSAGE_PATTERN.match(message.strip())
        if matched is None:
            continue
        constraint = matched.group(1)
        return f"a destination uniqueness constraint {constraint!r} violated (HTTP status {_UNIQUENESS_HTTP_STATUS})"
    return None


def _require_applyable_format(manifest: PlanManifest, *, run_id: str) -> None:
    """Refuse a plan whose format this version can read and review but not apply.

    A format-2 plan records no destination id on its updates, and an update is keyed by that
    id alone. Applying one would fall back to the create-shaped convergent upsert, whose
    failure mode on an incomplete payload is a silent duplicate — so the plan is read,
    rendered for review, and refused at the write.

    A format-3 plan was never checked for problems and carries no count of left-out records,
    so the person cannot confirm what it leaves out (spec 020 SC-006, research R9).
    """
    if manifest.format_version >= REPORT_FORMAT_VERSION:
        return
    reason = (
        "its updates carry no recorded destination id, so they cannot be keyed"
        if manifest.format_version < DESTINATION_ID_FORMAT_VERSION
        else "it was never checked for problems and records no count of the records it leaves out"
    )
    msg = (
        f"The plan artifact of run {run_id!r} declares format version {manifest.format_version}, which "
        f"this version of infrahub-sync can read and review but cannot apply: {reason}. "
        "Nothing was written to the destination."
    )
    raise PlanFormatApplyUnsupportedError(msg)


def _require_left_out_confirmation(manifest: PlanManifest, *, run_id: str, confirm_left_out: bool) -> None:
    """Refuse a plan that leaves out records unless the apply confirms it (spec 020 FR-010).

    The count is the manifest's, which the plan checksum binds, so it is the count of the plan
    this call applies. A plan that leaves out nothing accepts and ignores a confirmation.
    """
    left_out = 0 if manifest.report is None else manifest.report.left_out
    if left_out > 0 and not confirm_left_out:
        raise LeftOutConfirmationRequiredError(left_out, run_id=run_id)


def _validate_infrahub_plan_payload(
    destination: PlannedWriteDestination, operations: Sequence[PlannedOperation], *, run_id: str
) -> bool:
    """Check all Infrahub direct fields before dispatch; other adapters are unchanged.

    Returns whether the check ran, so a destination it does not apply to does not report it
    as passed.
    """
    from infrahub_sync.adapters.infrahub import InfrahubAdapter

    if not isinstance(destination, InfrahubAdapter):
        return False
    try:
        destination.validate_planned_payload_fields(operations)
    except InfrahubSDKError as exc:
        msg = (
            f"The live destination schema for run {run_id!r} could not be checked before apply: "
            f"{_operational_failure_summary(exc)}. Nothing was written to the destination."
        )
        raise PlanVerificationError(
            msg,
            next_action="Check destination access, then retry applying the reviewed plan.",
            checks=["planned_fields"],
        ) from exc
    return True


def _failure_reach_and_remedy(exc: Exception, *, record: ApplyRecord) -> tuple[str, str | None]:
    """How far the failing operation got, and what the operator should do about it.

    Three cases, and the message and the next action have to agree on which one it is.

    A refusal the record marks `failed_operation_wrote is False` attempted no destination
    mutation of its own, so it must not be described as a possible partial write (S6). The
    claim is about **this** operation: operations applied earlier in the plan stay written.

    A uniqueness refusal is the one destination rejection re-applying cannot fix. The plan
    proposes a create, the destination already holds an object the constraint collides with,
    and applying the same plan again proposes the same create and is refused identically —
    so "re-apply, it converges" is a loop, and the remedy is a fresh `plan`.

    Everything else has unknown reach and converges on re-apply (AD033), which is the
    unchanged majority and the default `next_action`.
    """
    if record.failed_operation_wrote is False:
        return (
            "this operation attempted no destination write, so the destination is otherwise as it was",
            OperationApplyFailedError.NOT_WRITTEN_NEXT_ACTION,
        )
    if isinstance(exc, GraphQLError) and _uniqueness_refusal_summary(exc) is not None:
        return (
            "the destination refused this operation for an existing object it conflicts with, so "
            "re-applying the same plan is refused the same way",
            OperationApplyFailedError.UNIQUENESS_NEXT_ACTION,
        )
    return (
        "this operation may itself have written part of its change before failing — re-applying the plan converges it",
        None,
    )


def _operational_failure_summary(exc: Exception) -> str:
    """Return stable operator context without rendering untrusted SDK/server text.

    SDK exceptions can embed a complete GraphQL request or response body in
    ``str(exc)``.  The exception remains chained for an explicitly requested
    developer traceback, while normal CLI output receives only its category, or for a refused
    token or a missing permission the classification, which quotes only the permission (FR-008).
    """
    if (classification := classify_infrahub_error(exc)) is not None:
        return classification.describe()  # Every `AuthenticationError` is classified here.
    if isinstance(exc, ServerNotResponsiveError):
        return "a destination timeout (ServerNotResponsiveError)"
    if isinstance(exc, GraphQLError):
        return _uniqueness_refusal_summary(exc) or "a destination GraphQL rejection (GraphQLError)"
    if isinstance(exc, InfrahubSDKError):
        return f"a destination SDK failure ({type(exc).__name__})"
    # PlanArtifactError and SkippedDeleteOperation are in-tree, purpose-built
    # operator errors. Their detail identifies the affected peer or plan field
    # and is not SDK/server response text.
    return str(exc)


def _nodes_by_kind(adapter: Adapter) -> dict[str, int]:
    """The number of nodes of every top-level kind and stored model, sorted by kind name.

    A top-level kind with no node shows `0` (research R5).
    """
    names = set(adapter.top_level) | set(adapter.get_all_model_names())
    return {name: adapter.count(name) for name in sorted(names)}


def _counted_nodes(side: Side, adapter: Adapter) -> dict[str, int]:
    """`_nodes_by_kind`, or no counts when the adapter's store cannot be counted.

    Counting only reports the read, so a failure here never changes the read's result
    (FR-007): it logs one WARNING with the error class and reports no counts.
    """
    try:
        return _nodes_by_kind(adapter)
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # reporting never fails a read
        logger.warning("Load: could not count the %s side's nodes (%s)", side, type(exc).__name__)
        return {}


class Potenda:
    def __init__(  # pylint: disable=too-many-positional-arguments  # public constructor; callers may pass positionally
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
        concurrent_load: bool = True,
    ):
        """Initialize adapters, execution options, and per-run cache state."""
        self.top_level = top_level
        self.tiers: list[set[str]] | None = tiers
        self.concurrent_load = concurrent_load
        # Cache/run identity — passed at construction so the object is fully
        # valid on return rather than mutated into shape by the caller.
        self.run_dir: Path | None = run_dir
        self.run_id: str | None = run_id
        self.cache_root: Path | None = cache_root
        self._schema_subhash: str = schema_subhash
        self._counts: dict[str, int] = {}
        self._last_plan_action_counts: dict[str, int] | None = None
        self._last_applied_plan_action_counts: dict[str, int] | None = None
        self.configuration_binding = getattr(config, "_configuration_binding", None)
        # The schema identity of the snapshot this run's models were built from, so the plan
        # records the semantics it was actually computed against rather than a second read.
        runtime_models = getattr(config, "_runtime_models", None)
        self.schema_fingerprint: str | None = None if runtime_models is None else runtime_models.schema_fingerprint
        if config is not None and runtime_models is None:
            # A direct run has no validated snapshot; plan derivation reads the same live
            # schema ordering does, so a generic reference expands consistently.
            from infrahub_sync.generic_peers import generic_peers_for_destination

            config._direct_generic_peers = generic_peers_for_destination(destination, None) or None
        self._did_full_extract: bool = False
        # Per-side extraction mode, recorded alongside the OR-accumulated
        # `_did_full_extract` rather than in place of it. FR-015 derives deletes only
        # when the *destination* side ran a full extract, and the OR-accumulated flag
        # cannot answer for one side: it is deliberately true when either side ran
        # full. Absent side means "did not load".
        self._side_full_extract: dict[str, bool] = {}
        self._side_cursors: dict[str, dict[str, CursorState]] = {}
        self._prev_run_resolved: bool = False
        self._prev_run_cached: Path | None = None
        # Runtime toggle set per-command by the CLI just before load.
        self.force_full_extract: bool = False
        # Receives the start and the end of each side's read; set by the caller after
        # construction, as `force_full_extract` is. `None` reports nothing.
        self.read_observer: SideReadObserver | None = None

        self.config = config

        self.source = source
        self.destination = destination

        # diffsync's `Adapter.top_level` is a ClassVar but the library supports per-instance overrides.
        self.source.top_level = top_level  # ty: ignore[invalid-attribute-access]
        self.destination.top_level = top_level  # ty: ignore[invalid-attribute-access]

        self.partition = partition
        self.progress_bar = None
        self.show_progress = show_progress if show_progress is not None else sys.stderr.isatty()

        if verbosity is not None:
            logging.getLogger("diffsync").setLevel(verbosity)

        # Effective DiffSync flags come from the one shared SYNC-78 rule —
        # never interpreted inline here. `config` is typed as SyncInstance but
        # tests pass None — guard explicitly.
        self.flags: DiffSyncFlags = resolve_effective_diffsync_flags(
            self.config.diffsync_flags if self.config is not None else None
        )

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
        """Write each resource snapshot with diagnostic extraction metadata."""
        if not self.run_dir:
            return
        from datetime import datetime, timezone

        from infrahub_sync.cache.incremental import (
            SOURCE_READ_COMPLETE,
            SOURCE_READ_INCOMPLETE,
            SOURCE_READ_METADATA_KEY,
        )
        from infrahub_sync.cache.parquet_io import write_resource_side

        admits = side == "A" and _admits_records(adapter)
        extract_ts = datetime.now(timezone.utc)
        # Snapshot timestamps are diagnostic metadata, never incremental bounds.
        for kind in adapter.top_level:
            records = list(adapter.get_all(kind))
            # Include identifiers for cached-row validation when the adapter supports get_identifiers.
            # Side B additionally carries `local_id`, the destination node id an update is
            # keyed by. Without it a warm run rebuilds destination models with no id and
            # every derived update is refused. It is written as its own column and never
            # conflated with the engine-controlled `_source_id`, which is DiffSync's
            # unique-id string and keys nothing at the destination. Side A carries `local_id`,
            # the source record ID, when the source admits records through `load_record`, so a
            # warm read resolves references to unchanged records (research R22).
            carries_local_id = side == "B" or admits
            rows = [
                {
                    **(r.get_identifiers() if hasattr(r, "get_identifiers") else {}),
                    **r.get_attrs(),
                    **({"local_id": getattr(r, "local_id", None)} if carries_local_id else {}),
                }
                for r in records
            ]
            source_ids = [r.get_unique_id() for r in records]
            metadata = None
            if admits:
                complete = adapter.kind_read_is_clean(kind) and all(  # ty: ignore[unresolved-attribute]
                    source_record_id(getattr(r, "local_id", None)) is not None for r in records
                )
                metadata = {SOURCE_READ_METADATA_KEY: SOURCE_READ_COMPLETE if complete else SOURCE_READ_INCOMPLETE}
            if side == "A":
                self._counts[kind] = len(records)
            write_resource_side(
                run_dir=self.run_dir,
                side=side,
                resource=kind,
                rows=rows,
                source_ids=source_ids,
                extract_ts=extract_ts,
                metadata=metadata,
            )

    def load_one_side(self, *, side: str, adapter: Adapter) -> None:
        """Load one side, choosing incremental vs full based on cursors.

        Falls back to the legacy full-extract path (``adapter.load()``) when
        there is no prior successful run, the schema-subhash mismatches, or
        the caller asked for a full extract.
        """
        from infrahub_sync.cache.cursors import capture_safe_cursors
        from infrahub_sync.cache.incremental import (
            apply_changed_rows,
            hydrate_from_parquet,
            load_cursors,
            required_resource_models,
            should_use_incremental,
            snapshot_carries_local_id,
        )

        # Capture every resource before the first query, including empty resources.
        # Clear earlier candidates so a failed repeated load cannot advance a cursor.
        self._side_cursors.pop(side, None)
        self._side_full_extract[side] = False
        next_cursors = {} if self.force_full_extract else capture_safe_cursors(adapter, adapter.top_level)

        prev_run = self._previous_run()

        use_inc = should_use_incremental(
            prev_run_dir=prev_run,
            current_subhash=self._schema_subhash,
            force_full=self.force_full_extract,
        )

        if not use_inc or prev_run is None:
            adapter.load()
            self._side_full_extract[side] = True
            self._did_full_extract = True
            self._side_cursors[side] = next_cursors
            return

        # Validate all models before querying; a skipped source kind could cause false deletes.
        models = required_resource_models(adapter=adapter, side=side)

        def _add(model_name: str, payload: dict, _adapter: Adapter = adapter) -> None:
            """Add a cached resource row to the selected adapter."""
            _adapter.add(models[model_name](**payload))

        cursors = load_cursors(prev_run / "cursors.json", side=side)
        full_resources: set[str] = set()
        if side == "A" and _admits_records(adapter):
            # A source with the read loop admits its changed records through `load_record`.
            self._load_source_kinds(
                adapter=cast("DiffSyncMixin", adapter),
                prev_run=prev_run,
                models=models,
                cursors=cursors,
                next_cursors=next_cursors,
                full_resources=full_resources,
            )
            self._side_full_extract[side] = full_resources == set(adapter.top_level)
            self._did_full_extract = self._did_full_extract or self._side_full_extract[side]
            self._side_cursors[side] = next_cursors
            return
        for resource in adapter.top_level:
            tier_supported = adapter.cursor_tier_for(resource)  # ty: ignore[unresolved-attribute]
            cursor = cursors.get(resource)
            model_cls = models[resource]
            cached = (prev_run / side / f"{resource}.parquet").is_file()
            if (
                not cached
                or cursor is None
                or not cursor.safe
                or cursor.tier is not tier_supported
                or resource not in next_cursors
            ):
                logger.info("Incremental: no safe cursor or snapshot for %s; loading in full", resource)
                adapter.model_loader(model_name=resource, model=model_cls)  # ty: ignore[unresolved-attribute]
                full_resources.add(resource)
                continue

            # Pre-format-3 destination snapshots lack the local_id required to key updates.
            if side == "B" and not snapshot_carries_local_id(run_dir=prev_run, side=side, resource=resource):
                logger.info(
                    "Incremental: the previous run's destination snapshot for %s carries no local_id column, "
                    "so it cannot key updates; extracting this resource in full instead",
                    resource,
                )
                adapter.model_loader(model_name=resource, model=model_cls)  # ty: ignore[unresolved-attribute]
                full_resources.add(resource)
                continue

            hydrate_from_parquet(
                run_dir=prev_run,
                side=side,
                resource=resource,
                add_row=_add,
            )
            apply_changed_rows(adapter, resource, model_cls, cursor)
        # Only successful full resource loads establish a complete side.
        self._side_full_extract[side] = full_resources == set(adapter.top_level)
        self._did_full_extract = self._did_full_extract or self._side_full_extract[side]
        self._side_cursors[side] = next_cursors

    def _load_source_kinds(  # pylint: disable=too-many-arguments
        self,
        *,
        adapter: DiffSyncMixin,
        prev_run: Path,
        models: Mapping[str, type[DiffSyncModel]],
        cursors: Mapping[str, CursorState],
        next_cursors: Mapping[str, CursorState],
        full_resources: set[str],
    ) -> None:
        """Read each kind of a source with the read loop, warm where that gives a cold read's result.

        A kind reads warm only when the adapter implements `list_changed_records`, the previous
        snapshot of the kind is marked complete, and no peer kind it references was unclean
        earlier in this read. A warm read that records a load report entry is discarded and the
        kind is read cold. A kind whose read records an entry, or whose warm read stored a
        changed record under another unique ID, is unclean (research R22).
        """
        from infrahub_sync.cache.incremental import (
            hydrate_from_parquet,
            referenced_kinds,
            source_snapshot_is_complete,
        )

        unclean: set[str] = set()
        for resource in adapter.top_level:
            model_cls = models[resource]
            cursor = cursors.get(resource)
            reason = None
            if (
                cursor is None
                or not cursor.safe
                or cursor.tier is not adapter.cursor_tier_for(resource)
                or resource not in next_cursors
            ):
                reason = "no safe cursor"
            elif not adapter.implements_list_changed_records():
                reason = "the source adapter does not implement list_changed_records"
            elif not source_snapshot_is_complete(run_dir=prev_run, resource=resource):
                reason = "the previous snapshot is missing, older, or holds records the read left out"
            elif referenced_kinds(self.config, resource) & unclean:
                reason = "a peer kind it references was read with problems or renamed records"
            if reason is None:
                start = len(adapter.load_report.entries)
                hydrate_from_parquet(
                    run_dir=prev_run,
                    side="A",
                    resource=resource,
                    add_row=lambda _name, payload, _model=model_cls: adapter.restore_record(_model, payload),
                )
                identities_changed = adapter.load_changed_records(resource, model_cls, cast("CursorState", cursor))
                if adapter.kind_read_is_clean(resource):
                    if identities_changed:
                        # Unchanged records that reference a renamed record hold its old identity.
                        unclean.add(resource)
                    continue
                adapter.discard_kind(resource, entries_from=start)
                reason = "the changed records hold problems"
            logger.info("Incremental: reading source kind %s in full (%s)", resource, reason)
            adapter.model_loader(model_name=resource, model=model_cls)
            full_resources.add(resource)
            if not adapter.kind_read_is_clean(resource):
                unclean.add(resource)

    def _load_side(self, *, side: str, adapter: Adapter) -> None:
        """Keep cursors publishable only when loading and snapshotting both succeed.

        Reports the start and the end of the read to `read_observer`, and logs the end. Side
        `A` is the source and side `B` the destination. A read succeeds only when the load and
        the snapshot both return; its node counts are taken after the snapshot is written.
        """
        side_name: Side = "source" if side == "A" else "destination"
        self._notify_read_observer(side_name, None)
        completed = False
        try:
            logger.info("Load: Importing data from %s", adapter)
            self.load_one_side(side=side, adapter=adapter)
            self._write_side_snapshot(side, adapter)
            completed = True
        except Exception as exc:
            # The class name only: the message can hold an address or a secret (FR-013).
            self._end_read(side_name, SideReadReport(succeeded=False, error_class=type(exc).__name__))
            msg = f"An error occurred while loading {adapter}: {exc!s}"
            raise SideLoadError(
                msg, role=side_name, adapter=_side_adapter_name(self.config, side_name, adapter)
            ) from exc
        finally:
            if not completed:
                self._side_cursors.pop(side, None)
        self._end_read(
            side_name,
            SideReadReport(
                succeeded=True,
                read_mode="full" if self._side_full_extract.get(side, False) else "incremental",
                nodes_by_kind=_counted_nodes(side_name, adapter),
            ),
        )

    def _end_read(self, side: Side, report: SideReadReport) -> None:
        """Report the end of one side's read to the observer, then log it."""
        self._notify_read_observer(side, report)
        if not report.succeeded:
            logger.info("Load: %s read failed (%s)", side, report.error_class)
            return
        total = sum(report.nodes_by_kind.values())
        counts = ", ".join(f"{kind} {count}" for kind, count in report.nodes_by_kind.items())
        logger.info(
            "Load: %s read succeeded (%s): %d nodes%s", side, report.read_mode, total, f"; {counts}" if counts else ""
        )

    def _notify_read_observer(self, side: Side, report: SideReadReport | None) -> None:
        """Call the observer's start method, or its end method when `report` is given.

        An observer that raises never changes the read's result (FR-007): its failure is
        logged once, by error class only, and the read continues.
        """
        observer = self.read_observer
        if observer is None:
            return
        try:
            if report is None:
                observer.read_started(side)
            else:
                observer.read_ended(side, report)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # an observer never fails a read
            logger.warning("Load: the read observer failed for the %s side (%s)", side, type(exc).__name__)

    def source_load(self):
        """Load the source and write its snapshot."""
        self._load_side(side="A", adapter=self.source)

    def destination_load(self):
        """Load the destination and write its snapshot."""
        self._load_side(side="B", adapter=self.destination)

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
        The destination's lineage is resolved first, then the destination decides whether its
        reads ask for metadata (a server version read for Infrahub). Both happen before either
        side is loaded, and `metadata_read` is set before the destination's cursors are captured.
        A lineage refusal, or a version read error that propagates (an HTTP 401 or a transport
        error), stops the plan before the source system is read. A version query that Infrahub
        refuses with a GraphQL error or a missing permission does not stop the plan: the plan
        continues without reading metadata (see `InfrahubAdapter.resolve_metadata_read`).
        """
        resolve_destination_lineage(self.destination)
        resolve_destination_metadata_read(self.destination)
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

    def _diff_to_rows(self, diff: Any) -> list[dict[str, str]]:
        """Materialize a diffsync.Diff into plan-row dicts (one per change)."""
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

        `plan.parquet` remains available for operator queries (V23); `apply_plan` reads
        only `<run_dir>/plan/` and refuses parquet-only runs. This method runs before any
        destination write (FR-001), including on the one-diff path.

        Returns the saved artifact's in-memory per-action counts, or `None` when no
        saved artifact can be written. For `operation="plan"`, the shared execution
        surface uses those counts instead of the narrower legacy parquet rows; legacy
        behavioral engines that return nothing retain the row fallback.
        """
        if not self.run_dir:
            self.write_plan_artifact([diff])
            return None
        from infrahub_sync.cache.parquet_io import write_plan

        write_plan(run_dir=self.run_dir, rows=self._diff_to_rows(diff))
        self._last_plan_action_counts = None
        self.write_plan_artifact([diff])
        return self._last_plan_action_counts

    def write_plan_artifact(self, diffs: Sequence[Any]) -> PlanManifest | None:
        """Derive and write `<run_dir>/plan/` for `diffs`, before any destination write.

        Infrahub identity checks run even without artifact storage and require a
        parsed configuration. Other destinations without storage skip derivation.
        With a cache identity, bind the source snapshot and configuration version,
        then write the artifact and return its manifest; otherwise return `None`.
        A sequence supports the tier path's one diff per tier. Derivation and write
        failures propagate before any destination write (FR-030, AD047).
        """
        written = self._write_plan_artifact(diffs)
        return None if written is None else written[0]

    def _write_plan_artifact(self, diffs: Sequence[Any]) -> tuple[PlanManifest, dict[str, int]] | None:
        """Check Infrahub identities even without storage, then write the artifact and its action counts."""
        if (not self.run_dir or not self.run_id) and getattr(self.destination, "type", None) != "Infrahub":
            logger.debug("Plan artifact: skipped, this run has no run_dir/run_id")
            return None
        if self.config is None:
            if getattr(self.destination, "type", None) == "Infrahub":
                msg = (
                    "Infrahub planning requires a parsed configuration to check source identities. "
                    "No destination write was attempted."
                )
                raise PlanVerificationError(
                    msg,
                    next_action="Supply the parsed configuration, then re-run `plan`.",
                )
            logger.debug("Plan artifact: skipped, this run has no config")
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
        from infrahub_sync.plan.last_changes import selected_last_changes
        from infrahub_sync.plan.models import SourceSnapshotRecord
        from infrahub_sync.plan.problems import run_problem_pass
        from infrahub_sync.plan.writer import write_plan_artifact as write_artifact
        from infrahub_sync.runtime_schema.worker import planned_dropdown_choices

        resolve_tier = partial(tier_of, tiers=self.tiers, top_level=self.top_level)
        last_changes = destination_last_changes(self.destination)  # FR-007; `None` writes no last-change file
        operations = []
        for diff in diffs:
            operations.extend(
                operations_from_diff(
                    diff,
                    config=self.config,
                    tier_of=resolve_tier,
                    source_adapter=self.source,
                    destination_adapter=self.destination,
                    last_changes=last_changes,
                )
            )

        # FR-015: deletes are derived only where the destination side holds a complete
        # picture, and the manifest records which of the two happened. A source hydrated
        # from a prior snapshot plus changed-since rows still holds objects deleted at the
        # source, so the source must also be fully extracted before deletes count as computed.
        deletes_computed = self._side_full_extract.get("A", False) and self._side_full_extract.get("B", False)
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
        identity_groups = warn_missing_convergence_key(
            destination=self.destination, operations=operations, source_adapter=self.source, config=self.config
        )
        # Spec 020 research R3: find every problem, then leave out the records that have one and
        # the records that depend on them, before anything is written. The report and the
        # selected operations are the plan; no store and no derived operation is changed.
        result = run_problem_pass(
            source=self.source,
            destination=self.destination,
            operations=operations,
            config=self.config,
            identity_groups=identity_groups,
            dropdown_choices=planned_dropdown_choices(self.config),
        )
        operations = result.operations
        action_counts: dict[str, int] = dict.fromkeys(ACTIONS, 0)
        for operation in operations:
            action_counts[operation.action] += 1

        if not self.run_dir or not self.run_id:
            logger.debug("Plan artifact: skipped after the problem pass, this run has no run_dir/run_id")
            return None

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
            configuration_binding=self.configuration_binding,
            schema_fingerprint=self.schema_fingerprint,
            lineage=destination_lineage_record(self.destination),
            last_changes=None if last_changes is None else selected_last_changes(last_changes.records, operations),
            metadata_read=last_changes is not None and last_changes.metadata_read,
            report=result.report,
            dropdown_values_checked=result.dropdown_values_checked,
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

    def apply_plan(  # pylint: disable=too-many-statements  # one sequential gate-then-write flow; splitting would hide the order
        self,
        *,
        ownership: WriteOwnership,
        config_version: str | None = None,
        artifact: RawPlanArtifact | None = None,
        expected_checksum: str | None = None,
        confirm_left_out: bool = False,
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
            ownership: The write-ownership boundary this apply dispatches through. It is
                proven immediately before every operation the loop dispatches and once
                after the last one, so a hold lost mid-apply stops the next write instead
                of being discovered afterwards. It is required and has no default: an
                apply that could run without one would run without one.
            config_version: The comparison value for the configuration-version check,
                compared for equality and never parsed. `None` recomputes it from the
                parsed configuration by the default rule (FR-011, AD013).
            artifact: The plan artifact's bytes, when the caller has already read them.
                `None` reads them here. Either way it is read once per apply.
            expected_checksum: An approved plan checksum the artifact must hash to. The
                **authoritative** answer to `--expected-checksum`, because it is given about
                the bytes this call applies; a caller's earlier check is a fast path, not a
                substitute. `None` asks for no approval comparison at all.
            confirm_left_out: The person confirmed that they reviewed the records the plan
                leaves out. Required when the plan's report leaves out any record, and
                ignored otherwise (spec 020 FR-010, FR-011).

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
            PlanFormatApplyUnsupportedError: the plan's format is 2 or 3; nothing was written.
            LeftOutConfirmationRequiredError: the plan leaves out records and
                `confirm_left_out` is false; nothing was written.
            OperationApplyFailedError: an operation failed inside the operational boundary
                (`OPERATIONAL_APPLY_FAILURES`), carrying the partial record.
            ApplyRecordInvariantError: a completed apply's record does not account for every
                operation in the plan (AD062). Carries the record it is complaining about.
            BaseException: anything outside that boundary — an interrupt, or a defect such as
                a `TypeError` from an SDK shape change — propagates **unchanged** with the
                partial record attached as `apply_record`.
        """
        from infrahub_sync.plan.reader import read_plan_artifact_bytes, require_plan_directory

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

        # The whole pre-write gate, on those bytes. `verify` runs the same function and stops
        # here, so the two cannot disagree about whether a plan would apply (SC-004).
        loaded = self.check_plan(
            run_id=run_id, config_version=comparison_version, artifact=raw, expected_checksum=expected_checksum
        ).loaded
        # The gate evaluated the write-surface check, so passing it proves the surface is
        # present — the cast narrows for the type checker; it is not a second gate.
        destination = cast("PlannedWriteDestination", self.destination)

        # The person's confirmation of the records the plan leaves out. Checked after
        # `check_plan`, not inside it: `verify` takes no confirmation, so it does not run this
        # check. The Sync API checks this too, and a direct caller of this method cannot skip
        # it; refused before the first dispatch, so nothing is written.
        _require_left_out_confirmation(loaded.manifest, run_id=run_id, confirm_left_out=confirm_left_out)
        self._last_applied_plan_action_counts = {
            action: sum(operation.action == action for operation in loaded.operations) for action in ACTIONS
        }

        # One memo for the whole apply, discarded with it — the same lifetime as the run. The
        # destination supplies it, so the engine builds a resolver for a destination it does
        # not have to name (AD086).
        peers = destination.new_peer_resolver()

        applied: list[str] = []
        skipped_deletes: list[str] = []
        # Read from the plan for the failure record of a stopped apply (spec 020 FR-014).
        operations_total = len(loaded.operations)
        for operation in loaded.operations:
            if operation.action == "delete":
                skipped_deletes.append(operation.operation_id)
                continue
            try:
                ownership.before_operation()
            except BaseException as exc:
                # A lost hold dispatched nothing, so this operation is named in neither
                # set and is **not** recorded as possibly half-written.
                # The suppression is not masking a defect — no annotation can declare an
                # attribute on an exception type this module does not own.
                exc.apply_record = ApplyRecord(  # ty: ignore[unresolved-attribute]
                    applied_operations=tuple(applied),
                    skipped_delete_operations=tuple(skipped_deletes),
                    operations_total=operations_total,
                )
                raise
            try:
                destination.apply_planned_operation(operation=operation, peers=peers)
            except BaseException as exc:
                # The partial record travels on the error so the CLI can merge what was
                # written before it records `failed` (AD069). Re-raising bare would lose it.
                partial = ApplyRecord(
                    applied_operations=tuple(applied),
                    skipped_delete_operations=tuple(skipped_deletes),
                    # Named on the record because a mutation can commit remotely before its
                    # response or transport fails, so this operation may have changed the
                    # destination while belonging to neither recorded set.
                    failed_operation=operation.operation_id,
                    # Carried up from the refusal itself: only the write surface knows
                    # whether it got as far as a mutation. `None` for anything that does not
                    # say, which is the unchanged "may have written" reading (S6).
                    failed_operation_wrote=getattr(exc, "wrote", None),
                    operations_total=operations_total,
                    failed_operation_detail={
                        "action": operation.action,
                        "kind": operation.kind,
                        "identity": dict(operation.identity),
                    },
                )
                if not isinstance(exc, OPERATIONAL_APPLY_FAILURES):
                    # An interrupt or a defect: it propagates as itself, with its own
                    # traceback, carrying the record so what was written stays readable from the
                    # run (AD062). See `OPERATIONAL_APPLY_FAILURES` for the boundary.
                    # The suppression is not masking a defect — no annotation can declare an
                    # attribute on an exception type this module does not own.
                    exc.apply_record = partial  # ty: ignore[unresolved-attribute]
                    raise
                reach, next_action = _failure_reach_and_remedy(exc, record=partial)
                msg = (
                    f"Applying operation {operation.operation_id!r} of run {run_id!r} to the destination "
                    f"failed with {_operational_failure_summary(exc)}. The {len(applied)} operation(s) "
                    f"applied before it stay written, and {reach}."
                )
                raise OperationApplyFailedError(msg, apply_record=partial, next_action=next_action) from exc
            applied.append(operation.operation_id)

        completed = ApplyRecord(
            applied_operations=tuple(applied),
            skipped_delete_operations=tuple(skipped_deletes),
        )

        # The closing proof, before anything may record this apply as complete: a hold
        # lost after the last operation means nobody can say the destination was left as
        # the plan describes, so it must not become a success.
        try:
            ownership.after_final_operation()
        except BaseException as exc:
            exc.apply_record = completed  # ty: ignore[unresolved-attribute]
            raise

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

    def check_plan(
        self,
        *,
        run_id: str,
        config_version: str | None,
        artifact: RawPlanArtifact,
        expected_checksum: str | None = None,
    ) -> PlanCheck:
        """Run the pre-write gate on `artifact`; return the parsed plan and the checks that passed.

        The gate `apply_plan` runs before its first write, and nothing after it: no ownership
        is proven and no destination write method is called, so `verify` runs it to learn
        whether the plan would apply now. The checks read the destination (its lineage, and
        for Infrahub its schema) but never write to it.

        `expected_checksum=None` skips only the approval comparison. Every other check runs.
        A check that does not apply to this destination — the planned-field check outside
        Infrahub, the lineage check for a destination that records no lineage — is skipped
        and not reported as passed.

        Raises:
            ValueError: no configuration version can be formed.
            PlanVerificationError: a check failed; `checks` on the error names it.
            PlanFormatApplyUnsupportedError: the plan's format can be reviewed but not applied.
            PlanArtifactTornError: an operations record fails validation.
            UnsupportedOperationActionError: an operation's `action` is outside `ACTIONS`.
            ReviewedPayloadFieldMissingError: the destination schema lacks a planned field.
            PlanLineageMissingError: the plan records no import account (lineage).
            ImportAccountMismatchError: the import account differs from the plan's (lineage).
            NamedLineageNodeNotFoundError: a named lineage node does not exist (lineage).
        """
        from infrahub_sync.plan.reader import parse_plan_artifact
        from infrahub_sync.plan.review import expected_checksum_refusal
        from infrahub_sync.plan.verify import verify_plan

        comparison_version = self._apply_config_version(config_version)
        raw = artifact
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
        # `last_changes.jsonl` is display-only: the gate above checked it, and apply never reads it.
        loaded = parse_plan_artifact(raw, run_id=run_id, parse_last_changes=False)

        # Readable and reviewable, but not applyable. Refused after the artifact has been
        # read, before `ownership.before_operation` and therefore before the first dispatch,
        # so the destination is provably untouched.
        _require_applyable_format(loaded.manifest, run_id=run_id)
        checks: list[PreWriteCheck] = [
            "run_binding",
            "plan_checksum",
            "source_snapshot",
            "config_version",
            "format_version",
            "write_surface",
        ]

        # The registered service has a schema fingerprint guard, but direct Python apply
        # callers need the same no-write guarantee for reviewed Infrahub payload fields.
        # Check the entire artifact before dispatching its first operation.
        if _validate_infrahub_plan_payload(destination, loaded.operations, run_id=run_id):
            checks.append("planned_fields")

        # Resolved again before the first write, never reused; compared with the plan's (FR-012).
        resolve_destination_lineage(destination, saved_plan=loaded.manifest)
        if isinstance(destination, LineageResolvingDestination):
            checks.append("lineage")
        return PlanCheck(loaded=loaded, checks=ordered_checks(checks))

    def persist_cursors_for_run(self, *, side: str) -> None:
        """Persist source-guaranteed bounds captured before a successful side load.

        Snapshot timestamps, including those of empty resources, do not establish
        a safe changed-since bound. Callers still own the successful-run lifecycle.
        """
        if not self.run_dir:
            return
        from infrahub_sync.cache.incremental import persist_cursors

        cursors = {
            resource: cursor
            for resource, cursor in self._side_cursors.get(side, {}).items()
            if (self.run_dir / side / f"{resource}.parquet").exists()
        }
        if cursors:
            persist_cursors(self.run_dir / "cursors.json", side=side, cursors=cursors)
