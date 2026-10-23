"""The development Compose file starts its services by profile.

`development/docker-compose.yml` holds the core services, which always start, the Sync
services under the `sync` profile, and the Infrahub services, included from
`docker-compose.infrahub.yml`, under the `infrahub` profile. A developer who runs the
Sync API and the worker on the host leaves `sync` off; one who syncs into another
Infrahub leaves `infrahub` off.

Every case reads the model Compose resolves, which needs the Compose CLI but no daemon.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404 -- fixed argv, reading the repository's own Compose file
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from tasks import dev

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "development" / "docker-compose.yml"
CORE_SERVICES = {"postgres", "object-store-init", "object-store", "prefect-server"}
SYNC_IMAGE = "infrahub-sync:dev"
# Where the image installs the project; the stack mounts the checkout over these copies.
SITE_PACKAGES = "/opt/infrahub-sync/venv/lib/python3.13/site-packages"
MOUNTED_PACKAGES = ("infrahub_sync", "opsmill_prefect_extras")


def _compose(*arguments: str) -> str:
    """Run `docker compose` on the development file with no settings from the caller."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed; the development stack's Compose file cannot be resolved")
    environment = {name: os.environ[name] for name in ("PATH", "HOME", "DOCKER_CONFIG") if name in os.environ}
    # A `.env` beside the file would supply values a clean checkout does not have.
    environment["COMPOSE_DISABLE_ENV_FILE"] = "1"
    result = subprocess.run(  # noqa: S603
        ["docker", "compose", "--file", str(COMPOSE_FILE), *arguments],  # noqa: S607 -- resolved on PATH above
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
    )
    if "is not a docker command" in result.stderr:
        pytest.skip("the docker CLI has no compose plugin; the development stack cannot be resolved")
    assert result.returncode == 0, f"docker compose {' '.join(arguments)} failed:\n{result.stderr}"
    return result.stdout


@cache
def _services(profiles: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Return the resolved services with `profiles` named."""
    arguments = [argument for profile in profiles for argument in ("--profile", profile)]
    return json.loads(_compose(*arguments, "config", "--format", "json"))["services"]


def test_the_file_declares_the_sync_and_infrahub_profiles() -> None:
    assert set(_compose("config", "--profiles").split()) == {"infrahub", "sync"}


def test_without_profiles_only_the_core_services_resolve() -> None:
    assert set(_services(())) == CORE_SERVICES


def test_the_sync_profile_adds_the_sync_services_only() -> None:
    assert set(_services(("sync",))) - CORE_SERVICES == {"sync-bootstrap", "sync-api", "sync-worker"}


def test_the_infrahub_profile_adds_the_included_infrahub_services() -> None:
    added = set(_services(("infrahub",))) - CORE_SERVICES

    assert "infrahub-server" in added
    assert not added & {"sync-bootstrap", "sync-api", "sync-worker"}


def test_the_prefect_server_publishes_its_ui_on_loopback() -> None:
    prefect = _services(())["prefect-server"]

    assert prefect["image"] == SYNC_IMAGE
    assert [(port["host_ip"], port["published"], port["target"]) for port in prefect["ports"]] == [
        ("127.0.0.1", "4230", 4200)
    ]
    assert prefect["environment"]["PREFECT_UI_ENABLED"] == "true"
    assert prefect["environment"]["PREFECT_UI_STATIC_DIRECTORY"].startswith("/tmp/infrahub-sync/")  # noqa: S108


def test_the_sync_api_publishes_on_loopback() -> None:
    api = _services(("sync",))["sync-api"]

    assert [(port["host_ip"], port["published"], port["target"]) for port in api["ports"]] == [
        ("127.0.0.1", "8001", 8000)
    ]


def test_the_included_infrahub_publishes_its_usual_port_on_loopback() -> None:
    server = _services(("infrahub",))["infrahub-server"]

    assert [(port["host_ip"], port["published"], port["target"]) for port in server["ports"]] == [
        ("127.0.0.1", "8000", 8000)
    ]


def test_the_development_settings_name_the_included_infrahub_on_port_8000() -> None:
    resolved = json.loads(_compose("--profile", "sync", "--profile", "infrahub", "config", "--format", "json"))

    assert dev.parse_dev_settings(resolved).infrahub_url == "http://127.0.0.1:8000"


def _sync_image_services() -> list[str]:
    services = _services(("sync", "infrahub"))
    return sorted(name for name, service in services.items() if service.get("image") == SYNC_IMAGE)


def test_the_sync_image_services_are_the_prefect_server_and_the_sync_services() -> None:
    assert _sync_image_services() == ["prefect-server", "sync-api", "sync-bootstrap", "sync-worker"]


@pytest.mark.parametrize("service", ["prefect-server", "sync-api", "sync-bootstrap", "sync-worker"])
@pytest.mark.parametrize("package", MOUNTED_PACKAGES)
def test_every_sync_image_service_mounts_the_checkout_read_only(service: str, package: str) -> None:
    """A code change reaches a restarted container without an image rebuild (FR-019)."""
    mounts = {mount["target"]: mount for mount in _services(("sync", "infrahub"))[service].get("volumes", [])}
    mount = mounts.get(f"{SITE_PACKAGES}/{package}")

    assert mount is not None, f"{service} does not mount {package}/ over the installed copy"
    assert mount["type"] == "bind"
    assert Path(mount["source"]) == REPO_ROOT / package
    assert mount.get("read_only") is True
