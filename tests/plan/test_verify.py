"""T025 — the pre-apply verifier's return value (FR-009, FR-010, SC-004, SC-015, AD058, AD059).

A **pure unit test of what `verify_plan` returns**, with no apply and no destination in
scope. `verify_plan` writes nothing, records no run state and constructs no adapter *by
construction*, so asserting any of those three here would be asserting against a stub rather
than against behaviour. The zero-writes and `failed`-run-state halves of SC-004 and SC-015
belong to T065, on the Phase F CLI apply path.

Two rules in this module are each other's exception, so both halves are asserted rather than
one being inferred from the other (AD053):

- the **format-version gate** short-circuits checks 2 to 5 and says they were not evaluated;
- once the gate passes, **every** remaining failure is named in one call.

A gate test that only asserted "the gate failure is present" would pass against an
implementation that evaluated everything, and an evaluate-all test alone would pass against
one that short-circuited on the first failure of any kind.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn

import pytest

from infrahub_sync.cache.parquet_io import write_resource_side
from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.errors import PlanArtifactUnreadableError
from infrahub_sync.plan.reader import read_plan_artifact_bytes
from infrahub_sync.plan.verify import GATED_CHECKS, verify_plan
from tests.plan.artifact_fixtures import (
    CONFIG_VERSION,
    OTHER_RUN_ID,
    RUN_ID,
    manifest_path,
    operations_path,
    tamper_with_operations,
    tamperable_operation,
    write_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from infrahub_sync.plan.models import VerificationFailure

UNSUPPORTED_FORMAT_VERSION = 99
MISSING_ADAPTER = "ExampleDestinationAdapter"

# `plan_checksum` is lowercase sha256 hex with no prefix (FR-027.8).
SHA256_HEX_LENGTH = 64

_EXTRACT_TS = datetime(2026, 7, 26, 18, 4, 11, tzinfo=timezone.utc)

SNAPSHOT_ROWS: list[dict[str, Any]] = [
    {"name": "prod", "description": "production"},
    {"name": "staging", "description": "staging"},
    {"name": "retired", "description": None},
]


def _write_snapshot(run_directory: Path, rows: list[dict[str, Any]]) -> None:
    """Write the source side's snapshot through the engine's own writer.

    The engine's writer rather than a hand-built table, because check 4 digests the table's
    **logical rows** with `_extract_ts` dropped (AD037) — a digest over a hand-built table
    missing the injected columns would not be the digest the verifier computes.
    """
    write_resource_side(
        run_dir=run_directory,
        side="A",
        resource="BuiltinTag",
        rows=list(rows),
        source_ids=[str(row["name"]) for row in rows],
        extract_ts=_EXTRACT_TS,
        tombstones=None,
    )


def _verifiable_run(tmp_path: Path, *, run_id: str = RUN_ID) -> Path:
    """A run directory whose artifact verifies clean, as every case's starting point."""
    directory = tmp_path / run_id
    directory.mkdir(parents=True, exist_ok=True)
    _write_snapshot(directory, SNAPSHOT_ROWS)
    write_artifact(
        directory,
        [tamperable_operation()],
        run_id=run_id,
        source_snapshot=source_snapshot_records(directory),
    )
    return directory


def _snapshot_path(run_directory: Path) -> Path:
    return run_directory / "A" / "BuiltinTag.parquet"


def _checks(failures: list[VerificationFailure]) -> list[str]:
    return [failure.check for failure in failures]


def _failure(failures: list[VerificationFailure], check: str) -> VerificationFailure:
    """The one failure carrying `check`, asserting it is present exactly once."""
    matching = [failure for failure in failures if failure.check == check]
    assert len(matching) == 1, f"expected exactly one {check!r} failure, got {_checks(failures)}"
    return matching[0]


def _text(failure: VerificationFailure) -> str:
    """A failure's whole operator-visible text, which is what "the message" means here.

    `VerificationFailure` is a record, not a rendered string, so a claim about its message is
    a claim about the fields a renderer shows: expected, found and the next action.
    """
    return " ".join(part for part in (failure.expected, failure.found, failure.next_action) if part)


def _verify(
    *,
    run_dir: Path,
    run_id: str,
    config_version: str,
    write_surface_missing_on: str | None = None,
) -> list[VerificationFailure]:
    """Read the artifact once and verify those bytes — the only shape `verify_plan` accepts.

    `verify_plan` takes a `RawPlanArtifact` rather than a directory so its caller applies
    the same bytes it verified; every case here reads through the same one-read function
    the apply path uses.
    """
    return verify_plan(
        artifact=read_plan_artifact_bytes(run_dir),
        run_id=run_id,
        config_version=config_version,
        write_surface_missing_on=write_surface_missing_on,
    )


# ======================================================================================
# The precondition every negative case rests on
# ======================================================================================


