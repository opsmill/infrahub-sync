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
5. a manifest whose `last_changes` field names a `last_changes.jsonl` that is absent, or
   whose digest or record count differs → `PlanArtifactTornError` naming the file, so review
   and apply refuse it (FR-007). A file that matches but whose records fail the content
   checks is not refused: the file is display-only, so the reader reports the first bad line
   in `LoadedPlan.last_changes_problem` and review shows it as a note;
5b. a format 4 manifest whose `report` record is missing, or whose `report.jsonl` is absent or
   does not match its digest, size or counts → `PlanVerificationError` (spec 019);
6. an operation whose `action` is outside `ACTIONS` → `UnsupportedOperationActionError`
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

import hashlib
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
    PlanVerificationError,
)
from infrahub_sync.plan.models import (
    LAST_CHANGES_FILE_NAME,
    REPORT_FILE_NAME,
    REPORT_FORMAT_VERSION,
    SUPPORTED_FORMAT_VERSIONS,
    LastChangeRecord,
    PlanManifest,
    PlannedOperation,
    PlanReport,
    PlanReportRecord,
)
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME

if TYPE_CHECKING:
    import os
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from infrahub_sync.plan.models import PlanLastChangesRecord

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

    `last_changes` holds the records of `last_changes.jsonl`, or `None` for a plan whose
    manifest has no `last_changes` field (FR-007). It is also `None` when the file matches
    its digest but a record fails the content checks; `last_changes_problem` then names the
    first bad line and the reason, which review shows as a verification note.
    """

    manifest: PlanManifest
    operations: list[PlannedOperation]
    manifest_mapping: dict[str, Any]
    operations_bytes: bytes
    last_changes: list[LastChangeRecord] | None = None
    last_changes_problem: str | None = None
    # The lines of `plan/report.jsonl` (plan format 4, spec 019), in the file's order; empty
    # for a format 2 or 3 plan, which has no report.
    report: tuple[dict[str, Any], ...] = ()


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
    # `None` when absent: a plan made before the file existed, or one whose destination keeps
    # no last changes. Whether it must exist is the manifest's `last_changes` field's to say.
    last_changes_bytes: bytes | None = None
    # `None` when absent: a format 2 or 3 plan has none; a format 4 manifest requires it.
    report_bytes: bytes | None = None


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
        last_changes_bytes=_read_optional_bytes(
            directory / LAST_CHANGES_FILE_NAME, description="plan last-change file"
        ),
        report_bytes=_read_optional_bytes(directory / REPORT_FILE_NAME, description="plan report file"),
    )


_REPORT_ENTRIES = ("problem", "left_out", "kept")


def report_mismatch(mapping: Mapping[str, Any], report_bytes: bytes | None) -> tuple[str, str] | None:
    """Compare `report.jsonl`'s bytes with a format 4 manifest's `report` record (spec 019).

    Returns `(expected, found)` when the record is missing or malformed, the file is absent,
    or its digest, size or counts differ from the record; `None` when they match, and for a
    format 2 or 3 manifest, which has no report. Shared by the reader and the pre-apply
    verifier, so review and apply refuse the same files.
    """
    version = mapping.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < REPORT_FORMAT_VERSION:
        return None
    try:
        record = PlanReportRecord.model_validate(mapping.get("report"))
    except ValidationError:
        return (
            "a 'report' record with a digest, a size and the five counts",
            "no 'report' record" if mapping.get("report") is None else "a malformed 'report' record",
        )
    if report_bytes is None:
        return f"a report at {PLAN_DIR_NAME}/{REPORT_FILE_NAME}", "no report file"
    digest = hashlib.sha256(report_bytes).hexdigest()
    if digest != record.digest or len(report_bytes) != record.size:
        return (
            f"a file of {record.size} byte(s) whose SHA-256 digest is {record.digest}",
            f"a file of {len(report_bytes)} byte(s) whose SHA-256 digest is {digest}",
        )
    return _report_count_mismatch(record, _report_lines(report_bytes))


def _report_count_mismatch(record: PlanReportRecord, lines: list[dict[str, Any]] | None) -> tuple[str, str] | None:
    """Compare the report's lines with the counts its manifest record holds."""
    if lines is None:
        return "one JSON object per line, each a problem, left-out or kept entry", "a line that is not"
    counted = {
        "problems": sum(1 for line in lines if line["entry"] == "problem"),
        "left_out": sum(1 for line in lines if line["entry"] == "left_out"),
        "left_out_with_problem": sum(
            1 for line in lines if line["entry"] == "left_out" and line.get("cause") == "has_problem"
        ),
        "left_out_dependent": sum(
            1 for line in lines if line["entry"] == "left_out" and line.get("cause") == "depends_on_left_out"
        ),
        "kept": sum(1 for line in lines if line["entry"] == "kept"),
    }
    recorded = {name: getattr(record, name) for name in counted}
    if counted != recorded:
        differing = sorted(name for name, count in counted.items() if count != recorded[name])
        return (
            ", ".join(f"{name} {recorded[name]}" for name in differing),
            ", ".join(f"{name} {counted[name]}" for name in differing),
        )
    return None


