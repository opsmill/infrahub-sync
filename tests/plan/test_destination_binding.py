"""The plan's destination binding, end to end in-process.

The config-version digest covers the parsed YAML only (PD-003/AD041), while the adapter
resolves its endpoint from environment variables **over** settings — so a plan reviewed
against one destination could apply to another with no signal. The manifest therefore
records the *effective* destination identity (normalized URL and branch, never the token)
as an additive `destination_binding` field, and `PlanApplier` compares it against the live
adapter before delegating to the engine.

Four behaviors from the spec's own test list: a mismatch refuses; `allow_destination_change`
applies anyway; a plan without the field skips the check; normalization keeps equivalent
addresses from false-refusing. Plus one more: the typed `destination_binding` check
serializes to its plain string form.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.plan.errors import PlanVerificationError
from infrahub_sync.plan.models import (
    ApplyRecord,
    DestinationBindingRecord,
    PlanManifest,
    VerificationFailure,
)
from infrahub_sync.plan.reader import read_plan_artifact_bytes
from infrahub_sync.plan.verify import destination_binding_failure
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.utils import PlanApplier
from tests.plan.artifact_fixtures import RUN_ID, manifest_path, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.potenda import Potenda

RECORDED_URL = "http://recorded.example:8000"
LIVE_URL = "http://elsewhere.example:8000"
BINDING_OVERRIDE = {"url": RECORDED_URL, "branch": "main"}


def _live(url: str = RECORDED_URL, branch: str | None = "main") -> DestinationBindingRecord:
    return DestinationBindingRecord(url=url, branch=branch)


def _artifact(tmp_path: Path, **manifest_overrides: Any) -> Any:  # noqa: ANN401 — the raw-artifact type is an implementation detail here
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    write_artifact(directory, **manifest_overrides)
    return read_plan_artifact_bytes(directory)


# ======================================================================================
# The record: normalization, and what it must never carry
# ======================================================================================


@pytest.mark.parametrize(
    ("spelled", "normalized"),
    [
        pytest.param("http://Recorded.Example:8000/", RECORDED_URL, id="host case and trailing slash"),
        pytest.param("HTTP://recorded.example:8000", RECORDED_URL, id="scheme case"),
        pytest.param("http://recorded.example:8000///", RECORDED_URL, id="repeated trailing slashes"),
        pytest.param("  http://recorded.example:8000 ", RECORDED_URL, id="surrounding whitespace"),
    ],
)
def test_equivalent_urls_normalize_to_one_form(spelled: str, normalized: str) -> None:
    """Equivalent addresses must not false-refuse an apply."""
    assert DestinationBindingRecord(url=spelled).url == normalized


def test_path_case_is_preserved_while_host_case_is_not() -> None:
    """Only scheme and host are case-insensitive; a path is not."""
    record = DestinationBindingRecord(url="http://Host:8000/Api/Path")
    assert record.url == "http://host:8000/Api/Path"


def test_userinfo_is_dropped_so_no_credential_can_reach_the_manifest() -> None:
    """The record must never carry a token, however the endpoint was spelled."""
    record = DestinationBindingRecord(url="http://user:secret-token@host:8000")
    assert "secret-token" not in record.url
    assert record.url == "http://host:8000"


def test_the_record_is_a_closed_field_set_with_no_token_field() -> None:
    """`url` and `branch` only: a token has no field to arrive through."""
    with pytest.raises(ValueError, match="token"):
        DestinationBindingRecord(url=RECORDED_URL, token="never")  # ty: ignore[unknown-argument]  # noqa: S106 — the refusal under test


def test_records_built_from_equivalent_spellings_compare_equal() -> None:
    """The comparison the apply-time check performs is record equality."""
    assert _live("http://Recorded.Example:8000/") == _live(RECORDED_URL)


# ======================================================================================
# The writer: the field is additive, and covered by the plan checksum
# ======================================================================================


def _written_manifest(tmp_path: Path, binding: DestinationBindingRecord | None) -> dict[str, Any]:
    directory = tmp_path / RUN_ID
    write_plan_artifact(
        run_dir=directory,
        run_id=RUN_ID,
        config_version="cfg-version",
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        destination_binding=binding,
    )
    return json.loads(manifest_path(directory).read_bytes())


def test_the_writer_records_the_binding_and_the_manifest_model_reads_it_back(tmp_path: Path) -> None:
    written = _written_manifest(tmp_path, _live())

    assert written["destination_binding"] == {"url": RECORDED_URL, "branch": "main"}
    manifest = PlanManifest.model_validate(written)
    assert manifest.destination_binding == _live()


def test_without_a_binding_the_manifest_keeps_the_pre_fix_shape(tmp_path: Path) -> None:
    """`None` writes no field at all — absent, not `null` — so older readers see no change."""
    written = _written_manifest(tmp_path, None)

    assert "destination_binding" not in written
    assert PlanManifest.model_validate(written).destination_binding is None


def test_tampering_with_the_recorded_binding_breaks_the_plan_checksum(tmp_path: Path) -> None:
    """The field is written before the checksum is computed, so it is covered by it."""
    from infrahub_sync.plan.checksum import compute_plan_checksum

    written = _written_manifest(tmp_path, _live())
    tampered = dict(written, destination_binding={"url": LIVE_URL, "branch": "main"})

    assert compute_plan_checksum(tampered, b"") != written["plan_checksum"]


# ======================================================================================
# The comparison — `destination_binding_failure`
# ======================================================================================


def test_a_matching_binding_produces_no_failure(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)

    assert destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=_live()) is None


def test_a_different_url_is_a_destination_binding_failure(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)

    failure = destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=_live(url=LIVE_URL))

    assert failure is not None
    assert failure.check == "destination_binding"
    assert RECORDED_URL in str(failure.expected)
    assert LIVE_URL in str(failure.found)
    assert "--allow-destination-change" in failure.next_action
    assert "Re-run `diff`" in failure.next_action


def test_a_different_branch_alone_is_a_destination_binding_failure(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)

    failure = destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=_live(branch="staging"))

    assert failure is not None
    assert failure.check == "destination_binding"
    assert "staging" in str(failure.found)


def test_a_plan_without_the_field_skips_the_check(tmp_path: Path) -> None:
    """Older-format plans carry no binding, and the check skips rather than refuses."""
    artifact = _artifact(tmp_path)

    assert destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=_live()) is None


def test_a_destination_without_a_live_binding_skips_the_check(tmp_path: Path) -> None:
    """An adapter that captures no binding gives the check nothing to compare against."""
    artifact = _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)

    assert destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=None) is None


def test_an_equivalently_spelled_recorded_url_does_not_false_refuse(tmp_path: Path) -> None:
    """Normalization applies to the manifest's value too, not only to live captures."""
    artifact = _artifact(tmp_path, destination_binding={"url": "HTTP://Recorded.Example:8000/", "branch": "main"})

    assert destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=_live()) is None


