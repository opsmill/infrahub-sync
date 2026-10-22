"""Image pins every Compose file of the repository must keep.

Every stack runs the bundled object store from `pgsty/silo`, pinned by a dated
release tag and its digest, and runs the Prefect server from the Sync image, so
no file names a Prefect image or a Prefect version: the `prefect` pin in
`pyproject.toml` and `uv.lock` is the only place that version is written.

The stack checks read the model `docker compose config` resolves each file to,
with every profile named. The file checks read the text of every Compose file
and of the `Dockerfile`, because a reference in a comment or in a profile no
check resolves is still a reference to remove.

The files are discovered rather than listed, so a Compose file added later is
covered by having been added. The development stack and the packaged copy of the
root file are found under either name they have while the stack is reorganized.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.compose.conftest import COMPOSE_FILE, CONTRACT_ENVIRONMENT, REPO_ROOT
from tests.compose.stacks import resolve_stack

if TYPE_CHECKING:
    from collections.abc import Mapping

SILO_REFERENCE = re.compile(r"^pgsty/silo:RELEASE\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z@sha256:[0-9a-f]{64}$")
SILO_HEALTH_CHECK = ["CMD", "silo", "healthcheck", "ready"]
SILO_USER = "65532:65532"
OBJECT_STORE_SERVICES = ("object-store", "object-store-init")
FORBIDDEN_OBJECT_STORE_IMAGES = ("cgr.dev/chainguard/minio", "minio/minio")
PREFECT_IMAGE = "prefecthq/prefect"
# A Prefect image, or `prefect` followed by a version: `prefect==3.8.6`,
# `prefect:3.8`, `prefect 3.8.6`. `opsmill_prefect_extras` and `PREFECT_HOME`
# name no version.
PREFECT_VERSION_OR_IMAGE = re.compile(r"prefecthq/|\bprefect(?:==|>=|~=|<=|[:@\s])\s*v?\d+\.\d+", re.IGNORECASE)
DOCKERFILE = REPO_ROOT / "Dockerfile"

DEVELOPMENT = REPO_ROOT / "development"
DEVELOPMENT_STACK = DEVELOPMENT / "docker-compose.yml"
PACKAGED_COPY = REPO_ROOT / "infrahub_sync" / "testcontainers" / "docker-compose.yml"
# The preview stack is deleted with the task renames that follow this change;
# until then it still runs MinIO and `prefecthq/prefect`.
PREVIEW_STACK = DEVELOPMENT / "docker-compose.preview.yml"

UNTIL_THE_PREVIEW_STACK_IS_DELETED = pytest.mark.xfail(
    strict=True, reason="T039 deletes the preview stack, which still runs MinIO and prefecthq/prefect"
)


def development_stack() -> Path | None:
    """The development stack's Compose file, when the checkout has it."""
    return DEVELOPMENT_STACK if DEVELOPMENT_STACK.exists() else None


def stacks() -> list[Any]:
    """Every stack that runs the bundled object store and a Prefect server, as test parameters."""
    found = [pytest.param(COMPOSE_FILE, CONTRACT_ENVIRONMENT, id="root")]
    if (development := development_stack()) is not None:
        found.append(pytest.param(development, None, id="development"))
    if PACKAGED_COPY.exists():
        found.append(pytest.param(PACKAGED_COPY, CONTRACT_ENVIRONMENT, id="packaged"))
    return found


def compose_files() -> list[Any]:
    """Every Compose file of the repository, as test parameters."""
    paths = {COMPOSE_FILE, *DEVELOPMENT.rglob("docker-compose*.yml"), *DEVELOPMENT.rglob("compose*.y*ml")}
    if PACKAGED_COPY.exists():
        paths.add(PACKAGED_COPY)
    return [
        pytest.param(
            path,
            id=str(path.relative_to(REPO_ROOT)),
            marks=UNTIL_THE_PREVIEW_STACK_IS_DELETED if path == PREVIEW_STACK else (),
        )
        for path in sorted(paths)
    ]


def resolved_services(path: Path, environment: Mapping[str, str] | None) -> dict[str, dict[str, Any]]:
    return dict(resolve_stack([path], profiles=("*",), environment=environment).model["services"])


