"""The release pin tasks keep the root Compose file's Sync image on the release version.

`release.update-docker-compose` rewrites the `${VERSION:-...}` default on every Sync
image line and nothing else; `release.validate-docker-compose` refuses a file whose
Sync image lines pin any other version. Each test runs against a copy of a small
fixture in `tmp_path`; the one exception only reads the repository's own
`docker-compose.yml`, to catch a pin that drifted from `pyproject.toml`.
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from invoke import Context
from invoke.exceptions import Exit

from tasks.release import DOCKER_COMPOSE_FILE, PACKAGED_COMPOSE_FILE, update_docker_compose, validate_docker_compose

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

THIRD_PARTY_IMAGE = (
    'image: "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"'
)
# The object store's pin, written as a folded scalar the way the root file writes it.
SILO_IMAGE = (
    "pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7"
)

FIXTURE = f"""---
# A comment that mentions ${{VERSION:-1.0.0}} must survive the rewrite.
services:
  postgres:
    {THIRD_PARTY_IMAGE}
  object-store:
    # A third-party digest pin the tasks leave alone.
    image: >-
      {SILO_IMAGE}
  prefect-server:
    image: "${{INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}}:${{VERSION:-3.0.0a5}}"
  sync-api:
    # Pinned by the release tasks.
    image: "${{INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}}:${{VERSION:-3.0.0a5}}"
  sync-worker:
    image: "${{INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}}:${{VERSION:-3.0.0a5}}"
  other:
    image: "example.invalid/other:${{VERSION:-0.1.0}}"
"""

NO_SYNC_IMAGE = f"""---
services:
  postgres:
    {THIRD_PARTY_IMAGE}
