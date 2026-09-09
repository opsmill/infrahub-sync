"""What the image build is allowed to take in, checked without a Docker daemon."""

from __future__ import annotations

import subprocess  # noqa: S404 -- fixed argv probe of this interpreter's import graph
import sys
from pathlib import Path

import pytest

from tasks import image
from tasks.release import release_identity
from tests.image.conftest import POSTGRES_IMAGE, external_image_references

# Every image this repository names outside the Dockerfile: the two scanners the
# supply-chain gate runs, the tool that copies a built platform out of the
# retained layout, and the database the API smoke starts beside the image. A tool
# or harness image that a re-pointed tag can change makes the gate itself
# unreproducible, so they are held to the same rule as the runtime base.
EXTERNAL_TOOL_IMAGES = (image.SYFT_IMAGE, image.GRYPE_IMAGE, image.SKOPEO_IMAGE, POSTGRES_IMAGE)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
COMMIT_TIME = "2026-09-04T10:15:00+02:00"
VERSION = "3.0.0a1"
IDENTITY = release_identity(version=VERSION, revision=COMMIT, created=COMMIT_TIME)

# Everything the image's five command forms import. The service modules resolve
# only on the Python range their extra supports, which is why this asks the
# running interpreter rather than importing them unconditionally.
RUNTIME_IMPORT_PROBE = """
import sys
import infrahub_sync
import infrahub_sync.cli
import infrahub_sync.utils
if sys.version_info >= (3, 11):
    import infrahub_sync.service.deploy
    import infrahub_sync.service.serve
    import infrahub_sync.service.worker
print(sorted(name for name in sys.modules if name.split(".")[0] == "pytest"))
"""


def test_every_external_base_is_pinned_by_digest() -> None:
    """A tag can be re-pointed; only a digest names one artifact for good."""
    references = external_image_references(DOCKERFILE.read_text(encoding="utf-8"))

    assert references, DOCKERFILE
    for reference in references:
        assert "@sha256:" in reference, reference


@pytest.mark.parametrize("reference", EXTERNAL_TOOL_IMAGES)
def test_every_external_tool_image_is_pinned_by_digest(reference: str) -> None:
    assert "@sha256:" in reference, reference


def test_the_install_is_frozen_against_the_committed_lock() -> None:
    """A resolve at build time would ship dependencies no lock ever recorded."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "uv sync --frozen" in dockerfile
    assert "--extra service" in dockerfile


def test_no_command_form_the_image_ships_imports_the_test_framework() -> None:
    """What makes the image's pytest exclusion safe rather than merely tidy.

    pytest is a runtime dependency of this project through `infrahub-sdk[all]`, so
    the image leaves it out at install time instead of dropping it from what the
    published package resolves. That is only valid while nothing the image runs
    reaches for it.
    """
    result = subprocess.run(  # noqa: S603 -- fixed argv on this interpreter's own path
        [sys.executable, "-c", RUNTIME_IMPORT_PROBE],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )

    assert result.stdout.strip().splitlines()[-1] == "[]"


def test_the_build_passes_exactly_the_three_declared_identity_arguments() -> None:
    """Any further build argument would reach image history and could carry a secret."""
    command = image.build_command(IDENTITY, platforms=("linux/amd64",), destination=Path("/tmp/layout"))  # noqa: S108

    passed = [command[index + 1] for index, word in enumerate(command) if word == "--build-arg"]
    assert sorted(passed) == [f"CREATED={COMMIT_TIME}", f"REVISION={COMMIT}", f"VERSION={VERSION}"]


def test_the_build_records_no_attestations_beside_the_image() -> None:
    """Attestation manifests would put build-host detail into the published index."""
    command = image.build_command(IDENTITY, platforms=image.PLATFORMS, destination=Path("/tmp/layout"))  # noqa: S108

    assert "--provenance=false" in command
    assert "--sbom=false" in command
