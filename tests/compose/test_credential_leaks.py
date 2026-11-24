"""No credential value appears where an operator can read it (spec 015 FR-016 to FR-018, SC-005; spec 027 SC-004).

One deployment of the operator file, every credential set to a distinct value of 24 characters
or more: the deployment's own passwords and tokens, both object store keys, the key of stored
credentials and the token of the `worker` account. The Infrahub token is stored through the
Sync API as the credential `infrahub-token`, with a second stored credential that no package
names and whose value is generated. The Infrahub token is not generated: a plan needs the
token the Infrahub fixture accepts, which is 36 characters. The fixture runs one plan of the
smoke package before any search, so the API has released the credential, the worker has
used it to read the destination, and both have written to the object store. Three surfaces are
searched for those values, and only the names found are reported:

- the output of the commands the operator documentation tells an operator to run
- every container's log
- every service's environment, against the receiver list of
  `dev/specs/027-stored-credentials/contracts/environment.md`. A stored credential has no
  receiver: its value is in no container's environment.

Boundary (spec 015 decision 2): the worker container log and the Prefect log mask a credential
only when it is six characters or longer. Every value here is longer, so the log check holds for
them; a shorter credential is 3.0 work and not checked here.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

from tasks.smoke_dataset import SMOKE_BRANCH
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT
from tests.compose.lifecycle import (
    FIXTURE_OVERRIDE,
    SMOKE_CREDENTIAL,
    Deployment,
    api_client,
    await_phase,
    canary,
    container_reachable_host,
    docker,
    encryption_key,
    idempotency,
    operator_environment,
    register,
    smoke_package,
    wait_for,
    worker_state,
)
from tests.compose.redaction import SECRETS

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

pytestmark = pytest.mark.compose

API_PORT = 8075
# The names the values of the two stored credentials are reported under. They are not
# settings: no `.env` line and no container environment carries them.
STORED_DESTINATION = f"stored credential {SMOKE_CREDENTIAL}"
UNUSED_CREDENTIAL = "leak-check-unused"
STORED_UNUSED = f"stored credential {UNUSED_CREDENTIAL}"
# Which services may hold each credential, by the `.env` setting that carries it.
RECEIVERS: dict[str, set[str]] = {
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": {"postgres", "db-bootstrap"},
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": {"db-bootstrap", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_PREFECT_PASSWORD": {"db-bootstrap", "prefect-server"},
    "INFRAHUB_SYNC_PREFECT_AUTH_STRING": {"prefect-server", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_S3_ACCESS_KEY": {"object-store", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_S3_SECRET_KEY": {"object-store", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_ENCRYPTION_KEY": {"sync-api"},
    "INFRAHUB_SYNC_WORKER_TOKEN": {"sync-api", "sync-worker"},
    # The Sync API keeps a stored credential encrypted in PostgreSQL and releases it to the
    # worker for one run, in memory: no container's environment holds one.
    STORED_DESTINATION: set(),
    STORED_UNUSED: set(),
}
# The commands the operator documentation tells an operator to run.
DOCUMENTED_COMMANDS = (
    ("config", "--quiet"),
    ("ps",),
    ("ps", "--all"),
    ("logs", "--no-color", "--tail", "200"),
)


def _environment_values(container: str) -> list[str]:
    """One container's environment entries, read raw and never rendered."""
    inspected = docker(["inspect", "--format", "{{json .Config.Env}}", container])
    assert inspected.returncode == 0, inspected.stderr
    return json.loads(inspected.unredacted().strip().splitlines()[-1])


def _run_one_plan(deployment: Deployment) -> None:
    """Register the smoke package and plan it, so the Sync API releases its credential before any search."""
    principal = deployment.setting("INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN")
    with api_client(deployment, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(deployment.destination), "compose suite: leak check configuration"
        )
        created = client.post(
            "/runs",
            headers=idempotency("compose-leaks-plan"),
            json={
                "operation": "plan",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": "compose suite: plan before the leak searches",
            },
        )
        assert created.status_code == 202, created.text
        await_phase(client, created.json()["run"]["run_id"], "planned")


