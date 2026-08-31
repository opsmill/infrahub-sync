"""AR7: a registered plan carries a typed, required destination-schema binding.

The binding is one field — a full SHA-256 `schema_fingerprint` over the consumed schema
semantics the plan was computed against. It is required on a manifest that carries a
configuration binding, covered by `plan_checksum` like every other manifest fact, and
absent from the unregistered manifests the local CLI has always written.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.errors import PlanArtifactTornError
from infrahub_sync.plan.models import PLAN_FORMAT_VERSION, PlanManifest
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME, write_plan_artifact
from tests.plan.artifact_fixtures import CONFIG_VERSION, RUN_ID, SYNC_NAME, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

# A registered plan carries two bindings: the configuration package version it was planned
# from, and the consumed destination-schema semantics it was planned against.
BINDING = ("config-001", 1, "a" * 64)
FINGERPRINT = "b" * 64


@pytest.fixture(autouse=True)
def _isolated_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep `read_saved_plan` inside `tmp_path` rather than a developer cache."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))


def _run_dir(tmp_path: Path) -> Path:
    """The run directory `read_saved_plan` resolves for `SYNC_NAME`/`RUN_ID`."""
    return tmp_path / SYNC_NAME / RUN_ID


def _write_registered(
    run_dir: Path,
    *,
    schema_fingerprint: str | None = FINGERPRINT,
    configuration_binding: tuple[str, int, str] | None = BINDING,
) -> PlanManifest:
    """Write one registered plan artifact through the real writer."""
    return write_plan_artifact(
        run_dir=run_dir,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        configuration_binding=configuration_binding,
        schema_fingerprint=schema_fingerprint,
    )


def _manifest_mapping(run_dir: Path) -> dict[str, Any]:
    """The manifest as written, read back as a mapping."""
    return json.loads((run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))


def _raw_manifest(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 — a raw manifest mapping is heterogeneous
    """A valid raw manifest mapping, before any binding is added."""
    record: dict[str, Any] = {
        "format_version": PLAN_FORMAT_VERSION,
        "run_id": RUN_ID,
        "created_at": "2026-07-26T18:04:11.512034+00:00",
        "config_version": CONFIG_VERSION,
        "source_snapshot": [],
        "operations_count": 0,
        "delete_operations_computed": True,
        "plan_checksum": "a91c",
    }
    record.update(overrides)
    return record


def _registered(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 — a raw manifest mapping is heterogeneous
    """A raw manifest mapping carrying the complete configuration binding."""
    return _raw_manifest(
        config_id=BINDING[0],
        registry_version=BINDING[1],
        package_checksum=BINDING[2],
        **overrides,
    )


# ======================================================================================
# The writer records it, and the checksum covers it
# ======================================================================================


def test_a_registered_plan_records_a_full_sha256_schema_fingerprint(tmp_path: Path) -> None:
    """The recorded binding is the full digest, not the legacy 12-hex kind-name subhash."""
    manifest = _write_registered(_run_dir(tmp_path))

    assert manifest.schema_fingerprint == FINGERPRINT
    assert _manifest_mapping(_run_dir(tmp_path))["schema_fingerprint"] == FINGERPRINT


def test_the_recorded_schema_fingerprint_is_covered_by_the_plan_checksum(tmp_path: Path) -> None:
    """Editing the recorded binding invalidates the plan rather than passing unnoticed."""
    run_dir = _run_dir(tmp_path)
    manifest = _write_registered(run_dir)
    on_disk = _manifest_mapping(run_dir)
    operations_bytes = (run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()

    assert manifest.plan_checksum == compute_plan_checksum(on_disk, operations_bytes)
    assert manifest.plan_checksum != compute_plan_checksum(
        {**on_disk, "schema_fingerprint": "c" * 64}, operations_bytes
    )


def test_an_unregistered_plan_records_no_schema_binding(tmp_path: Path) -> None:
    """The local CLI writes no binding, so its manifests keep the shape they always had."""
    run_dir = _run_dir(tmp_path)
    _write_registered(run_dir, configuration_binding=None, schema_fingerprint=None)

    assert "schema_fingerprint" not in _manifest_mapping(run_dir)


# ======================================================================================
# The field is typed and required for a registered manifest
# ======================================================================================


def test_a_registered_manifest_without_a_schema_binding_is_refused() -> None:
    """A registered plan that records no consumed schema semantics is incomplete."""
    with pytest.raises(ValidationError):
        PlanManifest(**_registered())


@pytest.mark.parametrize(
    "fingerprint",
    [
        pytest.param("", id="empty"),
        pytest.param("not-a-digest", id="text"),
        pytest.param("5f2c9b1e7a4d", id="legacy-12-hex-subhash"),
        pytest.param("b" * 63, id="too-short"),
        pytest.param("b" * 65, id="too-long"),
        pytest.param("B" * 64, id="uppercase"),
        pytest.param(" " + "b" * 64, id="padded"),
        pytest.param("b" * 63 + "\n", id="control-character"),
        pytest.param(0, id="non-string"),
        pytest.param(["b" * 64], id="list"),
    ],
)
def test_a_registered_manifest_with_a_malformed_schema_binding_is_refused(fingerprint: object) -> None:
    """The binding is one exact lowercase full digest; nothing is coerced into one."""
    with pytest.raises(ValidationError):
        PlanManifest(**_registered(schema_fingerprint=fingerprint))


def test_an_unregistered_manifest_needs_no_schema_binding() -> None:
    """Legacy unregistered manifests keep their current read behaviour."""
    manifest = PlanManifest(**_raw_manifest())

    assert manifest.schema_fingerprint is None
    assert manifest.registered_schema_fingerprint is None


def test_a_registered_manifest_reports_its_recorded_binding() -> None:
    manifest = PlanManifest(**_registered(schema_fingerprint=FINGERPRINT))

    assert manifest.registered_schema_fingerprint == FINGERPRINT


# ======================================================================================
# What a reader sees
# ======================================================================================


def test_a_registered_artifact_missing_its_schema_binding_reads_as_torn(tmp_path: Path) -> None:
    """Refused while reading, and therefore before any apply constructs a destination."""
    run_dir = _run_dir(tmp_path)
    write_artifact(
        run_dir,
        [operation_record()],
        run_id=RUN_ID,
        config_id=BINDING[0],
        registry_version=BINDING[1],
        package_checksum=BINDING[2],
    )

    with pytest.raises(PlanArtifactTornError, match="schema_fingerprint"):
        load_plan_artifact(run_dir)


def test_a_registered_artifact_with_a_malformed_schema_binding_reads_as_torn(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    write_artifact(
        run_dir,
        [operation_record()],
        run_id=RUN_ID,
        config_id=BINDING[0],
        registry_version=BINDING[1],
        package_checksum=BINDING[2],
        schema_fingerprint="not-a-digest",
    )

    with pytest.raises(PlanArtifactTornError, match="schema_fingerprint"):
        load_plan_artifact(run_dir)


def test_a_legacy_unregistered_artifact_still_reads(tmp_path: Path) -> None:
    """Preservation: an artifact written before either binding existed is unaffected."""
    run_dir = _run_dir(tmp_path)
    write_artifact(run_dir, [operation_record()], run_id=RUN_ID)

    loaded = load_plan_artifact(run_dir)

    assert loaded.manifest.configuration_binding is None
    assert loaded.manifest.schema_fingerprint is None


# ======================================================================================
# It survives read, review and publication unchanged
# ======================================================================================


def test_the_recorded_binding_survives_read_review_and_publication_unchanged(tmp_path: Path) -> None:
    """Review reads the plan as data and changes neither the binding nor the bytes."""
    run_dir = _run_dir(tmp_path)
    _write_registered(run_dir)
    before = (run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes()

    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID)

    assert saved.checksum_ok
    assert saved.manifest.registered_schema_fingerprint == FINGERPRINT
    # Byte-faithful: re-serializing the read manifest reproduces what was written, so a
    # published review renders the same binding the artifact carries.
    assert saved.manifest.model_dump(mode="json")["schema_fingerprint"] == FINGERPRINT
    assert (run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes() == before
