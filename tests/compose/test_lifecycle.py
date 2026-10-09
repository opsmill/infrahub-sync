"""One deployment, driven through its whole life with plain `docker compose` commands.

Everything an operator does to the root `docker-compose.yml`, in the order they
would do it: write a `.env`, `up -d --wait`, read status and logs, `stop`,
`restart`, and `down -v`. Each case drives the Compose command that replaced one
of the removed wrapper's commands, against a real daemon.

The cases run in file order against one module-scoped deployment, because a
lifecycle is a sequence and pretending otherwise would mean starting a stack per
case. Each case leaves the deployment as it found it, except the last two: they
are the teardown contract and what a deployment does after it, and they run last
on purpose.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT, PROJECT_LABEL
from tests.compose.lifecycle import (
    DURABLE_STATE,
    READY_TIMEOUT_SECONDS,
    Deployment,
    api_client,
    await_phase,
    await_verification,
    container_reachable_host,
    docker,
    idempotency,
    online_worker_names,
    operator_environment,
    plant_pending_update,
    probe_json,
    register,
    smoke_package,
    wait_for,
    worker_state,
)
from tests.compose.redaction import SECRETS

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.compose

# Its own ports, so this deployment and the shared one can both be up.
API_PORT = "8031"
PREFECT_PORT = "4231"
BUSY_OBSERVATION_SECONDS = 30
# The tail an operator asks `docker compose logs` for, per service.
LOG_TAIL = 200

# Every service the file declares, used to bound what `logs` may print.
SERVICES = (
    "postgres",
    "db-bootstrap",
    "object-store-init",
    "object-store",
    "prefect-server",
    "sync-bootstrap",
    "sync-api",
    "sync-worker",
)

# The credentials written into the `.env`. Their values reach real processes, so
# the log sweep looks for them rather than for a string planted only to be found.
GENERATED_SETTINGS = (
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD",
    "INFRAHUB_SYNC_PRODUCT_PASSWORD",
    "INFRAHUB_SYNC_PREFECT_PASSWORD",
    "INFRAHUB_SYNC_S3_SECRET_KEY",
    "INFRAHUB_SYNC_API_TOKEN",
)


def generated_credentials(deployment: Deployment) -> dict[str, str]:
    """The credential values this deployment's `.env` carries."""
    return {name: deployment.setting(name) for name in GENERATED_SETTINGS}


def wait_until_ready(deployment: Deployment) -> None:
    """Return once the Sync API reports a live worker, as the removed `start` waited."""
    wait_for(
        "the deployment reporting a live worker",
        lambda: worker_state(deployment) in {"ready", "busy"},
        timeout=READY_TIMEOUT_SECONDS,
    )


def submit(client: httpx.Client, config_id: str, registry_version: int, operation: str, reason: str) -> str:
    """Create one managed run and return its identifier.

    A `sync` writes at the destination in one admission, so the API requires the
    confirmation up front; a `plan` writes nothing and takes none.
    """
    body: dict[str, Any] = {
        "operation": operation,
        "config_id": config_id,
        "registry_version": registry_version,
        "branch": SMOKE_BRANCH,
        "reason": reason,
    }
    if operation == "sync":
        body["confirm_writes"] = True
    created = client.post("/runs", headers=idempotency(f"compose-{operation}"), json=body)
    assert created.status_code == 202, created.text
    return str(created.json()["run"]["run_id"])


def observe_worker_states() -> set[str]:
    """Collect every worker state the API reports while one run is in flight."""
    observed: set[str] = set()
    deadline = time.monotonic() + BUSY_OBSERVATION_SECONDS
    while time.monotonic() < deadline:
        response = httpx.get(f"http://127.0.0.1:{API_PORT}/status", timeout=15)
        response.raise_for_status()
        observed.add(response.json()["worker"]["state"])
        if "busy" in observed:
            break
        time.sleep(1)
    return observed


def destination_device_type(infrahub_fixture: dict[str, str]) -> str:
    """Read the applied value straight from the destination branch."""
    from infrahub_sdk import InfrahubClientSync

    client = InfrahubClientSync(address=infrahub_fixture["address"], config={"api_token": infrahub_fixture["token"]})
    device = client.get(kind=SMOKE_KIND, branch=SMOKE_BRANCH, name__value=SHARED_DEVICE_NAME)
    return str(device.type.value)  # ty: ignore[unresolved-attribute]


