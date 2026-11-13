"""The plan artifact writer (FR-004, FR-010, FR-019, FR-021, FR-022, FR-026, FR-027).

`operations.jsonl` is written **first**, then `last_changes.jsonl` when the plan has one, then
`report.jsonl` (plan format 4, spec 020), and `manifest.json` **last**; that order is the commit point (AD014): the manifest's presence is
what makes the artifact complete, so a failure part-way through the operations write leaves a
run directory with a `plan/` directory and no manifest — which the reader classifies as **torn** — while a run that
never reached this writer has no `plan/` directory at all and is classified as the
pre-existing v1 format. The two verdicts are therefore disjoint by construction rather
than by heuristic.

A committed generation is also **final**: `require_uncommitted_plan` refuses to write into a
run whose manifest already exists, so re-planning allocates a new run id rather than
replacing a plan a human may have approved.

Both writes go through one helper, `_atomic_write_bytes`, in the tmp+`Path.replace`
discipline already used for the run sidecars, so neither file is ever observed
half-written. Routing both through a single helper is also what makes the write order
observable to a test.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.errors import DuplicateOperationIdError, NotInSourcePeerInPlanError, PlanGenerationExistsError
from infrahub_sync.plan.models import (
    LAST_CHANGES_FILE_NAME,
    PLAN_FORMAT_VERSION,
    REPORT_FILE_NAME,
    PlanLastChangesRecord,
    PlanManifest,
    PlanReport,
    holds_not_in_source_peer,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync.plan.models import (
        DestinationBindingRecord,
        LastChangeRecord,
        PlanLineageRecord,
        PlannedOperation,
        SourceSnapshotRecord,
    )

# The artifact's directory and its files, in the order they are written. The last-change
# file is defined with its manifest record in `models.py` and re-exported here.
PLAN_DIR_NAME = "plan"
OPERATIONS_FILE_NAME = "operations.jsonl"
MANIFEST_FILE_NAME = "manifest.json"

__all__ = [
    "LAST_CHANGES_FILE_NAME",
    "MANIFEST_FILE_NAME",
    "OPERATIONS_FILE_NAME",
    "PLAN_DIR_NAME",
    "REPORT_FILE_NAME",
    "committed_manifest_path",
    "encode_last_changes",
    "require_uncommitted_plan",
    "write_plan_artifact",
]


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` to `path` via tmp+`Path.replace`, creating the parent directory.

    The same discipline as the run sidecars', in bytes rather than text because the
    artifact's encoding is fixed by `canonical_json_bytes` and must not be re-encoded by a
    text layer. Both artifact files are written through this one function, which is what
    lets a test observe that the operations file goes first.

    **Accepted tradeoff: no fsync.** Neither the temporary file's data nor the
    parent directory entry is fsynced, so "never observed half-written" is a guarantee
    against *process* failure — a crash or an exception between the two writes — and not
    against power loss or a filesystem crash, where a `replace` may be durable while the
    data it points at is not. The consequence is bounded: an artifact whose bytes did not
    survive fails the checksum comparison or classifies as torn, and is refused before any
    destination write. Adding fsync would be a deliberate change to the run directory's
    durability contract, which the run sidecars share and do not currently make.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        Path(tmp_name).replace(path)
    except BaseException:
        # Best-effort cleanup of the tmp file on any failure. Best-effort means its own
        # failure is suppressed: a cleanup `PermissionError` superseding the
        # write or replace error would hide the very failure — ENOSPC above all — that
        # explains the torn artifact.
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink(missing_ok=True)
        raise


def committed_manifest_path(run_dir: Path) -> Path:
    """The path whose existence means this run holds a committed plan generation (AD014)."""
    return run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME


def require_uncommitted_plan(run_dir: Path, *, run_id: str) -> None:
    """Refuse to write over a plan generation that was already committed.

    A generation is committed once its manifest exists, and a committed generation is never
    overwritten: `diff --run-id R` used to rewrite `R/plan/` in place, so the plan a human
    reviewed could be replaced by a different one that verifies just as cleanly under the
    same run id — the checksum proves the integrity of whatever currently occupies the run,
    not identity with what was approved. Re-planning therefore means a new run id.

    The condition is the manifest's presence **alone**, which is what keeps a first-write
    crash retryable: the writer publishes `operations.jsonl` first and the manifest last, so
    a run left holding operations and no manifest never committed a generation and may be
    written again under the same run id.

    Raises:
        PlanGenerationExistsError: `<run_dir>/plan/manifest.json` already exists.
    """
    manifest = committed_manifest_path(run_dir)
    if not manifest.exists():
        return
    msg = (
        f"Run {run_id!r} already holds a committed plan generation at {manifest}, and a committed "
        f"plan is never overwritten: a plan that has been reviewed must stay the plan that is applied."
    )
    raise PlanGenerationExistsError(msg)


def _refuse_duplicate_identifiers(operations: Sequence[PlannedOperation]) -> None:
    """Fail the plan run when two operations share an operation identifier (FR-021).

    Under FR-002's closed action vocabulary exactly one operation exists per
    `(action, kind, identity)`, so a collision is always pathological. The check runs
    **before** the first write, so a duplicate leaves no artifact at all — neither an
    operations file nor a manifest.
    """
    seen: dict[str, PlannedOperation] = {}
    for operation in operations:
        first = seen.get(operation.operation_id)
        if first is not None:
            msg = (
                f"Two operations share the identifier {operation.operation_id!r}: "
                f"kind {first.kind!r}, action {first.action!r}, identity {first.identity!r}; and "
                f"kind {operation.kind!r}, action {operation.action!r}, identity {operation.identity!r}."
            )
            raise DuplicateOperationIdError(msg)
        seen[operation.operation_id] = operation


def _refuse_not_in_source_peers(operations: Sequence[PlannedOperation]) -> None:
    """Refuse an operation whose identity, payload or relationships still name a peer not in the source.

    The selection step of the problem pass removes every such placeholder (spec 020 research
    R5); one that reaches this point would be written, and applied, as a destination identity.
    The check runs before the first write, so a refusal leaves no artifact.
    """
    for operation in operations:
        if holds_not_in_source_peer(_operation_line_mapping(operation)):
            msg = (
                f"Operation {operation.operation_id!r} of kind {operation.kind!r} still names a peer that the "
                "source does not return, so the plan was not written."
            )
            raise NotInSourcePeerInPlanError(msg)


def _ordered_operations(operations: Sequence[PlannedOperation]) -> list[PlannedOperation]:
    """Order operations by `(tier, operation_id)` — tier ascending, then identifier (AD001)."""
    return sorted(operations, key=lambda operation: (operation.tier, operation.operation_id))


def _operation_line_mapping(operation: PlannedOperation) -> dict[str, Any]:
    """Render one operation as the mapping its `operations.jsonl` line encodes.

    Dumped in Python mode so `canonical_value` performs the PD-002 normalization — one
    normalizer for the whole artifact rather than pydantic's JSON mode for some values and
    `canonical_value` for the rest.

    Three keys are **omitted** rather than encoded as `null`: `payload` on a delete,
    `relationships` when the operation carries no reference, and `destination_id` on
    anything that is not an update. `relationships` is omitted for an empty list too,
    because the wire format admits "absent" and never `[]` for that key — the
    absent-versus-empty distinction FR-028.2 makes load-bearing is one level down, in a
    reference's own `peers`.

    Omitting `destination_id` is what keeps a format-3 create and delete byte-identical to
    the format-2 line for the same operation: only an update's encoding changes, which is
    exactly the set of operations the format bump is about.
    """
    record = operation.model_dump()
    if record.get("payload") is None:
        record.pop("payload", None)
    if not record.get("relationships"):
        record.pop("relationships", None)
    if record.get("destination_id") is None:
        record.pop("destination_id", None)
    return record


def _encode_operations(operations: Sequence[PlannedOperation]) -> bytes:
    """Encode the ordered operations as `operations.jsonl`'s exact bytes.

    One canonical JSON object per line, every line LF-terminated including the last. Zero
    operations encode to zero bytes, which is what keeps an empty plan a *present*,
    zero-byte file rather than an absent one (FR-022).
    """
    return b"".join(
        canonical_json_bytes(_operation_line_mapping(operation), kind=operation.kind) + b"\n"
        for operation in operations
    )


def encode_last_changes(records: Sequence[LastChangeRecord]) -> bytes:
    """Encode the last-change records as `last_changes.jsonl`'s exact bytes.

    Sorted by `operation_id`, then `field`, one canonical JSON object per line, every line
    LF-terminated, so the same plan inputs give the same bytes. `None` values are kept as
    `null`. Zero records encode to zero bytes.
    """
    ordered = sorted(records, key=lambda record: (record.operation_id, record.field))
    return b"".join(canonical_json_bytes(record.model_dump()) + b"\n" for record in ordered)


def write_plan_artifact(
    *,
    run_dir: Path,
    run_id: str,
    config_version: str,
    source_snapshot: Sequence[SourceSnapshotRecord],
    deletes_computed: bool,
    operations: Sequence[PlannedOperation],
    destination_binding: DestinationBindingRecord | None = None,
    configuration_binding: tuple[str, int, str] | None = None,
    schema_fingerprint: str | None = None,
    lineage: PlanLineageRecord | None = None,
    last_changes: Sequence[LastChangeRecord] | None = None,
    metadata_read: bool = False,
    report: PlanReport | None = None,
    dropdown_values_checked: bool = True,
) -> PlanManifest:
    """Write `<run_dir>/plan/` and return the manifest that was written.

    Operations are ordered by `(tier, operation_id)` and their identifiers asserted unique;
    `operations.jsonl` is written first and `manifest.json` last, each atomically. The
    returned `PlanManifest` is the one on disk, `plan_checksum` included.

    `destination_binding` is the resolved destination identity the plan is bound to —
    endpoint URL and branch, never the token. It is additive: `None`
    (a destination that exposes none) writes a manifest without the field, exactly the
    shape older manifests carry, and the apply-time comparison skips such plans.

    `schema_fingerprint` is the consumed destination-schema semantics the plan was computed
    against. It is **required** alongside `configuration_binding`: a registered plan whose
    caller supplies none is refused by `PlanManifest` rather than written without the
    binding a registered apply compares against.

    `lineage` is the import account and the lineage the plan writes, which an Infrahub apply
    compares its own import account with (FR-012). `None` (a destination that records no
    lineage) writes a manifest without the field.

    `last_changes` are the records of `last_changes.jsonl`, written after the operations and
    before the manifest; the manifest's `last_changes` field records the file's digest and
    record count, and `metadata_read`. `None` (a destination that keeps no last changes)
    writes neither the file nor the field.

    `report` is the plan report of the problem pass (spec 020), written to `report.jsonl`
    after the operations and before the manifest; the manifest's `report` field records its
    digest, size and counts. `None` writes an empty report, so every format 4 plan has one
    shape. `dropdown_values_checked=False` records that the run had no destination schema
    snapshot, so the plan could not check Dropdown values.

    Raises:
        PlanGenerationExistsError: the run already holds a committed plan generation, which
            is never overwritten.
        DuplicateOperationIdError: two operations share an operation identifier (FR-021).
        NotInSourcePeerInPlanError: an operation still names a peer the source does not return.
        UnserializablePayloadValueError: a payload value is outside the canonical-value table.
        ValidationError: the manifest the caller's values assemble is not a valid manifest —
            a registered plan with no `schema_fingerprint`, above all.
    """
    require_uncommitted_plan(run_dir, run_id=run_id)
    _refuse_duplicate_identifiers(operations)
    _refuse_not_in_source_peers(operations)
    report_bytes = (report or PlanReport()).encode()
    ordered = _ordered_operations(operations)
    operations_bytes = _encode_operations(ordered)

    plan_dir = run_dir / PLAN_DIR_NAME
    # FIRST. A failure here leaves no manifest, so the artifact reads as torn (AD014).
    _atomic_write_bytes(plan_dir / OPERATIONS_FILE_NAME, operations_bytes)

    last_changes_field: dict[str, Any] | None = None
    if last_changes is not None:
        last_changes_bytes = encode_last_changes(last_changes)
        # Before the manifest: a failure here leaves no manifest, so the artifact reads as torn.
        _atomic_write_bytes(plan_dir / LAST_CHANGES_FILE_NAME, last_changes_bytes)
        last_changes_field = PlanLastChangesRecord(
            path=LAST_CHANGES_FILE_NAME,
            sha256=hashlib.sha256(last_changes_bytes).hexdigest(),
            count=len(last_changes),
            metadata_read=metadata_read,
        ).model_dump()

    # Before the manifest: a failure here leaves no manifest, so the artifact reads as torn.
    _atomic_write_bytes(plan_dir / REPORT_FILE_NAME, report_bytes)

    body: dict[str, Any] = {
        "format_version": PLAN_FORMAT_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_version": config_version,
        "source_snapshot": [record.model_dump() for record in source_snapshot],
        "operations_count": len(ordered),
        "delete_operations_computed": deletes_computed,
    }
    if destination_binding is not None:
        # Before the checksum below, so the recorded binding is covered by it.
        body["destination_binding"] = destination_binding.model_dump()
    if configuration_binding is not None:
        body.update(
            config_id=configuration_binding[0],
            registry_version=configuration_binding[1],
            package_checksum=configuration_binding[2],
        )
    if schema_fingerprint is not None:
        # Before the checksum below, so the recorded schema identity is covered by it.
        body["schema_fingerprint"] = schema_fingerprint
    if lineage is not None:
        # Before the checksum below, so the recorded import account is covered by it.
        body["lineage"] = lineage.model_dump()
    if last_changes_field is not None:
        # Before the checksum below, so the file's digest, and through it its bytes, are covered.
        body["last_changes"] = last_changes_field
    # Before the checksum below, so the report's digest, and through it its bytes, are covered.
    body["report"] = (report or PlanReport()).record(report_bytes).model_dump()
    if not dropdown_values_checked:
        body["dropdown_values_checked"] = False
    body["plan_checksum"] = compute_plan_checksum(body, operations_bytes)
    manifest = PlanManifest.model_validate(body)

    # LAST. Its presence is the commit point.
    _atomic_write_bytes(plan_dir / MANIFEST_FILE_NAME, canonical_json_bytes(body))
    return manifest
