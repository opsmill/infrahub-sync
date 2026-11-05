"""The Prefect server starts from the Sync image, and an image that cannot run it says why.

The root file runs `prefect-server` from the same image as the Sync services,
with the uvicorn factory command, and publishes no port for it. The first case
reads the shared deployment: the server reports healthy and Compose maps no host
port to it.

The second case covers an operator who builds a custom image without the
`service` extra. Such an image has no Prefect package, so the factory module
cannot be imported. The image is derived from the image under test by removing
the installed `prefect` package, and the case checks the outcome an operator
sees: `prefect-server` never reports healthy, and its log names the import
error.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import start_command
from tests.compose.lifecycle import Deployment, docker, operator_environment

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.compose

PREFECT_SERVER = "prefect-server"
# The services `prefect-server` needs to start: the database and the job that
# creates its role.
PREFECT_SERVER_CLOSURE = ("postgres", "db-bootstrap", PREFECT_SERVER)
# Long enough for the server to import its factory and fail, and much shorter
# than the health check's own retry budget, which the case does not wait out.
FAILED_START_BOUND_SECONDS = 90
FAILED_START_CUSHION_SECONDS = 60
# The site-packages directory of the image's Python environment.
SITE_PACKAGES = "/opt/infrahub-sync/venv/lib/python3.13/site-packages"
WITHOUT_PREFECT = f"""\
FROM {{image}}
USER 0:0
RUN rm -rf {SITE_PACKAGES}/prefect {SITE_PACKAGES}/prefect-*.dist-info
USER 10001:10001
"""
# Another installed distribution can leave `prefect` behind as a namespace
# package, so the error names `prefect` or one of its modules.
START_ERROR = "ModuleNotFoundError: No module named 'prefect"


def health(deployment: Deployment, service: str) -> str:
    """Return the Docker health status of one service's container."""
    result = docker(["inspect", "--format", "{{.State.Health.Status}}", deployment.container(service)])
    assert result.returncode == 0, result.output
    return result.stdout.strip()


def test_the_prefect_server_is_healthy_and_publishes_no_port(deployment: Deployment) -> None:
    """The server answers its health check on the deployment's network and nowhere else."""
    published = deployment.compose(["port", PREFECT_SERVER, "4200"])
    bindings = docker(
        ["inspect", "--format", "{{json .HostConfig.PortBindings}}", deployment.container(PREFECT_SERVER)]
    )

    assert health(deployment, PREFECT_SERVER) == "healthy"
    assert published.returncode != 0 or not published.stdout.strip(), published.output
    assert bindings.stdout.strip() in {"{}", "null"}, bindings.output


@pytest.fixture(scope="module")
def image_without_prefect(sync_image: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """An image like the one under test, built without the Prefect package, removed afterwards."""
    context: Path = tmp_path_factory.mktemp("without-prefect")
    (context / "Dockerfile").write_text(WITHOUT_PREFECT.format(image=sync_image), encoding="utf-8")
    reference = f"infrahub-sync:without-prefect-{uuid.uuid4().hex[:12]}"
    built = docker(["build", "--quiet", "--tag", reference, str(context)], timeout=600)
    assert built.returncode == 0, built.output
    try:
        yield reference
    finally:
        docker(["image", "rm", "--force", reference])


@pytest.fixture(scope="module")
def deployment_without_prefect(
    image_without_prefect: str, canaries: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Deployment]:
    """A project of the root file that runs the image without the Prefect package."""
    environment_file = operator_environment(
        tmp_path_factory.mktemp(f"noprefect{uuid.uuid4().hex[:8]}"),
        image=image_without_prefect,
        destination_token="",
        canaries=canaries,
    )
    deployment = Deployment(instance=f"noprefect{uuid.uuid4().hex[:12]}", environment_file=environment_file)
    try:
        yield deployment
    finally:
        deployment.down(volumes=True)


def test_an_image_without_the_service_extra_fails_the_health_check_and_logs_why(
    deployment_without_prefect: Deployment,
) -> None:
    """The operator sees a server that is not healthy and a log line that names the missing package."""
    started = deployment_without_prefect.compose(
        start_command(PREFECT_SERVER_CLOSURE, bound=FAILED_START_BOUND_SECONDS),
        timeout=FAILED_START_BOUND_SECONDS + FAILED_START_CUSHION_SECONDS,
    )
    logs = deployment_without_prefect.logs(PREFECT_SERVER)

    assert started.returncode != 0, started.output
    assert health(deployment_without_prefect, PREFECT_SERVER) != "healthy"
    assert START_ERROR in logs.stdout, logs.output
