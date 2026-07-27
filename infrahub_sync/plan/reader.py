"""The plan artifact reader (FR-007, FR-010, FR-017, FR-019, FR-027).

The reader **classifies before it parses**. Every verdict below is reached from the
filesystem and from the manifest's own header fields, so an operator is told what is wrong
with the artifact before any of its contents are interpreted:

1. no `plan/` directory at all → `PlanFormatV1Error`, because a run that never reached the
   writer holds the pre-existing row format (FR-019);
2. a path that exists but cannot be examined or read → `PlanArtifactUnreadableError` naming
   the path, never degraded to "absent" (AD036);
3. `plan/` present without a complete, parseable manifest, an `operations.jsonl` that is
   absent, or a line count disagreeing with `operations_count` → `PlanArtifactTornError`
   naming **which** part is torn;
4. a `format_version` outside `SUPPORTED_FORMAT_VERSIONS` → `PlanFormatVersionError`
   listing the versions supported, deliberately distinct in wording from (1) because the
   remedies differ (FR-027, SC-018);
5. an operation whose `action` is outside `ACTIONS` → `UnsupportedOperationActionError`
   listing `ACTIONS`. This is what puts FR-017's genuinely-unsupported class **before any
   destination write**, and because the review path reads through this same function, an
   unrecognized action refuses review too — the one bound on AD031's "review renders
   rather than refuses", which is scoped to verification failures (AD055).

The last arm matters most in practice. An operations line that parses as JSON but fails
model validation for any reason **other** than its action — a `create` with no `payload`, a
stored `operation_id` that does not match its own triple, a `cardinality: "one"` carrying
two peers — is caught per line and re-raised as `PlanArtifactTornError` naming the **line
number** and the field that failed. Without that arm the likeliest corruption class reaches
the operator as a raw pydantic traceback with no next action, which is what AD059 forbids.
The action case takes precedence, so a hand-edited artifact reports the action rather than
a generic tear.

Unknown manifest fields survive verbatim: `PlanManifest` allows extras, so they stay in the
model and therefore stay inside the recomputed checksum (FR-027, AD028).
"""

from __future__ import annotations

import json
import stat as stat_module
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from infrahub_sync.plan.errors import (
    PlanArtifactTornError,
    PlanArtifactUnreadableError,
    PlanFormatV1Error,
    PlanFormatVersionError,
)
from infrahub_sync.plan.models import (
    SUPPORTED_FORMAT_VERSIONS,
    PlanManifest,
    PlannedOperation,
)
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME

if TYPE_CHECKING:
    import os
    from pathlib import Path


@dataclass(frozen=True)
class LoadedPlan:
    """One saved plan artifact, read and validated.

    Internal to this outcome: FR-029 fixes `read_saved_plan` as the **only** supported
    reading entry point, so this record is not re-exported from the package (AD029).

    `manifest_mapping` and `operations_bytes` are carried because the checksum is computed
    over exactly those two things — the manifest as it was written, unknown fields included,
    and the operations file's raw bytes — so a caller recomputing it never has to re-read or
    re-encode the artifact and risk a re-encoding that differs from what was hashed.
    """

    manifest: PlanManifest
    operations: list[PlannedOperation] = field(default_factory=list)
    manifest_mapping: dict[str, Any] = field(default_factory=dict)
    operations_bytes: bytes = b""


def supported_versions_text() -> str:
    """List `SUPPORTED_FORMAT_VERSIONS` for a message, lowest first (AD059)."""
    return ", ".join(str(version) for version in sorted(SUPPORTED_FORMAT_VERSIONS))


