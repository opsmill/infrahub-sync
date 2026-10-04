"""Bootstrap converges. Repeated, it finds what it made and changes nothing.

`start` is safe to repeat, and Compose re-runs a completion-style job whenever
its container is gone, so every one of these jobs runs again on an ordinary
restart. A second run that created a second database, bucket, pool, or
deployment would turn restart into a slow corruption rather than a no-op.

The configuration registry is the operator's. A bootstrap registers nothing into
it, and a repeat leaves whatever an operator put there exactly as it was.

Each case reads the durable state from inside the deployment, before and after,
and compares. Reading an exit code alone would pass for a job that succeeded at
making a duplicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.compose.lifecycle import api_client, probe_json, register, run_bootstrap, smoke_package

if TYPE_CHECKING:
    from tests.compose.lifecycle import Deployment

pytestmark = pytest.mark.compose

DATABASES = """
import json, os, psycopg
with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
        databases = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolcanlogin ORDER BY rolname")
        roles = [row[0] for row in cursor.fetchall()]
print(json.dumps({"databases": databases, "roles": roles}))
"""

BUCKETS = """
import json, os, boto3
client = boto3.client(
    "s3",
    endpoint_url=os.environ["INFRAHUB_SYNC_S3_ENDPOINT_URL"],
    region_name=os.environ["INFRAHUB_SYNC_S3_REGION"],
)
print(json.dumps(sorted(bucket["Name"] for bucket in client.list_buckets()["Buckets"])))
"""

# Infrahub's task manager also holds Infrahub's own pools and deployments, so the
# probe keeps Sync's: its work pool, and the deployments of the service flow.
PREFECT = """
import base64, json, os, httpx
PREFECT_AUTH = {"Authorization": "Basic " + base64.b64encode(os.environ["PREFECT_API_AUTH_STRING"].encode()).decode()}
base = "http://task-manager:4200/api"
pool = os.environ["INFRAHUB_SYNC_SERVICE_WORK_POOL"]
pools = httpx.post(f"{base}/work_pools/filter", json={}, headers=PREFECT_AUTH, timeout=30).json()
flows = httpx.post(
    f"{base}/flows/filter", json={"flows": {"name": {"any_": ["infrahub-sync-service"]}}}, headers=PREFECT_AUTH, timeout=30
).json()
flow_ids = [flow["id"] for flow in flows]
deployments = httpx.post(
    f"{base}/deployments/filter", json={"flows": {"id": {"any_": flow_ids}}}, headers=PREFECT_AUTH, timeout=30
).json() if flow_ids else []
print(json.dumps({
    "pools": sorted((entry["name"], entry["type"]) for entry in pools if entry["name"] == pool),
    "deployments": sorted(deployment["name"] for deployment in deployments),
}))
"""

CONFIGURATIONS = """
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

AUDIT = """
import json
from infrahub_sync.service.storage import service_product_projection
events = service_product_projection().audit_events()
print(json.dumps(sorted(event.actor + "/" + event.operation for event in events)))
"""


@pytest.fixture(scope="module")
def destination_url() -> str:
    """The address the fixture destination answers on, as a container names it."""
    from tests.compose.conftest import FIXTURE_INFRAHUB_PORT

    return f"http://host.docker.internal:{FIXTURE_INFRAHUB_PORT}"


@pytest.fixture(scope="module")
def converged(deployment: Deployment) -> dict[str, Any]:
    """Everything durable the first bootstrap left behind."""
    return {
        "databases": probe_json(deployment, DATABASES),
        "buckets": probe_json(deployment, BUCKETS),
        "prefect": probe_json(deployment, PREFECT),
        "configurations": probe_json(deployment, CONFIGURATIONS),
        "audit": probe_json(deployment, AUDIT),
    }


def test_the_first_bootstrap_created_the_product_database_and_its_owner_role(
    deployment: Deployment, converged: dict[str, Any]
) -> None:
    """Sync's own database and owner, beside the task manager's untouched `prefect` database."""
    state = converged["databases"]
    product = deployment.setting("INFRAHUB_SYNC_PRODUCT_DATABASE")
    assert {product, "prefect"} <= set(state["databases"]), state
    assert deployment.setting("INFRAHUB_SYNC_PRODUCT_ROLE") in state["roles"], state


def test_the_first_bootstrap_created_the_bucket_the_pool_and_the_deployment(
    converged: dict[str, Any],
) -> None:
    """The artifact transport, the pool the worker joins, and the flow it runs."""
    assert converged["buckets"] == ["infrahub-sync"], converged["buckets"]
    assert converged["prefect"]["pools"] == [["infrahub-sync", "process"]], converged["prefect"]
    assert converged["prefect"]["deployments"] == ["run"], converged["prefect"]


def test_the_first_bootstrap_registered_no_configuration_and_recorded_no_event(
    converged: dict[str, Any],
) -> None:
    """A started deployment holds an empty registry until an operator fills it."""
    assert converged["configurations"] == [], converged["configurations"]
    assert [event for event in converged["audit"] if "configs.register" in event] == [], converged["audit"]


def test_the_database_bootstrap_repeats_without_changing_anything(
    deployment: Deployment, converged: dict[str, Any]
) -> None:
    """It runs again on every restart, so a second run has to be a no-op."""
    repeated = run_bootstrap(deployment, "db-bootstrap")

    assert repeated.returncode == 0, repeated.output
    assert probe_json(deployment, DATABASES) == converged["databases"]


def test_the_sync_bootstrap_repeats_without_duplicating_any_durable_object(
    deployment: Deployment, converged: dict[str, Any]
) -> None:
    """Bucket, pool, and deployment converge rather than accumulate."""
    repeated = run_bootstrap(deployment)

    assert repeated.returncode == 0, repeated.output
    assert probe_json(deployment, BUCKETS) == converged["buckets"]
    assert probe_json(deployment, PREFECT) == converged["prefect"]


def test_a_repeated_bootstrap_leaves_an_explicitly_registered_package_alone(
    deployment: Deployment, canaries: dict[str, str], destination_url: str
) -> None:
    """The registry the operator filled is the state a repeat must not touch.

    Registered through the API first, because an empty registry is preserved by a
    bootstrap that wipes one as readily as by one that writes nothing.
    """
    with api_client(deployment, canaries["principal"]) as client:
        _config_id, registry_version = register(
            client,
            smoke_package(destination_url),
            "compose suite: a configuration a repeated bootstrap must preserve",
        )
    registered = probe_json(deployment, CONFIGURATIONS)
    audited = probe_json(deployment, AUDIT)

    repeated = run_bootstrap(deployment)

    assert repeated.returncode == 0, repeated.output
    assert probe_json(deployment, CONFIGURATIONS) == registered
    assert probe_json(deployment, AUDIT) == audited
    assert [entry[1] for entry in registered if entry[0] == "compose-suite-registered"] == [registry_version]
