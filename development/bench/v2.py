"""Legacy CLI arguments for the separately installed v2 release, never the v3 CLI."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def release_environment(directory: Path) -> dict[str, str]:
    """Keep inherited Python settings from selecting the caller's checkout or environment."""
    removed = {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    environment = {name: value for name, value in os.environ.items() if name not in removed}
    environment["INFRAHUB_SYNC_CACHE_DIR"] = str(directory / ".infrahub-sync-cache")
    environment["UV_PROJECT_ENVIRONMENT"] = str(directory / ".venv")
    return environment


def sync_command(environment: Path, configuration: Path, variant: str) -> list[str]:
    """Select full/parallel/incremental flags for the executable inside the v2 environment."""
    command = [
        str(environment / ".venv/bin/infrahub-sync"),
        "sync",
        "--name",
        "from-netbox",
        "--directory",
        str(configuration),
        "--no-full-extract" if variant == "incremental" else "--full-extract",
    ]
    command += (
        ["--parallel", "--concurrent-load"] if variant == "parallel" else ["--no-parallel", "--no-concurrent-load"]
    )
    return command


def generation_command(environment: Path, configuration: Path) -> list[str]:
    """Generate the v2 adapter classes required by its sync command, outside measurement."""
    return [
        str(environment / ".venv/bin/infrahub-sync"),
        "generate",
        "--name",
        "from-netbox",
        "--directory",
        str(configuration),
    ]