def stat_or_unreadable(path: Path, *, description: str) -> os.stat_result | None:
    """Stat `path`, returning `None` when it is absent and raising when it is unreadable.

    `Path.is_dir()` and `Path.is_file()` swallow `PermissionError` and answer `False`, so a
    run directory an operator cannot read would otherwise be classified as *absent* — the
    exact degradation AD036 forbids, and the one that would send a first-run operator to
    "re-plan" when the real remedy is a permission change.
    """
    try:
        return path.stat()
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        # A parent component is a file, so nothing can exist at `path`.
        return None
    except OSError as exc:
        msg = f"The {description} at {str(path)!r} exists but could not be examined: {exc.strerror or exc}."
        raise PlanArtifactUnreadableError(msg) from exc


def _read_bytes(path: Path, *, description: str) -> bytes:
    """Read `path` whole, mapping any I/O failure to the unreadable verdict."""
    try:
        return path.read_bytes()
    except OSError as exc:
        msg = f"The {description} at {str(path)!r} could not be read: {exc.strerror or exc}."
        raise PlanArtifactUnreadableError(msg) from exc


def _torn(run_id: str, what: str, *, expected: str, found: str) -> PlanArtifactTornError:
    """Build the torn verdict, naming the run, which part is torn, and expected vs found."""
    msg = f"The plan artifact of run {run_id!r} is incomplete: {what}. Expected {expected}; found {found}."
    return PlanArtifactTornError(msg)


