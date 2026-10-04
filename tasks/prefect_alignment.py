"""Keep Sync's Prefect pin equal to the Prefect version Infrahub ships.

Sync runs on Infrahub's task manager, so Infrahub's Prefect version is the
reference. This check reads the Prefect pin from Infrahub's `pyproject.toml` at
its latest release tag and fails when Sync's pin differs, so a Prefect bump in
Infrahub is caught before a Sync release rather than by a worker that refuses to
start.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess  # noqa: S404 -- one fixed gh argv reads a public repository
from pathlib import Path

from invoke import Context, task

from .utils import REPO_BASE

INFRAHUB_REPOSITORY = "opsmill/infrahub"
_PREFECT_PIN = re.compile(r"^prefect==(?P<version>[0-9][0-9A-Za-z.]*)(?:\s*;.*)?$")


class PrefectAlignmentError(RuntimeError):
    """Sync's Prefect pin is missing, inconsistent, or differs from Infrahub's."""


def prefect_pins(pyproject_text: str) -> set[str]:
    """Return every exact `prefect==` version a pyproject declares, across all dependency lists."""
    # Imported here: the task collection must still load on Python 3.10, which has no tomllib.
    import tomllib  # noqa: PLC0415  # ty: ignore[unresolved-import] -- TODO: drop with Python 3.10 support

    data = tomllib.loads(pyproject_text)
    project = data.get("project", {})
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    pins = set()
    for requirement in requirements:
        match = _PREFECT_PIN.match(requirement.strip())
        if match:
            pins.add(match.group("version"))
    return pins


def single_prefect_pin(pyproject_text: str, owner: str) -> str:
    """Return the one Prefect pin a pyproject declares, or refuse none or several."""
    pins = prefect_pins(pyproject_text)
    if len(pins) != 1:
        msg = f"{owner} must pin exactly one Prefect version, found {sorted(pins) or 'none'}"
        raise PrefectAlignmentError(msg)
    return pins.pop()


def compare(sync_pyproject: str, infrahub_pyproject: str, infrahub_tag: str) -> str:
    """Return the shared version, or refuse when Sync's pin differs from Infrahub's."""
    sync_version = single_prefect_pin(sync_pyproject, "infrahub-sync")
    infrahub_version = single_prefect_pin(infrahub_pyproject, f"Infrahub {infrahub_tag}")
    if sync_version != infrahub_version:
        msg = (
            f"infrahub-sync pins prefect=={sync_version}, and Infrahub {infrahub_tag} ships "
            f"prefect=={infrahub_version}; align the pin with Infrahub's"
        )
        raise PrefectAlignmentError(msg)
    return sync_version


def _gh_api(path: str) -> dict:
    """Read one GitHub API resource through the gh CLI, which carries the CI token."""
    try:
        output = subprocess.run(  # noqa: S603 -- fixed gh argv; the path is built from constants and a tag
            ["gh", "api", path],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        payload = json.loads(output)
    except FileNotFoundError:
        msg = "the gh CLI is not installed; it reads Infrahub's release from GitHub"
        raise PrefectAlignmentError(msg) from None
    except subprocess.CalledProcessError as error:
        msg = f"gh api {path} failed: {(error.stderr or '').strip() or f'exit status {error.returncode}'}"
        raise PrefectAlignmentError(msg) from None
    except json.JSONDecodeError:
        msg = f"gh api {path} did not return JSON"
        raise PrefectAlignmentError(msg) from None
    if not isinstance(payload, dict):
        msg = f"gh api {path} did not return a JSON object"
        raise PrefectAlignmentError(msg)
    return payload


def _field(payload: dict, name: str, path: str) -> str:
    """Return one string field of a GitHub API answer, or refuse naming what is missing."""
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        msg = f"gh api {path} answered without {name!r}"
        raise PrefectAlignmentError(msg)
    return value


def latest_infrahub_tag() -> str:
    """Return the tag of Infrahub's latest published release."""
    path = f"repos/{INFRAHUB_REPOSITORY}/releases/latest"
    return _field(_gh_api(path), "tag_name", path)


def infrahub_pyproject(tag: str) -> str:
    """Return Infrahub's pyproject.toml at one tag."""
    path = f"repos/{INFRAHUB_REPOSITORY}/contents/pyproject.toml?ref={tag}"
    return base64.b64decode(_field(_gh_api(path), "content", path)).decode("utf-8")


@task(name="prefect-alignment")
def prefect_alignment(context: Context, infrahub_tag: str = "") -> None:  # noqa: ARG001
    """Fail when Sync's Prefect pin differs from the one Infrahub's latest release ships."""
    tag = infrahub_tag or latest_infrahub_tag()
    version = compare(
        Path(REPO_BASE / "pyproject.toml").read_text(encoding="utf-8"),
        infrahub_pyproject(tag),
        tag,
    )
    print(f"prefect=={version} matches Infrahub {tag}")
