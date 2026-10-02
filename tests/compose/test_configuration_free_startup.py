"""A deployment that starts, repeats and resets with no configuration at all.

Sync-owned only. This module starts one real deployment of the image under test
from the root `docker-compose.yml`, drives it with the plain `docker compose`
commands an operator uses, and it depends on no external system: no Infrahub, no source, no destination, no credential for one.
That is the property — a deployment reaches READY on its own dependencies, holds
an empty registry until an operator registers something, and comes back empty
after `docker compose down --volumes`.

The package registered here declares unreachable external addresses and
credential references nothing resolves. Registration is content admission and
reaches no network, so the package stays registered and is never planned,
applied or synced.

This is an empty-startup and lifecycle proof. It is not the write-bearing
lifecycle matrix, which runs elsewhere against a real destination.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.compose.lifecycle import (
    Deployment,
    api_client,
    operator_environment,
    probe_json,
    register,
    run_bootstrap,
    wait_for,
    worker_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.compose

# This module's own loopback ports, so it can run beside every other deployment
# this suite starts.
API_PORT = "8051"
PREFECT_PORT = "4251"

# Addresses that resolve to nothing and a credential nothing sets. A registration
# admits declared content, so this package is registrable and unusable — which is
# what keeps a startup proof from acquiring a destination.
UNREACHABLE_SOURCE = "http://sync-r2-source.invalid:8000"
UNREACHABLE_DESTINATION = "http://sync-r2-destination.invalid:8000"
DECLARED_NAME = "configuration-free-startup"

# What a registry holds, read from inside the deployment through the store that
# holds it rather than from the API that serves it.
REGISTRY = """
import json
from infrahub_sync.configuration.models import parse_configuration_package
from infrahub_sync.product_store import configs
from infrahub_sync.service.storage import service_product_projection
projection = service_product_projection()
registry = []
for summary in configs.list_configs(projection=projection):
    for version in projection.list_configuration_versions(summary.config_id):
        registry.append(
            [
                parse_configuration_package(version.declared_content).configuration.name,
                version.registry_version,
                version.package_checksum,
            ]
        )
print(json.dumps(sorted(registry)))
"""

# Every audit event the deployment holds, as actor and operation. A registration
# is a decision and leaves one; convergence is not, and leaves none.
AUDIT = """
import json
from infrahub_sync.service.storage import service_product_projection
events = service_product_projection().audit_events()
print(json.dumps(sorted(event.actor + "/" + event.operation for event in events)))
"""

# The actor a bootstrap used to register under. Nothing writes it now, and a
# census carrying it would mean a startup registered something.
RETIRED_BOOTSTRAP_ACTOR = "compose-bootstrap"

SCHEMA_TABLES = """
import json, os, psycopg
with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()"
        )
        print(json.dumps(cursor.fetchone()[0]))
"""


def declared_package() -> dict[str, Any]:
    """A valid declared package whose external inputs resolve to nothing."""
    return {
        "format_version": 1,
        "configuration": {
            "name": DECLARED_NAME,
            "source": {
                "name": "infrahub",
                "settings": {
                    "url": UNREACHABLE_SOURCE,
                    "branch": "main",
                    "token": {"$credential": "startup-token"},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": UNREACHABLE_DESTINATION,
                    "branch": "main",
                    "token": {"$credential": "startup-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": "InfraDevice",
                    "mapping": "InfraDevice",
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}],
                }
            ],
        },
        # Nothing sets this variable, so the reference is declared and unresolved.
        "credentials": {"startup-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_R2_ABSENT_TOKEN"}},
    }


def wait_until_ready(deployment: Deployment) -> None:
    """Return once the Sync API reports a live worker."""
    wait_for("the deployment reporting a live worker", lambda: worker_state(deployment) in {"ready", "busy"})


@pytest.fixture(scope="module")
def started(
    sync_image: str, canaries: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Deployment]:
    """One fresh project of the operator file, taken to READY with no configuration.

    Its `.env` carries the required credentials and nothing else: no destination
    or source credential is set, which is the whole point of this module.
    """
    environment_file = operator_environment(
        tmp_path_factory.mktemp(f"startup{uuid.uuid4().hex[:8]}"),
        image=sync_image,
        destination_token="",
        canaries=canaries,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )
    deployment = Deployment(
        instance=f"startup{uuid.uuid4().hex[:12]}",
        environment_file=environment_file,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )
    launched = deployment.up()
    assert launched.returncode == 0, launched.stderr[-3000:]
    try:
        wait_until_ready(deployment)
        yield deployment
    finally:
        deployment.down(volumes=True)


@pytest.fixture(scope="module")
def principal(started: Deployment) -> str:
    """The bearer token of this deployment's one principal."""
    tokens = json.loads(started.setting("INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"))
    return str(next(iter(tokens.values()))["token"])


