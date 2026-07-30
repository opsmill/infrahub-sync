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

An operations line that parses as JSON but fails model validation for any reason **other**
than its action — a `create` with no `payload`, a stored `operation_id` that does not match
its own triple, a `cardinality: "one"` carrying two peers — is caught per line and re-raised
as `PlanArtifactTornError` naming the **line number** and the field that failed, rather than
reaching the operator as a raw pydantic traceback with no next action (AD059). The action
case takes precedence, so a hand-edited artifact reports its action rather than a generic
tear.

Unknown manifest fields survive verbatim: `PlanManifest` allows extras, so they stay in the
model and therefore stay inside the recomputed checksum (FR-027, AD028).
"""

from __future__ import annotations

import json
import stat as stat_module
from dataclasses import dataclass
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
    from collections.abc import Sequence
    from pathlib import Path

# The run-identifier enumeration's bound (AD073). Nothing in this repository prunes a run
# directory and retention is out of scope, so an hourly pipeline would otherwise answer a
# typo with thousands of lines. It lives here rather than in `review.py` because FR-008 puts
# the enumeration on **both** of its arms — an unknown run identifier and a located run that
# holds no plan artifact — and the second arm is raised from this module.
RUN_ID_LISTING_LIMIT = 20


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
    operations: list[PlannedOperation]
    manifest_mapping: dict[str, Any]
    operations_bytes: bytes


@dataclass(frozen=True)
class RawPlanArtifact:
    """The plan artifact's two files as bytes, read from disk exactly once.

    The single read that both the pre-apply verifier and `parse_plan_artifact` consume, so
    the bytes that were verified are — by construction — the bytes that are parsed and
    applied. An absent file reads as `None` rather than raising, because absence is a
    verdict the verifier reports in its failure list (torn, or the format-version gate) and
    the parser turns into its own torn refusal; a present-but-unreadable file keeps
    `PlanArtifactUnreadableError` (AD036).
    """

    run_dir: Path
    manifest_bytes: bytes | None
    operations_bytes: bytes | None


def read_plan_artifact_bytes(run_dir: Path) -> RawPlanArtifact:
    """Read `<run_dir>/plan/`'s files once, tolerating absence and refusing unreadability.

    Raises:
        PlanArtifactUnreadableError: a path exists but could not be read (AD036).
    """
    directory = run_dir / PLAN_DIR_NAME
    return RawPlanArtifact(
        run_dir=run_dir,
        manifest_bytes=_read_optional_bytes(directory / MANIFEST_FILE_NAME, description="plan manifest"),
        operations_bytes=_read_optional_bytes(directory / OPERATIONS_FILE_NAME, description="plan operations file"),
    )


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


def stored_run_ids(cache_root: Path) -> list[str]:
    """Return the sync's stored run identifiers, most recent first.

    `cache_root_for` **computes** a path and neither creates nor checks it
    (`infrahub_sync.cache.paths`), so an unguarded listing raises `FileNotFoundError`
    for a sync that never ran — from what is supposed to be a helpful error path (AD073). An
    absent root is therefore "no stored runs", not a failure. A root that exists but cannot be
    listed keeps its own verdict rather than being degraded to "no runs" (AD036).

    Run identifiers sort by time by construction (`:46-52`), so "most recent" is a reverse
    lexicographic sort and needs no `stat` calls.
    """
    try:
        names = [entry.name for entry in cache_root.iterdir() if entry.is_dir()]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        msg = f"The cache root at {str(cache_root)!r} exists but could not be listed: {exc.strerror or exc}."
        raise PlanArtifactUnreadableError(msg) from exc
    return sorted(names, reverse=True)


def run_id_listing_text(stored: Sequence[str], *, cache_root: Path) -> str:
    """Render FR-008's enumeration of the run identifiers that exist, bounded by AD073.

    One sentence, shared by both arms FR-008 names so their wording cannot drift: an unknown
    run identifier (`review.py`) and a located run holding no plan artifact
    (`require_plan_directory` below). The caller supplies the already-listed identifiers
    because one of the two arms also branches on whether the list is empty.
    """
    if not stored:
        return f"This sync has no stored runs at all — {cache_root} holds no run directories."
    shown = list(stored[:RUN_ID_LISTING_LIMIT])
    truncation = (
        f" (Showing the {len(shown)} most recent of {len(stored)} stored runs.)" if len(stored) > len(shown) else ""
    )
    return f"The most recent run identifiers for this sync are: {', '.join(shown)}.{truncation}"


def _read_optional_bytes(path: Path, *, description: str) -> bytes | None:
    """Return `path`'s bytes, `None` when absent, raising when present but unreadable.

    An absent file is a verdict this function's callers classify themselves; an unreadable
    one is a different condition with a different remedy, so it keeps its own error class
    rather than being flattened into "absent" (AD036).
    """
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return None
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


def operation_record_lines(operations_bytes: bytes) -> list[bytes]:
    """Split `operations.jsonl`'s bytes into its record lines.

    Every line is LF-terminated including the last, so the trailing empty element of the
    split is dropped and the length is the record count. Shared by the reader and the
    verifier so the two cannot disagree about how many records a file holds; the split is
    byte-level, so counting never depends on how a decoder treats bytes that are not UTF-8.
    """
    lines = operations_bytes.split(b"\n")
    if lines and not lines[-1]:
        lines.pop()
    return lines


def _operation_lines(operations_bytes: bytes, run_id: str) -> list[bytes]:
    """The record lines, refusing a file that is not valid UTF-8.

    The writer emits UTF-8 by construction, so a file that does not decode is torn — the
    validity gate runs over the whole file first, keeping the failing byte offset in the
    verdict.
    """
    try:
        operations_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _torn(
            run_id,
            f"{OPERATIONS_FILE_NAME} is not valid UTF-8",
            expected="UTF-8 encoded JSON lines",
            found=f"a byte sequence that failed to decode at offset {exc.start}",
        ) from exc
    return operation_record_lines(operations_bytes)


def _parse_manifest(raw: RawPlanArtifact, run_id: str) -> tuple[PlanManifest, dict[str, Any]]:
    """Version-gate and validate the manifest's bytes, returning it and its raw mapping."""
    manifest_path = raw.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME
    if raw.manifest_bytes is None:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} is absent, so the artifact was never committed",
            expected=f"a manifest at {manifest_path}",
            found="no manifest",
        )
    try:
        mapping = json.loads(raw.manifest_bytes)
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
    # The `isinstance` guard runs first (MIN-002): an unhashable hand-edited value like
    # `[2]` would raise `TypeError` from the frozenset membership test. A non-integer is a
    # version this release does not support, so it takes the version refusal below.
    if not isinstance(found_version, int) or found_version not in SUPPORTED_FORMAT_VERSIONS:
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


