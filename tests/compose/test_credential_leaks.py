"""No credential value appears where an operator can read it (spec 021 FR-016 to FR-018, SC-005).

One deployment of the operator file, every credential set to a distinct value of 24 characters
or more: the deployment's own passwords and tokens, both object store keys, and all 24 adapter
credential variables. Each value is generated, except the Infrahub token: a plan needs the
token the Infrahub fixture accepts, which is 36 characters. The fixture runs one plan of the
smoke package before any search, so the API and the worker have resolved a credential, read
the destination and written to the object store. The smoke package resolves only the Infrahub
token; the other 23 adapter variables reach the worker but no run resolves them. Three surfaces
are searched for those values, and only the names found are reported:

- the output of the commands the operator documentation tells an operator to run
- every container's log
- every service's environment, against the receiver list of
  `dev/specs/021-compose-store-credentials-ca/contracts/environment-variables.md`

Boundary (spec 021 decision 2): the worker container log and the Prefect log mask a credential
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
    Deployment,
    api_client,
    await_phase,
    canary,
    container_reachable_host,
    docker,
    idempotency,
    operator_environment,
    register,
    smoke_package,
    wait_for,
    worker_state,
)
from tests.compose.redaction import SECRETS
from tests.compose.test_compose_contract import WORKER_CREDENTIAL_SETTINGS

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

pytestmark = pytest.mark.compose

API_PORT = 8075
DESTINATION_SETTING = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
# Which services may hold each credential, by the `.env` setting that carries it.
RECEIVERS: dict[str, set[str]] = {
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": {"postgres", "db-bootstrap"},
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": {"db-bootstrap", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_PREFECT_PASSWORD": {"db-bootstrap", "prefect-server"},
    "INFRAHUB_SYNC_PREFECT_AUTH_STRING": {"prefect-server", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_S3_ACCESS_KEY": {"object-store", "sync-bootstrap", "sync-api", "sync-worker"},
    "INFRAHUB_SYNC_S3_SECRET_KEY": {"object-store", "sync-bootstrap", "sync-api", "sync-worker"},
    DESTINATION_SETTING: {"sync-api", "sync-worker"},
    **{setting: {"sync-worker"} for setting in WORKER_CREDENTIAL_SETTINGS if setting != DESTINATION_SETTING},
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
    """Register the smoke package and plan it, so a run resolves its credential before any search."""
    principal = json.loads(deployment.setting("INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"))["compose-suite"]["token"]
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
    values = {
        "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": canaries["administrator"],
        "INFRAHUB_SYNC_PRODUCT_PASSWORD": canaries["product"],
        "INFRAHUB_SYNC_PREFECT_PASSWORD": canaries["prefect"],
        "INFRAHUB_SYNC_PREFECT_AUTH_STRING": canaries["prefect_auth"],
        # Set before the bundled store first starts: the store takes it as its root user.
        "INFRAHUB_SYNC_S3_ACCESS_KEY": canary("s3-access-key"),
        "INFRAHUB_SYNC_S3_SECRET_KEY": canaries["object_store"],
        # The token the Infrahub fixture accepts, so the plan can read the destination.
        DESTINATION_SETTING: infrahub_fixture["token"],
    }
    for setting in WORKER_CREDENTIAL_SETTINGS:
        if setting != DESTINATION_SETTING:
            values[setting] = canary(setting.removeprefix("INFRAHUB_SYNC_CREDENTIAL_").lower())
    assert all(len(value) >= 24 for value in values.values())
    directory = tmp_path_factory.mktemp("leaks")
    environment_file = operator_environment(
        directory,
        image=sync_image,
        destination_token=values[DESTINATION_SETTING],
        canaries=canaries,
        api_port=API_PORT,
        access_key=values["INFRAHUB_SYNC_S3_ACCESS_KEY"],
    )
    with environment_file.open("a", encoding="utf-8") as settings:
        for setting in WORKER_CREDENTIAL_SETTINGS:
            if setting != DESTINATION_SETTING:
                settings.write(f"{setting}={values[setting]}\n")
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
    assert holders["INFRAHUB_SYNC_CREDENTIAL_CISCO_APIC_PASSWORD"] == {"sync-worker"}
