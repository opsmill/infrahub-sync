"""A real registered run, through the deployed worker, against a real Infrahub.

The property this module exists for: a registered managed plan resolves its
declared configuration out of PostgreSQL and both adapter classes out of
installed code, so the worker container needs no configuration mount and shares
no filesystem with the API that submitted the run.

Proving that by reading the Compose file would prove nothing — a worker can
resolve nothing at all and still have an empty mount list. So the run here is
real: the API admits it, Prefect schedules it, the deployed worker claims it,
the bundled Infrahub adapter reads a real source branch, and the review artifact
comes back out of the object store.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH
from tests.compose.lifecycle import (
    api_client,
    await_phase,
    idempotency,
    inspect,
    plant_pending_update,
    register,
    smoke_package,
)
from tests.compose.redaction import SECRETS

if TYPE_CHECKING:
    from tests.compose.lifecycle import Deployment

pytestmark = pytest.mark.compose


@pytest.fixture(scope="module")
def pending_update(infrahub_fixture: dict[str, str]) -> str:
    """One real difference on the source, so the plan is not the empty one."""
    return plant_pending_update(infrahub_fixture)


@pytest.fixture(scope="module")
def destination_url() -> str:
    from tests.compose.conftest import FIXTURE_INFRAHUB_PORT

    return f"http://host.docker.internal:{FIXTURE_INFRAHUB_PORT}"


def test_the_deployed_worker_container_mounts_nothing(deployment: Deployment) -> None:
    """Read from the running container, not the file that asked for it.

    A resolved Compose model says what was requested; this says what Docker
    actually gave the process that ran the plan below.
    """
    worker = inspect(deployment.container("sync-worker"))
    api = inspect(deployment.container("sync-api"))

    assert worker["Mounts"] == [], f"the worker container mounts {worker['Mounts']}"
    assert {mount["Source"] for mount in worker["Mounts"]} & {mount["Source"] for mount in api["Mounts"]} == set()


def test_the_deployed_worker_has_no_configuration_directory_in_its_environment(
    deployment: Deployment,
) -> None:
    """Nothing names one, so nothing can quietly start depending on one."""
    worker = inspect(deployment.container("sync-worker"))
    names = {entry.split("=", 1)[0] for entry in worker["Config"]["Env"]}

    assert "INFRAHUB_SYNC_CONFIG_DIRECTORY" not in names


def test_a_registered_plan_runs_through_the_deployed_worker(
    deployment: Deployment, canaries: dict[str, str], destination_url: str, pending_update: str
) -> None:
    """The whole path: admit, schedule, claim, extract, plan, publish, retrieve.

    The review artifact is fetched back through the API, which reads it from the
    object store the worker published it to — the transport that exists so the
    two never need a filesystem in common.
    """
    with api_client(deployment, canaries["principal"]) as client:
        config_id, registry_version = register(
            client, smoke_package(destination_url), "compose suite: register the qualification configuration"
        )
        created = client.post(
            "/runs",
            headers=idempotency("compose-plan"),
            json={
                "operation": "plan",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": "compose suite: plan through the deployed worker",
            },
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]

        await_phase(client, run_id, "planned")

        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        document = plan.json()
        artifacts = client.get(f"/runs/{run_id}/artifacts")
        assert artifacts.status_code == 200, artifacts.text

    # The one difference planted on the source, and nothing else. A plan that
    # read nothing would also reach "planned" with a valid checksum.
    rendered = json.dumps(document["operations"])
    assert document["checksum_ok"] is True
    assert artifacts.json()["artifacts"], "the worker published no review artifact"
    assert document["summary"]["by_action"] == {"update": 1}, document["summary"]
    assert SHARED_DEVICE_NAME in rendered, rendered
    assert pending_update in rendered, rendered


def test_no_canary_reaches_the_retained_deployment_output(deployment: Deployment, canaries: dict[str, str]) -> None:
    """Every credential this deployment resolved is a throwaway planted for this run.

    The sweep reads the *raw* stream on purpose. The redaction boundary would
    remove these values from anything rendered, so searching what it returns
    would pass whether the deployment leaked or not. What is under test here is
    the deployment, not the boundary; only the names of anything found are
    reported, so this failure message stays as safe as a redacted one.
    """
    retained = deployment.logs(tail=2000)

    leaked = SECRETS.leaked(retained.unredacted(), canaries)
    assert leaked == [], f"these credentials appear in retained deployment output: {leaked}"