@pytest.fixture(scope="module")
def leak_deployment(
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Deployment, dict[str, str]]]:
    planted = {**canaries, "worker": canary("worker-token"), "encryption_key": encryption_key()}
    values = {
        "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": planted["administrator"],
        "INFRAHUB_SYNC_PRODUCT_PASSWORD": planted["product"],
        "INFRAHUB_SYNC_PREFECT_PASSWORD": planted["prefect"],
        "INFRAHUB_SYNC_PREFECT_AUTH_STRING": planted["prefect_auth"],
        # Set before the bundled store first starts: the store takes it as its root user.
        "INFRAHUB_SYNC_S3_ACCESS_KEY": canary("s3-access-key"),
        "INFRAHUB_SYNC_S3_SECRET_KEY": planted["object_store"],
        "INFRAHUB_SYNC_ENCRYPTION_KEY": planted["encryption_key"],
        "INFRAHUB_SYNC_WORKER_TOKEN": planted["worker"],
        # The token the Infrahub fixture accepts, so the plan can read the destination.
        STORED_DESTINATION: infrahub_fixture["token"],
        # A stored credential that no package names: the Sync API never releases it.
        STORED_UNUSED: canary("stored-unused-token"),
    }
    assert all(len(value) >= 24 for value in values.values())
    directory = tmp_path_factory.mktemp("leaks")
    environment_file = operator_environment(
        directory,
        image=sync_image,
        canaries=planted,
        api_port=API_PORT,
        access_key=values["INFRAHUB_SYNC_S3_ACCESS_KEY"],
    )
    deployment = Deployment(
        instance=f"leaks{uuid.uuid4().hex[:10]}",
        environment_file=environment_file,
        overrides=(FIXTURE_OVERRIDE,),
        destination=f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}",
        api_port=API_PORT,
    )
    launched = deployment.up()
    try:
        assert launched.returncode == 0, launched.stderr[-3000:]
        wait_for("the deployment reporting a live worker", lambda: worker_state(deployment) in {"ready", "busy"})
        assert SECRETS.leaked(launched.unredacted(), values) == []
        # Both credentials are stored before the package that names one of them is registered.
        deployment.store_credentials(
            {
                SMOKE_CREDENTIAL: {"token": values[STORED_DESTINATION]},
                UNUSED_CREDENTIAL: {"token": values[STORED_UNUSED]},
            }
        )
        _run_one_plan(deployment)
        yield deployment, values
    finally:
        deployment.down(volumes=True)


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS, ids=" ".join)
def test_no_documented_command_prints_a_credential(
    leak_deployment: tuple[Deployment, Mapping[str, str]], command: tuple[str, ...]
) -> None:
    deployment, values = leak_deployment
    result = deployment.compose(list(command))

    assert result.returncode == 0, result.stderr[-1000:]
    assert SECRETS.leaked(result.unredacted(), values) == []


def test_no_container_log_holds_a_credential(leak_deployment: tuple[Deployment, Mapping[str, str]]) -> None:
    """Every container, the worker and the Prefect server included: each value here is over six characters."""
    deployment, values = leak_deployment
    found = {}
    for service, container in deployment.containers().items():
        logs = docker(["logs", container])
        leaked = SECRETS.leaked(logs.unredacted(), values)
        if leaked:
            found[service] = leaked

    assert found == {}


def test_each_credential_reaches_only_its_receivers(leak_deployment: tuple[Deployment, Mapping[str, str]]) -> None:
    deployment, values = leak_deployment
    holders: dict[str, set[str]] = {setting: set() for setting in values}
    for service, container in deployment.containers().items():
        entries = _environment_values(container)
        for setting, value in values.items():
            if any(value in entry for entry in entries):
                holders[setting].add(service)

    misplaced = {
        setting: sorted(held - RECEIVERS[setting]) for setting, held in holders.items() if held - RECEIVERS[setting]
    }
    assert misplaced == {}
    assert holders["INFRAHUB_SYNC_ENCRYPTION_KEY"] == {"sync-api"}
    assert holders["INFRAHUB_SYNC_WORKER_TOKEN"] == {"sync-api", "sync-worker"}
    assert holders[STORED_DESTINATION] == set()
