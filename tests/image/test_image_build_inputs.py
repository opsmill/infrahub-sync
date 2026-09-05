"""What the image build is allowed to take in, checked without a Docker daemon."""

from __future__ import annotations

import subprocess  # noqa: S404 -- fixed argv probe of this interpreter's import graph
import sys
from pathlib import Path

import pytest

from tasks import image
from tests.image.conftest import POSTGRES_IMAGE, external_image_references

# Every image this repository names outside the Dockerfile: the two scanners the
# supply-chain gate runs, and the database the API smoke starts beside the image.
# A tool or harness image that a re-pointed tag can change makes the gate itself
# unreproducible, so they are held to the same rule as the runtime base.
EXTERNAL_TOOL_IMAGES = (image.SYFT_IMAGE, image.GRYPE_IMAGE, POSTGRES_IMAGE)

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

COMMIT = "708a8fca4b3fe300ae33242ddcd791a181926eeb"
# The two forms Git writes for a commit's own timestamp: a numeric offset, and
# the terminal `Z` it uses when the commit was made at UTC.
COMMIT_TIME = "2026-09-04T10:15:00+02:00"
COMMIT_TIME_UTC = "2026-09-05T01:29:37Z"

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


@pytest.mark.parametrize(
    "revision",
    ["", "708a8fc", "708a8fca4b3fe300ae33242ddcd791a181926eez", COMMIT + "0", "HEAD"],
)
def test_provenance_refuses_a_revision_that_is_not_a_commit_identifier(revision: str) -> None:
    with pytest.raises(image.ImageTaskError):
        image.source_provenance(version="2.0.1", revision=revision, created=COMMIT_TIME)


@pytest.mark.parametrize(
    "created",
    ["", "2026-09-04", "2026-09-04T10:15:00", "yesterday", "2026-13-04T10:15:00+02:00"],
)
def test_provenance_refuses_a_creation_time_that_is_not_an_absolute_instant(created: str) -> None:
    """`created` has to be a timestamp a reader can resolve, not a local wall clock."""
    with pytest.raises(image.ImageTaskError):
        image.source_provenance(version="2.0.1", revision=COMMIT, created=created)


@pytest.mark.parametrize("version", ["", "  ", "2.0.1 dirty", "v2.0.1\n"])
def test_provenance_refuses_a_version_that_is_not_a_release_identifier(version: str) -> None:
    with pytest.raises(image.ImageTaskError):
        image.source_provenance(version=version, revision=COMMIT, created=COMMIT_TIME)


@pytest.mark.parametrize("created", [COMMIT_TIME, COMMIT_TIME_UTC])
def test_provenance_accepts_the_recorded_release_identity(created: str) -> None:
    """Both offset forms are accepted, and the label keeps the commit's own text.

    Rewriting `Z` into the recorded value would put a timestamp in image metadata
    that the commit it names never carried.
    """
    provenance = image.source_provenance(version="2.0.1", revision=COMMIT, created=created)

    assert (provenance.version, provenance.revision, provenance.created) == ("2.0.1", COMMIT, created)


def test_the_build_passes_exactly_the_three_declared_provenance_arguments() -> None:
    """Any further build argument would reach image history and could carry a secret."""
    provenance = image.source_provenance(version="2.0.1", revision=COMMIT, created=COMMIT_TIME)

    command = image.build_command(provenance, platforms=("linux/amd64",), destination=Path("/tmp/layout"))  # noqa: S108

    passed = [command[index + 1] for index, word in enumerate(command) if word == "--build-arg"]
    assert sorted(passed) == [f"CREATED={COMMIT_TIME}", f"REVISION={COMMIT}", "VERSION=2.0.1"]


def test_the_build_records_no_attestations_beside_the_image() -> None:
    """Attestation manifests would put build-host detail into the published index."""
    provenance = image.source_provenance(version="2.0.1", revision=COMMIT, created=COMMIT_TIME)

    command = image.build_command(provenance, platforms=image.PLATFORMS, destination=Path("/tmp/layout"))  # noqa: S108

    assert "--provenance=false" in command
    assert "--sbom=false" in command
