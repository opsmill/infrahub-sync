"""The pre-apply verifier (FR-009, FR-010, FR-011, FR-023).

`verify_plan` returns a list. An **empty** list means the plan is safe to apply; a
non-empty one means refuse, before any destination write. The function writes nothing,
records no run state, and constructs or touches no adapter — the caller owns all three
(AD069).

The manifest and operations file arrive as an already-read `RawPlanArtifact` rather than
being read here, so the bytes this gate verifies are the very bytes the caller goes on to
parse and apply — a verifier that re-read the disk would certify a copy that is then
discarded (DBR-006, DBA-004). Only the source snapshots are digested from disk, because
they are verification *subjects*, never applied.

Two rules shape the check order and they are each other's exception, which is why both are
stated here rather than left to a reader of the loop (AD053):

- **Check 1 is a gate.** An artifact whose `format_version` this release does not
  understand cannot have its remaining fields meaningfully interpreted, so when the gate
  fails checks 2 to 5 are **not evaluated** and the failure says so (PD-006).
- **Once the gate passes, all of 2 to 5 are evaluated and every failure is named**, so one
  apply attempt tells the operator everything that is wrong rather than one thing at a time
  (AD036).

The `write_surface` check is deliberately **not** behind the gate. It is derived from an
argument, not from the artifact, so there is nothing about it a bad format version makes
uninterpretable; the gate's rationale simply does not reach it. It is evaluated last
because it is the only check whose subject is the destination adapter rather than the
artifact.

Its parameter is the adapter's **name**, not a boolean (AD058). The failure it drives
promises a message that *names the adapter*, which a boolean cannot supply — the earlier
signature made its own promised message unwritable from the arguments the function
received. `None` means the planned-write surface is present.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pyarrow import ArrowInvalid
from pydantic import ValidationError

from infrahub_sync.plan.checksum import compute_plan_checksum, snapshot_digest_and_row_count
from infrahub_sync.plan.errors import PlanArtifactUnreadableError
from infrahub_sync.plan.models import (
    SUPPORTED_FORMAT_VERSIONS,
    DestinationBindingRecord,
    VerificationFailure,
    require_run_relative_path,
)
from infrahub_sync.plan.reader import operation_record_lines, stat_or_unreadable, supported_versions_text
from infrahub_sync.plan.writer import OPERATIONS_FILE_NAME, PLAN_DIR_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from infrahub_sync.plan.models import VerificationCheck
    from infrahub_sync.plan.reader import RawPlanArtifact

# The checks the format-version gate short-circuits, named in its own message so the
# operator knows what was and was not looked at (AD053).
GATED_CHECKS: tuple[VerificationCheck, ...] = ("run_binding", "plan_checksum", "source_snapshot", "config_version")

RE_PLAN_NEXT_ACTION = "Re-run `diff` for this sync to rebuild the plan artifact, then apply that run."


def _failure(
    check: VerificationCheck,
    *,
    run_id: str,
    expected: str,
    found: str,
    next_action: str = RE_PLAN_NEXT_ACTION,
) -> VerificationFailure:
    """Build one failed check; AD059's re-plan instruction is the default next action.

    Every site names its check, the refused run, and expected versus found; most share the
    re-plan next action, so it is the default rather than restated.
    """
    return VerificationFailure(check=check, run_id=run_id, expected=expected, found=found, next_action=next_action)


def manifest_mapping_or_none(manifest_bytes: bytes | None) -> dict[str, Any] | None:
    """Return the manifest bytes as a mapping, or `None` when absent or unparseable.

    Both conditions are the gate's, per the contract's check-1 row: "not in
    `SUPPORTED_FORMAT_VERSIONS`, **or the manifest cannot be parsed**".

    Public because the CLI's `--expected-checksum` check needs the same bytes-to-mapping step
    before it can hash a stored manifest, and its own copy of it caught `JSONDecodeError`
    alone: `json.loads` decodes first, so non-UTF-8 manifest bytes raise `UnicodeDecodeError`,
    which escaped that refusal path as a traceback (LOC-03). One helper, one answer for both
    callers.

    Named `..._or_none` rather than `manifest_mapping`, which `plan_checksum_failure` below
    already uses for the parameter that receives this function's result.
    """
    if manifest_bytes is None:
        return None
    try:
        mapping = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return mapping if isinstance(mapping, dict) else None


def _gate_failure(run_id: str, mapping: dict[str, Any] | None) -> VerificationFailure | None:
    """Evaluate check 1. Returns the gate failure, or `None` when the gate passes."""
    if mapping is None:
        found = "no readable, parseable manifest"
    else:
        declared = mapping.get("format_version")
        # The `isinstance` guard runs first (MIN-002): an unhashable hand-edited value like
        # `[2]` would raise `TypeError` from the frozenset membership test, in the component
        # built to classify corrupt manifests.
        if isinstance(declared, int) and declared in SUPPORTED_FORMAT_VERSIONS:
            return None
        found = "no 'format_version' field" if "format_version" not in mapping else repr(declared)
    return _failure(
        "format_version",
        run_id=run_id,
        # Worded as a sentence rather than as the bare version list, because this failure is
        # what an operator reads when an artifact a newer release wrote reaches this one: it
        # is the apply path's whole answer for SC-018, which requires the message to name the
        # version found **and** the versions supported.
        expected=f"one of the supported plan format versions: {supported_versions_text()}",
        found=found,
        next_action=(
            f"The remaining checks ({', '.join(GATED_CHECKS)}) were not evaluated: an artifact whose "
            f"format version this release does not understand cannot have its remaining fields "
            f"meaningfully interpreted. Re-run `diff` with this version of infrahub-sync to rebuild "
            f"the artifact, or apply it with the version that wrote it."
        ),
    )


def _run_binding_failures(run_id: str, mapping: dict[str, Any]) -> list[VerificationFailure]:
    """Evaluate check 2 — the plan's own run identifier against the run being applied.

    A separate equality comparison rather than a checksum input, because `run_id` is
    deliberately excluded from `plan_checksum` for SC-006 — which is exactly what would
    otherwise let a `plan/` directory copied between run directories verify clean (AD012).
    """
    recorded = mapping.get("run_id")
    if recorded == run_id:
        return []
    return [
        _failure(
            "run_binding",
            run_id=run_id,
            expected=run_id,
            found=repr(recorded),
            next_action=(
                "The plan artifact in this run directory was written by a different run, so it is "
                "not this run's plan. Apply the run that produced it, or re-run `diff` for this sync."
            ),
        )
    ]


def _operations_failures(run_id: str, artifact: RawPlanArtifact, mapping: dict[str, Any]) -> list[VerificationFailure]:
    """Evaluate check 3 — the operations file's integrity, then its checksum.

    Torn is reported **instead of** a checksum mismatch, because a checksum cannot be
    computed over bytes that are not there (FR-010).
    """
    operations_path = artifact.run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME
    recorded_count = mapping.get("operations_count")
    operations_bytes = artifact.operations_bytes
    if operations_bytes is None:
        return [
            _failure(
                "torn_operations",
                run_id=run_id,
                expected=f"{recorded_count} operation line(s) at {operations_path}",
                found="no operations file",
            )
        ]
    line_count = len(operation_record_lines(operations_bytes))
    if line_count != recorded_count:
        return [
            _failure(
                "torn_operations",
                run_id=run_id,
                expected=f"{recorded_count} operation line(s)",
                found=f"{line_count} operation line(s)",
            )
        ]

    failure = plan_checksum_failure(run_id=run_id, manifest_mapping=mapping, operations_bytes=operations_bytes)
    return [] if failure is None else [failure]


def plan_checksum_failure(
    *,
    run_id: str,
    manifest_mapping: Mapping[str, Any],
    operations_bytes: bytes,
) -> VerificationFailure | None:
    """Evaluate the plan-checksum comparison alone, returning `None` when it matches.

    Public because FR-010 puts this check on the **review** path as well as the apply path:
    `read_saved_plan` derives `checksum_ok` and its note from this comparison, so both paths
    reach one implementation and their verdicts cannot drift — the same rule
    `source_snapshot_failures` already follows.

    `compute_plan_checksum` removes the excluded manifest fields itself, so the mapping is
    passed through exactly as read, unknown fields included (AD035, FR-027).
    """
    recomputed = compute_plan_checksum(manifest_mapping, operations_bytes)
    recorded_checksum = manifest_mapping.get("plan_checksum")
    if recomputed == recorded_checksum:
        return None
    return _failure(
        "plan_checksum",
        run_id=run_id,
        expected=str(recorded_checksum),
        found=recomputed,
        next_action=(
            "The artifact changed after it was written, so what would be applied is not what was "
            f"reviewed. {RE_PLAN_NEXT_ACTION}"
        ),
    )


def source_snapshot_failures(*, run_id: str, run_dir: Path, mapping: dict[str, Any]) -> list[VerificationFailure]:
    """Evaluate check 4 — the source-snapshot binding (FR-004, FR-010, AD037).

    Absent, truncated and mismatched all land on this one check name — the three words
    SC-004 enumerates — as does a snapshot whose bytes are not readable Parquet at all, and
    each failure names the snapshot it is about, so a plan bound to several snapshots
    reports which one disagreed. The digest is over **logical rows** with `_extract_ts`
    dropped, not the file's raw bytes (AD037).

    Public because FR-010 puts this check on the **review** path as well as the apply path:
    a run whose recorded snapshot is absent or truncated would otherwise render with
    `checksum: OK` and no note, which is a safety check reporting a result it never
    computed. `read_saved_plan` calls this and turns each failure into a verification note,
    so both paths reach one implementation and their verdicts cannot drift.
    """
    recorded = mapping.get("source_snapshot")
    if not isinstance(recorded, list):
        return [
            _failure(
                "source_snapshot",
                run_id=run_id,
                expected="a list of recorded source snapshots",
                found=f"a {type(recorded).__name__}",
            )
        ]
    failures: list[VerificationFailure] = []
    for record in recorded:
        if not isinstance(record, dict):
            failures.append(
                _failure(
                    "source_snapshot",
                    run_id=run_id,
                    expected="a recorded snapshot object",
                    found=f"a {type(record).__name__}",
                )
            )
            continue
        # MIN-003: the check reads the raw manifest mapping, so the model's run-relative
        # rule is mirrored here — a `..` segment or an absolute path would send the digest
        # below to a file outside the run directory, and a record with no `path` at all
        # used to be probed at `<run_dir>/None`.
        raw_path = record.get("path")
        try:
            relative = require_run_relative_path(raw_path) if isinstance(raw_path, str) else None
        except ValueError:
            relative = None
        if relative is None:
            failures.append(
                _failure(
                    "source_snapshot",
                    run_id=run_id,
                    expected="a run-relative snapshot path with no absolute, '.' or '..' segments",
                    found=repr(raw_path),
                )
            )
            continue
        snapshot_path = run_dir / relative
        if stat_or_unreadable(snapshot_path, description="source snapshot") is None:
            failures.append(
                _failure(
                    "source_snapshot",
                    run_id=run_id,
                    expected=f"{relative}: {record.get('row_count')} row(s), digest {record.get('digest')}",
                    found=f"{relative}: absent",
                    next_action=(
                        "The source snapshot the plan was computed against is gone, so the plan cannot "
                        f"be shown to still describe it. {RE_PLAN_NEXT_ACTION}"
                    ),
                )
            )
            continue
        try:
            digest, row_count = snapshot_digest_and_row_count(snapshot_path)
        except ArrowInvalid:
            # Byte corruption, not row-level truncation: the file stats fine but its bytes
            # are not a readable Parquet table. Same check, same remedy as the other three
            # snapshot conditions (FR-010, AD059).
            failures.append(
                _failure(
                    "source_snapshot",
                    run_id=run_id,
                    expected=f"{relative}: {record.get('row_count')} row(s), digest {record.get('digest')}",
                    found=f"{relative}: bytes that are not a readable Parquet snapshot",
                    next_action=(
                        "The source snapshot's bytes are corrupt, so the plan cannot be shown to "
                        f"still describe it. {RE_PLAN_NEXT_ACTION}"
                    ),
                )
            )
            continue
        except OSError as exc:
            # The stat above succeeded, so this is removed-between-stat-and-open or
            # stat-allowed/read-denied — unreadable, not absent, with a different remedy
            # (AD036); it is raised rather than flattened into a failure entry.
            msg = f"The source snapshot at {str(snapshot_path)!r} exists but could not be read: {exc.strerror or exc}."
            raise PlanArtifactUnreadableError(msg) from exc
        if digest == record.get("digest") and row_count == record.get("row_count"):
            continue
        failures.append(
            _failure(
                "source_snapshot",
                run_id=run_id,
                expected=f"{relative}: {record.get('row_count')} row(s), digest {record.get('digest')}",
                found=f"{relative}: {row_count} row(s), digest {digest}",
                next_action=(
                    "The source snapshot changed after the plan was computed, so the plan no longer "
                    f"describes it. {RE_PLAN_NEXT_ACTION}"
                ),
            )
        )
    return failures


def _config_version_failures(run_id: str, mapping: dict[str, Any], config_version: str) -> list[VerificationFailure]:
    """Evaluate check 5 — configuration-version equality, never interpretation.

    The value is compared and **never parsed** (FR-011, SC-013). The caller supplies the
    comparison value: recomputed by the default rule on the CLI path, or verbatim from an
    in-process caller (AD013).
    """
    recorded = mapping.get("config_version")
    if recorded == config_version:
        return []
    return [
        _failure(
            "config_version",
            run_id=run_id,
            expected=str(recorded),
            found=config_version,
            next_action=(
                "The configuration changed after the plan was saved, so the plan may no longer describe "
                f"what this configuration would do. {RE_PLAN_NEXT_ACTION}"
            ),
        )
    ]


def _write_surface_failures(run_id: str, write_surface_missing_on: str | None) -> list[VerificationFailure]:
    """Evaluate the write-surface check, whose subject is the adapter and not the artifact.

    Evaluated in the same pre-write gate rather than surfacing later as a per-operation
    failure (FR-023), and the failure **names the adapter** — which is the whole reason the
    parameter is the adapter's name and not a boolean (AD058).
    """
    if write_surface_missing_on is None:
        return []
    return [
        _failure(
            "write_surface",
            run_id=run_id,
            expected="a destination adapter that implements the planned-write surface",
            found=f"adapter {write_surface_missing_on!r} does not implement it",
            next_action=(
                f"The destination adapter {write_surface_missing_on!r} cannot apply a saved plan. Use "
                f"`infrahub-sync sync` for this destination, or apply against a destination whose "
                f"adapter implements the planned-write surface."
            ),
        )
    ]


def destination_binding_failure(
    *,
    run_id: str,
    artifact: RawPlanArtifact,
    live: DestinationBindingRecord | None,
) -> VerificationFailure | None:
    """Compare the manifest's recorded destination against the live one (FIX-005, spec 002).

    The plan records the **effective** destination — endpoint URL and branch as the adapter
    resolved them, environment variables included — precisely because the config-version
    digest is blind to that resolution (PD-003/AD041 cover the parsed YAML only). A plan
    reviewed against one destination must not silently apply to another.

    Evaluated at the apply seam (`PlanApplier`), not inside `verify_plan`: like
    `write_surface`, its subject is the destination adapter rather than the artifact, but
    unlike every `verify_plan` check it is also **overridable** — the CLI's
    `--allow-destination-change` exists for a deliberate cross-environment apply — so it
    cannot sit behind a gate whose non-empty result is an unconditional refusal.

    Returns `None` — the check is skipped, not passed — when the manifest is absent or
    unparseable (the format gate owns that verdict), when it predates the field, or when
    the live adapter exposes no binding to compare against.
    """
    mapping = manifest_mapping_or_none(artifact.manifest_bytes)
    if mapping is None or live is None:
        return None
    recorded = mapping.get("destination_binding")
    if recorded is None:
        return None
    try:
        recorded_binding = DestinationBindingRecord.model_validate(recorded)
    except ValidationError:
        return _failure(
            "destination_binding",
            run_id=run_id,
            expected="a destination_binding record with a url and an optional branch",
            found=repr(recorded),
        )
    if recorded_binding == live:
        return None
    return _failure(
        "destination_binding",
        run_id=run_id,
        expected=f"url {recorded_binding.url!r}, branch {recorded_binding.branch!r}",
        found=f"url {live.url!r}, branch {live.branch!r}",
        next_action=(
            "The plan was computed against a different destination than this apply would write "
            "to. Re-run `diff` against this destination to rebuild the plan, or pass "
            "--allow-destination-change to deliberately apply it across environments."
        ),
    )


def verify_plan(
    *,
    artifact: RawPlanArtifact,
    run_id: str,
    config_version: str,
    write_surface_missing_on: str | None = None,
) -> list[VerificationFailure]:
    """Run the FR-009 pre-apply checks and return every failure, empty when safe to apply.

    Args:
        artifact: The artifact's bytes as `read_plan_artifact_bytes` returned them. Taking
            the bytes rather than a directory is what makes "the bytes verified are the
            bytes applied" hold: the caller parses and applies this same object, and no
            second read exists for a concurrent rewrite to slip through (DBR-006). The
            source snapshots it names are still digested from disk, under its `run_dir`.
        run_id: The run being applied. Compared against the artifact's own `run_id`, and
            carried on every failure so an operator applying several runs knows which one
            was refused (AD036).
        config_version: The comparison value for check 5, compared for equality and never
            parsed (FR-011).
        write_surface_missing_on: The **name** of the destination adapter that lacks the
            planned-write surface, or `None` when the surface is present (AD058).

    Returns:
        Every failed check, each naming itself, the refused run, expected and found where
        neither value is secret, and the operator's next action. Empty means safe to apply.

    Raises:
        PlanArtifactUnreadableError: a snapshot path exists but could not be examined, or
            could not be read after a successful stat. Unreadable is a different condition
            from absent, with a different remedy, so it is not flattened into a failure
            entry (AD036).
    """
    mapping = manifest_mapping_or_none(artifact.manifest_bytes)
    gate = _gate_failure(run_id, mapping)
    if gate is not None or mapping is None:
        # `mapping is None` cannot occur with `gate is None`; the second clause is what
        # tells `ty` that `mapping` is a mapping below.
        return [*([] if gate is None else [gate]), *_write_surface_failures(run_id, write_surface_missing_on)]

    failures: list[VerificationFailure] = []
    failures.extend(_run_binding_failures(run_id, mapping))
    failures.extend(_operations_failures(run_id, artifact, mapping))
    failures.extend(source_snapshot_failures(run_id=run_id, run_dir=artifact.run_dir, mapping=mapping))
    failures.extend(_config_version_failures(run_id, mapping, config_version))
    failures.extend(_write_surface_failures(run_id, write_surface_missing_on))
    return failures
