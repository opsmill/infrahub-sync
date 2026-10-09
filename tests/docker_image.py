"""How every Docker-backed suite names the Sync image under test.

The same two settings the root `docker-compose.yml` reads: `INFRAHUB_SYNC_DOCKER_IMAGE`
(the repository) and `VERSION` (the tag). The image smoke suite and the Compose suite
both read them, so one command picks the image for either:

    docker build -t infrahub-sync:local .
    INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=local uv run pytest -m docker tests/image
"""

from __future__ import annotations

import os

IMAGE_REPOSITORY_ENV = "INFRAHUB_SYNC_DOCKER_IMAGE"
IMAGE_VERSION_ENV = "VERSION"
DEFAULT_IMAGE_REPOSITORY = "registry.opsmill.io/opsmill/infrahub-sync"


def image_settings() -> dict[str, str]:
    """Return both settings as the environment holds them, stripped, empty when unset."""
    return {name: os.environ.get(name, "").strip() for name in (IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV)}


def missing_image_settings() -> list[str]:
    """Return the names of the settings that are unset or empty."""
    return [name for name, value in image_settings().items() if not value]


def image_reference() -> str | None:
    """Return `<repository>:<tag>` when both settings are set, otherwise `None`."""
    settings = image_settings()
    if missing_image_settings():
        return None
    return f"{settings[IMAGE_REPOSITORY_ENV]}:{settings[IMAGE_VERSION_ENV]}"