def _parse_operations(
    raw: RawPlanArtifact, manifest: PlanManifest, run_id: str
) -> tuple[list[PlannedOperation], bytes]:
    """Validate `operations.jsonl`'s bytes, returning the operations and those same bytes."""
    operations_path = raw.run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME
    operations_bytes = raw.operations_bytes
    if operations_bytes is None:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} is present but {OPERATIONS_FILE_NAME} is absent",
            expected=f"{manifest.operations_count} operation line(s) at {operations_path}",
            found="no operations file",
        )
    lines = _operation_lines(operations_bytes, run_id)
    if len(lines) != manifest.operations_count:
        raise _torn(
            run_id,
            f"{OPERATIONS_FILE_NAME} holds a different number of lines than the manifest records",
            expected=f"{manifest.operations_count} operation line(s)",
            found=f"{len(lines)} operation line(s)",
        )

    operations: list[PlannedOperation] = []
    # MIN-024: the writer asserts identifier uniqueness (FR-021) but lines validate
    # independently here, so a checksum-valid, hand-built artifact repeating an identifier
    # would otherwise load, review, and apply with last-write-wins semantics. The invariant
    # is re-established at this boundary, naming the identifier and both lines.
    line_by_operation_id: dict[str, int] = {}
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
            operation = PlannedOperation.model_validate(record)
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
        first_line = line_by_operation_id.get(operation.operation_id)
        if first_line is not None:
            raise _torn(
                run_id,
                f"{OPERATIONS_FILE_NAME} repeats operation identifier {operation.operation_id!r}",
                expected="each operation identifier exactly once (FR-021)",
                found=f"operation {operation.operation_id!r} on both line {first_line} and line {number}",
            )
        line_by_operation_id[operation.operation_id] = number
        operations.append(operation)
    return operations, operations_bytes


