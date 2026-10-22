"""`infrahub_sync.testcontainers.TestInfrahubSyncDocker` against a real Docker engine.

Two classes, each with its own Infrahub and its own Sync project:

- `TestPlanAndApply` checks what a package author relies on: the worker reaches
  `infrahub-server`, a registered package plans and applies against that Infrahub, and the
  Prefect server runs the same Prefect version as the worker, because both run from the
  Sync image.
- `TestSettingsReachTheProject` checks the two ways a test changes the Sync project: an
  extra Compose file returned by the `infrahub_sync_compose_files` hook, and a variable
  exported in `os.environ` before the class starts.

Both run the image the suite names with `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION`, and
refuse to run when the engine does not hold it, as every other case in this suite does.
"""

from __future__ import annotations

import json
import socket
import subprocess  # noqa: S404 -- `docker compose port`, through the project's own command
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

pytest.importorskip("infrahub_testcontainers", reason="needs the `testcontainers` extra")

from infrahub_testcontainers.container import PROJECT_ENV_VARIABLES

from infrahub_sync import testcontainers as sync_testcontainers
from tests.compose.lifecycle import await_phase, idempotency, register

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sync.testcontainers.helpers import InfrahubSyncDockerCompose

pytestmark = pytest.mark.compose

OVERRIDE_FILE = Path(__file__).resolve().parent / "testcontainers-override.yml"
OVERRIDE_VARIABLE = "INFRAHUB_SYNC_TESTCONTAINERS_OVERRIDE"
OVERRIDE_VALUE = "set-by-the-override-file"

# `sync-worker` joins the Infrahub project's network and reaches the server by its
# service name; the host reaches the same server at the port Docker published.
INFRAHUB_INTERNAL_URL = "http://infrahub-server:8000"
INFRAHUB_TOKEN = PROJECT_ENV_VARIABLES["INFRAHUB_TESTING_INITIAL_ADMIN_TOKEN"]
SOURCE_BRANCH = "testcontainers-source"
TAG_KIND = "BuiltinTag"

PREFECT_VERSION = "import prefect; print(prefect.__version__)"
REACH_INFRAHUB = f"import httpx; print(httpx.get('{INFRAHUB_INTERNAL_URL}/api/config', timeout=30).status_code)"


def run_in(project: InfrahubSyncDockerCompose, service: str, command: list[str]) -> str:
    """Run `command` in the running `service` container and return its standard output."""
    stdout, stderr, exit_code = project.exec_in_container(command=command, service_name=service)
    assert exit_code == 0, f"{command} failed in {service}: {stderr}"
    return stdout.strip()


def tag_package(name: str) -> dict[str, object]:
    """An Infrahub-to-Infrahub package over `BuiltinTag`, a kind every Infrahub has.

    `SOURCE_BRANCH` is the source and `main` the destination. The token is a credential
    reference that the worker resolves from its own environment, where the test class sets
    it to the Infrahub test deployment's administrator token.
    """
    settings = {"url": INFRAHUB_INTERNAL_URL, "token": {"$credential": "infrahub-token"}}
    return {
        "format_version": 1,
        "configuration": {
            "name": name,
            "source": {"name": "infrahub", "settings": {**settings, "branch": SOURCE_BRANCH}},
            "destination": {"name": "infrahub", "settings": {**settings, "branch": "main"}},
            "schema_mapping": [
                {
                    "name": TAG_KIND,
                    "mapping": TAG_KIND,
                    "identifiers": ["name"],
                    "fields": [{"name": field, "mapping": field} for field in ("name", "description")],
                }
            ],
        },
        "credentials": {
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"}
        },
    }