def _report_lines(report_bytes: bytes) -> list[dict[str, Any]] | None:
    """The report's lines as mappings, or `None` when a line is not a valid report entry.

    Each line is checked against the model of its entry, so a review that reads the report
    later (`SavedPlan.summary()`, the report pages) never meets a line it cannot read.
    """
    lines: list[dict[str, Any]] = []
    for raw_line in operation_record_lines(report_bytes):
        try:
            line = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(line, dict) or line.get("entry") not in _REPORT_ENTRIES:
            return None
        lines.append(line)
    try:
        PlanReport.from_lines(lines)
    except ValidationError:
        return None
    return lines


def _parse_report(raw: RawPlanArtifact, mapping: Mapping[str, Any], run_id: str) -> tuple[dict[str, Any], ...]:
    """Check `report.jsonl` against a format 4 manifest and return its lines (spec 019).

    Raises:
        PlanVerificationError: the report is missing or does not match the manifest's record.
    """
    mismatch = report_mismatch(mapping, raw.report_bytes)
    if mismatch is not None:
        msg = (
            f"The plan report of run {run_id!r} does not match its manifest: expected {mismatch[0]}; "
            f"found {mismatch[1]}. The plan is not applied."
        )
        raise PlanVerificationError(msg, next_action="Re-run `plan` for this sync, then review and apply that run.")
    if raw.report_bytes is None:
        return ()
    return tuple(_report_lines(raw.report_bytes) or ())


def last_changes_mismatch(record: PlanLastChangesRecord, sidecar_bytes: bytes | None) -> tuple[str, str] | None:
    """Compare `last_changes.jsonl`'s bytes with the manifest's record of them.

    Returns `(expected, found)` when the file is absent, or its digest or record count
    differs from the record; `None` when they match. Shared by the reader and the pre-apply
    verifier, so review and apply refuse the same files.
    """
    return _checked_last_change_lines(record, sidecar_bytes)[0]


def _checked_last_change_lines(
    record: PlanLastChangesRecord, sidecar_bytes: bytes | None
) -> tuple[tuple[str, str] | None, list[bytes]]:
    """`last_changes_mismatch`'s result, with the file's record lines once the digest matches.

    The lines are empty when the file is absent or its digest differs, so the reader splits
    the file once for both the count check and the records it parses.
    """
    if sidecar_bytes is None:
        return (f"{record.count} record(s) at {PLAN_DIR_NAME}/{LAST_CHANGES_FILE_NAME}", "no last-change file"), []
    digest = hashlib.sha256(sidecar_bytes).hexdigest()
    if digest != record.sha256:
        return (f"a file whose SHA-256 digest is {record.sha256}", f"a file whose SHA-256 digest is {digest}"), []
    lines = operation_record_lines(sidecar_bytes)
    if len(lines) != record.count:
        return (f"{record.count} record(s)", f"{len(lines)} record(s)"), lines
    return None, lines


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