def volume_exists(name: str) -> bool:
    return docker(["volume", "inspect", name]).returncode == 0


def plant_foreign_volume() -> str:
    """Create a volume named the way another project's would be."""
    name = f"infrahub-sync-{uuid.uuid4().hex[:16]}_postgres-data"
    created = docker(["volume", "create", "--label", f"{PROJECT_LABEL}=someone-else", name])
    assert created.returncode == 0, created.stderr
    return name


@pytest.fixture(scope="module")
def started(
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Deployment]:
    """A fresh project of the operator file, written a `.env` and taken to READY.

    The destination credential is supplied for the managed rows below, which do
    register a package and run against the pinned Infrahub. The start itself
    needs neither.
    """
    destination = f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}"
    environment_file = operator_environment(
        tmp_path_factory.mktemp("lifecycle"),
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )
    deployment = Deployment(
        instance=f"lifecycle{uuid.uuid4().hex[:12]}",
        environment_file=environment_file,
        destination=destination,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )
    # Started here rather than in the first case, so every case below runs
    # against a deployment that exists however few of them are selected.
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


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def test_up_wait_starts_a_ready_deployment(started: Deployment) -> None:
    """`up -d --wait`, then a live worker: the sequence that replaced `start`.

    Readiness here is the API's own report of a registered worker. A stack whose
    containers were all running but whose worker never joined the pool would not
    report READY.
    """
    assert started.status() == "READY"


def test_a_second_up_of_the_same_deployment_is_a_no_op(started: Deployment) -> None:
    """`up` is the command an operator repeats, so repeating it has to converge.

    Every container is already in its declared state, so Compose replaces none
    of them: the API keeps the container it had.
    """
    before = started.container("sync-api")
    repeated = started.up()

    assert repeated.returncode == 0, repeated.stderr[-3000:]
    assert started.container("sync-api") == before
    wait_until_ready(started)
    assert started.status() == "READY"


# ---------------------------------------------------------------------------
# Endpoint-backed state
# ---------------------------------------------------------------------------


def test_a_started_deployment_reports_ready(started: Deployment) -> None:
    """READY is the API's answer about a registered worker, not a container count."""
    assert started.status() == "READY"
    assert worker_state(started) in {"ready", "busy"}


def test_a_busy_worker_is_still_ready_at_the_deployment_level(started: Deployment, principal: str) -> None:
    """A deployment with work in flight is working, not degraded.

    `busy` is a positive scheduled queue depth -- `service.py` derives the state
    as `"no-live-worker" if live == 0 else "busy" if snapshot.queue_depth > 0
    else "ready"` -- so it lasts exactly until a worker claims. Waiting to catch
    it raced that claim, and a plan taken before the first sample left the run
    reporting `{'ready'}` and failing for a reason the property is not about.

    The claim is prevented for the observation instead, so the depth this reads
    is held rather than caught. A paused container is still live to the API for
    the heartbeat window it derives `live` from, which is what makes `busy` the
    state under test here and `no-live-worker` the neighbouring case's.
    """
    worker = started.container("sync-worker")
    with api_client(started, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(started.destination), "compose suite: configuration for the busy check"
        )
        paused = docker(["pause", worker])
        assert paused.returncode == 0, paused.stderr
        try:
            created = client.post(
                "/runs",
                headers=idempotency("compose-busy"),
                json={
                    "operation": "plan",
                    "config_id": config_id,
                    "registry_version": registry_version,
                    "branch": SMOKE_BRANCH,
                    "reason": "compose suite: occupy the worker",
                },
            )
            assert created.status_code == 202, created.text
            run_id = created.json()["run"]["run_id"]

            observed = observe_worker_states()
        finally:
            # In `finally` so a failed assertion above cannot leave the
            # deployment's only worker paused for every case after this one.
            docker(["unpause", worker])
        await_phase(client, run_id, "planned")

    assert "busy" in observed, observed
    assert observed <= {"ready", "busy"}, observed


def test_logs_are_bounded_and_carry_no_generated_credential(started: Deployment) -> None:
    """Logs are where an operator looks first, so they are swept rather than trusted."""
    printed = started.logs(tail=LOG_TAIL)

    assert printed.returncode == 0, printed.stderr
    assert len(printed.stdout.splitlines()) <= LOG_TAIL * len(SERVICES), len(printed.stdout.splitlines())
    # The raw stream, deliberately. The boundary would strip these values from
    # anything rendered, so sweeping what it returns would pass whether `logs`
    # printed a credential or not. Only the names of anything found are reported.
    leaked = SECRETS.leaked(printed.unredacted(), generated_credentials(started))
    assert leaked == [], f"these generated credentials appear in the logs: {leaked}"


