"""The plan artifact writer (FR-004, FR-010, FR-019, FR-021, FR-022, FR-026, FR-027).

`operations.jsonl` is written **first** and `manifest.json` **last**, and that order is the
commit point (AD014): the manifest's presence is what makes the artifact complete, so a
failure part-way through the operations write leaves a run directory with a `plan/`
directory and no manifest — which the reader classifies as **torn** — while a run that
never reached this writer has no `plan/` directory at all and is classified as the
pre-existing v1 format. The two verdicts are therefore disjoint by construction rather
than by heuristic.

A committed generation is also **final**: `require_uncommitted_plan` refuses to write into a
run whose manifest already exists, so re-planning allocates a new run id rather than
replacing a plan a human may have approved (FIX-010, spec 002).

Both writes go through one helper, `_atomic_write_bytes`, in the tmp+`Path.replace`
discipline already used for the run sidecars, so neither file is ever observed
half-written. Routing both through a single helper is also what makes the write order
observable to a test.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.errors import DuplicateOperationIdError, PlanGenerationExistsError
from infrahub_sync.plan.models import PLAN_FORMAT_VERSION, PlanManifest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync.plan.models import DestinationBindingRecord, PlannedOperation, SourceSnapshotRecord

# The artifact's directory and its two files, in the order they are written.
PLAN_DIR_NAME = "plan"
OPERATIONS_FILE_NAME = "operations.jsonl"
MANIFEST_FILE_NAME = "manifest.json"


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` to `path` via tmp+`Path.replace`, creating the parent directory.

    The same discipline as the run sidecars', in bytes rather than text because the
    artifact's encoding is fixed by `canonical_json_bytes` and must not be re-encoded by a
    text layer. Both artifact files are written through this one function, which is what
    lets a test observe that the operations file goes first.

    **Accepted tradeoff (MIN-004): no fsync.** Neither the temporary file's data nor the
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
        # failure is suppressed (MIN-025): a cleanup `PermissionError` superseding the
        # write or replace error would hide the very failure — ENOSPC above all — that
        # explains the torn artifact.
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink(missing_ok=True)
        raise


def committed_manifest_path(run_dir: Path) -> Path:
    """The path whose existence means this run holds a committed plan generation (AD014)."""
    return run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME


def require_uncommitted_plan(run_dir: Path, *, run_id: str) -> None:
    """Refuse to write over a plan generation that was already committed (FIX-010, spec 002).

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


def _ordered_operations(operations: Sequence[PlannedOperation]) -> list[PlannedOperation]:
    """Order operations by `(tier, operation_id)` — tier ascending, then identifier (AD001)."""
    return sorted(operations, key=lambda operation: (operation.tier, operation.operation_id))


def _operation_line_mapping(operation: PlannedOperation) -> dict[str, Any]:
    """Render one operation as the mapping its `operations.jsonl` line encodes.

    Dumped in Python mode so `canonical_value` performs the PD-002 normalization — one
    normalizer for the whole artifact rather than pydantic's JSON mode for some values and
    `canonical_value` for the rest.

    Two keys are **omitted** rather than encoded as `null`: `payload` on a delete, and
    `relationships` when the operation carries no reference. `relationships` is omitted for
    an empty list too, because the wire format admits "absent" and never `[]` for that key
    — the absent-versus-empty distinction FR-028.2 makes load-bearing is one level down, in
    a reference's own `peers`.
    """
    record = operation.model_dump()
    if record.get("payload") is None:
        record.pop("payload", None)
    if not record.get("relationships"):
        record.pop("relationships", None)
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


def write_plan_artifact(
    *,
    run_dir: Path,
    run_id: str,
    config_version: str,
    source_snapshot: Sequence[SourceSnapshotRecord],
    deletes_computed: bool,
    operations: Sequence[PlannedOperation],
    destination_binding: DestinationBindingRecord | None = None,
) -> PlanManifest:
    """Write `<run_dir>/plan/` and return the manifest that was written.

    Operations are ordered by `(tier, operation_id)` and their identifiers asserted unique;
    `operations.jsonl` is written first and `manifest.json` last, each atomically. The
    returned `PlanManifest` is the one on disk, `plan_checksum` included.

    `destination_binding` is the resolved destination identity the plan is bound to —
    endpoint URL and branch, never the token (FIX-005, spec 002). It is additive: `None`
    (a destination that exposes none) writes a manifest without the field, exactly the
    pre-FIX-005 shape, and the apply-time comparison skips such plans.

    Raises:
        PlanGenerationExistsError: the run already holds a committed plan generation, which
            is never overwritten (FIX-010, spec 002).
        DuplicateOperationIdError: two operations share an operation identifier (FR-021).
        UnserializablePayloadValueError: a payload value is outside the canonical-value table.
    """
    require_uncommitted_plan(run_dir, run_id=run_id)
    _refuse_duplicate_identifiers(operations)
    ordered = _ordered_operations(operations)
    operations_bytes = _encode_operations(ordered)

    plan_dir = run_dir / PLAN_DIR_NAME
    # FIRST. A failure here leaves no manifest, so the artifact reads as torn (AD014).
    _atomic_write_bytes(plan_dir / OPERATIONS_FILE_NAME, operations_bytes)

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
    body["plan_checksum"] = compute_plan_checksum(body, operations_bytes)
    manifest = PlanManifest.model_validate(body)

    # LAST. Its presence is the commit point.
    _atomic_write_bytes(plan_dir / MANIFEST_FILE_NAME, canonical_json_bytes(body))
    return manifest