def test_a_clean_artifact_verifies_with_an_empty_list(tmp_path: Path) -> None:
    """Empty means safe to apply. Without this, every case below could pass vacuously."""
    directory = _verifiable_run(tmp_path)

    assert _verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION) == []


# ======================================================================================
# SC-004 (verifier half) — six negative cases
# ======================================================================================


def _case_checksum_mismatch(run_directory: Path) -> str:
    """The artifact's contents changed after it was written."""
    tamper_with_operations(run_directory)
    return CONFIG_VERSION


def _case_config_version_mismatch(run_directory: Path) -> str:  # noqa: ARG001 — the mismatch is in the argument, not the tree
    """The configuration changed after the plan was saved."""
    return "a-different-configuration-version"


def _case_snapshot_binding_mismatch(run_directory: Path) -> str:
    """The source snapshot changed in place: same row count, different values."""
    changed = [dict(row, description="edited") for row in SNAPSHOT_ROWS]
    _write_snapshot(run_directory, changed)
    return CONFIG_VERSION


def _case_absent_operations(run_directory: Path) -> str:
    """The operations file is gone, so no checksum can be computed over it (FR-010)."""
    operations_path(run_directory).unlink()
    return CONFIG_VERSION


def _case_truncated_snapshot(run_directory: Path) -> str:
    """The snapshot lost rows, so both its digest and its row count disagree."""
    _write_snapshot(run_directory, SNAPSHOT_ROWS[:1])
    return CONFIG_VERSION


def _case_absent_snapshot(run_directory: Path) -> str:
    """The snapshot the plan was computed against is gone (User Story 2 scenario 1)."""
    _snapshot_path(run_directory).unlink()
    return CONFIG_VERSION


SC004_CASES: dict[str, tuple[Callable[[Path], str], str]] = {
    "checksum_mismatch": (_case_checksum_mismatch, "plan_checksum"),
    "config_version_mismatch": (_case_config_version_mismatch, "config_version"),
    "snapshot_binding_mismatch": (_case_snapshot_binding_mismatch, "source_snapshot"),
    "absent_operations": (_case_absent_operations, "torn_operations"),
    "truncated_snapshot": (_case_truncated_snapshot, "source_snapshot"),
    "absent_snapshot": (_case_absent_snapshot, "source_snapshot"),
}


@pytest.mark.parametrize(("mutate", "expected_check"), list(SC004_CASES.values()), ids=list(SC004_CASES))
def test_sc004_case_is_refused_naming_the_failed_check(
    tmp_path: Path, mutate: Callable[[Path], str], expected_check: str
) -> None:
    """Each of SC-004's six cases returns a non-empty list naming its own check.

    The list being non-empty is the refusal: a non-empty return is what the caller turns into
    a refusal before any destination write, and the write side of that is T065's.
    """
    directory = _verifiable_run(tmp_path)
    config_version = mutate(directory)

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version=config_version)

    assert failures != []
    failure = _failure(failures, expected_check)
    assert failure.run_id == RUN_ID
    assert failure.expected
    assert failure.found
    assert failure.expected != failure.found


@pytest.mark.parametrize(("mutate", "expected_check"), list(SC004_CASES.values()), ids=list(SC004_CASES))
def test_every_sc004_failure_carries_a_next_action(
    tmp_path: Path, mutate: Callable[[Path], str], expected_check: str
) -> None:
    """AD059 over the whole set, so a check cannot be added later without a next action."""
    directory = _verifiable_run(tmp_path)
    config_version = mutate(directory)

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version=config_version)

    assert expected_check in _checks(failures)
    for failure in failures:
        assert failure.next_action.strip()


def test_an_absent_operations_file_is_reported_as_torn_and_not_as_a_checksum_mismatch(tmp_path: Path) -> None:
    """A checksum cannot be computed over bytes that are not there (FR-010).

    Reporting a mismatch here would send the operator looking for tampering when the artifact
    is simply incomplete.
    """
    directory = _verifiable_run(tmp_path)
    operations_path(directory).unlink()

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert "plan_checksum" not in _checks(failures)
    assert "torn_operations" in _checks(failures)


def test_the_checksum_failure_names_both_hex_values(tmp_path: Path) -> None:
    """`expected` and `found` are the two digests, neither of which is secret (FR-018)."""
    directory = _verifiable_run(tmp_path)
    tamper_with_operations(directory)

    failure = _failure(_verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION), "plan_checksum")

    assert failure.expected is not None
    assert failure.found is not None
    assert len(failure.expected) == SHA256_HEX_LENGTH
    assert len(failure.found) == SHA256_HEX_LENGTH