"""

MIXED_PINS = FIXTURE.replace("${VERSION:-3.0.0a5}", "${VERSION:-3.0.0a4}", 1)
PINNED_WORKER = 'image: "${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-3.0.0a5}"'
# Each replaces the last Sync image line (line 16) with a reference the tasks cannot pin.
MISFORMATTED_SYNC_IMAGES = {
    "hard-coded-tag": 'image: "registry.opsmill.io/opsmill/infrahub-sync:3.0.0a5"',
    "latest": "image: registry.opsmill.io/opsmill/infrahub-sync:latest",
    "unclosed-version-default": (
        'image: "${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-3.0.0a5"'
    ),
}


# The Sync image lines of the fixture: `prefect-server`, `sync-api` and `sync-worker`.
SYNC_LINE_NUMBERS = (11, 14, 16)
SYNC_LINE_COUNT = len(SYNC_LINE_NUMBERS)


def _sync_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-" in line]


@pytest.fixture(name="compose_file")
def _compose_file(tmp_path: Path) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(FIXTURE, encoding="utf-8")
    return path


@pytest.mark.parametrize("version", ["3.0.0", "3.0.0a6", "3.1.0rc1", "3.0.0.post1"])
def test_every_sync_image_line_is_rewritten(compose_file: Path, version: str) -> None:
    update_docker_compose(Context(), version=version, docker_file=str(compose_file))

    lines = _sync_lines(compose_file.read_text(encoding="utf-8"))
    assert len(lines) == SYNC_LINE_COUNT
    assert all(line.endswith(f':${{VERSION:-{version}}}"') for line in lines)


def test_only_sync_image_lines_change(compose_file: Path) -> None:
    update_docker_compose(Context(), version="3.0.0a6", docker_file=str(compose_file))

    before = FIXTURE.splitlines()
    after = compose_file.read_text(encoding="utf-8").splitlines()
    changed = [index for index, (old, new) in enumerate(zip(before, after, strict=True)) if old != new]
    assert [before[index] for index in changed] == _sync_lines(FIXTURE)


def test_a_second_run_produces_no_diff(compose_file: Path) -> None:
    update_docker_compose(Context(), version="3.0.0a6", docker_file=str(compose_file))
    once = compose_file.read_bytes()

    update_docker_compose(Context(), version="3.0.0a6", docker_file=str(compose_file))

    assert compose_file.read_bytes() == once


def test_a_third_party_image_line_is_untouched(compose_file: Path) -> None:
    update_docker_compose(Context(), version="3.0.0a6", docker_file=str(compose_file))

    text = compose_file.read_text(encoding="utf-8")
    assert f"    {THIRD_PARTY_IMAGE}\n" in text
    assert f"    image: >-\n      {SILO_IMAGE}\n" in text
    assert 'image: "example.invalid/other:${VERSION:-0.1.0}"' in text
    assert "# A comment that mentions ${VERSION:-1.0.0} must survive the rewrite." in text


@pytest.mark.parametrize(
    "version", ["", "not-a-version", "v3.0.0", "3.0.0-alpha6", "3.0.0A6", "1!3.0.0", "3.0.0+local"]
)
def test_an_invalid_or_non_canonical_version_fails(compose_file: Path, version: str) -> None:
    """`v3.0.0` and `3.0.0-alpha6` parse under PEP 440, but no image is tagged with either spelling.

    `1!3.0.0` and `3.0.0+local` are canonical, but `!` and `+` are outside the Docker tag grammar.
    """
    with pytest.raises(Exit) as raised:
        update_docker_compose(Context(), version=version, docker_file=str(compose_file))

    assert raised.value.code != 0
    assert compose_file.read_text(encoding="utf-8") == FIXTURE


@pytest.mark.parametrize("task", [update_docker_compose, validate_docker_compose], ids=["update", "validate"])
def test_a_file_with_no_sync_image_line_fails(tmp_path: Path, task: Callable[..., None]) -> None:
    path = tmp_path / "docker-compose.yml"
    path.write_text(NO_SYNC_IMAGE, encoding="utf-8")

    with pytest.raises(Exit) as raised:
        task(Context(), version="3.0.0a6", docker_file=str(path))

    assert raised.value.code != 0
    assert "no Sync image line" in str(raised.value.message)
    assert path.read_text(encoding="utf-8") == NO_SYNC_IMAGE


@pytest.mark.parametrize("version", ["3.0.0a5", "3.0.0a6"])
def test_validate_passes_when_every_sync_line_matches(compose_file: Path, version: str) -> None:
    update_docker_compose(Context(), version=version, docker_file=str(compose_file))

    validate_docker_compose(Context(), version=version, docker_file=str(compose_file))


@pytest.mark.parametrize(
    ("content", "offending"),
    [(FIXTURE, ["3.0.0a5", "3.0.0a5", "3.0.0a5"]), (MIXED_PINS, ["3.0.0a4", "3.0.0a5", "3.0.0a5"])],
    ids=["all-other", "one-other"],
)
def test_validate_fails_on_a_mismatch_and_lists_the_offending_lines(
    tmp_path: Path, content: str, offending: list[str]
) -> None:
    path = tmp_path / "docker-compose.yml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(Exit) as raised:
        validate_docker_compose(Context(), version="3.0.0a6", docker_file=str(path))

    message = str(raised.value.message)
    assert raised.value.code != 0
    assert "3.0.0a6" in message
    for line_number, found in zip(SYNC_LINE_NUMBERS, offending, strict=True):
        assert f"line {line_number}: pins {found}" in message


def test_validate_fails_on_a_single_mismatched_line_among_matches(tmp_path: Path) -> None:
    path = tmp_path / "docker-compose.yml"
    path.write_text(MIXED_PINS, encoding="utf-8")

    with pytest.raises(Exit) as raised:
        validate_docker_compose(Context(), version="3.0.0a5", docker_file=str(path))

    message = str(raised.value.message)
    assert "line 11: pins 3.0.0a4" in message
    assert "line 14" not in message
    assert "line 16" not in message


@pytest.mark.parametrize("task", [update_docker_compose, validate_docker_compose], ids=["update", "validate"])
@pytest.mark.parametrize("line", list(MISFORMATTED_SYNC_IMAGES.values()), ids=list(MISFORMATTED_SYNC_IMAGES))
def test_a_sync_image_line_outside_the_pinned_form_fails(tmp_path: Path, task: Callable[..., None], line: str) -> None:
    """Skipping such a line would leave a Sync service on an image the release does not pin."""
    content = line.join(FIXTURE.rsplit(PINNED_WORKER, 1))
    assert content != FIXTURE
    path = tmp_path / "docker-compose.yml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(Exit) as raised:
        task(Context(), version="3.0.0a5", docker_file=str(path))

    message = str(raised.value.message)
    assert raised.value.code != 0
    assert f"line 16: {line}" in message
    assert "line 11" not in message
    assert "line 14" not in message
    assert path.read_text(encoding="utf-8") == content


def test_the_repository_compose_file_pins_the_project_version() -> None:
    """A release bump that forgets the Compose file is caught before the tag is."""
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+; the other interpreters still cover it
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]

    validate_docker_compose(Context(), version=version, docker_file=str(DOCKER_COMPOSE_FILE))


def test_the_repository_prefect_server_is_pinned_with_the_sync_services(tmp_path: Path) -> None:
    """`prefect-server` runs the Sync image, so a release moves it with the other Sync services."""
    path = tmp_path / "docker-compose.yml"
    path.write_text(DOCKER_COMPOSE_FILE.read_text(encoding="utf-8"), encoding="utf-8")

    update_docker_compose(Context(), version="9.9.9", docker_file=str(path))

    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index("  prefect-server:")
    end = next(index for index in range(start + 1, len(lines)) if re.fullmatch(r"  [a-z][a-z0-9-]*:", lines[index]))
    pinned = [line for line in lines[start:end] if line.endswith(':${VERSION:-9.9.9}"')]
    assert len(pinned) == 1, f"prefect-server image lines after the release: {lines[start:end]}"


def test_the_update_writes_the_pinned_file_over_the_packaged_copy(compose_file: Path, tmp_path: Path) -> None:
    copy = tmp_path / "package" / "docker-compose.yml"

    update_docker_compose(Context(), version="3.0.0a6", docker_file=str(compose_file), packaged_file=str(copy))

    assert copy.read_bytes() == compose_file.read_bytes()
    assert all(line.endswith(':${VERSION:-3.0.0a6}"') for line in _sync_lines(copy.read_text(encoding="utf-8")))


def test_an_update_of_a_named_file_leaves_the_packaged_copy_alone(compose_file: Path) -> None:
    before = PACKAGED_COMPOSE_FILE.read_bytes()

    update_docker_compose(Context(), version="3.0.0a6", docker_file=str(compose_file))

    assert PACKAGED_COMPOSE_FILE.read_bytes() == before


@pytest.mark.parametrize("copy_state", ["differs", "missing"])
def test_validate_fails_when_the_packaged_copy_is_not_byte_equal(
    compose_file: Path, tmp_path: Path, copy_state: str
) -> None:
    copy = tmp_path / "copy.yml"
    if copy_state == "differs":
        copy.write_text(FIXTURE.replace("3.0.0a5", "3.0.0a4"), encoding="utf-8")

    with pytest.raises(Exit) as raised:
        validate_docker_compose(Context(), version="3.0.0a5", docker_file=str(compose_file), packaged_file=str(copy))

    assert raised.value.code != 0
    assert "not byte-equal" in str(raised.value.message)


def test_validate_passes_when_the_packaged_copy_is_byte_equal(compose_file: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy.yml"
    copy.write_bytes(compose_file.read_bytes())

    validate_docker_compose(Context(), version="3.0.0a5", docker_file=str(compose_file), packaged_file=str(copy))


def test_the_repository_files_validate_together_at_the_project_version() -> None:
    """Run without `--docker-file`, as release-publish.yml runs it: the root file and the packaged copy."""
    tomllib = pytest.importorskip("tomllib")
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]

    validate_docker_compose(Context(), version=version)


# `infrahub-testcontainers` pins `prefect-client` exactly. The Prefect server and worker run
# Sync's `prefect`, and the test class talks to them through that `prefect-client`, so a lock
# where the two differ would test Sync against a Prefect it does not ship.
UV_LOCK = PYPROJECT.parent / "uv.lock"


def _locked_versions(name: str) -> list[str]:
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+; the other interpreters still cover it
    packages = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))["package"]
    return [package["version"] for package in packages if package["name"] == name]


def _locked_package(name: str) -> dict:
    tomllib = pytest.importorskip("tomllib")
    packages = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))["package"]
    (package,) = [package for package in packages if package["name"] == name]
    return package


def test_the_locked_prefect_and_prefect_client_versions_are_equal() -> None:
    prefect = _locked_versions("prefect")
    prefect_client = _locked_versions("prefect-client")

    assert len(prefect) == 1, f"uv.lock resolves several prefect versions: {prefect}"
    assert prefect_client == prefect


def test_the_locked_infrahub_testcontainers_pins_prefect_client() -> None:
    package = _locked_package("infrahub-testcontainers")

    assert "prefect-client" in {dependency["name"] for dependency in package["dependencies"]}


def test_the_installed_infrahub_testcontainers_pins_the_locked_prefect_client() -> None:
    """uv.lock records which packages a dependency needs, not its specifier; the metadata holds the pin."""
    try:
        installed = importlib.metadata.version("infrahub-testcontainers")
        requirements = importlib.metadata.requires("infrahub-testcontainers") or []
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("infrahub-testcontainers is not installed (Python 3.10 or the base install)")
    assert installed == _locked_package("infrahub-testcontainers")["version"]

    (locked_client,) = _locked_versions("prefect-client")
    pins = [
        requirement.split(";")[0].replace(" ", "")
        for requirement in requirements
        if requirement.startswith("prefect-client")
    ]
    assert pins == [f"prefect-client=={locked_client}"]
