"""The pre-apply verifier (FR-009, FR-010, FR-011, FR-023).

`verify_plan` returns a list. An **empty** list means the plan is safe to apply; a
non-empty one means refuse, before any destination write. The function writes nothing,
records no run state, and constructs or touches no adapter — the caller owns all three
(AD069).

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

from infrahub_sync.plan.checksum import compute_plan_checksum, snapshot_digest_and_row_count
from infrahub_sync.plan.errors import PlanArtifactUnreadableError
from infrahub_sync.plan.models import SUPPORTED_FORMAT_VERSIONS, VerificationFailure
from infrahub_sync.plan.reader import stat_or_unreadable, supported_versions_text
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME

if TYPE_CHECKING:
    from pathlib import Path

# The checks the format-version gate short-circuits, named in its own message so the
# operator knows what was and was not looked at (AD053).
GATED_CHECKS: tuple[str, ...] = ("run_binding", "plan_checksum", "source_snapshot", "config_version")

RE_PLAN_NEXT_ACTION = "Re-run `diff` for this sync to rebuild the plan artifact, then apply that run."


def _read_optional_bytes(path: Path, *, description: str) -> bytes | None:
    """Return `path`'s bytes, `None` when absent, raising when present but unreadable.

    An absent file is a verdict this function's callers report as a failure in the returned
    list; an unreadable one is a different condition with a different remedy, so it keeps
    its own error class rather than being flattened into "absent" (AD036).
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


def _read_manifest_mapping(plan_dir: Path) -> dict[str, Any] | None:
    """Return the manifest as a mapping, or `None` when absent or unparseable.

    Both conditions are the gate's, per the contract's check-1 row: "not in
    `SUPPORTED_FORMAT_VERSIONS`, **or the manifest cannot be parsed**".
    """
    raw = _read_optional_bytes(plan_dir / MANIFEST_FILE_NAME, description="plan manifest")
    if raw is None:
        return None
    try:
        mapping = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return mapping if isinstance(mapping, dict) else None


def _gate_failure(run_id: str, mapping: dict[str, Any] | None) -> VerificationFailure | None:
    """Evaluate check 1. Returns the gate failure, or `None` when the gate passes."""
    if mapping is None:
        found = "no readable, parseable manifest"
    else:
        declared = mapping.get("format_version")
        if declared in SUPPORTED_FORMAT_VERSIONS:
            return None
        found = "no 'format_version' field" if "format_version" not in mapping else repr(declared)
    return VerificationFailure(
        check="format_version",
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
        VerificationFailure(
            check="run_binding",
            run_id=run_id,
            expected=run_id,
            found=repr(recorded),
            next_action=(
                "The plan artifact in this run directory was written by a different run, so it is "
                "not this run's plan. Apply the run that produced it, or re-run `diff` for this sync."
            ),
        )
    ]


def _operations_failures(run_id: str, run_dir: Path, mapping: dict[str, Any]) -> list[VerificationFailure]:
    """Evaluate check 3 — the operations file's integrity, then its checksum.

    Torn is reported **instead of** a checksum mismatch, because a checksum cannot be
    computed over bytes that are not there (FR-010).
    """
    operations_path = run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME
    recorded_count = mapping.get("operations_count")
    operations_bytes = _read_optional_bytes(operations_path, description="plan operations file")
    if operations_bytes is None:
        return [
            VerificationFailure(
                check="torn_operations",
                run_id=run_id,
                expected=f"{recorded_count} operation line(s) at {operations_path}",
                found="no operations file",
                next_action=RE_PLAN_NEXT_ACTION,
            )
        ]
    text = operations_bytes.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    if len(lines) != recorded_count:
        return [
            VerificationFailure(
                check="torn_operations",
                run_id=run_id,
                expected=f"{recorded_count} operation line(s)",
                found=f"{len(lines)} operation line(s)",
                next_action=RE_PLAN_NEXT_ACTION,
            )
        ]

    # `compute_plan_checksum` removes the excluded manifest fields itself, so the mapping is
    # passed through exactly as read, unknown fields included (AD035, FR-027).
    recomputed = compute_plan_checksum(mapping, operations_bytes)
    recorded_checksum = mapping.get("plan_checksum")
    if recomputed == recorded_checksum:
        return []
    return [
        VerificationFailure(
            check="plan_checksum",
            run_id=run_id,
            expected=str(recorded_checksum),
            found=recomputed,
            next_action=(
                "The artifact changed after it was written, so what would be applied is not what was "
                f"reviewed. {RE_PLAN_NEXT_ACTION}"
            ),
        )
    ]


