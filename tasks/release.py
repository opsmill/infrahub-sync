"""Release tasks: keep the root `docker-compose.yml` pinned to the release version.

Every Sync service in the root Compose file names its image as
`${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-X}`.
`update-docker-compose` rewrites `X` on those lines, and `validate-docker-compose`
refuses a file where any of them names another version. Both edit or read the file
as text, line by line, so comments, ordering and third-party pins stay byte-for-byte.
Both also refuse an `image:` line that references the Sync image in any other form,
such as a hard-coded tag or `:latest`, rather than skip it.

Unlike infrahub, which bumps only for stable releases, this runs for pre-releases
too: every tag in this repository publishes an image of the same version.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog
from invoke import Context, task
from invoke.exceptions import Exit
from packaging.version import InvalidVersion, Version

from .utils import REPO_BASE

log = structlog.get_logger(__name__)

DOCKER_COMPOSE_FILE = REPO_BASE / "docker-compose.yml"
# Only a line carrying this is a Sync image line; any other `${VERSION:-...}` is left alone.
SYNC_IMAGE_MARKER = "registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-"
VERSION_DEFAULT = re.compile(r"\$\{VERSION:-(?P<version>[^}]*)\}")
# Any `image:` value that names the Sync image, in whatever form.
SYNC_IMAGE_REFERENCE = re.compile(r"^\s*(?:-\s*)?image:.*opsmill/infrahub-sync")
# The one form the tasks pin, optionally quoted and followed by a comment.
PINNED_SYNC_IMAGE = re.compile(
    r"^\s*(?:-\s*)?image:\s*(?P<quote>[\"']?)"
    r"\$\{INFRAHUB_SYNC_DOCKER_IMAGE:-registry\.opsmill\.io/opsmill/infrahub-sync\}"
    r":\$\{VERSION:-(?P<version>[^}\s\"']+)\}(?P=quote)\s*(?:#.*)?$"
)


def _canonical(version: str) -> str:
    """Return `version` if it is a canonical PEP 440 version, or stop the task.

    `v3.0.0` and `3.0.0-alpha6` parse, but they are not the string the image is tagged with,
    so only the normalized spelling is accepted rather than silently rewritten.
    """
    try:
        parsed = Version(version)
    except InvalidVersion:
        msg = f"'{version}' is not a valid PEP 440 version."
        raise Exit(msg, code=1) from None
    if str(parsed) != version:
        msg = f"'{version}' is not in canonical form; the release tag would be '{parsed}'."
        raise Exit(msg, code=1)
    return version


def _compose_path(docker_file: str | None) -> Path:
    path = Path(docker_file) if docker_file else DOCKER_COMPOSE_FILE
    if not path.is_file():
        msg = f"{path} does not exist."
        raise Exit(msg, code=1)
    return path


def _sync_image_pins(path: Path, lines: list[str]) -> list[tuple[int, str]]:
    """Return `(line index, pinned version)` for every Sync image line.

    Stops the task when a line references the Sync image as its `image:` but not in
    pinned form, listing every such line: skipping it would leave it unpinned.
    """
    pins = []
    malformed = []
    for index, line in enumerate(lines):
        if not SYNC_IMAGE_REFERENCE.match(line):
            continue
        match = PINNED_SYNC_IMAGE.match(line.rstrip("\r\n"))
        if match:
            pins.append((index, match["version"]))
        else:
            malformed.append(f"line {index + 1}: {line.strip()}")
    if malformed:
        log.error("compose_image_line_not_pinned", file=str(path), offending=malformed)
        msg = (
            f"{path} references the Sync image outside the pinned form "
            f"'${{INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}}:${{VERSION:-X}}':\n  "
            + "\n  ".join(malformed)
        )
        raise Exit(msg, code=1)
    return pins


def _require_pins(path: Path, lines: list[str]) -> list[tuple[int, str]]:
    pins = _sync_image_pins(path, lines)
    if not pins:
        msg = f"{path} has no Sync image line containing '{SYNC_IMAGE_MARKER}'."
        raise Exit(msg, code=1)
    return pins


@task(
    help={
        "version": "Release version to pin, in canonical PEP 440 form (for example 3.0.0a6).",
        "docker_file": "Compose file to edit (default: the repository's root docker-compose.yml).",
    }
)
def update_docker_compose(context: Context, version: str, docker_file: str | None = None) -> None:  # noqa: ARG001  # pylint: disable=unused-argument
    """Pin every Sync image line of the root Compose file to `version`."""
    version = _canonical(version)
    path = _compose_path(docker_file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pins = _require_pins(path, lines)

    for index, _old in pins:
        line = lines[index]
        start = line.index(SYNC_IMAGE_MARKER)
        lines[index] = line[:start] + VERSION_DEFAULT.sub(f"${{VERSION:-{version}}}", line[start:], count=1)

    changed = [index + 1 for index, old in pins if old != version]
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    log.info("compose_image_pinned", file=str(path), version=version, sync_lines=len(pins), changed_lines=changed)


@task(
    help={
        "version": "Release version every Sync image line must pin.",
        "docker_file": "Compose file to check (default: the repository's root docker-compose.yml).",
    }
)
def validate_docker_compose(context: Context, version: str, docker_file: str | None = None) -> None:  # noqa: ARG001  # pylint: disable=unused-argument
    """Refuse a Compose file whose Sync image lines pin anything but `version`."""
    path = _compose_path(docker_file)
    pins = _require_pins(path, path.read_text(encoding="utf-8").splitlines())

    offending = [f"line {index + 1}: pins {found}" for index, found in pins if found != version]
    if offending:
        log.error("compose_image_pin_mismatch", file=str(path), expected=version, offending=offending)
        msg = f"{path} does not pin every Sync image to {version}:\n  " + "\n  ".join(offending)
        raise Exit(msg, code=1)
    log.info("compose_image_pin_valid", file=str(path), version=version, sync_lines=len(pins))
