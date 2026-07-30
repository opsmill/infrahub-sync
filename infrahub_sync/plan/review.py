"""The review surface: FR-029's single supported reading entry point.

`read_saved_plan` returns **data** — never rendered text — so a caller consumes a saved plan
without parsing output, and the command-line review mode is a thin renderer over this object
that re-implements no reading, filtering or summarizing (FR-029).

It constructs **no adapter**, extracts nothing, takes **no** pipeline lock, creates or
modifies nothing in the run directory, and never mutates run state (FR-008, AD021, AD031).
A plan that would fail apply verification is **rendered anyway**, with a verification note
saying why — and with `checksum_ok` false when the failing check is the checksum itself; a
plan bound to a torn source snapshot checksums clean and is disclosed by the note alone
(FR-010). The one bound is an operation whose `action` this release cannot interpret, which
is refused while reading (AD031, AD055).

Two obligations sit on the **renderer**, not here: turning an empty `operations(kind=…)`
result into FR-006's error, and annotating `deletes_not_executed`. Both are presentation
rules, and a programmatic caller must not have to catch an exception to learn a count
(AD058).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.cache.paths import cache_root_for, run_dir
from infrahub_sync.plan.errors import UnknownPlanKindError, UnknownRunIdentifierError, UnsafeRunIdentifierError
from infrahub_sync.plan.models import PlanSummary
from infrahub_sync.plan.reader import (
    RUN_ID_LISTING_LIMIT,
    load_plan_artifact,
    run_id_listing_text,
    stat_or_unreadable,
    stored_run_ids,
)
from infrahub_sync.plan.verify import plan_checksum_failure, source_snapshot_failures
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, PLAN_DIR_NAME

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from infrahub_sync import SyncConfig
    from infrahub_sync.plan.models import PlanManifest, PlannedOperation, VerificationFailure

# Re-exported from the reader, which owns the bound and the enumeration's wording because
# FR-008 puts the same enumeration on the arm that module raises (AD073). Kept importable from
# here: this is the module the review surface is read from.
__all__ = ["RUN_ID_LISTING_LIMIT", "SavedPlan", "read_saved_plan", "require_stored_run"]


class SavedPlan:
    """One saved plan, read back as data (FR-029).

    Attributes:
        manifest: The manifest as read, tolerated unknown fields included (FR-027).
        checksum_ok: Whether the recomputed `plan_checksum` matches the recorded one — that
            check alone, and not a verdict on the whole artifact: the checksum covers the
            manifest and the operations file and says nothing about the source snapshot, so
            a run bound to a torn snapshot has `checksum_ok` true and a note. Reported,
            never a refusal: review renders a plan that would fail verification rather than
            withholding it (AD031).
        verification_notes: Human-readable notes about any check that did not pass — the
            plan checksum, and the source-snapshot binding FR-010 puts on this path too.
            An empty list is the only "nothing to report" answer.
    """

    def __init__(
        self,
        *,
        manifest: PlanManifest,
        operations: Iterable[PlannedOperation],
        checksum_ok: bool,
        verification_notes: Iterable[str],
        declared_kinds: Iterable[str] | None = None,
    ) -> None:
        self.manifest = manifest
        self.checksum_ok = checksum_ok
        self.verification_notes = list(verification_notes)
        self._operations = list(operations)
        # `None` means no configuration was supplied, so declaration is unknowable and the
        # plan's own kinds are the only vocabulary available.
        self._declared_kinds = None if declared_kinds is None else frozenset(declared_kinds)

    @property
    def _plan_kinds(self) -> set[str]:
        """The destination kinds the plan actually holds operations for."""
        return {operation.kind for operation in self._operations}

    def summary(self) -> PlanSummary:
        """Return the counts a review renders, plus AD056's two disclosure fields.

        `delete_operations_computed` is carried up from the manifest and
        `deletes_not_executed` is the plan's delete count. Both are derived **on read**, so
        the artifact format and `plan_checksum` are untouched. Without the first, a plan
        whose whole delete class was never computed renders identically to a plan that has
        no deletes, and FR-015's "explicit and reviewable" claim is carried by nothing.
        """
        by_action: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for operation in self._operations:
            by_action[operation.action] = by_action.get(operation.action, 0) + 1
            by_kind[operation.kind] = by_kind.get(operation.kind, 0) + 1
        return PlanSummary(
            by_action=dict(sorted(by_action.items())),
            by_kind=dict(sorted(by_kind.items())),
            total=len(self._operations),
            delete_operations_computed=self.manifest.delete_operations_computed,
            deletes_not_executed=by_action.get("delete", 0),
        )

    def operations(self, *, kind: str | None = None) -> list[PlannedOperation]:
        """Return the plan's operations, optionally narrowed to one destination kind.

        A `kind` the configuration **declares** but the plan holds no operation for returns
        `[]` — a legitimate answer. Only a `kind` the configuration does **not** declare
        raises (AD058). When no configuration was supplied, declaration is unknowable and
        the plan's own kinds are the vocabulary.

        Raises:
            UnknownPlanKindError: the configuration was supplied and does not declare `kind`
                — whether or not the plan holds operations for it — or no configuration was
                supplied and the plan does not hold it. The message lists the kinds the plan
                does hold, so the operator picks from what exists rather than guessing again
                (AD059).
        """
        if kind is None:
            return list(self._operations)
        held = self._plan_kinds
        # Where a configuration was supplied it is the *sole* authority on which kinds exist
        # for this synchronization, so an undeclared kind raises whether or not the plan
        # happens to hold operations for it — a plan can outlive the configuration that
        # produced it, and answering for a kind the operator has since removed reports on a
        # synchronization that no longer exists. Only without a configuration is declaration
        # unknowable, and there the plan's own kinds are the vocabulary.
        vocabulary = held if self._declared_kinds is None else self._declared_kinds
        if kind not in vocabulary:
            held_text = ", ".join(sorted(held)) if held else "<none: the plan contains no operations>"
            msg = (
                f"No destination kind {kind!r} is declared for this synchronization. The plan holds "
                f"operations for: {held_text}."
            )
            raise UnknownPlanKindError(msg)
        return [operation for operation in self._operations if operation.kind == kind]


def _run_directory_exists(directory: Path) -> bool:
    """Whether `directory` exists, raising rather than answering `False` when unreadable.

    `stat_or_unreadable` is used instead of `Path.is_dir()` for the reason given at its own
    definition: `is_dir()` swallows `PermissionError` and answers `False`, which would
    present an unreadable run as an unknown one and send the operator to the wrong remedy
    (AD036).
    """
    return stat_or_unreadable(directory, description="run directory") is not None


def _unknown_run_error(sync_name: str, run_id: str, expected_artifact: Path) -> UnknownRunIdentifierError:
    """Build the unknown-run refusal, with the enumeration AD073 requires (AD059).

    The enumeration's wording is the reader's, because FR-008 requires the same listing when a
    run is located but holds no plan artifact — a verdict `require_plan_directory` raises — and
    two hand-written listings would drift.
    """
    cache_root = cache_root_for(sync_name)
    stored = stored_run_ids(cache_root)
    msg = (
        f"No run {run_id!r} is stored for synchronization {sync_name!r}: the plan artifact was "
        f"expected at {expected_artifact}. {run_id_listing_text(stored, cache_root=cache_root)}"
    )
    if not stored:
        return UnknownRunIdentifierError.no_runs(msg, sync_name=sync_name)
    return UnknownRunIdentifierError(msg)


def _snapshot_note(failure: VerificationFailure) -> str:
    """Render one source-snapshot failure as a review note (FR-010).

    The note carries the three things FR-010 requires of every torn-artifact refusal —
    which part is torn (the failure's `expected` and `found` both lead with the snapshot's
    run-relative path), the expected and found values, and the next action — because the
    review path is one of the two paths a torn artifact is reachable from.
    """
    return (
        f"The source-snapshot binding does not hold: expected {failure.expected}; found "
        f"{failure.found}. Applying this run would refuse. {failure.next_action}"
    )


def require_stored_run(sync_name: str, run_id: str) -> Path:
    """Return the run's directory, refusing with the enumerated message when it is absent.

    The **single** raising site for the unknown-run refusal, shared by the review path below
    and by the CLI's apply guard, so AD073's bounded enumeration and AD059's next action are
    written once and cannot drift between the two commands an operator reaches them from.

    Raises:
        UnsafeRunIdentifierError: the identifier is not a single path segment (FIX-004).
        UnknownRunIdentifierError: no run with that identifier is stored. The message lists
            the most recent stored identifiers, or states plainly that the sync has no
            stored runs at all (AD073).
        PlanArtifactUnreadableError: the run directory or the cache root exists but could
            not be examined or listed (AD036).
    """
    # `run_dir` applies `_require_safe_segment`'s traversal guard to **both** arguments
    # (`infrahub_sync.cache.paths`), so a `..` or absolute value is
    # rejected before any path is joined — as a `ValueError`, which is translated into the
    # taxonomy here rather than left to escape as a traceback out of the two commands that
    # reach this function (FIX-004, spec 002). The translation lives at the single raising
    # site, so neither guard has to catch a second exception type to stay one line of output.
    try:
        directory = run_dir(sync_name, run_id)
    except ValueError as exc:
        msg = (
            f"Run identifier {run_id!r} is not usable: a run id names one directory under the "
            f"synchronization's cache root, so it cannot contain '/' or '..' segments or be an "
            f"absolute path ({exc})."
        )
        raise UnsafeRunIdentifierError(msg) from exc
    if not _run_directory_exists(directory):
        raise _unknown_run_error(sync_name, run_id, directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME)
    return directory


def read_saved_plan(
    *,
    sync_name: str,
    run_id: str,
    config: SyncConfig | None = None,
) -> SavedPlan:
    """Read the plan artifact stored for one run and return it as data (FR-029).

    Args:
        sync_name: The synchronization's name, used to locate its cache root.
        run_id: The stored run to read.
        config: Optional, and used for **one** thing: deciding whether a `kind` filter names
            a kind the configuration declares (FR-006). Review is otherwise
            configuration-independent once the run is located.

    Returns:
        A `SavedPlan`. Data, never rendered text, so SC-010's canary scan can scan the
        returned value as data rather than parsed output.

    Raises:
        UnsafeRunIdentifierError: the identifier is not a single path segment (FIX-004).
        UnknownRunIdentifierError: no run with that identifier is stored. The message lists
            the most recent stored identifiers, or states plainly that the sync has no
            stored runs (AD073).
        PlanFormatV1Error: the run exists but holds no `plan/` directory (FR-019).
        PlanArtifactTornError: the artifact is present but incomplete (FR-010).
        PlanFormatVersionError: the artifact's `format_version` is unsupported (FR-027).
        PlanArtifactUnreadableError: a path exists but could not be read (AD036).
        UnsupportedOperationActionError: an operation's `action` is outside `ACTIONS`. The
            review path refuses here, with the same message the apply path shows: this is
            the one bound on AD031's "review renders rather than refuses", which is scoped
            to verification failures (AD055).
    """
    directory = require_stored_run(sync_name, run_id)

    loaded = load_plan_artifact(directory)
    # Both checks below are routed through the verifier's own implementations rather than
    # recomputed here, so the review verdicts and the apply refusals cannot drift (FR-010).
    checksum_failure = plan_checksum_failure(
        run_id=run_id,
        manifest_mapping=loaded.manifest_mapping,
        operations_bytes=loaded.operations_bytes,
    )
    notes: list[str] = []
    if checksum_failure is not None:
        notes.append(
            f"The plan checksum does not match: the manifest records {checksum_failure.expected!r} "
            f"and the artifact's contents hash to {checksum_failure.found!r}. "
            f"{checksum_failure.next_action}"
        )
    # FR-010's snapshot half, on the review path. The plan checksum covers the manifest and
    # the operations file and says nothing about the snapshot the plan was computed against,
    # so without this a run whose snapshot was deleted or truncated renders `checksum: OK`
    # with no note — a safety check reporting a result it never computed.
    notes.extend(
        _snapshot_note(failure)
        for failure in source_snapshot_failures(run_id=run_id, run_dir=directory, mapping=loaded.manifest_mapping)
    )
    declared_kinds = None if config is None else [entry.name for entry in config.schema_mapping]
    return SavedPlan(
        manifest=loaded.manifest,
        operations=loaded.operations,
        checksum_ok=checksum_failure is None,
        verification_notes=notes,
        declared_kinds=declared_kinds,
    )
