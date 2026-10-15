"""A deployment of the previous release keeps its plans and run files when it moves to this file.

US3, FR-019, SC-006: this file keeps the bundled MinIO build, its digest, its
volume and its credential. A project started from the previous release's root
`docker-compose.yml` that planned one run must, after `up -d --wait` with this
file on the same project and with no reset step, still serve that run's record,
its plan and the plan's operations from the object store.

The previous file is found the way `test_prefect_server_upgrade.py` finds it:
the newest release tag of version 3 or later that ships one, or, until one
exists, the commit before the one that removed the stock Prefect image.
`INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF` names another Git reference.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tasks.preview import SMOKE_BRANCH
from tests.compose.conftest import COMPOSE_FILE, FIXTURE_INFRAHUB_PORT
from tests.compose.lifecycle import (
    FIXTURE_OVERRIDE,
    Deployment,
    api_client,
    await_phase,
    container_reachable_host,
    idempotency,
    operator_environment,
    plant_pending_update,
    register,
    smoke_package,
    wait_for,
    worker_state,
)
from tests.compose.test_prefect_server_upgrade import PREVIOUS_REF_SETTING, git, previous_compose_ref

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.compose.redaction import Captured

pytestmark = pytest.mark.compose

API_PORT = 8072
# The previous file still publishes Prefect, so it gets a port of its own too.
PREFECT_PORT = 4272
READY_SECONDS = 420
CLI_SERVICE = "cli"


@pytest.fixture(scope="module")
def previous_release_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the previous release's root compose file to a scratch directory."""
    reference = previous_compose_ref()
    if reference is None:
        pytest.skip(
            "no release tag of version 3 or later holds docker-compose.yml, and no earlier commit was found; "
            f"set {PREVIOUS_REF_SETTING} to a Git reference that holds one"
        )
    shown = git("show", f"{reference}:docker-compose.yml")
    assert shown.returncode == 0, f"{reference} holds no docker-compose.yml: {shown.stderr.strip()}"
    path = tmp_path_factory.mktemp("previous-object-store-release") / "docker-compose.yml"
    path.write_text(shown.stdout, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def upgraded_project(
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    previous_release_file: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Deployment, Deployment]]:
    """One project started from the previous file, and the same project under this file."""
    environment_file = operator_environment(
        tmp_path_factory.mktemp("object-store-upgrade"),
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        api_port=API_PORT,
        prefect_port=PREFECT_PORT,
    )
    shared: dict[str, Any] = {
        "instance": f"storeupgrade{uuid.uuid4().hex[:12]}",
        "environment_file": environment_file,
        "destination": f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}",
        "api_port": API_PORT,
        "overrides": (FIXTURE_OVERRIDE,),
    }
    previous = Deployment(compose_file=previous_release_file, **shared)
    current = Deployment(compose_file=COMPOSE_FILE, **shared)
    try:
        yield previous, current
    finally:
        current.down(volumes=True)


def wait_until_ready(deployment: Deployment) -> None:
    wait_for(
        "the deployment reporting a live worker",
        lambda: worker_state(deployment) in {"ready", "busy"},
        timeout=READY_SECONDS,
    )


def plan_one_run(deployment: Deployment, principal: str, infrahub_fixture: dict[str, str]) -> tuple[str, str]:
    """Register the example package, plan one run, and return its identifier and plan checksum."""
    plant_pending_update(infrahub_fixture)
    with api_client(deployment, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(deployment.destination), "object store upgrade: configuration before the upgrade"
        )
        created = client.post(
            "/runs",
            headers=idempotency("store-upgrade-plan"),
            json={
                "operation": "plan",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": "object store upgrade: plan before the upgrade",
            },
        )
        assert created.status_code == 202, created.text
        run_id = str(created.json()["run"]["run_id"])
        await_phase(client, run_id, "planned")
        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        return run_id, str(plan.json()["checksum"])


def cli(deployment: Deployment, *arguments: str) -> Captured:
    """Run the operator's client the way the deployment page documents it."""
    return deployment.compose(["--profile", CLI_SERVICE, "run", "--rm", "--no-deps", "-T", CLI_SERVICE, *arguments])


def test_a_plan_from_the_previous_release_is_read_after_the_move(
    upgraded_project: tuple[Deployment, Deployment], canaries: dict[str, str], infrahub_fixture: dict[str, str]
) -> None:
    previous, current = upgraded_project

    launched = previous.up()
    assert launched.returncode == 0, launched.stderr[-3000:]
    wait_until_ready(previous)
    run_id, checksum = plan_one_run(previous, canaries["principal"], infrahub_fixture)

    # No reset step: the same project, the same `.env`, this file, one waited start.
    moved = current.up()
    assert moved.returncode == 0, moved.stderr[-3000:]
    wait_until_ready(current)

    shown = cli(current, "runs", "show", run_id)
    assert shown.returncode == 0, shown.output
    assert run_id in shown.stdout, shown.output
    assert "planned" in shown.stdout, shown.output

    # The plan and its operations are artifacts in the object store; rendering
    # them in detail reads both back through the moved deployment.
    planned = cli(current, "runs", "plan", run_id, "--detail")
    assert planned.returncode == 0, planned.output
    assert checksum in planned.stdout, planned.output

    with api_client(current, canaries["principal"]) as client:
        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        assert plan.json()["checksum"] == checksum
