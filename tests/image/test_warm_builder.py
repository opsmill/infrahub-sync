"""What a second build on an already-warm builder is allowed to install.

uv caches the wheel it builds for a local distribution. A cache hit after a
Python-only edit installs the earlier source into the newer image while the
recorded revision advances, and every later gate — smoke, SBOM, scan, the
lifecycle matrix — then makes a true statement about the wrong code.

The builder is named through the environment rather than assumed, because the
property only exists on a builder that already holds a previous build's cache.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

import pytest

from tests.image.conftest import REPO_ROOT, docker

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.builder

BUILDER_ENV = "INFRAHUB_SYNC_BUILDER"

DOCKERFILE = REPO_ROOT / "Dockerfile"
# Two full image builds, the first of which may resolve the whole dependency tree.
BUILD_TIMEOUT_SECONDS = 2700

# What the Dockerfile's build stage copies. The test builds from its own copy of
# these so it can edit the source between the two builds without writing to the
# repository it is running in.
CONTEXT_ENTRIES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE.txt", "infrahub_sync", "opsmill_prefect_extras")

# A module inside the one local distribution's own import tree, holding nothing
# but a string. Editing it changes no dependency, so a builder that reinstalls
# only when the lock moves will not notice it.
SENTINEL_MODULE = "infrahub_sync/_warm_builder_sentinel.py"
SENTINEL_IMPORT = "import infrahub_sync._warm_builder_sentinel as sentinel; print(sentinel.VALUE)"

REVISION_LABEL = "org.opencontainers.image.revision"
FIRST_REVISION = "1" * 40
SECOND_REVISION = "2" * 40
FIRST_SOURCE = "first-source"
SECOND_SOURCE = "second-source"


@pytest.fixture(scope="session")
def builder() -> str:
    """Return the buildx builder whose cache the two builds share."""
    name = os.environ.get(BUILDER_ENV)
    if not name:
        pytest.skip(f"{BUILDER_ENV} is unset; run this suite through `uv run invoke image.freshness`")
    return name


@pytest.fixture
def build_context(tmp_path: Path) -> Path:
    """Return a copy of the build context this test may edit between builds."""
    root = tmp_path / "context"
    root.mkdir()
    for entry in CONTEXT_ENTRIES:
        source = REPO_ROOT / entry
        target = root / entry
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target)
    return root


@pytest.fixture
def tags() -> Iterator[tuple[str, str]]:
    """Name the two loaded images and remove them however the test ends."""
    names = ("infrahub-sync-warm-builder:first", "infrahub-sync-warm-builder:second")
    try:
        yield names
    finally:
        for name in names:
            docker(["image", "rm", "--force", name])


def write_sentinel(context: Path, value: str) -> None:
    """Put one Python-only edit into the build context, and nothing else."""
    (context / SENTINEL_MODULE).write_text(f'VALUE = "{value}"\n', encoding="utf-8")


def build(context: Path, builder: str, *, revision: str, tag: str) -> None:
    """Build the image from that context on the shared builder, pinning everything but the source.

    Both builds declare the same version and creation time so the revision is the
    only label that moves, and provenance and SBOM attestations are off because a
    second build of one source would otherwise differ by attestation alone.
    """
    result = docker(
        [
            "buildx",
            "build",
            "--builder",
            builder,
            "--file",
            str(DOCKERFILE),
            "--provenance=false",
            "--sbom=false",
            "--build-arg",
            "VERSION=0.0.0",
            "--build-arg",
            f"REVISION={revision}",
            "--build-arg",
            "CREATED=2026-09-06T00:00:00+00:00",
            "--tag",
            tag,
            "--load",
            str(context),
        ],
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr


def installed_sentinel(tag: str) -> str:
    """Return the sentinel the image's *installed* distribution exposes."""
    probe = docker(["run", "--rm", "--network=none", tag, "python", "-c", SENTINEL_IMPORT])
    assert probe.returncode == 0, probe.stderr
    return probe.stdout.strip()


def inspected(tag: str, template: str) -> str:
    """Return what the daemon reports about a loaded image, rendered by one Go template."""
    result = docker(["image", "inspect", "--format", template, tag])
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_python_only_edit_reaches_the_second_image_on_a_warm_builder(
    build_context: Path, builder: str, tags: tuple[str, str]
) -> None:
    """The second image must serve the second source, not a cached wheel of the first."""
    first, second = tags

    write_sentinel(build_context, FIRST_SOURCE)
    build(build_context, builder, revision=FIRST_REVISION, tag=first)
    write_sentinel(build_context, SECOND_SOURCE)
    build(build_context, builder, revision=SECOND_REVISION, tag=second)

    assert installed_sentinel(second) == SECOND_SOURCE
    assert inspected(second, f'{{{{index .Config.Labels "{REVISION_LABEL}"}}}}') == SECOND_REVISION
    assert inspected(second, "{{.Id}}") != inspected(first, "{{.Id}}")