def test_an_unreadable_recorded_binding_is_a_failure_not_a_crash(tmp_path: Path) -> None:
    """A manifest claiming a binding the record type cannot read must not verify clean."""
    artifact = _artifact(tmp_path, destination_binding={"endpoint": "not-the-field-set"})

    failure = destination_binding_failure(run_id=RUN_ID, artifact=artifact, live=_live())

    assert failure is not None
    assert failure.check == "destination_binding"


# ======================================================================================
# The apply seam — `PlanApplier.apply_plan` refuses, overrides, or skips
# ======================================================================================


class _RecordingEngine:
    """Stands in for `Potenda` at the seam: counts delegations, constructs nothing."""

    def __init__(self, destination: object) -> None:
        self.destination = destination
        self.apply_calls = 0
        self.artifacts: list[object] = []

    def apply_plan(
        self,
        *,
        config_version: str | None = None,
        artifact: object = None,
        expected_checksum: str | None = None,
    ) -> ApplyRecord:
        _ = config_version, expected_checksum
        self.apply_calls += 1
        # Recorded, not ignored: the seam's read is what the engine must apply, so a delegation
        # that arrived without it would mean the engine reads the artifact a second time.
        self.artifacts.append(artifact)
        return ApplyRecord()


class _BoundDestination:
    """A destination double exposing a live binding, as the Infrahub adapter does."""

    def __init__(self, url: str, branch: str | None = "main") -> None:
        self.destination_binding = DestinationBindingRecord(url=url, branch=branch)


def _applier(tmp_path: Path, destination: object) -> tuple[PlanApplier, _RecordingEngine]:
    engine = _RecordingEngine(destination)
    return PlanApplier(cast("Potenda", engine), run_dir=tmp_path / RUN_ID, run_id=RUN_ID), engine


def test_a_destination_mismatch_refuses_before_the_engine_is_reached(tmp_path: Path) -> None:
    _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)
    applier, engine = _applier(tmp_path, _BoundDestination(LIVE_URL))

    with pytest.raises(PlanVerificationError) as raised:
        applier.apply_plan()

    message = str(raised.value)
    assert RECORDED_URL in message
    assert LIVE_URL in message
    assert "Nothing was written" in message
    assert "--allow-destination-change" in message
    assert engine.apply_calls == 0


def test_the_override_applies_and_discloses_the_change(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)
    applier, engine = _applier(tmp_path, _BoundDestination(LIVE_URL))

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.utils"):
        applier.apply_plan(allow_destination_change=True)

    assert engine.apply_calls == 1
    warnings = " ".join(record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING)
    assert RECORDED_URL in warnings
    assert LIVE_URL in warnings


def test_a_matching_destination_applies_without_ceremony(tmp_path: Path) -> None:
    _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)
    applier, engine = _applier(tmp_path, _BoundDestination(RECORDED_URL))

    applier.apply_plan()

    assert engine.apply_calls == 1
    assert engine.artifacts == [engine.artifacts[0]], "the engine was delegated to exactly once"
    assert engine.artifacts[0] is not None, (
        "the binding was compared against a read the engine never received, so the two could diverge"
    )


def test_a_plan_without_the_field_applies_against_any_destination(tmp_path: Path) -> None:
    """The absent-field skip, on the seam itself (older-format plans)."""
    _artifact(tmp_path)
    applier, engine = _applier(tmp_path, _BoundDestination(LIVE_URL))

    applier.apply_plan()

    assert engine.apply_calls == 1


def test_a_destination_exposing_no_binding_applies_unchecked(tmp_path: Path) -> None:
    _artifact(tmp_path, destination_binding=BINDING_OVERRIDE)
    applier, engine = _applier(tmp_path, object())

    applier.apply_plan()

    assert engine.apply_calls == 1


# ======================================================================================
# The typed check vocabulary
# ======================================================================================


def test_the_destination_binding_check_serializes_to_its_plain_string() -> None:
    """The `VerificationCheck` alias changes no serialized value."""
    failure = VerificationFailure(check="destination_binding", run_id=RUN_ID, next_action="re-plan")

    assert failure.model_dump()["check"] == "destination_binding"
    assert json.loads(failure.model_dump_json())["check"] == "destination_binding"
