"""Bootstrap converges. Repeated, it finds what it made and changes nothing.

`start` is safe to repeat, and Compose re-runs a completion-style job whenever
its container is gone, so every one of these jobs runs again on an ordinary
restart. A second run that created a second database, bucket, pool, deployment,
or registration would turn restart into a slow corruption rather than a no-op.

Each case reads the durable state from inside the deployment, before and after,
and compares. Reading an exit code alone would pass for a job that succeeded at
making a duplicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.compose.lifecycle import probe_json, run_bootstrap

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

PREFECT = """
import json, httpx
base = "http://prefect-server:4200/api"
pools = httpx.post(f"{base}/work_pools/filter", json={}, timeout=30).json()
deployments = httpx.post(f"{base}/deployments/filter", json={}, timeout=30).json()
print(json.dumps({
    "pools": sorted((pool["name"], pool["type"]) for pool in pools),
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
def converged(deployment: Deployment) -> dict[str, Any]:
    """Everything durable the first bootstrap left behind."""
    return {
        "databases": probe_json(deployment, DATABASES),
        "buckets": probe_json(deployment, BUCKETS),
        "prefect": probe_json(deployment, PREFECT),
        "configurations": probe_json(deployment, CONFIGURATIONS),
        "audit": probe_json(deployment, AUDIT),
    }


def test_the_first_bootstrap_created_the_two_databases_and_their_owner_roles(
    converged: dict[str, Any],
) -> None:
    """Separate owners and separate databases, so neither service can reach the other's."""
    state = converged["databases"]
    assert {"infrahub_sync", "prefect"} <= set(state["databases"]), state
    assert {"infrahub_sync", "prefect"} <= set(state["roles"]), state


def test_the_first_bootstrap_created_the_bucket_the_pool_and_the_deployment(
    converged: dict[str, Any],
) -> None:
    """The artifact transport, the pool the worker joins, and the flow it runs."""
    assert converged["buckets"] == ["infrahub-sync"], converged["buckets"]
    assert converged["prefect"]["pools"] == [["infrahub-sync", "process"]], converged["prefect"]
    assert converged["prefect"]["deployments"] == ["run"], converged["prefect"]


def test_the_first_bootstrap_registered_the_bundled_configuration(converged: dict[str, Any]) -> None:
    """One version of it, from the file the job was given read-only."""
    bundled = [entry for entry in converged["configurations"] if entry[0] == "infrahub-sync-qualification"]

    assert [entry[1] for entry in bundled] == [1], converged["configurations"]


def test_the_database_bootstrap_repeats_without_changing_anything(
    deployment: Deployment, converged: dict[str, Any]
) -> None:
    """It runs again on every restart, so a second run has to be a no-op."""
    repeated = run_bootstrap(deployment, "db-bootstrap")

    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert probe_json(deployment, DATABASES) == converged["databases"]


def test_the_sync_bootstrap_repeats_without_duplicating_any_durable_object(
    deployment: Deployment, converged: dict[str, Any]
) -> None:
    """Bucket, pool, deployment, and registration all converge rather than accumulate."""
    repeated = run_bootstrap(deployment)

    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert probe_json(deployment, BUCKETS) == converged["buckets"]
    assert probe_json(deployment, PREFECT) == converged["prefect"]
    assert probe_json(deployment, CONFIGURATIONS) == converged["configurations"]


def test_a_repeated_bootstrap_records_no_second_registration_event(
    deployment: Deployment, converged: dict[str, Any]
) -> None:
    """Registering is a decision and leaves evidence; finding it again is not."""
    run_bootstrap(deployment)

    assert probe_json(deployment, AUDIT) == converged["audit"]
    assert converged["audit"].count("compose-bootstrap/configs.register") == 1, converged["audit"]
