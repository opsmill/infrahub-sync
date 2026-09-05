"""The shipped entry point takes a clean copy of the bundle to a ready deployment.

Everything else in this suite drives Compose directly, which is how the tests
stay readable but is not how an operator starts anything. This case runs
`infrahub-sync-compose` itself, against a real daemon, from a bundle that has
never been initialized: identity generated, credentials generated, preflight
answered by Docker rather than a shim, and readiness taken from the API's own
view of a worker that registered.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- the subject of this suite is a shell entry point
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import BUNDLE, FIXTURE_INFRAHUB_PORT
from tests.compose.lifecycle import container_reachable_host, docker

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.compose

# Its own ports, so this deployment and the shared one can both be up.
API_PORT = "8031"
PREFECT_PORT = "4231"
START_TIMEOUT_SECONDS = 900


@pytest.fixture(scope="module")
def fresh_bundle(
    sync_image: str, infrahub_fixture: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Path]:
    """A never-initialized copy of the shipped bundle, removed with its deployment.

    Its declared destination is the pinned Infrahub, named by an address that
    reaches the host from inside a container. Preflight probes the destination
    before anything in the bundle is running, which is what a real deployment's
    external destination is: reachable without help from the bundle itself.
    """
    copy = tmp_path_factory.mktemp("entry-point") / "compose"
    shutil.copytree(BUNDLE, copy)
    package = copy / "configuration" / "qualification.yaml"
    package.write_text(
        package.read_text(encoding="utf-8").replace(
            "http://infrahub.example.net:8000", f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}"
        ),
        encoding="utf-8",
    )
    del sync_image, infrahub_fixture
    yield copy
    identity = (copy / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()
    docker(["compose", "--project-name", f"infrahub-sync-{identity}", "down", "--volumes", "--remove-orphans"])


def entry_point(bundle: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed argv
        [str(bundle / "infrahub-sync-compose"), command],
        capture_output=True,
        text=True,
        check=False,
        timeout=START_TIMEOUT_SECONDS,
        env=os.environ.copy(),
    )


def test_the_entry_point_initializes_preflights_and_starts_a_ready_deployment(
    fresh_bundle: Path, sync_image: str, infrahub_fixture: dict[str, str]
) -> None:
    """One operator sequence, end to end, with Docker answering every question.

    Readiness here is the API's own report of a registered worker. A stack whose
    containers were all running but whose worker never joined the pool would
    reach neither the `READY` line nor the end of `start`.
    """
    infrahub_token = infrahub_fixture["token"]
    created = entry_point(fresh_bundle, "init")
    assert created.returncode == 0, created.stderr

    settings = fresh_bundle / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8")
        .replace("INFRAHUB_SYNC_IMAGE=REPLACE-ME", f"INFRAHUB_SYNC_IMAGE={sync_image}")
        .replace("INFRAHUB_API_TOKEN=REPLACE-ME", f"INFRAHUB_API_TOKEN={infrahub_token}")
        + f"INFRAHUB_SYNC_IMAGE_PULL_POLICY=never\nINFRAHUB_SYNC_API_PORT={API_PORT}\n"
        f"INFRAHUB_SYNC_PREFECT_PORT={PREFECT_PORT}\n",
        encoding="utf-8",
    )

    checked = entry_point(fresh_bundle, "preflight")
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert "preflight passed" in checked.stdout

    started = entry_point(fresh_bundle, "start")
    assert started.returncode == 0, started.stderr + started.stdout[-3000:]
    assert "the deployment is READY" in started.stdout


def test_a_second_start_of_the_same_deployment_is_a_no_op(fresh_bundle: Path) -> None:
    """`start` is the command an operator repeats, so repeating it has to converge.

    It also proves the port check reads its own publication as owned: the first
    start left both loopback ports held by this instance's containers.
    """
    repeated = entry_point(fresh_bundle, "start")

    assert repeated.returncode == 0, repeated.stderr + repeated.stdout[-3000:]
    assert "already" in repeated.stdout
    assert "the deployment is READY" in repeated.stdout