class DuplicateManifestKeyError(ValueError):
    """The manifest's bytes declare one key twice, so what it records is ambiguous.

    Not a member of the plan-artifact taxonomy: it is a decode-level signal, like
    `json.JSONDecodeError`, that each caller turns into its own typed verdict — a torn
    artifact for the reader, an unparseable manifest for the verifier's gate.
    """


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object, refusing a key that appears more than once in it."""
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            msg = f"the manifest declares the key {key!r} more than once"
            raise DuplicateManifestKeyError(msg)
        mapping[key] = value
    return mapping


def load_manifest_mapping(manifest_bytes: bytes) -> Any:
    """Decode the manifest's bytes, refusing a duplicate key before anything reads them.

    `json.loads` keeps the **last** of two duplicate keys and canonical re-encoding collapses
    them, so a manifest carrying one field twice can be re-checksummed into a self-consistent,
    operator-approvable artifact whose recorded value depends on decode order. Its bindings
    are then ambiguous rather than wrong, which no later comparison can detect. Every reader
    of the manifest goes through this one function so the ambiguity is refused once, before
    interpretation, rather than resolved differently in two places.

    Applies at every level of the document, not only the top: `object_pairs_hook` runs for
    each object, so a duplicated key inside `destination_binding` or a snapshot record is
    refused on the same rule. Operation records keep their ordinary decoding — this is the
    manifest's loader, not a general JSON policy.

    Raises:
        DuplicateManifestKeyError: some object in the manifest declares a key twice.
        json.JSONDecodeError: the bytes are not JSON.
        UnicodeDecodeError: the bytes are not decodable text.
    """
    return json.loads(manifest_bytes, object_pairs_hook=_refuse_duplicate_keys)


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
        mapping = load_manifest_mapping(raw.manifest_bytes)
    except DuplicateManifestKeyError as exc:
        raise _torn(
            run_id,
            f"{MANIFEST_FILE_NAME} declares a key more than once, so what it records is ambiguous",
            expected="each manifest key exactly once",
            found=str(exc),
        ) from exc
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

    # The version gate runs before the rest of the manifest is interpreted: an unsupported
    # revision cannot have its remaining fields meaningfully read
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
    # Keep the version refusal type for callers, but a malformed declaration cannot
    # identify a compatible writer. Give it the same rebuild remedy as a torn artifact.
    if not isinstance(found_version, int) or isinstance(found_version, bool):
        msg = (
            f"The plan artifact of run {run_id!r} declares an unusable format version "
            f"{found_version!r}; the manifest is incomplete. Expected an integer format version "
            f"supported by this version of infrahub-sync: {supported_versions_text()}."
        )
        raise PlanFormatVersionError(msg, next_action=PlanArtifactTornError.next_action)
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
    # The writer asserts identifier uniqueness (FR-021) but lines validate
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
            # The record rules that depend on the artifact's declared version — which of
            # `destination_id`'s three states is legal — cannot be read off the record
            # itself, so the manifest's version travels into validation as context.
            operation = PlannedOperation.model_validate(record, context={"format_version": manifest.format_version})
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


def _parse_last_changes(
    raw: RawPlanArtifact, manifest: PlanManifest, operations: Sequence[PlannedOperation], run_id: str
) -> tuple[list[LastChangeRecord] | None, str | None]:
    """Check `last_changes.jsonl` against the manifest and return `(records, problem)` (FR-007).

    `(None, None)` when the manifest has no `last_changes` field: the plan was made before
    the file existed, or its destination keeps no last changes.

    Integrity is refused: the digest is part of the checksummed manifest, so a file that is
    absent, or whose digest or record count does not match, was changed after the plan was
    written, and review and apply refuse it.

    Content is not refused. The file is display-only and apply never reads it, so a record
    that fails the checks below — a writer bug, since the digest matches — must not block
    review or apply. The first bad line's reason comes back as `problem`, with `records`
    `None`, so review shows one warning and no last-change details at all.

    Each record must be a JSON object, a valid `LastChangeRecord`, name an update of the
    plan and a field that update writes, with the matching field type, and no field of an
    update may have two records. An update field with no record is accepted: the payload
    holds every source attribute, not only the changed ones, so the operations alone do not
    show which fields must have a record.
    """
    record = manifest.last_changes
    if record is None:
        return None, None
    mismatch, lines = _checked_last_change_lines(record, raw.last_changes_bytes)
    if mismatch is not None:
        raise _torn(
            run_id,
            f"{LAST_CHANGES_FILE_NAME} does not match the manifest's record of it",
            expected=mismatch[0],
            found=mismatch[1],
        )
    # What each update writes, by field and field type: an attribute is a payload key, a
    # relationship is a recorded reference. A record may name only a field its update writes.
    updates: dict[str, set[tuple[str, str]]] = {
        operation.operation_id: {(name, "attribute") for name in operation.payload or {}}
        | {(reference.field, "relationship") for reference in operation.relationships or []}
        for operation in operations
        if operation.action == "update"
    }
    line_by_field: dict[tuple[str, str], int] = {}
    records: list[LastChangeRecord] = []
    for number, line in enumerate(lines, start=1):
        change = _checked_last_change(line, updates, line_by_field)
        if isinstance(change, str):
            return None, f"{LAST_CHANGES_FILE_NAME} line {number} {change}"
        line_by_field[change.operation_id, change.field] = number
        records.append(change)
    return records, None


def _checked_last_change(
    line: bytes, updates: dict[str, set[tuple[str, str]]], line_by_field: dict[tuple[str, str], int]
) -> LastChangeRecord | str:
    """Parse one last-change line, or return why its content is bad, after the line number."""
    try:
        change = LastChangeRecord.model_validate(json.loads(line))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "is not parseable JSON"
    except ValidationError as exc:
        return f"is not a valid last-change record ({_describe_validation_error(exc)})"
    if change.operation_id not in updates:
        return f"names no update of this plan (operation {change.operation_id!r})"
    if (change.field, change.field_type) not in updates[change.operation_id]:
        return (
            "names a field its update does not write "
            f"({change.field_type} {change.field!r} of operation {change.operation_id!r})"
        )
    first_line = line_by_field.get((change.operation_id, change.field))
    if first_line is not None:
        return (
            f"repeats field {change.field!r} of operation {change.operation_id!r} (first recorded on line {first_line})"
        )
    return change


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


def parse_plan_artifact(raw: RawPlanArtifact, *, run_id: str, parse_last_changes: bool = True) -> LoadedPlan:
    """Validate an already-read artifact into a `LoadedPlan` — the same bytes, uninterrupted.

    The parsing half of `load_plan_artifact`, split out so a caller that verified a
    `RawPlanArtifact` can parse and apply **that object** rather than re-reading the disk —
    a second read is what let files replaced between verification and apply execute
    unverified (DBR-006, DBA-004).

    With `parse_last_changes=False`, `last_changes.jsonl` is neither checked nor parsed and
    `last_changes` is `None`. An apply passes it: apply never reads the file, and
    `verify_plan` has already checked it against the manifest.

    Raises:
        PlanArtifactTornError: the artifact is incomplete or inconsistent — a missing or
            malformed manifest, an absent operations file, a line count disagreeing with
            `operations_count`, or an operations line that fails record validation for a
            reason other than its action (FR-010); or a `last_changes.jsonl` that is absent
            or does not match the manifest's digest or record count (FR-007). A record that
            fails the content checks is not refused: see `LoadedPlan.last_changes_problem`.
        PlanVerificationError: a format 4 plan whose `report.jsonl` is missing or does not
            match the manifest's `report` record (spec 019).
        PlanFormatVersionError: `format_version` is outside `SUPPORTED_FORMAT_VERSIONS`
            (FR-027).
        UnsupportedOperationActionError: an operation's `action` is outside `ACTIONS`,
            refused here — while parsing — and therefore before any destination write
            (FR-017, AD055).
    """
    manifest, mapping = _parse_manifest(raw, run_id)
    operations, operations_bytes = _parse_operations(raw, manifest, run_id)
    report = _parse_report(raw, mapping, run_id)
    last_changes, last_changes_problem = (
        _parse_last_changes(raw, manifest, operations, run_id) if parse_last_changes else (None, None)
    )
    return LoadedPlan(
        manifest=manifest,
        operations=operations,
        manifest_mapping=mapping,
        operations_bytes=operations_bytes,
        last_changes=last_changes,
        last_changes_problem=last_changes_problem,
        report=report,
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
