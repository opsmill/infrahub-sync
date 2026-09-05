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
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND
from tests.compose.lifecycle import (
    api_client,
    await_phase,
    idempotency,
    inspect,
    register,
)

if TYPE_CHECKING:
    from tests.compose.lifecycle import Deployment

pytestmark = pytest.mark.compose

# The fields the qualification package maps. Both sides are the bundled
# `infrahub` adapter, so the registered worker resolves them through the
# installed loader with nothing generated and nothing on a filesystem.
SMOKE_FIELDS = ("name", "type")


def smoke_package(destination_url: str) -> dict[str, Any]:
    """The declared package this suite registers, shaped like the bundled one.

    Infrahub to Infrahub against the fixture's own instance: `main` as the
    source, the disposable smoke branch as the destination. The token is a
    credential reference the worker resolves from its own environment, so no
    secret value is posted, stored, or echoed.
    """
    return {
        "format_version": 1,
        "configuration": {
            "name": "compose-suite-registered",
            "source": {
                "name": "infrahub",
                "settings": {
                    "url": destination_url,
                    "branch": "main",
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": destination_url,
                    "branch": SMOKE_BRANCH,
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": SMOKE_KIND,
                    "mapping": SMOKE_KIND,
                    "identifiers": ["name"],
                    "fields": [{"name": name, "mapping": name} for name in SMOKE_FIELDS],
                }
            ],
        },
        "credentials": {"infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"}},
    }


@pytest.fixture(scope="module")
def destination_url() -> str:
    from tests.compose.conftest import FIXTURE_INFRAHUB_PORT

    return f"http://host.docker.internal:{FIXTURE_INFRAHUB_PORT}"


@pytest.fixture(scope="module")
def pending_update(infrahub_fixture: dict[str, str]) -> str:
    """Give the destination exactly one real difference to plan, and return its value.

    The fixture seeds the shared device onto `main` before the smoke branch forks,
    so both branches hold it and a plan taken now would be empty. Empty is the
    trap: a plan with no operation still reaches `planned`, and every signal a
    weaker assertion could rest on stays green while the source was never read.

    The value is fresh on every run because a fixed one converges — the first
    apply writes it to the destination, and the next run's plan is empty again.
    """
    from infrahub_sdk import InfrahubClientSync

    value = f"compose-suite-{uuid.uuid4().hex[:12]}"
    client = InfrahubClientSync(address=infrahub_fixture["address"], config={"api_token": infrahub_fixture["token"]})
    device = client.get(kind=SMOKE_KIND, branch="main", name__value=SHARED_DEVICE_NAME)
    device.type.value = value  # ty: ignore[invalid-assignment]  # the SDK types this node union-wide
    device.save()
    return value


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

    Scanning for the values rather than asserting a particular message is
    redacted is the difference between proving nothing leaked and proving one
    known string was handled.
    """
    retained = deployment.logs(tail=2000)

    leaked = sorted(kind for kind, value in canaries.items() if value in retained)
    assert leaked == [], f"these credentials appear in retained deployment output: {leaked}"
