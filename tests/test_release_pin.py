"""The release pin tasks keep the root Compose file's Sync image on the release version.

`release.update-docker-compose` rewrites the `${VERSION:-...}` default on every Sync
image line and nothing else; `release.validate-docker-compose` refuses a file whose
Sync image lines pin any other version. Each test runs against a copy of a small
fixture in `tmp_path`; the one exception only reads the repository's own
`docker-compose.yml`, to catch a pin that drifted from `pyproject.toml`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from invoke import Context
from invoke.exceptions import Exit

from tasks.release import DOCKER_COMPOSE_FILE, update_docker_compose, validate_docker_compose

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

THIRD_PARTY_IMAGE = (
    'image: "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"'
)

FIXTURE = f"""---
# A comment that mentions ${{VERSION:-1.0.0}} must survive the rewrite.
services:
  postgres:
    {THIRD_PARTY_IMAGE}
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
# Each replaces the second Sync image line (line 10) with a reference the tasks cannot pin.
MISFORMATTED_SYNC_IMAGES = {
    "hard-coded-tag": 'image: "registry.opsmill.io/opsmill/infrahub-sync:3.0.0a5"',
    "latest": "image: registry.opsmill.io/opsmill/infrahub-sync:latest",
    "unclosed-version-default": (
        'image: "${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-3.0.0a5"'
    ),
}


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
    assert len(lines) == 2
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
    assert 'image: "example.invalid/other:${VERSION:-0.1.0}"' in text
    assert "# A comment that mentions ${VERSION:-1.0.0} must survive the rewrite." in text


@pytest.mark.parametrize("version", ["", "not-a-version", "v3.0.0", "3.0.0-alpha6", "3.0.0A6"])
def test_an_invalid_or_non_canonical_version_fails(compose_file: Path, version: str) -> None:
    """`v3.0.0` and `3.0.0-alpha6` parse under PEP 440, but no image is tagged with either spelling."""
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
    [(FIXTURE, ["3.0.0a5", "3.0.0a5"]), (MIXED_PINS, ["3.0.0a4", "3.0.0a5"])],
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
    for line_number, found in zip((8, 10), offending, strict=True):
        assert f"line {line_number}: pins {found}" in message


def test_validate_fails_on_a_single_mismatched_line_among_matches(tmp_path: Path) -> None:
    path = tmp_path / "docker-compose.yml"
    path.write_text(MIXED_PINS, encoding="utf-8")

    with pytest.raises(Exit) as raised:
        validate_docker_compose(Context(), version="3.0.0a5", docker_file=str(path))

    message = str(raised.value.message)
    assert "line 8: pins 3.0.0a4" in message
    assert "line 10" not in message


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
    assert f"line 10: {line}" in message
    assert "line 8" not in message
    assert path.read_text(encoding="utf-8") == content


def test_the_repository_compose_file_pins_the_project_version() -> None:
    """A release bump that forgets the Compose file is caught before the tag is."""
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+; the other interpreters still cover it
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]

    validate_docker_compose(Context(), version=version, docker_file=str(DOCKER_COMPOSE_FILE))