def test_a_deployment_with_no_configuration_reaches_ready(started: Deployment) -> None:
    """No package, no credentials, no destination — and a live worker anyway."""
    assert started.status() == "READY"
    assert worker_state(started) in {"ready", "busy"}
    assert not started.setting("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"), "the fixture supplied a destination credential"
    assert probe_json(started, SCHEMA_TABLES) > 0, "the deployment came back with no product schema"
    assert probe_json(started, REGISTRY) == [], "a start registered something"
    assert probe_json(started, AUDIT) == [], "a start recorded an audit event"


def test_a_repeated_up_keeps_the_registry_empty(started: Deployment) -> None:
    """`up -d --wait` is the command an operator repeats, and it registers nothing either time."""
    repeated = started.up()

    assert repeated.returncode == 0, repeated.stderr[-3000:]
    wait_until_ready(started)
    assert probe_json(started, REGISTRY) == []
    assert probe_json(started, AUDIT) == [], "a repeated start recorded an audit event"


def test_a_repeated_bootstrap_leaves_registered_content_exactly_as_it_was(started: Deployment, principal: str) -> None:
    """The registry is the operator's, and convergence passes over it untouched.

    Registered through the API and never planned: the package declares addresses
    that resolve to nothing, so admitting it proves registration is content
    admission rather than a connection.
    """
    with api_client(started, principal) as client:
        config_id, registry_version = register(
            client, declared_package(), "startup gate: register a configuration bootstrap must preserve"
        )
    registered = probe_json(started, REGISTRY)
    audited = probe_json(started, AUDIT)
    assert [entry[0] for entry in registered] == [DECLARED_NAME], registered

    repeated = run_bootstrap(started)

    assert repeated.returncode == 0, repeated.output
    assert probe_json(started, REGISTRY) == registered
    assert [entry[1] for entry in registered] == [registry_version]
    assert config_id
    # The operator's registration is the one decision recorded, and a repeated
    # bootstrap adds nothing to the census under any actor. The operation name is
    # the API's to choose, so what is asserted is the count and the actor.
    census = probe_json(started, AUDIT)
    assert census == audited, census
    assert len(audited) == 1, audited
    assert [event for event in census if event.startswith(f"{RETIRED_BOOTSTRAP_ACTOR}/")] == [], census


def test_stop_and_up_preserve_the_registered_package(started: Deployment) -> None:
    """`docker compose stop` keeps data; `up` again converges without touching the registry."""
    before = probe_json(started, REGISTRY)

    stopped = started.compose(["stop"])
    assert stopped.returncode == 0, stopped.stderr
    restarted = started.up()
    assert restarted.returncode == 0, restarted.stderr[-3000:]

    wait_for("the deployment reporting a live worker again", lambda: worker_state(started) in {"ready", "busy"})
    assert probe_json(started, REGISTRY) == before
    assert before, "this case compared a registry that held nothing"
    assert [event for event in probe_json(started, AUDIT) if event.startswith(f"{RETIRED_BOOTSTRAP_ACTOR}/")] == []


def test_down_volumes_and_the_next_up_comes_back_empty(started: Deployment) -> None:
    """What `docker compose down --volumes` is for, from the other side of a real second start.

    Runs last: it takes this module's deployment down, with its data, and brings
    it up again from nothing under the same project name.
    """
    removed = started.down(volumes=True)
    assert removed.returncode == 0, removed.stderr

    launched = started.up()
    assert launched.returncode == 0, launched.stderr[-3000:]
    wait_until_ready(started)
    assert started.status() == "READY"
    assert probe_json(started, SCHEMA_TABLES) > 0
    assert probe_json(started, REGISTRY) == [], "a cold start came back with a registry"
    assert probe_json(started, AUDIT) == [], "a cold start came back with an audit event"
