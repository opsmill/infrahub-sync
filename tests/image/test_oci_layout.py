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

# The annotation an OCI exporter writes beside the index descriptor, and the
# value buildx puts there for an export nobody named. It is the only name the
# layout carries, so it is the only name a binding record can be honest about.
REF_NAME_ANNOTATION = "org.opencontainers.image.ref.name"
EXPORTED_NAME = "latest"


def write_blob(layout: Path, digest: str, document: object) -> None:
    """Write one JSON blob into a layout, at the path its digest names."""
    algorithm, _, encoded = digest.partition(":")
    blob = layout / "blobs" / algorithm / encoded
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps(document), encoding="utf-8")


def write_layout(layout: Path, root_digest: str, root: object, *, name: str | None = EXPORTED_NAME) -> None:
    """Write a layout whose index names one root descriptor, and that root itself."""
    layout.mkdir(parents=True, exist_ok=True)
    descriptor: dict[str, object] = {"digest": root_digest}
    if name is not None:
        descriptor["annotations"] = {REF_NAME_ANNOTATION: name}
    (layout / "index.json").write_text(json.dumps({"manifests": [descriptor]}), encoding="utf-8")
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
        "index_name": EXPORTED_NAME,
        "platforms": {"linux/arm64": {"manifest": MANIFEST_DIGEST, "config": CONFIG_DIGEST}},
    }


def test_a_single_platform_export_names_its_manifest_without_an_index(tmp_path: Path) -> None:
    """buildx writes no index for one platform; the platform comes from the configuration."""
    layout = tmp_path / "oci"
    write_layout(layout, MANIFEST_DIGEST, manifest())
    write_blob(layout, CONFIG_DIGEST, {"os": "linux", "architecture": "amd64"})

    assert image.read_layout(layout) == {
        "index": MANIFEST_DIGEST,
        "index_name": EXPORTED_NAME,
        "platforms": {"linux/amd64": {"manifest": MANIFEST_DIGEST, "config": CONFIG_DIGEST}},
    }


def test_the_layouts_own_index_annotation_is_what_is_recorded(tmp_path: Path) -> None:
    """The record's index reference has to name what the exporter wrote, not a constant.

    A hard-coded name would agree with this repository's own export today and
    keep agreeing after the exporter started writing something else, which is
    the one thing a binding an operator resolves must not do.
    """
    layout = tmp_path / "oci"
    write_layout(
        layout,
        INDEX_DIGEST,
        {"manifests": [{"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}}]},
        name="infrahub-sync-candidate",
    )
    write_blob(layout, MANIFEST_DIGEST, manifest())
    write_blob(layout, CONFIG_DIGEST, {"os": "linux", "architecture": "amd64"})

    assert image.read_layout(layout)["index_name"] == "infrahub-sync-candidate"


def test_an_index_descriptor_with_no_reference_annotation_records_no_name(tmp_path: Path) -> None:
    """Reading a layout is not where this refuses; the record producer is.

    A layout with no name is still a readable layout, and `image.inspect` has to
    keep working on one. What cannot happen is a bundle shipping a binding whose
    index half names nothing, so the refusal belongs to whoever writes it.
    """
    layout = tmp_path / "oci"
    write_layout(
        layout,
        INDEX_DIGEST,
        {"manifests": [{"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}}]},
        name=None,
    )
    write_blob(layout, MANIFEST_DIGEST, manifest())
    write_blob(layout, CONFIG_DIGEST, {"os": "linux", "architecture": "amd64"})

    assert not image.read_layout(layout)["index_name"]


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
