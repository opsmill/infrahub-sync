"""Version floors the image and its lock hold so the scan policy stays satisfiable.

The vulnerability policy fails a build on a high or critical finding that has a
fix available, and there is no waiver file to fall back on. What keeps that gate
passable is the floors below: each one is the first release carrying the fix for
a finding the scan reported against this image. A lock refresh that drops under
one of them puts the image back under the policy, so it fails here first, where
the reason is legible, rather than in a scanner report.
"""

from __future__ import annotations

import re

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from tests.image.conftest import REPO_ROOT, external_image_references

DOCKERFILE = REPO_ROOT / "Dockerfile"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "uv.lock"

# The readable tag and the interpreter series the runtime is built on. The digest
# beside the tag is what the build actually resolves; the tag is checked because a
# digest alone tells a reader nothing about which Python they are running.
RUNTIME_BASE_TAG = "python:3.13-slim-bookworm"
RUNTIME_PYTHON_FLOOR = Version("3.13.14")

PYARROW_FIX = Version("23.0.1")

# Where each package has to land. The floor is the first release carrying the fix.
# A cap appears only where clearing the finding does not require crossing a major,
# because the correction is meant to be the smallest dependency change that works.
FIX_RANGES = {
    "cryptography": SpecifierSet(">=50.0.0"),
    "dulwich": SpecifierSet(">=1.2.5"),
    "pyarrow": SpecifierSet(">=23.0.1,<24"),
    "ujson": SpecifierSet(">=5.12.1,<6"),
    "urllib3": SpecifierSet(">=2.7.0"),
}

# Releases the declared PyArrow constraint has to refuse: the three that predate
# the fix, and the next major. 23.0.0 is the one worth naming — it is inside the
# series that carries the fix but before the patch that does.
PYARROW_EXCLUDED = (Version("21.0.0"), Version("22.0.0"), Version("23.0.0"), Version("24.0.0"))


def locked_version(package: str) -> Version:
    """Return the version `uv.lock` resolves for one package."""
    found = re.search(
        rf'^name = "{re.escape(package)}"\nversion = "([^"]+)"', LOCK.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert found is not None, f"{package} is absent from {LOCK}"
    return Version(found.group(1))


def declared_requirement(package: str) -> Requirement:
    """Return the project's own declared constraint for one dependency."""
    text = PYPROJECT.read_text(encoding="utf-8")
    found = re.search(rf'"\s*({re.escape(package)}[^"]*)"', text)
    assert found is not None, f"{package} is not declared in {PYPROJECT}"
    return Requirement(found.group(1))


def test_the_runtime_base_names_the_supported_python_series() -> None:
    """A digest alone hides which interpreter ships; the tag beside it is the label."""
    base = external_image_references(DOCKERFILE.read_text(encoding="utf-8"))[0]

    assert base.startswith(f"{RUNTIME_BASE_TAG}@sha256:"), base


@pytest.mark.parametrize(("package", "accepted"), sorted(FIX_RANGES.items()))
def test_every_package_carrying_a_known_fix_is_locked_in_its_accepted_range(
    package: str, accepted: SpecifierSet
) -> None:
    locked = locked_version(package)

    assert locked in accepted, f"{package} {locked} is outside the accepted range {accepted}"


def test_the_project_accepts_the_pyarrow_release_that_carries_its_fix() -> None:
    """The lock records one resolution; the declared constraint governs every other."""
    assert PYARROW_FIX in declared_requirement("pyarrow").specifier


@pytest.mark.parametrize("excluded", PYARROW_EXCLUDED, ids=str)
def test_the_project_cannot_resolve_a_pyarrow_release_without_its_fix(excluded: Version) -> None:
    assert excluded not in declared_requirement("pyarrow").specifier, f"pyarrow {excluded} still resolves"


@pytest.mark.parametrize("series", ["3.10", "3.11", "3.12", "3.13"])
def test_the_project_still_supports_every_python_it_claims(series: str) -> None:
    """Raising a dependency floor must not quietly narrow the interpreters supported."""
    text = PYPROJECT.read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10,<3.14"' in text
    assert f'"Programming Language :: Python :: {series}"' in text