def test_the_config_version_failure_carries_both_opaque_values(tmp_path: Path) -> None:
    """Compared, never parsed (FR-011, SC-013): the two values are reported verbatim."""
    directory = _verifiable_run(tmp_path)

    failure = _failure(
        _verify(run_dir=directory, run_id=RUN_ID, config_version="an-opaque-caller-value"),
        "config_version",
    )

    assert failure.expected == CONFIG_VERSION
    assert failure.found == "an-opaque-caller-value"


# ======================================================================================
# SC-015 (verifier half) — the run binding
# ======================================================================================


def test_a_plan_directory_copied_into_another_run_fails_the_run_binding(tmp_path: Path) -> None:
    """The copied artifact is not this run's plan, and nothing else about it disagrees.

    `run_id` is deliberately outside `plan_checksum` for SC-006, which is exactly what would
    let a copied `plan/` verify clean without this separate equality check (AD012).
    """
    source = _verifiable_run(tmp_path, run_id=RUN_ID)
    destination = tmp_path / OTHER_RUN_ID
    shutil.copytree(source, destination)

    failures = _verify(run_dir=destination, run_id=OTHER_RUN_ID, config_version=CONFIG_VERSION)

    assert _checks(failures) == ["run_binding"]
    failure = failures[0]
    assert failure.expected == OTHER_RUN_ID
    assert RUN_ID in str(failure.found)
    assert failure.next_action


def test_the_copied_artifacts_checksum_still_verifies(tmp_path: Path) -> None:
    """The precondition of the case above: the copy is intact, only its binding is wrong."""
    source = _verifiable_run(tmp_path, run_id=RUN_ID)
    destination = tmp_path / OTHER_RUN_ID
    shutil.copytree(source, destination)

    failures = _verify(run_dir=destination, run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert failures == []


# ======================================================================================
# PD-006 / AD053 — the gate, and the evaluate-all rule that is its exception
# ======================================================================================


def test_the_format_version_gate_short_circuits_the_remaining_checks(tmp_path: Path) -> None:
    """One failure and no other, and its message says the rest were not evaluated.

    The plan below is broken **twice** — an unsupported format version and a configuration
    version that disagrees — so an implementation that evaluated checks 2 to 5 anyway would
    return two failures and fail here. That second break is what makes the assertion
    falsifiable.
    """
    directory = _verifiable_run(tmp_path)
    write_artifact(
        directory,
        [tamperable_operation()],
        source_snapshot=source_snapshot_records(directory),
        format_version=UNSUPPORTED_FORMAT_VERSION,
    )

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version="a-different-configuration-version")

    assert _checks(failures) == ["format_version"]
    failure = failures[0]
    assert str(UNSUPPORTED_FORMAT_VERSION) in str(failure.found)
    assert "not evaluated" in failure.next_action
    for gated in GATED_CHECKS:
        assert gated in failure.next_action


def test_the_gate_names_the_checks_it_did_not_evaluate() -> None:
    """The gated set is exactly checks 2 to 5 — the operator is told what was skipped."""
    assert GATED_CHECKS == ("run_binding", "plan_checksum", "source_snapshot", "config_version")


def test_an_unhashable_format_version_fails_the_gate_rather_than_raising(tmp_path: Path) -> None:
    """MIN-002: a hand-edited `format_version` like `[2]` is unhashable.

    A bare `declared in SUPPORTED_FORMAT_VERSIONS` raises `TypeError` against a frozenset —
    a raw traceback from the very component built to classify corrupt manifests. It must be
    the gate's ordinary refusal instead.
    """
    directory = _verifiable_run(tmp_path)
    write_artifact(
        directory,
        [tamperable_operation()],
        source_snapshot=source_snapshot_records(directory),
        format_version=[2],
    )

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert _checks(failures) == ["format_version"]
    assert "[2]" in str(failures[0].found)


def test_an_unparseable_manifest_also_fails_the_gate(tmp_path: Path) -> None:
    """Check 1's second condition: "or the manifest cannot be parsed" (contract, check 1)."""
    directory = _verifiable_run(tmp_path)
    manifest_path(directory).write_bytes(b'{"format_version": 2, "run_id":')

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert _checks(failures) == ["format_version"]
    assert "manifest" in str(failures[0].found)


def test_once_the_gate_passes_two_simultaneous_failures_are_both_named(tmp_path: Path) -> None:
    """The gate's exception: one apply attempt tells the operator everything (AD036)."""
    directory = _verifiable_run(tmp_path)
    _write_snapshot(directory, SNAPSHOT_ROWS[:2])

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version="a-different-configuration-version")

    assert set(_checks(failures)) == {"source_snapshot", "config_version"}