class TestPlanAndApply(sync_testcontainers.TestInfrahubSyncDocker):
    """A package plans and applies through the Sync project the class starts."""

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def image_is_held(cls, sync_image: str) -> str:
        """Refuse the class when the engine does not hold the image the suite names."""
        return sync_image

    @pytest.fixture(scope="class")
    @classmethod
    def planted_tag(cls, infrahub_port: int) -> str:
        """Create a tag on the source branch only, and return its name.

        `main` does not see what a branch records until the branch is merged, so the plan
        has exactly one object to create on `main`.
        """
        from infrahub_sdk import InfrahubClientSync

        client = InfrahubClientSync(address=f"http://127.0.0.1:{infrahub_port}", config={"api_token": INFRAHUB_TOKEN})
        client.branch.create(branch_name=SOURCE_BRANCH, sync_with_git=False)
        name = f"testcontainers-{uuid.uuid4().hex[:8]}"
        client.create(kind=TAG_KIND, branch=SOURCE_BRANCH, name=name, description="planted on the source branch").save()
        return name

    @staticmethod
    def test_the_project_runs_the_image_the_suite_names(
        infrahub_sync_app: InfrahubSyncDockerCompose, sync_image: str
    ) -> None:
        container = infrahub_sync_app.get_container("sync-worker")
        configured = subprocess.run(  # noqa: S603 -- a fixed `docker inspect` of this project's container
            ["docker", "inspect", "--format", "{{.Config.Image}}", str(container.ID)],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        assert configured == sync_image

    @staticmethod
    def test_the_worker_reaches_infrahub_server(infrahub_sync_app: InfrahubSyncDockerCompose) -> None:
        status = run_in(infrahub_sync_app, "sync-worker", ["python", "-c", REACH_INFRAHUB])
        assert status == "200"

    @staticmethod
    def test_the_prefect_server_and_the_worker_run_the_same_prefect_version(
        infrahub_sync_app: InfrahubSyncDockerCompose,
    ) -> None:
        server = run_in(infrahub_sync_app, "prefect-server", ["python", "-c", PREFECT_VERSION])
        worker = run_in(infrahub_sync_app, "sync-worker", ["python", "-c", PREFECT_VERSION])
        assert server
        assert server == worker

    @staticmethod
    def test_a_package_plans_and_applies(
        sync_api_url: str, sync_api_token: str, planted_tag: str, infrahub_port: int
    ) -> None:
        with httpx.Client(
            base_url=sync_api_url, headers={"Authorization": f"Bearer {sync_api_token}"}, timeout=60
        ) as client:
            config_id, registry_version = register(
                client, tag_package("testcontainers-tags"), "testcontainers: register the tag package"
            )
            created = client.post(
                "/runs",
                headers=idempotency("testcontainers-plan"),
                json={
                    "operation": "plan",
                    "config_id": config_id,
                    "registry_version": registry_version,
                    "branch": "main",
                    "reason": "testcontainers: plan the tag package",
                },
            )
            assert created.status_code == 202, created.text
            run_id = str(created.json()["run"]["run_id"])
            await_phase(client, run_id, "planned")

            plan = client.get(f"/runs/{run_id}/plan")
            assert plan.status_code == 200, plan.text
            document = plan.json()
            assert document["summary"]["by_action"] == {"create": 1}, document["summary"]
            assert planted_tag in json.dumps(document["operations"])

            applied = client.post(
                f"/runs/{run_id}/apply",
                headers=idempotency("testcontainers-apply"),
                json={
                    "expected_checksum": document["checksum"],
                    "confirm_writes": True,
                    "branch": "main",
                    "reason": "testcontainers: apply the reviewed plan",
                },
            )
            assert applied.status_code == 202, applied.text
            await_phase(client, run_id, "applied")

        from infrahub_sdk import InfrahubClientSync

        infrahub = InfrahubClientSync(address=f"http://127.0.0.1:{infrahub_port}", config={"api_token": INFRAHUB_TOKEN})
        tag = infrahub.get(kind=TAG_KIND, branch="main", name__value=planted_tag)
        assert tag.description.value == "planted on the source branch"  # ty: ignore[unresolved-attribute]  # schema-driven node


def free_loopback_port() -> int:
    """Return a loopback port that nothing listens on now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class TestSettingsReachTheProject(sync_testcontainers.TestInfrahubSyncDocker):
    """An override file from the hook and an exported variable both reach the running project."""

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def exported_api_port(cls, sync_image: str) -> Iterator[int]:  # noqa: ARG003  # pylint: disable=unused-argument
        """Export `INFRAHUB_SYNC_API_PORT` before the class starts, and restore it afterwards.

        Autouse, so it runs before the class's other fixtures write the project's `.env`.
        `sync_image` refuses the class when the engine does not hold the image the suite names.
        """
        port = free_loopback_port()
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("INFRAHUB_SYNC_API_PORT", str(port))
            yield port

    @pytest.fixture(scope="class")
    @classmethod
    def infrahub_sync_compose_files(cls) -> list[Path]:
        return [OVERRIDE_FILE]

    @staticmethod
    def test_the_override_file_sets_its_variable_on_the_worker(infrahub_sync_app: InfrahubSyncDockerCompose) -> None:
        value = run_in(infrahub_sync_app, "sync-worker", ["printenv", OVERRIDE_VARIABLE])
        assert value == OVERRIDE_VALUE

    @staticmethod
    def test_the_exported_port_is_the_published_sync_api_port(
        infrahub_sync_app: InfrahubSyncDockerCompose, exported_api_port: int, sync_api_url: str
    ) -> None:
        published = subprocess.run(  # noqa: S603 -- the project's own `docker compose` command
            [*infrahub_sync_app.compose_command_property, "port", "sync-api", "8000"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        assert published == f"127.0.0.1:{exported_api_port}"
        assert sync_api_url == f"http://127.0.0.1:{exported_api_port}"
        assert httpx.get(f"{sync_api_url}/version", timeout=30).status_code == 200