def require_plan_directory(run_dir: Path) -> Path:
    """Return `<run_dir>/plan/`, refusing when it is absent or is not a directory.

    Split out of `load_plan_artifact` so the CLI's apply path reaches the same verdict —
    and therefore the same message and next action — **before** it constructs an adapter or
    allocates a run directory (AD026).

    The refusal **enumerates the run identifiers that exist**, because FR-008 puts that
    enumeration on this arm as well as on the unknown-run arm. They come from
    `run_dir.parent`, the sync's cache root by construction, so no synchronization name has
    to be threaded into the reader.

    Raises:
        PlanFormatV1Error: no `plan/` directory exists — the run predates this format, or its
            plan directory was never written or has been removed (FR-019).
        PlanArtifactTornError: `plan/` exists but is not a directory.
        PlanArtifactUnreadableError: the path exists but could not be examined, or the cache
            root exists but could not be listed (AD036).
    """
    run_id = run_dir.name
    plan_dir = run_dir / PLAN_DIR_NAME
    entry = stat_or_unreadable(plan_dir, description="plan artifact directory")
    if entry is None:
        cache_root = run_dir.parent
        stored = stored_run_ids(cache_root)
        listing = f" {run_id_listing_text(stored, cache_root=cache_root)}" if stored else ""
        msg = (
            f"Run {run_id!r} holds no plan artifact: no {PLAN_DIR_NAME!r} directory exists at "
            f"{plan_dir}. Either the run predates the saved plan artifact format, or its plan "
            f"directory was never written or has since been removed — either way there is "
            f"nothing to apply or review.{listing}"
        )
        raise PlanFormatV1Error(msg)
    if not stat_module.S_ISDIR(entry.st_mode):
        raise _torn(
            run_id,
            f"{PLAN_DIR_NAME!r} is not a directory",
            expected=f"a directory at {plan_dir}",
            found="a non-directory path",
        )
    return plan_dir


def parse_plan_artifact(raw: RawPlanArtifact, *, run_id: str) -> LoadedPlan:
    """Validate an already-read artifact into a `LoadedPlan` — the same bytes, uninterrupted.

    The parsing half of `load_plan_artifact`, split out so a caller that verified a
    `RawPlanArtifact` can parse and apply **that object** rather than re-reading the disk —
    a second read is what let files replaced between verification and apply execute
    unverified (DBR-006, DBA-004).

    Raises:
        PlanArtifactTornError: the artifact is incomplete or inconsistent — a missing or
            malformed manifest, an absent operations file, a line count disagreeing with
            `operations_count`, or an operations line that fails record validation for a
            reason other than its action (FR-010).
        PlanFormatVersionError: `format_version` is outside `SUPPORTED_FORMAT_VERSIONS`
            (FR-027).
        UnsupportedOperationActionError: an operation's `action` is outside `ACTIONS`,
            refused here — while parsing — and therefore before any destination write
            (FR-017, AD055).
    """
    manifest, mapping = _parse_manifest(raw, run_id)
    operations, operations_bytes = _parse_operations(raw, manifest, run_id)
    return LoadedPlan(
        manifest=manifest,
        operations=operations,
        manifest_mapping=mapping,
        operations_bytes=operations_bytes,
    )


def load_plan_artifact(run_dir: Path) -> LoadedPlan:
    """Read `<run_dir>/plan/` — once — and return it as a validated `LoadedPlan`.

    Raises:
        PlanFormatV1Error: the run holds no `plan/` directory — it predates this format, or
            the directory was never written or has been removed — so it must be re-planned
            (FR-019).
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
    require_plan_directory(run_dir)
    return parse_plan_artifact(read_plan_artifact_bytes(run_dir), run_id=run_id)