def source_snapshot_failures(*, run_id: str, run_dir: Path, mapping: dict[str, Any]) -> list[VerificationFailure]:
    """Evaluate check 4 — the source-snapshot binding (FR-004, FR-010, AD037).

    Absent, truncated and mismatched all land on this one check name — the three words
    SC-004 enumerates — and each failure names the snapshot it is about, so a plan bound to
    several snapshots reports which one disagreed. The digest is over **logical rows** with
    `_extract_ts` dropped, not the file's raw bytes (AD037).

    Public because FR-010 puts this check on the **review** path as well as the apply path:
    a run whose recorded snapshot is absent or truncated would otherwise render with
    `checksum: OK` and no note, which is a safety check reporting a result it never
    computed. `read_saved_plan` calls this and turns each failure into a verification note,
    so both paths reach one implementation and their verdicts cannot drift.
    """
    recorded = mapping.get("source_snapshot")
    if not isinstance(recorded, list):
        return [
            VerificationFailure(
                check="source_snapshot",
                run_id=run_id,
                expected="a list of recorded source snapshots",
                found=f"a {type(recorded).__name__}",
                next_action=RE_PLAN_NEXT_ACTION,
            )
        ]
    failures: list[VerificationFailure] = []
    for record in recorded:
        if not isinstance(record, dict):
            failures.append(
                VerificationFailure(
                    check="source_snapshot",
                    run_id=run_id,
                    expected="a recorded snapshot object",
                    found=f"a {type(record).__name__}",
                    next_action=RE_PLAN_NEXT_ACTION,
                )
            )
            continue
        relative = str(record.get("path"))
        snapshot_path = run_dir / relative
        if stat_or_unreadable(snapshot_path, description="source snapshot") is None:
            failures.append(
                VerificationFailure(
                    check="source_snapshot",
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
        digest, row_count = snapshot_digest_and_row_count(snapshot_path)
        if digest == record.get("digest") and row_count == record.get("row_count"):
            continue
        failures.append(
            VerificationFailure(
                check="source_snapshot",
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
        VerificationFailure(
            check="config_version",
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
        VerificationFailure(
            check="write_surface",
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


def verify_plan(
    *,
    run_dir: Path,
    run_id: str,
    config_version: str,
    write_surface_missing_on: str | None = None,
) -> list[VerificationFailure]:
    """Run the FR-009 pre-apply checks and return every failure, empty when safe to apply.

    Args:
        run_dir: The run directory holding `plan/`. Read only — nothing is created here.
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
        PlanArtifactUnreadableError: a path exists but could not be read. Unreadable is a
            different condition from absent, with a different remedy, so it is not
            flattened into a failure entry (AD036).
    """
    mapping = _read_manifest_mapping(run_dir / PLAN_DIR_NAME)
    gate = _gate_failure(run_id, mapping)
    if gate is not None or mapping is None:
        # `mapping is None` cannot occur with `gate is None`; the second clause is what
        # tells `ty` that `mapping` is a mapping below.
        return [*([] if gate is None else [gate]), *_write_surface_failures(run_id, write_surface_missing_on)]

    failures: list[VerificationFailure] = []
    failures.extend(_run_binding_failures(run_id, mapping))
    failures.extend(_operations_failures(run_id, run_dir, mapping))
    failures.extend(source_snapshot_failures(run_id=run_id, run_dir=run_dir, mapping=mapping))
    failures.extend(_config_version_failures(run_id, mapping, config_version))
    failures.extend(_write_surface_failures(run_id, write_surface_missing_on))
    return failures
