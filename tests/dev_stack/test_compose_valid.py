"""The local development stack's Compose file is valid on its own.

`development/docker-compose.yml` is the zero-ceremony local stack: it builds the
image from the working tree and runs it. Nothing else checks it, because the Compose
contract suite in `tests/compose` asserts the operator file's properties -- the
registry image reference, required credentials, no host mounts -- and this file
deliberately has none of them.

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
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "development" / "docker-compose.yml"
# The profile sets the tasks start: core alone, core with the Sync services, core with
# the included Infrahub, and everything.
PROFILE_SETS = ((), ("sync",), ("infrahub",), ("sync", "infrahub"))

# The settings the file reads from the environment, all optional. Removed from the
# caller's own environment rather than merely left alone, so a developer who exports one
# cannot make the run pass for everybody else.
OPTIONAL_SETTINGS = ("ENCRYPTION_KEY", "WORKER_TOKEN")
# The settings of stored credentials that take a fixed development value when the shell sets
# none (spec 027, contracts/environment.md), with the services that are given each.
DEVELOPMENT_DEFAULTS = {
    "INFRAHUB_SYNC_ENCRYPTION_KEY": {"sync-api"},
    "INFRAHUB_SYNC_WORKER_TOKEN": {"sync-api", "sync-worker"},
}

_NO_COMPOSE_PLUGIN = "is not a docker command"


def _resolve(profiles: tuple[str, ...] = ("sync", "infrahub")) -> subprocess.CompletedProcess[str]:
    """Resolve the development stack with none of its optional settings supplied."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed; the development stack's Compose file cannot be resolved")
    environment = dict(os.environ)
    for setting in OPTIONAL_SETTINGS:
        environment.pop(setting, None)
    # A `.env` beside the file would supply values a clean checkout does not have.
    environment["COMPOSE_DISABLE_ENV_FILE"] = "1"
    profile_arguments = [argument for profile in profiles for argument in ("--profile", profile)]
    return subprocess.run(  # noqa: S603
        ["docker", "compose", "--file", str(COMPOSE_FILE), *profile_arguments, "config"],  # noqa: S607 -- resolved on PATH above
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
    )


def _resolved_or_skip(profiles: tuple[str, ...] = ("sync", "infrahub")) -> subprocess.CompletedProcess[str]:
    """Return a successful resolution, skipping when the Compose CLI is unavailable."""
    resolved = _resolve(profiles)
    if _NO_COMPOSE_PLUGIN in resolved.stderr:
        pytest.skip("the docker CLI has no compose plugin; the development stack cannot be resolved")
    return resolved


def test_compose_file_is_present() -> None:
    """The development Compose file exists, which is what `invoke dev.start` runs."""
    assert COMPOSE_FILE.is_file(), f"{COMPOSE_FILE} is missing"


@pytest.mark.parametrize("profiles", PROFILE_SETS, ids=lambda profiles: "+".join(profiles) or "core")
def test_resolves_without_its_optional_settings(profiles: tuple[str, ...]) -> None:
    """Every setting is inlined or defaulted, so a clean checkout can start the stack."""
    resolved = _resolved_or_skip(profiles)
    assert resolved.returncode == 0, f"`docker compose config` failed:\n{resolved.stderr}"


@pytest.mark.parametrize("setting", sorted(DEVELOPMENT_DEFAULTS))
def test_the_settings_of_stored_credentials_resolve_to_a_development_value(setting: str) -> None:
    """A clean checkout stores credentials: the key and the worker token have fixed development values.

    The root `docker-compose.yml` guards both with `${VAR:?}`, because an operator must choose
    them. Here they are public development values, as the password and the token of `admin` are.
    """
    resolved = _resolved_or_skip()
    if resolved.returncode != 0:
        pytest.skip("the development stack could not be resolved; the validity case reports that")
    services = yaml.safe_load(resolved.stdout)["services"]

    holders = {name for name, definition in services.items() if (definition.get("environment") or {}).get(setting)}

    assert holders == DEVELOPMENT_DEFAULTS[setting]


def test_no_development_service_is_given_a_credential_variable() -> None:
    """Every `INFRAHUB_SYNC_CREDENTIAL_*` variable is removed: the Sync API stores the credentials."""
    resolved = _resolved_or_skip()
    if resolved.returncode != 0:
        pytest.skip("the development stack could not be resolved; the validity case reports that")
    services = yaml.safe_load(resolved.stdout)["services"]

    held = sorted(
        f"{name}: {setting}"
        for name, definition in services.items()
        for setting in (definition.get("environment") or {})
        if setting.startswith("INFRAHUB_SYNC_CREDENTIAL_")
    )

    assert held == []


def _sync_api_environment(path: Path) -> dict[str, object]:
    """Read the `sync-api` environment as written, before Compose interpolates it."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document["services"]["sync-api"]["environment"]


def test_the_development_sync_api_links_to_its_own_prefect_ui() -> None:
    """US3: the development stack publishes the Prefect UI on loopback, so its links point there."""
    environment = _sync_api_environment(COMPOSE_FILE)

    assert environment["INFRAHUB_SYNC_PREFECT_UI_URL"] == "http://127.0.0.1:${PREFECT_PORT:-4230}"


def test_the_operator_sync_api_takes_its_prefect_ui_url_from_the_operator() -> None:
    """US3: only the operator knows where its Prefect UI is reachable; unset leaves the link off."""
    environment = _sync_api_environment(REPO_ROOT / "docker-compose.yml")

    assert environment["INFRAHUB_SYNC_PREFECT_UI_URL"] == "${INFRAHUB_SYNC_PREFECT_UI_URL:-}"


@pytest.mark.parametrize("path", [REPO_ROOT / "docker-compose.yml", COMPOSE_FILE], ids=["operator", "development"])
def test_the_worker_reads_the_approval_setting_of_the_sync_api(path: Path) -> None:
    """Research R6: the worker checks the approval setting before a scheduled run, so it needs the same value."""
    services = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]
    api_value = services["sync-api"]["environment"]["INFRAHUB_SYNC_REQUIRE_APPROVAL"]

    assert services["sync-worker"]["environment"].get("INFRAHUB_SYNC_REQUIRE_APPROVAL") == api_value
