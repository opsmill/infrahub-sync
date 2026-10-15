"""A deployment of the previous release keeps its Prefect records when it moves to this file.

US1 scenario 3: the previous release's root `docker-compose.yml` ran the stock
`prefecthq/prefect` image; this one runs the Prefect server from the Sync image.
Both keep their records in the same PostgreSQL database, so `up -d --wait` with
the new file on the same project must find the work pool, the deployment and the
earlier flow run where the old server left them.

The previous file is read from the newest release tag of version 3 or later that
ships one. Until such a tag exists, it is read from the commit before the one
that removed the stock Prefect image from the file. Set
`INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF` to read it from another Git reference.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # noqa: S404 -- fixed argv Git probes
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tasks.preview import SMOKE_BRANCH
from tests.compose.conftest import COMPOSE_FILE, FIXTURE_INFRAHUB_PORT, REPO_ROOT
from tests.compose.lifecycle import (
    Deployment,
    api_client,
    await_phase,
    await_verification,
    container_reachable_host,
    idempotency,
    operator_environment,
    plant_pending_update,
    probe_json,
    register,
    smoke_package,
    wait_for,
    worker_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.compose

PREVIOUS_REF_SETTING = "INFRAHUB_SYNC_PREVIOUS_COMPOSE_REF"
# Release tags of the V3 line and later, the first line that ships a root compose file.
STOCK_PREFECT_IMAGE = "prefecthq/prefect"
RELEASE_TAG = re.compile(r"^v?(?P<major>[0-9]+)\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?$")
API_PORT = 8071
PREFECT_PORT = 4271
READY_SECONDS = 420

# Every Prefect record the scenario names, read inside the deployment's network
# with the Prefect API credential.
PREFECT_RECORDS = """
import base64, json, os, httpx
PREFECT_AUTH = {"Authorization": "Basic " + base64.b64encode(os.environ["PREFECT_API_AUTH_STRING"].encode()).decode()}

def listed(path):
    answer = httpx.post("http://prefect-server:4200/api/" + path, json={}, headers=PREFECT_AUTH, timeout=30)
    answer.raise_for_status()
    return answer.json()

print(json.dumps({
    "work_pools": sorted(entry["name"] for entry in listed("work_pools/filter")),
    "deployments": sorted(entry["id"] for entry in listed("deployments/filter")),
    "flow_runs": sorted(entry["id"] for entry in listed("flow_runs/filter")),
}))
"""


def git(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run one fixed-argv Git command in this repository."""
    return subprocess.run(  # noqa: S603 -- fixed argv
        ["git", "-C", str(REPO_ROOT), *argv],  # noqa: S607 -- Git is resolved from PATH
        capture_output=True,
        text=True,
        check=False,
    )


def previous_compose_ref() -> str | None:
    """Return the Git reference of the previous release's root compose file, if one exists."""
    configured = os.environ.get(PREVIOUS_REF_SETTING)
    if configured:
        return configured
    tags = git("tag", "--list", "--sort=-version:refname").stdout.split()
    for tag in tags:
        match = RELEASE_TAG.fullmatch(tag)
        if match and int(match["major"]) >= 3 and git("cat-file", "-e", f"{tag}:docker-compose.yml").returncode == 0:
            return tag
    # The newest commit that changed how often the file names the stock image is
    # the one that removed it; its parent holds the last file that ran it.
    removal = git(
        "log", "-n", "1", "--format=%H", f"-S{STOCK_PREFECT_IMAGE}", "--", "docker-compose.yml"
    ).stdout.strip()
    if removal and STOCK_PREFECT_IMAGE in git("show", f"{removal}^:docker-compose.yml").stdout:
        return f"{removal}^"
    return None


