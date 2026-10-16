"""The lines the CLI prints for the cause of a failed or stopped run (contracts/cli.md).

Every line is built from the fields of the run record's `failure` object only; the CLI adds
no text of its own from an error (ADR 0021). The caller renders each line for a terminal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync.client import ApplyProgressResource, PlanRecordReferenceResource, RunFailureCauseResource

_STOPPED_TYPES = frozenset({"left_out_review_required"})


def _identity(identity: dict[str, object] | None) -> str:
    return " ".join(f"{name}={value}" for name, value in (identity or {}).items())


def _record(record: PlanRecordReferenceResource) -> str:
    parts = [record.kind]
    if record.identity:
        parts.append(_identity(record.identity))
    if record.source_id is not None:
        parts.append(f"source_id={record.source_id}")
    return " ".join(parts)


def _progress_lines(progress: ApplyProgressResource) -> list[str]:
    lines = [f"  written: {progress.operations_written} of {progress.operations_total} operations"]
    failed = progress.failed_operation
    if failed is not None:
        line = f"  failed operation: {failed.operation_id} {failed.action} {failed.kind}"
        if failed.identity:
            line = f"{line} {_identity(failed.identity)}"
        if progress.failed_operation_may_have_written:
            line = f"{line} (may have written part of its change)"
        lines.append(line)
    return lines


def failure_lines(failure: RunFailureCauseResource) -> list[str]:
    """The headline, the detail lines and the next action of one run failure cause.

    Infrahub's error code is part of the summary of a rejected write, so it has no line.
    """
    stopped = failure.failure_type in _STOPPED_TYPES or failure.apply_progress is not None
    summary = failure.summary[:1].lower() + failure.summary[1:]
    lines = [f"{failure.stage} {'stopped' if stopped else 'failed'}: {summary}"]
    located = [
        f"{label}: {value}"
        for label, value in (("node kind", failure.node_kind), ("attribute", failure.attribute))
        if value is not None
    ]
    if located:
        lines.append(f"  {'   '.join(located)}")
    if failure.record is not None:
        lines.append(f"  record: {_record(failure.record)}")
    if failure.field is not None:
        lines.append(f"  field: {failure.field}")
    if failure.infrahub_message is not None:
        lines.append(f"  Infrahub message: {failure.infrahub_message}")
    if failure.apply_progress is not None:
        lines.extend(_progress_lines(failure.apply_progress))
    lines.append(f"  next: {failure.next_action}")
    return lines