def test_a_paused_worker_ages_into_degraded_while_its_container_still_runs(started: Deployment) -> None:
    """The case container state cannot see: a process that is up and answering nothing.

    A paused container is still `running` to Docker. The API ages its worker out
    of the live set once the heartbeat stops arriving, which is the only signal
    that distinguishes a working deployment from a hung one.
    """
    worker = started.container("sync-worker")
    docker(["pause", worker])
    try:
        assert docker(["inspect", "--format", "{{.State.Running}}", worker]).stdout.strip() == "true"
        degraded = wait_for(
            "the deployment ageing into DEGRADED",
            lambda: started.status() == "DEGRADED",
            timeout=180,
        )
        assert degraded
    finally:
        docker(["unpause", worker])
    wait_for(
        "the deployment returning to READY",
        lambda: started.status() == "READY",
        timeout=180,
    )


def test_a_stopped_worker_ages_into_degraded_while_the_api_stays_reachable(started: Deployment) -> None:
    """The worker is gone and the API is not, which is a degraded deployment.

    Distinct from the paused case above: there the container is running and the
    process is not answering, here the container is stopped outright. They fail
    the same test at the API — no fresh heartbeat — and they must not be allowed
    to reach it by different accidents, so both are driven.

    Distinct from `STOPPED` too. Nothing about this deployment is off: the API
    answers, the databases answer, submissions are accepted and will sit
    unclaimed. Reporting that as stopped would tell an operator to start a
    deployment that is already running, and reporting it as ready would tell
    them nothing is wrong.
    """
    worker = started.container("sync-worker")
    halted = docker(["stop", worker])
    assert halted.returncode == 0, halted.stderr
    try:
        # Stopped, not removed: the container is still this project's, and the
        # deployment still has every part it was started with.
        assert docker(["inspect", "--format", "{{.State.Running}}", worker]).stdout.strip() == "false"
        degraded = wait_for(
            "the deployment ageing into DEGRADED",
            lambda: started.status() == "DEGRADED",
            timeout=180,
        )
        assert degraded
        # The half that makes this DEGRADED rather than STOPPED, asked of the
        # published surface an operator would use.
        answered = httpx.get(f"{started.api}/version", timeout=30)
        assert answered.status_code == 200, answered.text
        assert worker_state(started) == "no-live-worker"
    finally:
        resumed = docker(["start", worker])
        assert resumed.returncode == 0, resumed.stderr
    wait_for(
        "the deployment returning to READY",
        lambda: started.status() == "READY",
        timeout=180,
    )


def test_a_stopped_deployment_reports_stopped_and_starts_again(started: Deployment) -> None:
    """`docker compose stop` keeps containers and data; status then reports absence, not a fault."""
    before = probe_json(started, DURABLE_STATE)
    stopped = started.compose(["stop"])
    assert stopped.returncode == 0, stopped.stderr

    assert started.status() == "STOPPED"

    restarted = started.up()
    assert restarted.returncode == 0, restarted.stderr[-2000:]
    wait_until_ready(started)
    assert started.status() == "READY"
    assert probe_json(started, DURABLE_STATE) == before


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------


def test_restart_replaces_the_worker_identity_and_keeps_every_durable_record(started: Deployment) -> None:
    """The proof that run state crosses through PostgreSQL and the object store.

    A worker is identified to Prefect by a name it generates per process, so a
    replacement is a different worker as far as the server is concerned. Nothing
    it needs came from its own filesystem, so every durable record has to be
    exactly what it was.
    """
    before_state = probe_json(started, DURABLE_STATE)
    before_workers = online_worker_names(started)
    assert before_workers, "the deployment reported no online worker before restart"

    restarted = started.compose(["restart", "sync-api", "sync-worker"])
    assert restarted.returncode == 0, restarted.stderr[-2000:]

    after_workers = wait_for(
        "a replacement worker registering",
        lambda: [name for name in online_worker_names(started) if name not in before_workers],
        timeout=180,
    )
    assert after_workers, "no worker registered under a new identity"
    assert probe_json(started, DURABLE_STATE) == before_state


