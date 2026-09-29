"""The local development stack's Compose file is valid on its own.

`compose.yaml` at the repository root is the zero-ceremony local stack: it builds the
image from the working tree and runs it. Nothing else checks it, because the Compose
contract suite in `tests/compose` asserts the shipped bundle's properties -- digest-
pinned images, tmpfs scratch roots, no shared mounts -- and this file deliberately has
none of them.

What is checked here is the one property a developer depends on: the file resolves when
none of its optional settings are set. Every value it needs is either inlined or carries
a default, so a clean checkout with no `.env` and nothing exported can run it. This needs
the Compose CLI but no daemon.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- fixed argv, reading the repository's own Compose file
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "compose.yaml"

# The settings the file reads from the environment, all optional. Removed from the
# caller's own environment rather than merely left alone, so a developer who exports one
# cannot make the run pass for everybody else.
OPTIONAL_SETTINGS = ("INFRAHUB_API_TOKEN", "NETBOX_TOKEN", "NAUTOBOT_TOKEN")

_NO_COMPOSE_PLUGIN = "is not a docker command"


def _resolve() -> subprocess.CompletedProcess[str]:
    """Resolve the development stack with none of its optional settings supplied."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed; the development stack's Compose file cannot be resolved")
    environment = dict(os.environ)
    for setting in OPTIONAL_SETTINGS:
        environment.pop(setting, None)
    # A `.env` beside the file would supply values a clean checkout does not have.
    environment["COMPOSE_DISABLE_ENV_FILE"] = "1"
    return subprocess.run(  # noqa: S603
        ["docker", "compose", "--file", str(COMPOSE_FILE), "config"],  # noqa: S607 -- resolved on PATH above
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
    )


def _resolved_or_skip() -> subprocess.CompletedProcess[str]:
    """Return a successful resolution, skipping when the Compose CLI is unavailable."""
    resolved = _resolve()
    if _NO_COMPOSE_PLUGIN in resolved.stderr:
        pytest.skip("the docker CLI has no compose plugin; the development stack cannot be resolved")
    return resolved


def test_compose_file_is_present() -> None:
    """The root Compose file exists, which is what `invoke start` runs."""
    assert COMPOSE_FILE.is_file(), f"{COMPOSE_FILE} is missing"


def test_resolves_without_its_optional_settings() -> None:
    """Every setting is inlined or defaulted, so a clean checkout can start the stack."""
    resolved = _resolved_or_skip()
    assert resolved.returncode == 0, f"`docker compose config` failed:\n{resolved.stderr}"


@pytest.mark.parametrize("setting", OPTIONAL_SETTINGS)
def test_optional_settings_resolve_to_empty(setting: str) -> None:
    """An unset source credential leaves an empty value rather than refusing to resolve.

    The shipped bundle guards its required settings with `${VAR:?}`. These three stay
    optional there too, and they have to stay optional here: the stack starts before any
    configuration package is registered, so there is no destination to hold a credential
    for yet.
    """
    resolved = _resolved_or_skip()
    if resolved.returncode != 0:
        pytest.skip("the development stack could not be resolved; the validity case reports that")
    assert f'{setting}: ""' in resolved.stdout
