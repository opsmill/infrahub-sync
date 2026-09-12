"""The identity a containerd image store gives a loaded legacy docker-archive.

Docker's classic image store keeps the configuration digest as a loaded image's
ID. The containerd image store — the default from Docker Engine 29 — keeps the
digest of a Docker schema2 manifest the engine synthesizes while it loads an
archive that carries none. A deployment that names only the first identity
cannot resolve the image it was qualified against on such a host.

That synthesized manifest is a pure function of the archive's own bytes, so the
release derives the identity here rather than asking an engine for it: a value
read from a daemon would describe whatever that daemon happened to hold, and
the machine that builds a bundle is not the machine that runs it.

Nothing here runs Docker. The archives below are written by this module, and
the one real observation is pinned as descriptors rather than as an image.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from typing import TYPE_CHECKING

import pytest

from tasks import image

if TYPE_CHECKING:
    from pathlib import Path

MANIFEST_MEDIA_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
CONFIG_MEDIA_TYPE = "application/vnd.docker.container.image.v1+json"
LAYER_MEDIA_TYPE = "application/vnd.docker.image.rootfs.diff.tar"

# The configuration blob of the synthetic archive. Its content is irrelevant to
# the identity — only its digest and its length reach the manifest — but it is
# valid image configuration so nothing reading the archive is surprised by it.
CONFIG_DOCUMENT = {"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": []}}

# A zstd frame's magic, as the first four bytes of a compressed layer blob.
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def digest_of(blob: bytes) -> str:
    """Return the `sha256:`-prefixed digest of one blob."""
    return f"sha256:{hashlib.sha256(blob).hexdigest()}"


def plain_layer() -> bytes:
    """Return an uncompressed layer tar, as skopeo writes one into a docker-archive."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as layer:
        content = b"a file this layer adds\n"
        entry = tarfile.TarInfo("opt/infrahub-sync/marker")
        entry.size = len(content)
        layer.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def write_archive(path: Path, *, entries: list[dict[str, object]], blobs: dict[str, bytes]) -> Path:
    """Write one docker-archive holding exactly the manifest and blobs given."""
    with tarfile.open(path, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, blob in blobs.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(blob)
            archive.addfile(entry, io.BytesIO(blob))
        document = json.dumps(entries).encode("utf-8")
        entry = tarfile.TarInfo("manifest.json")
        entry.size = len(document)
        archive.addfile(entry, io.BytesIO(document))
    return path


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """A one-image archive with two layers: one uncompressed, one gzipped."""
    config = json.dumps(CONFIG_DOCUMENT).encode("utf-8")
    first = plain_layer()
    second = gzip.compress(first)
    return write_archive(
        tmp_path / "image-linux-amd64.tar",
        entries=[
            {
                "Config": f"{hashlib.sha256(config).hexdigest()}.json",
                "RepoTags": ["infrahub-sync-local:linux-amd64"],
                "Layers": ["first/layer.tar", "second/layer.tar"],
            }
        ],
        blobs={
            f"{hashlib.sha256(config).hexdigest()}.json": config,
            "first/layer.tar": first,
            "second/layer.tar": second,
        },
    )


def test_the_synthesized_manifest_is_the_compact_schema2_document() -> None:
    """Every byte of it decides the digest, so every byte is the assertion.

    Key order, the compact separators, and the media type each layer's own
    magic selects: a document that differs from Docker's in any of them hashes
    to an identity no engine holds.
    """
    config = json.dumps(CONFIG_DOCUMENT).encode("utf-8")
    first = plain_layer()
    second = gzip.compress(first)

    document = image.synthesized_manifest(
        (CONFIG_MEDIA_TYPE, digest_of(config), len(config)),
        [
            (LAYER_MEDIA_TYPE, digest_of(first), len(first)),
            (f"{LAYER_MEDIA_TYPE}.gzip", digest_of(second), len(second)),
        ],
    )

    assert document == (
        b'{"schemaVersion":2,'
        b'"mediaType":"' + MANIFEST_MEDIA_TYPE.encode("ascii") + b'",'
        b'"config":{"mediaType":"' + CONFIG_MEDIA_TYPE.encode("ascii") + b'",'
        b'"digest":"' + digest_of(config).encode("ascii") + b'","size":' + str(len(config)).encode("ascii") + b"},"
        b'"layers":[{"mediaType":"' + LAYER_MEDIA_TYPE.encode("ascii") + b'",'
        b'"digest":"' + digest_of(first).encode("ascii") + b'","size":' + str(len(first)).encode("ascii") + b"},"
        b'{"mediaType":"' + LAYER_MEDIA_TYPE.encode("ascii") + b'.gzip",'
        b'"digest":"' + digest_of(second).encode("ascii") + b'","size":' + str(len(second)).encode("ascii") + b"}]}"
    )
    assert document == document.decode("utf-8").encode("ascii")


def test_the_archive_identity_is_the_digest_of_that_document(archive: Path) -> None:
    """The archive is read once and the identity is the hash of what it describes."""
    config = json.dumps(CONFIG_DOCUMENT).encode("utf-8")
    first = plain_layer()
    second = gzip.compress(first)
    document = image.synthesized_manifest(
        (CONFIG_MEDIA_TYPE, digest_of(config), len(config)),
        [
            (LAYER_MEDIA_TYPE, digest_of(first), len(first)),
            (f"{LAYER_MEDIA_TYPE}.gzip", digest_of(second), len(second)),
        ],
    )

    assert image.archive_manifest(archive) == digest_of(document)


def test_a_zstd_layer_carries_the_media_type_its_own_magic_names(tmp_path: Path) -> None:
    """Compression is read from the blob, not from what the archive calls the file."""
    config = json.dumps(CONFIG_DOCUMENT).encode("utf-8")
    compressed = ZSTD_MAGIC + b"a frame body this test never decodes"
    archive = write_archive(
        tmp_path / "image-linux-amd64.tar",
        entries=[{"Config": "config.json", "Layers": ["only/layer.tar"]}],
        blobs={"config.json": config, "only/layer.tar": compressed},
    )

    document = image.synthesized_manifest(
        (CONFIG_MEDIA_TYPE, digest_of(config), len(config)),
        [(f"{LAYER_MEDIA_TYPE}.zstd", digest_of(compressed), len(compressed))],
    )

    assert image.archive_manifest(archive) == digest_of(document)


@pytest.mark.parametrize("count", [0, 2])
def test_an_archive_that_does_not_hold_exactly_one_image_is_refused(tmp_path: Path, count: int) -> None:
    """One archive is one candidate; anything else names no single identity."""
    config = json.dumps(CONFIG_DOCUMENT).encode("utf-8")
    entry: dict[str, object] = {"Config": "config.json", "Layers": []}
    archive = write_archive(
        tmp_path / "image-linux-amd64.tar",
        entries=[dict(entry) for _ in range(count)],
        blobs={"config.json": config},
    )

    with pytest.raises(image.ImageTaskError, match="exactly one image"):
        image.archive_manifest(archive)


def test_an_archive_naming_no_configuration_is_refused(tmp_path: Path) -> None:
    """The configuration descriptor is the first thing the manifest holds."""
    archive = write_archive(tmp_path / "image-linux-amd64.tar", entries=[{"Layers": []}], blobs={"config.json": b"{}"})

    with pytest.raises(image.ImageTaskError, match="no image configuration"):
        image.archive_manifest(archive)


def test_something_that_is_not_an_archive_is_refused(tmp_path: Path) -> None:
    """A truncated or unrelated file says nothing, and must not be read as an image."""
    path = tmp_path / "image-linux-amd64.tar"
    path.write_bytes(b"not a tar archive")

    with pytest.raises(image.ImageTaskError, match="readable Docker-load archive"):
        image.archive_manifest(path)


# The one real observation this function exists to reproduce. The descriptors
# are the h1.2 candidate's own configuration and layers, in the order its
# archive lists them, and the expected value is the image ID Docker 29.8.0's
# containerd image store reported after loading that archive. Pinned as
# descriptors rather than as an archive because the archive is 700 MB and the
# claim is about the derivation, not about holding a copy of the candidate.
H12_CONFIG = ("sha256:48e29d2331f1cd8dd60b5164e934f63c2b585802b6e7a2305b6df9ff2d082070", 9323)
H12_LAYERS = (
    ("sha256:1d69a5fd31932841d7825ef4780c06f008eea65aaa9f3110fe09d5832ed5c7d8", 77895680),
    ("sha256:374a61fb2659809c4af957114e4ca2a8d7adebfdd214c8d043011033696cf1ac", 9604096),
    ("sha256:d6089aa301166860bb4497f6697a270237fe37370859d24af18013cf90f3f98d", 38222336),
    ("sha256:645121c1975a375f54c50900277f6d4ec775d961b232c219b76c03649b9af1ec", 5120),
    ("sha256:849b248980a2a0b4789be0e0203f1cf473a3bebd9eb69e6a42049d513f5a0ecf", 1040384),
    ("sha256:4e0bbc179960f097aa6561a9ce06580859e7719446b253634552fd400f03868d", 13824),
    ("sha256:cd2132a47b8d04403eb90e480a5deb62115fafab1c926a585d03cc7494b6ce2b", 567255552),
    ("sha256:c99878f25eb192640cfd0121463945e1592f613ec3da104df5d29f1bc39cfc12", 620544),
    ("sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef", 1024),
)
H12_IMAGE_ID = "sha256:9d85f2c4c8056a63a1371bed201d3015ed6bb983a204a5ac3eb7243eb2296d7f"


def test_the_derivation_reproduces_the_image_id_docker_29_reported_for_the_h12_candidate() -> None:
    """The whole design rests on this: the derived value is what an engine assigns."""
    document = image.synthesized_manifest(
        (CONFIG_MEDIA_TYPE, *H12_CONFIG),
        [(LAYER_MEDIA_TYPE, digest, size) for digest, size in H12_LAYERS],
    )

    assert digest_of(document) == H12_IMAGE_ID