def test_all_four_gated_checks_can_fail_in_one_call(tmp_path: Path) -> None:
    """The evaluate-all rule does not stop at two: checks 2 to 5 all report in one call."""
    source = _verifiable_run(tmp_path, run_id=RUN_ID)
    directory = tmp_path / OTHER_RUN_ID
    shutil.copytree(source, directory)
    tamper_with_operations(directory)
    _snapshot_path(directory).unlink()

    failures = _verify(run_dir=directory, run_id=OTHER_RUN_ID, config_version="a-different-configuration-version")

    assert set(_checks(failures)) == {"run_binding", "plan_checksum", "source_snapshot", "config_version"}


# ======================================================================================
# AD058 — the write-surface parameter is the adapter's name
# ======================================================================================


def test_the_write_surface_failure_names_the_adapter_that_was_passed_in(tmp_path: Path) -> None:
    """FR-023's message names the adapter, which a `bool` argument could not have supplied.

    This is the whole reason the parameter is `write_surface_missing_on: str | None` and not
    `write_surface_available: bool` (AD058): the earlier signature made its own promised
    message unwritable from the arguments the function received.
    """
    directory = _verifiable_run(tmp_path)

    failures = _verify(
        run_dir=directory,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        write_surface_missing_on=MISSING_ADAPTER,
    )

    assert _checks(failures) == ["write_surface"]
    failure = failures[0]
    assert MISSING_ADAPTER in _text(failure)
    assert MISSING_ADAPTER in str(failure.found)
    assert MISSING_ADAPTER in failure.next_action
    assert "sync" in failure.next_action


def test_none_means_the_write_surface_is_present(tmp_path: Path) -> None:
    """`None` is the present case, so a clean plan with a capable adapter returns empty."""
    directory = _verifiable_run(tmp_path)

    failures = _verify(
        run_dir=directory,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        write_surface_missing_on=None,
    )

    assert failures == []


def test_the_write_surface_check_is_not_behind_the_format_version_gate(tmp_path: Path) -> None:
    """Its subject is the adapter, not the artifact, so a bad version makes it no less legible.

    The gate's rationale — an unreadable revision cannot have its remaining fields
    interpreted — simply does not reach a check derived from an argument.
    """
    directory = _verifiable_run(tmp_path)
    write_artifact(
        directory,
        [tamperable_operation()],
        source_snapshot=source_snapshot_records(directory),
        format_version=UNSUPPORTED_FORMAT_VERSION,
    )

    failures = _verify(
        run_dir=directory,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        write_surface_missing_on=MISSING_ADAPTER,
    )

    assert set(_checks(failures)) == {"format_version", "write_surface"}


# ======================================================================================
# FIX-003 / RIG-07 (spec 002) — snapshot bytes that stat fine but cannot be digested
# ======================================================================================


def test_a_byte_corrupt_snapshot_is_a_classified_source_snapshot_failure(tmp_path: Path) -> None:
    """Garbage bytes at the manifest-declared snapshot path land on check 4, not a crash.

    `pyarrow` raises `ArrowInvalid` — not an `OSError` — for a file whose bytes are not a
    Parquet table, so without the classification the verifier raises an undocumented
    exception instead of returning the refusal (FIX-003).
    """
    directory = _verifiable_run(tmp_path)
    _snapshot_path(directory).write_bytes(b"these bytes are not a Parquet table")

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION)

    failure = _failure(failures, "source_snapshot")
    text = _text(failure)
    assert "A/BuiltinTag.parquet" in text
    assert "not a readable Parquet snapshot" in text
    assert "Re-run `diff`" in failure.next_action


def test_a_byte_corrupt_snapshot_still_lets_every_other_failure_be_named(tmp_path: Path) -> None:
    """Classification, not short-circuit: the evaluate-all disclosure survives (AD036)."""
    directory = _verifiable_run(tmp_path)
    _snapshot_path(directory).write_bytes(b"garbage")

    failures = _verify(run_dir=directory, run_id=RUN_ID, config_version="a-different-config-version")

    assert set(_checks(failures)) == {"source_snapshot", "config_version"}


def test_a_read_denied_snapshot_raises_the_unreadable_taxonomy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RIG-07: a read-time `OSError` after a successful stat is unreadable, not absent.

    The snapshot stats fine, then the digest read is denied — removed between stat and
    open, or stat-allowed/read-denied permissions. That must surface as the taxonomy's
    `PlanArtifactUnreadableError` naming the path, with its next action, rather than as a
    raw `PermissionError` (AD036, AD059).
    """
    directory = _verifiable_run(tmp_path)

    def _deny(path: str) -> NoReturn:
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr("infrahub_sync.plan.checksum.read_table", _deny)

    with pytest.raises(PlanArtifactUnreadableError) as raised:
        _verify(run_dir=directory, run_id=RUN_ID, config_version=CONFIG_VERSION)

    message = str(raised.value)
    assert "BuiltinTag.parquet" in message
    assert "could not be read" in message
    assert "Next action:" in message