def test_a_plan_apply_and_separate_sync_run_through_the_replacement_worker(
    started: Deployment, principal: str, infrahub_fixture: dict[str, str]
) -> None:
    """The whole approved path, served by the worker that replaced the original.

    Plan, review the retained artifact, verify it, apply it against the real
    destination behind the reviewed checksum, and then run a separate managed
    sync that converges.
    """
    planted = plant_pending_update(infrahub_fixture)
    with api_client(started, principal) as client:
        config_id, registry_version = register(
            client, smoke_package(started.destination), "compose suite: configuration for the approved path"
        )
        run_id = submit(client, config_id, registry_version, "plan", "compose suite: plan after restart")
        await_phase(client, run_id, "planned")

        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        document = plan.json()
        assert document["summary"]["by_action"] == {"update": 1}, document["summary"]
        assert planted in json.dumps(document["operations"])

        verified = client.post(
            f"/runs/{run_id}/verify",
            headers=idempotency("compose-verify"),
            json={"reason": "compose suite: verify the plan"},
        )
        assert verified.status_code == 202, verified.text
        verification = await_verification(client, run_id)
        assert verification["outcome"] == "verified", verification
        assert verification["checksum"] == document["checksum"], verification
        assert verification["checksum_ok"] is True, verification

        applied = client.post(
            f"/runs/{run_id}/apply",
            headers=idempotency("compose-apply"),
            json={
                "expected_checksum": document["checksum"],
                "confirm_writes": True,
                "branch": SMOKE_BRANCH,
                "reason": "compose suite: apply the reviewed plan",
            },
        )
        assert applied.status_code == 202, applied.text
        await_phase(client, run_id, "applied")

        sync_id = submit(client, config_id, registry_version, "sync", "compose suite: separate sync after apply")
        # A managed sync ends in the same durable phase an approved apply does.
        await_phase(client, sync_id, "applied")

    assert destination_device_type(infrahub_fixture) == planted


# ---------------------------------------------------------------------------
# Ownership and teardown
# ---------------------------------------------------------------------------


def test_down_volumes_removes_only_this_project_and_leaves_a_foreign_volume(started: Deployment) -> None:
    """`docker compose down --volumes`, the command that replaced `reset`, takes this project only.

    Its containers, its network and its two named volumes go. The planted volume
    is named the way another project's would be, and Compose removes volumes by
    the names this file declares under this project, so it survives.
    """
    foreign = plant_foreign_volume()
    owned = [f"{started.project}_postgres-data", f"{started.project}_object-store-data"]
    try:
        assert all(volume_exists(volume) for volume in owned), owned

        removed = started.down(volumes=True)

        assert removed.returncode == 0, removed.stderr
        assert [volume for volume in owned if volume_exists(volume)] == []
        remaining = docker(["ps", "--all", "--filter", f"label={PROJECT_LABEL}={started.project}", "--quiet"])
        assert remaining.stdout.split() == []
        assert volume_exists(foreign), "down --volumes removed a volume this project does not own"
    finally:
        docker(["volume", "rm", "--force", foreign])


def test_the_next_up_after_down_volumes_is_a_cold_bootstrap(started: Deployment) -> None:
    """What `down --volumes` is for: the deployment comes back with nothing, and comes back working.

    A teardown that removed the volumes but left the deployment unable to start
    again would be a broken file, and one that started against surviving state
    would not have removed anything. Both are only visible from the other side of
    a real second start, so this drives one.

    Cold is asserted, not assumed: no runs, no artifacts, and an empty
    configuration registry. Before the teardown there were runs, artifacts, and
    the configurations this suite registered itself.
    """
    # The case above already removed it. Run on its own, this case has to remove
    # it too, or it would be restarting a deployment rather than bootstrapping one.
    removed = started.down(volumes=True)
    assert removed.returncode == 0, removed.stderr

    foreign = plant_foreign_volume()
    try:
        launched = started.up()
        assert launched.returncode == 0, launched.stderr[-3000:]
        wait_until_ready(started)
        assert started.status() == "READY"

        state = probe_json(started, DURABLE_STATE)
        assert state["runs"] == 0, state["runs"]
        assert state["objects"] == [], state["objects"]
        assert state["configuration_versions"] == [], state["configuration_versions"]
        assert volume_exists(foreign), "the cold start took a volume this project does not own"
    finally:
        started.down(volumes=True)
        docker(["volume", "rm", "--force", foreign])