@pytest.fixture(scope="module")
def previous_compose_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the previous release's root compose file to a scratch directory."""
    reference = previous_compose_ref()
    if reference is None:
        pytest.skip(
            "no release tag of version 3 or later holds docker-compose.yml, and no commit removed "
            f"{STOCK_PREFECT_IMAGE} from it; set {PREVIOUS_REF_SETTING} to a Git reference that holds one"
        )
    shown = git("show", f"{reference}:docker-compose.yml")
    assert shown.returncode == 0, f"{reference} holds no docker-compose.yml: {shown.stderr.strip()}"
    path = tmp_path_factory.mktemp("previous-release") / "docker-compose.yml"
    path.write_text(shown.stdout, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def upgraded_project(
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    previous_compose_file: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Deployment, Deployment]]:
    """One project started from the previous file, and the same project under this file."""
    environment_file = operator_environment(
        tmp_path_factory.mktemp("upgrade"),
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        api_port=API_PORT,
        prefect_port=PREFECT_PORT,
    )
    instance = f"upgrade{uuid.uuid4().hex[:12]}"
    destination = f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}"
    shared: dict[str, Any] = {
        "instance": instance,
        "environment_file": environment_file,
        "destination": destination,
        "api_port": API_PORT,
        "prefect_port": PREFECT_PORT,
    }
    previous = Deployment(compose_file=previous_compose_file, **shared)
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


def plan_and_apply(deployment: Deployment, principal: str, infrahub_fixture: dict[str, str]) -> None:
    """Register the example package, then plan, verify and apply one run."""
    plant_pending_update(infrahub_fixture)
    with api_client(deployment, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(deployment.destination), "upgrade suite: configuration before the upgrade"
        )
        created = client.post(
            "/runs",
            headers=idempotency("upgrade-plan"),
            json={
                "operation": "plan",
                "config_id": config_id,
                "registry_version": registry_version,
                "branch": SMOKE_BRANCH,
                "reason": "upgrade suite: plan before the upgrade",
            },
        )
        assert created.status_code == 202, created.text
        run_id = str(created.json()["run"]["run_id"])
        await_phase(client, run_id, "planned")
        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        checksum = plan.json()["checksum"]

        verified = client.post(
            f"/runs/{run_id}/verify",
            headers=idempotency("upgrade-verify"),
            json={"reason": "upgrade suite: verify the plan"},
        )
        assert verified.status_code == 202, verified.text
        assert await_verification(client, run_id)["outcome"] == "verified"

        applied = client.post(
            f"/runs/{run_id}/apply",
            headers=idempotency("upgrade-apply"),
            json={
                "expected_checksum": checksum,
                "confirm_writes": True,
                "branch": SMOKE_BRANCH,
                "reason": "upgrade suite: apply before the upgrade",
            },
        )
        assert applied.status_code == 202, applied.text
        await_phase(client, run_id, "applied")


def test_the_prefect_records_survive_the_move_to_the_sync_image_server(
    upgraded_project: tuple[Deployment, Deployment], canaries: dict[str, str], infrahub_fixture: dict[str, str]
) -> None:
    previous, current = upgraded_project

    launched = previous.up()
    assert launched.returncode == 0, launched.stderr[-3000:]
    wait_until_ready(previous)
    plan_and_apply(previous, canaries["principal"], infrahub_fixture)
    before = probe_json(previous, PREFECT_RECORDS)
    assert before["work_pools"], before
    assert before["deployments"], before
    assert before["flow_runs"], before

    moved = current.up()
    assert moved.returncode == 0, moved.stderr[-3000:]
    wait_until_ready(current)
    after = probe_json(current, PREFECT_RECORDS)

    assert set(before["work_pools"]) <= set(after["work_pools"]), after["work_pools"]
    assert set(before["deployments"]) <= set(after["deployments"]), after["deployments"]
    assert set(before["flow_runs"]) <= set(after["flow_runs"]), after["flow_runs"]
    server_image = current.compose(["images", "--format", "json", "prefect-server"])
    assert server_image.returncode == 0, server_image.output
    assert "prefecthq/prefect" not in json.dumps(json.loads(server_image.stdout))
