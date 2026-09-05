"""Reading the digests out of the OCI layout an export writes.

The digests recorded here are what later units consume to identify a candidate
without rebuilding it, so a layout this cannot read has to be an error rather
than a partially filled record.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tasks import image

if TYPE_CHECKING:
    from pathlib import Path

INDEX_DIGEST = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
MANIFEST_DIGEST = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
CONFIG_DIGEST = "sha256:3333333333333333333333333333333333333333333333333333333333333333"


def write_blob(layout: Path, digest: str, document: object) -> None:
    """Write one JSON blob into a layout, at the path its digest names."""
    algorithm, _, encoded = digest.partition(":")
    blob = layout / "blobs" / algorithm / encoded
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps(document), encoding="utf-8")


def write_layout(layout: Path, root_digest: str, root: object) -> None:
    """Write a layout whose index names one root descriptor, and that root itself."""
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "index.json").write_text(json.dumps({"manifests": [{"digest": root_digest}]}), encoding="utf-8")
    write_blob(layout, root_digest, root)


def manifest() -> dict:
    """Return an image manifest naming the shared configuration blob."""
    return {"config": {"digest": CONFIG_DIGEST}}


def test_a_multi_platform_export_records_one_manifest_digest_for_each_platform(tmp_path: Path) -> None:
    layout = tmp_path / "oci"
    write_layout(
        layout,
        INDEX_DIGEST,
        {"manifests": [{"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}}]},
    )
    write_blob(layout, MANIFEST_DIGEST, manifest())
    write_blob(layout, CONFIG_DIGEST, {"os": "linux", "architecture": "arm64"})

    assert image.read_layout(layout) == {
        "index": INDEX_DIGEST,
        "platforms": {"linux/arm64": {"manifest": MANIFEST_DIGEST, "config": CONFIG_DIGEST}},
    }


def test_a_single_platform_export_names_its_manifest_without_an_index(tmp_path: Path) -> None:
    """buildx writes no index for one platform; the platform comes from the configuration."""
    layout = tmp_path / "oci"
    write_layout(layout, MANIFEST_DIGEST, manifest())
    write_blob(layout, CONFIG_DIGEST, {"os": "linux", "architecture": "amd64"})

    assert image.read_layout(layout) == {
        "index": MANIFEST_DIGEST,
        "platforms": {"linux/amd64": {"manifest": MANIFEST_DIGEST, "config": CONFIG_DIGEST}},
    }


def test_an_attestation_manifest_is_refused_rather_than_recorded_as_an_image(tmp_path: Path) -> None:
    """The build asks for none, so one appearing means the export is not what it claims."""
    layout = tmp_path / "oci"
    write_layout(
        layout,
        INDEX_DIGEST,
        {"manifests": [{"digest": MANIFEST_DIGEST, "platform": {"os": "unknown", "architecture": "unknown"}}]},
    )
    write_blob(layout, MANIFEST_DIGEST, manifest())

    with pytest.raises(image.ImageTaskError, match="attestation"):
        image.read_layout(layout)


def test_a_layout_missing_a_blob_the_index_names_is_refused(tmp_path: Path) -> None:
    layout = tmp_path / "oci"
    write_layout(
        layout,
        INDEX_DIGEST,
        {"manifests": [{"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}}]},
    )

    with pytest.raises(image.ImageTaskError, match="does not hold the blob"):
        image.read_layout(layout)


def test_a_manifest_without_a_configuration_digest_is_refused(tmp_path: Path) -> None:
    layout = tmp_path / "oci"
    write_layout(
        layout,
        INDEX_DIGEST,
        {"manifests": [{"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}}]},
    )
    write_blob(layout, MANIFEST_DIGEST, {"config": {}})

    with pytest.raises(image.ImageTaskError, match="configuration digest"):
        image.read_layout(layout)


def test_a_directory_that_is_not_a_layout_names_the_build_that_makes_one(tmp_path: Path) -> None:
    with pytest.raises(image.ImageTaskError, match=r"image\.build"):
        image.read_layout(tmp_path)