def test_the_stacks_include_the_root_file_and_the_development_stack() -> None:
    """Discovery that finds neither would leave every stack check below without a case."""
    assert COMPOSE_FILE.exists(), f"no root file at {COMPOSE_FILE}"
    assert development_stack() is not None, f"no development stack under {DEVELOPMENT}"


@pytest.mark.parametrize(("path", "environment"), stacks())
def test_the_object_store_runs_the_pinned_silo_release(path: Path, environment: Mapping[str, str] | None) -> None:
    """A dated Silo release and its digest, for the server and the one-shot owner repair alike."""
    resolved = resolved_services(path, environment)
    images = {name: resolved[name].get("image") for name in OBJECT_STORE_SERVICES if name in resolved}

    assert set(images) == set(OBJECT_STORE_SERVICES), f"{path.name} runs only {sorted(images)}"
    off_pin = {name: image for name, image in images.items() if not SILO_REFERENCE.fullmatch(str(image))}
    assert off_pin == {}, f"{path.name} runs the object store off the Silo pin: {off_pin}"


@pytest.mark.parametrize(("path", "environment"), stacks())
def test_the_object_store_checks_its_health_with_silo_and_runs_unprivileged(
    path: Path, environment: Mapping[str, str] | None
) -> None:
    """Silo has no `wget` or `mc`, so the check is its own command, run as the non-root owner."""
    object_store = resolved_services(path, environment)["object-store"]

    assert object_store["healthcheck"]["test"] == SILO_HEALTH_CHECK
    assert object_store.get("user") == SILO_USER


@pytest.mark.parametrize(("path", "environment"), stacks())
def test_no_stack_runs_a_prefect_image(path: Path, environment: Mapping[str, str] | None) -> None:
    """The Prefect server runs from the Sync image in every stack, with every profile named."""
    resolved = resolved_services(path, environment)
    prefect = sorted(
        f"{name}: {definition['image']}"
        for name, definition in resolved.items()
        if PREFECT_IMAGE in str(definition.get("image") or "")
    )

    assert prefect == [], f"{path.name} runs {prefect}"


@pytest.mark.parametrize("path", compose_files())
def test_no_compose_file_names_minio_a_prefect_image_or_a_prefect_version(path: Path) -> None:
    """Read from the text, so a comment or an unresolved profile cannot keep an old pin alive."""
    text = path.read_text(encoding="utf-8")
    minio = [image for image in FORBIDDEN_OBJECT_STORE_IMAGES if image in text]
    prefect = [
        f"line {number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        if PREFECT_VERSION_OR_IMAGE.search(line)
    ]

    assert minio == [], f"{path.name} references {minio}"
    assert prefect == [], f"{path.name} names a Prefect image or version: {prefect}"


def test_the_dockerfile_names_no_prefect_version_or_image() -> None:
    """The image takes Prefect from the locked `service` extra and from nowhere else."""
    named = [
        f"line {number}: {line.strip()}"
        for number, line in enumerate(DOCKERFILE.read_text(encoding="utf-8").splitlines(), start=1)
        if PREFECT_VERSION_OR_IMAGE.search(line)
    ]

    assert named == [], f"the Dockerfile names a Prefect image or version: {named}"


@pytest.mark.parametrize(
    ("line", "names_prefect"),
    [
        pytest.param('image: "prefecthq/prefect:3.8.6-python3.13"', True, id="image"),
        pytest.param("# the same version as the extra (prefect==3.8.6).", True, id="requirement"),
        pytest.param("RUN pip install prefect 3.8.6", True, id="spaced"),
        pytest.param("COPY opsmill_prefect_extras ./opsmill_prefect_extras", False, id="vendored-package"),
        pytest.param("PREFECT_HOME=/var/lib/infrahub-sync/prefect", False, id="home"),
        pytest.param("command: uvicorn --factory infrahub_sync.service.prefect_server:create_app", False, id="module"),
    ],
)
def test_the_prefect_pattern_names_versions_and_images_only(line: str, *, names_prefect: bool) -> None:
    """The text check must catch a pin without catching the Sync package's own Prefect modules."""
    assert bool(PREFECT_VERSION_OR_IMAGE.search(line)) is names_prefect