def _describe_validation_error(exc: ValidationError) -> str:
    """Render a pydantic failure as `field: reason` pairs.

    A raw `ValidationError` reaching the operator is a traceback with no next action
    (AD059), so its content is re-stated as the field that failed and why. A model-level
    failure carries an empty location, which reads as `<record>`.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "<record>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _operation_lines(operations_bytes: bytes, run_id: str) -> list[str]:
    """Split `operations.jsonl` into its record lines.

    Every line is LF-terminated including the last, so the trailing empty element of the
    split is dropped and the length is the record count. A file that is not valid UTF-8 is
    torn: the writer emits UTF-8 by construction.
    """
    try:
        text = operations_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _torn(
            run_id,
            f"{OPERATIONS_FILE_NAME} is not valid UTF-8",
            expected="UTF-8 encoded JSON lines",
            found=f"a byte sequence that failed to decode at offset {exc.start}",
        ) from exc
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    return lines


def _load_manifest(plan_dir: Path, run_id: str) -> tuple[PlanManifest, dict[str, Any]]:
    """Read, version-gate and validate the manifest, returning it and its raw mapping."""
    manifest_path = plan_dir / MANIFEST_FILE_NAME
    if stat_or_unreadable(manifest_path, description="plan manifest") is None:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} is absent, so the artifact was never committed",
            expected=f"a manifest at {manifest_path}",
            found="no manifest",
        )
    raw = _read_bytes(manifest_path, description="plan manifest")
    try:
        mapping = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} is not parseable JSON",
            expected="a JSON object",
            found=f"unparseable bytes ({exc})",
        ) from exc
    if not isinstance(mapping, dict):
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} does not hold a JSON object",
            expected="a JSON object",
            found=f"a JSON {type(mapping).__name__}",
        )

    # The version gate runs before the rest of the manifest is interpreted: a revision this
    # release does not understand cannot have its remaining fields meaningfully read
    # (PD-006). A manifest with **no** `format_version` at all is incomplete, not
    # forward-dated, so it is torn rather than a version refusal.
    if "format_version" not in mapping:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} declares no 'format_version'",
            expected=f"one of the supported plan format versions: {supported_versions_text()}",
            found="no 'format_version' field",
        )
    found_version = mapping["format_version"]
    if found_version not in SUPPORTED_FORMAT_VERSIONS:
        msg = (
            f"The plan artifact of run {run_id!r} declares format version {found_version!r}, which "
            f"this version of infrahub-sync does not support. Supported plan format versions: "
            f"{supported_versions_text()}."
        )
        raise PlanFormatVersionError(msg)

    try:
        manifest = PlanManifest.model_validate(mapping)
    except ValidationError as exc:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} is missing or malformed in {exc.error_count()} field(s)",
            expected="a complete plan manifest",
            found=_describe_validation_error(exc),
        ) from exc
    return manifest, mapping


def _load_operations(plan_dir: Path, manifest: PlanManifest, run_id: str) -> tuple[list[PlannedOperation], bytes]:
    """Read and validate `operations.jsonl`, returning the operations and the raw bytes."""
    operations_path = plan_dir / OPERATIONS_FILE_NAME
    if stat_or_unreadable(operations_path, description="plan operations file") is None:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} is present but {OPERATIONS_FILE_NAME} is absent",
            expected=f"{manifest.operations_count} operation line(s) at {operations_path}",
            found="no operations file",
        )
    operations_bytes = _read_bytes(operations_path, description="plan operations file")
    lines = _operation_lines(operations_bytes, run_id)
    if len(lines) != manifest.operations_count:
        raise _torn(
            run_id,
            f"{OPERATIONS_FILE_NAME} holds a different number of lines than the manifest records",
            expected=f"{manifest.operations_count} operation line(s)",
            found=f"{len(lines)} operation line(s)",
        )

    operations: list[PlannedOperation] = []
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _torn(
                run_id,
                f"{OPERATIONS_FILE_NAME} line {number} is not parseable JSON",
                expected="one canonical JSON object per line",
                found=f"unparseable text on line {number} ({exc.msg} at column {exc.colno})",
            ) from exc
        try:
            operations.append(PlannedOperation.model_validate(record))
        except ValidationError as exc:
            # `UnsupportedOperationActionError` is **not** a `ValidationError`: it is raised
            # from `PlannedOperation`'s before-validator and pydantic propagates it
            # unchanged, so the action case reaches the caller as itself and takes
            # precedence over this generic tear (AD055).
            raise _torn(
                run_id,
                f"{OPERATIONS_FILE_NAME} line {number} is not a valid operation record",
                expected="a complete, self-consistent operation record",
                found=f"line {number} — {_describe_validation_error(exc)}",
            ) from exc
    return operations, operations_bytes


def load_plan_artifact(run_dir: Path) -> LoadedPlan:
    """Read `<run_dir>/plan/` and return it as a validated `LoadedPlan`.

    Raises:
        PlanFormatV1Error: the run holds no `plan/` directory, so its plan predates this
            format and must be re-planned (FR-019).
        PlanArtifactTornError: the artifact is present but incomplete or inconsistent — a
            missing or malformed manifest, an absent operations file, a line count
            disagreeing with `operations_count`, or an operations line that fails record
            validation for a reason other than its action (FR-010).
        PlanFormatVersionError: `format_version` is outside `SUPPORTED_FORMAT_VERSIONS`
            (FR-027).
        PlanArtifactUnreadableError: a path exists but could not be examined or read
            (AD036).
        UnsupportedOperationActionError: an operation's `action` is outside `ACTIONS`,
            refused here — while reading — and therefore before any destination write
            (FR-017, AD055).
    """
    run_id = run_dir.name
    plan_dir = run_dir / PLAN_DIR_NAME
    entry = stat_or_unreadable(plan_dir, description="plan artifact directory")
    if entry is None:
        msg = (
            f"Run {run_id!r} holds no plan artifact: no {PLAN_DIR_NAME!r} directory exists at "
            f"{plan_dir}. The run predates the saved plan artifact format, so there is nothing "
            f"to apply or review."
        )
        raise PlanFormatV1Error(msg)
    if not stat_module.S_ISDIR(entry.st_mode):
        raise _torn(
            run_id,
            f"{PLAN_DIR_NAME!r} is not a directory",
            expected=f"a directory at {plan_dir}",
            found="a non-directory path",
        )

    manifest, mapping = _load_manifest(plan_dir, run_id)
    operations, operations_bytes = _load_operations(plan_dir, manifest, run_id)
    return LoadedPlan(
        manifest=manifest,
        operations=operations,
        manifest_mapping=mapping,
        operations_bytes=operations_bytes,
    )
