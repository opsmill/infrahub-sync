"""One deployment, driven through its whole life by the shipped entry point.

Everything else in this suite drives Compose directly, which is how those tests
stay readable but is not how an operator does anything. Here it is
`infrahub-sync-compose` itself, against a real daemon, from a bundle that has
never been initialized.

The cases run in file order against one module-scoped deployment, because a
lifecycle is a sequence and pretending otherwise would mean starting a stack per
case. Each case leaves the deployment as it found it, except the last three:
they are the teardown contract and what a bundle does after it, and they run
last on purpose.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND
from tests.compose.conftest import BUNDLE, DEFAULTS_FILE, FIXTURE_INFRAHUB_PORT, INSTANCE_LABEL
from tests.compose.lifecycle import (
    DURABLE_STATE,
    GATEWAY_PROBE_IMAGE,
    Deployment,
    api_client,
    await_phase,
    await_verification,
    container_reachable_host,
    docker,
    idempotency,
    online_worker_names,
    plant_pending_update,
    probe_json,
    register,
    smoke_package,
    wait_for,
)
from tests.compose.redaction import SECRETS, Captured, capture

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.compose

# Its own ports, so this deployment and the shared one can both be up.
API_PORT = "8031"
PREFECT_PORT = "4231"
START_TIMEOUT_SECONDS = 900
BUSY_OBSERVATION_SECONDS = 30

# Every service the bundle declares, used to bound what `logs` may print.
SERVICES = ("postgres", "db-bootstrap", "object-store", "prefect-server", "sync-bootstrap", "sync-api", "sync-worker")

# The settings `init` generates. Their values reach real processes, so the log
# sweep looks for them rather than for a string planted only to be found.
GENERATED_SETTINGS = (
    "INFRAHUB_SYNC_PRODUCT_PASSWORD",
    "INFRAHUB_SYNC_PREFECT_PASSWORD",
    "INFRAHUB_SYNC_S3_SECRET_KEY",
    "INFRAHUB_SYNC_S3_ACCESS_KEY",
)


def entry_point(bundle: Path, *arguments: str) -> Captured:
    """Run one lifecycle command exactly as an operator would.

    The entry point prints Compose's own output, so what comes back is retained
    Compose output and goes through the same redaction boundary as the rest.
    """
    return capture(
        [str(bundle / "infrahub-sync-compose"), *arguments],
        timeout=START_TIMEOUT_SECONDS,
        env=os.environ.copy(),
    )


def verdict(result: Captured) -> str:
    """Return the state word `status` printed, which is its last line."""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def family(result: Captured) -> str:
    """Return the refusal family a run reported, or '' when it did not refuse."""
    for line in result.stderr.splitlines():
        if line.startswith("infrahub-sync: "):
            return line.removeprefix("infrahub-sync: ").split(":", 1)[0]
    return ""


def setting(bundle: Path, name: str) -> str:
    """Read one operator setting the way the entry point does."""
    for line in (bundle / "operator.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    return ""


def generated_credentials(bundle: Path) -> dict[str, str]:
    """The credential values `init` generated for this deployment."""
    return {name: setting(bundle, name) for name in GENERATED_SETTINGS}


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


def plant_foreign_container(deployment: Deployment) -> str:
    """Create a container inside this Compose project under another instance's label.

    Selecting teardown targets by project name alone would take it; selecting by
    this instance's label does not.
    """
    created = docker(
        [
            "run",
            "--detach",
            "--label",
            f"com.docker.compose.project={deployment.project}",
            "--label",
            f"{INSTANCE_LABEL}=someone-else",
            "--entrypoint",
            "sleep",
            GATEWAY_PROBE_IMAGE,
            "600",
        ]
    )
    assert created.returncode == 0, created.stderr
    return created.stdout.strip()


def plant_foreign_volume() -> str:
    """Create a volume named the way another instance's would be."""
    name = f"infrahub-sync-{uuid.uuid4().hex[:16]}_postgres-data"
    created = docker(["volume", "create", "--label", f"{INSTANCE_LABEL}=someone-else", name])
    assert created.returncode == 0, created.stderr
    return name


@pytest.fixture(scope="module")
def started(
    sync_image: str, infrahub_fixture: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Deployment]:
    """A never-initialized copy of the bundle, taken to READY by the entry point.

    Its declared destination is the pinned Infrahub, named by an address that
    reaches the host from inside a container. Preflight probes the destination
    before anything in the bundle is running, which is what a real deployment's
    external destination is: reachable without help from the bundle itself.
    """
    bundle = tmp_path_factory.mktemp("lifecycle") / "compose"
    shutil.copytree(BUNDLE, bundle)
    destination = f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}"
    package = bundle / "configuration" / "qualification.yaml"
    package.write_text(
        package.read_text(encoding="utf-8").replace("http://infrahub.example.net:8000", destination),
        encoding="utf-8",
    )

    created = entry_point(bundle, "init")
    assert created.returncode == 0, created.stderr
    settings = bundle / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8")
        .replace("INFRAHUB_SYNC_IMAGE=REPLACE-ME", f"INFRAHUB_SYNC_IMAGE={sync_image}")
        .replace("INFRAHUB_API_TOKEN=REPLACE-ME", f"INFRAHUB_API_TOKEN={infrahub_fixture['token']}")
        + f"INFRAHUB_SYNC_IMAGE_PULL_POLICY=never\nINFRAHUB_SYNC_API_PORT={API_PORT}\n"
        f"INFRAHUB_SYNC_PREFECT_PORT={PREFECT_PORT}\n",
        encoding="utf-8",
    )
    instance = (bundle / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()
    # Started here rather than in the first case, so every case below runs
    # against a deployment that exists however few of them are selected.
    launched = entry_point(bundle, "start")
    assert launched.returncode == 0, launched.stderr + launched.stdout[-3000:]
    deployment = Deployment(
        instance=instance,
        environment_file=settings,
        environment_files=(DEFAULTS_FILE, settings, bundle / ".instance"),
        compose_file=bundle / "compose.yaml",
        bundle=bundle,
        destination=destination,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )
    yield deployment
    entry_point(bundle, "reset", instance)
    deployment.down(volumes=True)


@pytest.fixture(scope="module")
def principal(started: Deployment) -> str:
    """The bearer token `init` generated for this deployment's one principal."""
    tokens = json.loads(setting(started.bundle, "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"))
    return str(next(iter(tokens.values()))["token"])


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def test_the_entry_point_preflights_and_starts_a_ready_deployment(started: Deployment) -> None:
    """One operator sequence, end to end, with Docker answering every question.

    Readiness here is the API's own report of a registered worker. A stack whose
    containers were all running but whose worker never joined the pool would
    reach neither the `READY` line nor the end of `start`.
    """
    checked = entry_point(started.bundle, "preflight")
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert "preflight passed" in checked.stdout

    launched = entry_point(started.bundle, "start")
    assert launched.returncode == 0, launched.stderr + launched.stdout[-3000:]
    assert "the deployment is READY" in launched.stdout
    assert verdict(entry_point(started.bundle, "status")) == "READY"


def test_a_second_start_of_the_same_deployment_is_a_no_op(started: Deployment) -> None:
    """`start` is the command an operator repeats, so repeating it has to converge.

    It also proves the port check reads its own publication as owned: the first
    start left both loopback ports held by this instance's containers.
    """
    repeated = entry_point(started.bundle, "start")

    assert repeated.returncode == 0, repeated.stderr + repeated.stdout[-3000:]
    assert "already" in repeated.stdout
    assert "the deployment is READY" in repeated.stdout


# ---------------------------------------------------------------------------
# Endpoint-backed state
# ---------------------------------------------------------------------------


def test_a_started_deployment_reports_ready(started: Deployment) -> None:
    """READY is the API's answer about a registered worker, not a container count."""
    reported = entry_point(started.bundle, "status")

    assert reported.returncode == 0, reported.stdout + reported.stderr
    assert verdict(reported) == "READY"
    assert "live worker" in reported.stdout


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
    printed = entry_point(started.bundle, "logs")

    assert printed.returncode == 0, printed.stderr
    assert len(printed.stdout.splitlines()) <= 200 * len(SERVICES), len(printed.stdout.splitlines())
    # The raw stream, deliberately. The boundary would strip these values from
    # anything rendered, so sweeping what it returns would pass whether `logs`
    # printed a credential or not. Only the names of anything found are reported.
    leaked = SECRETS.leaked(printed.unredacted(), generated_credentials(started.bundle))
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
            lambda: verdict(entry_point(started.bundle, "status")) == "DEGRADED",
            timeout=180,
        )
        assert degraded
    finally:
        docker(["unpause", worker])
    wait_for(
        "the deployment returning to READY",
        lambda: verdict(entry_point(started.bundle, "status")) == "READY",
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
            lambda: verdict(entry_point(started.bundle, "status")) == "DEGRADED",
            timeout=180,
        )
        assert degraded
        # The half that makes this DEGRADED rather than STOPPED, asked of the
        # published surface an operator would use.
        answered = httpx.get(f"{started.api}/version", timeout=30)
        assert answered.status_code == 200, answered.text
        reported = entry_point(started.bundle, "status")
        assert reported.returncode == 3, reported.stdout
        assert "no live worker" in reported.stdout
    finally:
        resumed = docker(["start", worker])
        assert resumed.returncode == 0, resumed.stderr
    wait_for(
        "the deployment returning to READY",
        lambda: verdict(entry_point(started.bundle, "status")) == "READY",
        timeout=180,
    )


def test_a_stopped_deployment_reports_stopped_and_starts_again(started: Deployment) -> None:
    """Stop keeps containers and data; status then reports absence, not a fault."""
    stopped = entry_point(started.bundle, "stop")
    assert stopped.returncode == 0, stopped.stderr

    reported = entry_point(started.bundle, "status")
    assert verdict(reported) == "STOPPED"
    assert reported.returncode == 4, reported.stdout

    restarted = entry_point(started.bundle, "start")
    assert restarted.returncode == 0, restarted.stderr + restarted.stdout[-2000:]
    assert verdict(entry_point(started.bundle, "status")) == "READY"


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

    restarted = entry_point(started.bundle, "restart")
    assert restarted.returncode == 0, restarted.stderr + restarted.stdout[-2000:]

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
# Port occupancy
# ---------------------------------------------------------------------------
# The occupants a container scan cannot see. Each one is real: a process holding
# a loopback bind, and a container of no project of ours published on every
# address. Preflight asks the engine for the bind rather than reading anything
# back about who holds it, so both answer the same way.

# Ports this suite's own deployments do not use, so the occupant planted below is
# the only thing holding them.
SPARE_API_PORT = "8041"
SPARE_PREFECT_PORT = "4241"


@pytest.fixture
def unstarted(started: Deployment, sync_image: str, tmp_path: Path) -> Iterator[Path]:
    """A second copy of the bundle, initialized on spare ports and never started.

    Preflight is the whole subject here, and it refuses at the ports before it
    ever reaches the destination probe, so nothing in this bundle has to run.
    """
    bundle = tmp_path / "compose"
    shutil.copytree(BUNDLE, bundle)
    # The same real destination the started deployment uses, so a preflight that
    # gets past the ports is answered by something rather than refused for an
    # unrelated reason.
    package = bundle / "configuration" / "qualification.yaml"
    package.write_text(
        package.read_text(encoding="utf-8").replace("http://infrahub.example.net:8000", started.destination),
        encoding="utf-8",
    )
    created = entry_point(bundle, "init")
    assert created.returncode == 0, created.stderr
    settings = bundle / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8")
        .replace("INFRAHUB_SYNC_IMAGE=REPLACE-ME", f"INFRAHUB_SYNC_IMAGE={sync_image}")
        .replace("INFRAHUB_API_TOKEN=REPLACE-ME", f"INFRAHUB_API_TOKEN={setting(started.bundle, 'INFRAHUB_API_TOKEN')}")
        + f"INFRAHUB_SYNC_IMAGE_PULL_POLICY=never\nINFRAHUB_SYNC_API_PORT={SPARE_API_PORT}\n"
        f"INFRAHUB_SYNC_PREFECT_PORT={SPARE_PREFECT_PORT}\n",
        encoding="utf-8",
    )
    yield bundle
    # Nothing was started, so the only thing that could remain is a bind probe
    # that failed to be removed. The assertion below is what proves there is not.
    entry_point(bundle, "reset", (bundle / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip())


def probe_containers(bundle: Path) -> list[str]:
    """Every bind-probe container this bundle's instance could have left behind."""
    instance = (bundle / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()
    listed = docker(["ps", "--all", "--quiet", "--filter", f"name=infrahub-sync-portprobe-{instance}-"])
    assert listed.returncode == 0, listed.stderr
    return listed.stdout.split()


def test_preflight_refuses_a_bind_held_by_a_process_that_is_not_a_container(unstarted: Path) -> None:
    """A host listener publishes nothing and carries no label; it still holds the bind.

    This is the occupant the removed scan could never have found. Nothing about
    it appears in any container listing, and starting anyway would fail at the
    publication with an engine error instead of a refusal an operator can act on.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", int(SPARE_API_PORT)))
    listener.listen(1)
    try:
        refused = entry_point(unstarted, "preflight")

        assert family(refused) == "port-occupied", refused.stderr
        assert SPARE_API_PORT in refused.stderr
    finally:
        listener.close()
    assert probe_containers(unstarted) == [], "the bind probe left a container behind"


def test_preflight_refuses_a_bind_a_foreign_container_published_on_every_address(unstarted: Path) -> None:
    """Published on `0.0.0.0`, which covers loopback and is not the string a scan looked for.

    The removed check matched `127.0.0.1:<port>->` in a container's port column.
    A container published this way holds the same bind and matches nothing.
    """
    planted = docker(
        [
            "run",
            "--detach",
            "--publish",
            f"0.0.0.0:{SPARE_PREFECT_PORT}:5432",
            "--entrypoint",
            "sleep",
            GATEWAY_PROBE_IMAGE,
            "600",
        ]
    )
    assert planted.returncode == 0, planted.stderr
    container = planted.stdout.strip()
    try:
        refused = entry_point(unstarted, "preflight")

        assert family(refused) == "port-occupied", refused.stderr
        assert SPARE_PREFECT_PORT in refused.stderr
    finally:
        docker(["rm", "--force", container])
    assert probe_containers(unstarted) == [], "the bind probe left a container behind"


def test_preflight_passes_when_nothing_holds_either_bind(unstarted: Path) -> None:
    """The probe is disposable: it takes each bind, gives it straight back, and leaves.

    A probe that kept a port would make the very next check fail, and a probe
    that was left behind would make the next start fail, so the same case proves
    both the pass and the cleanup.
    """
    checked = entry_point(unstarted, "preflight")

    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert f"127.0.0.1:{SPARE_API_PORT} is free" in checked.stdout
    assert f"127.0.0.1:{SPARE_PREFECT_PORT} is free" in checked.stdout
    assert probe_containers(unstarted) == [], "the bind probe left a container behind"


# ---------------------------------------------------------------------------
# Ownership and teardown
# ---------------------------------------------------------------------------


def test_stop_refuses_a_foreign_resource_before_it_changes_anything(started: Deployment) -> None:
    """A container carrying this project's name but another instance's label.

    Selecting by project name alone would take it. The refusal happens before the
    first mutation, so the deployment is still running afterwards and so is the
    planted container.
    """
    planted = plant_foreign_container(started)
    try:
        refused = entry_point(started.bundle, "stop")

        assert refused.returncode == 1, refused.stdout
        assert family(refused) == "foreign-resource", refused.stderr
        assert docker(["inspect", "--format", "{{.State.Running}}", planted]).stdout.strip() == "true"
        assert verdict(entry_point(started.bundle, "status")) == "READY"
    finally:
        docker(["rm", "--force", planted])


def test_reset_refuses_without_the_exact_instance_identity(started: Deployment) -> None:
    """There is no forcing flag, so the confirmation is the whole gate."""
    for confirmation in ("", "wrong", started.instance[:-1], started.instance.upper()):
        refused = entry_point(started.bundle, "reset", confirmation)

        assert refused.returncode == 1, refused.stdout
        assert family(refused) == "confirmation-required", refused.stderr
    assert verdict(entry_point(started.bundle, "status")) == "READY"


def test_reset_removes_only_this_instance_and_leaves_a_foreign_volume(started: Deployment) -> None:
    """Confirmed reset takes this instance's containers, network, and volumes.

    The planted volume is named the way another instance's would be. Nothing
    here selects by name shape, so it survives, and the next start of this
    bundle would be a cold one.
    """
    foreign = plant_foreign_volume()
    owned = [f"{started.project}_postgres-data", f"{started.project}_object-store-data"]
    try:
        assert all(volume_exists(volume) for volume in owned), owned

        removed = entry_point(started.bundle, "reset", started.instance)

        assert removed.returncode == 0, removed.stderr + removed.stdout
        assert [volume for volume in owned if volume_exists(volume)] == []
        # Read straight from Docker: reset removed the identity file, so there is
        # no longer a Compose project for this bundle to ask about.
        remaining = docker(
            ["ps", "--all", "--filter", f"label=com.docker.compose.project={started.project}", "--quiet"]
        )
        assert remaining.stdout.split() == []
        assert volume_exists(foreign), "reset removed a volume this instance does not own"
    finally:
        docker(["volume", "rm", "--force", foreign])


def test_the_next_start_after_reset_is_a_cold_bootstrap(started: Deployment) -> None:
    """What reset is for: the bundle comes back with nothing, and comes back working.

    A reset that removed the volumes but left the deployment unable to start
    again would be a broken bundle, and one that started against surviving state
    would not have reset anything. Both are only visible from the other side of
    a real second start, so this drives one.

    Cold is asserted, not assumed: no runs, no artifacts, and exactly one
    registered configuration version — the bundled one this bootstrap just
    registered. Before the reset there were runs, artifacts, and a second
    configuration this suite registered itself.
    """
    bundle = started.bundle
    # The reset case above already took this bundle's identity. Run on its own,
    # this case has to take it too, or it would be restarting a deployment
    # rather than bootstrapping one.
    if (bundle / ".instance").is_file():
        previous = (bundle / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()
        removed = entry_point(bundle, "reset", previous)
        assert removed.returncode == 0, removed.output

    foreign = plant_foreign_volume()
    created = entry_point(bundle, "init")
    assert created.returncode == 0, created.stderr
    instance = (bundle / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()
    assert instance != started.instance, "reset left the identity its volumes were labelled with"
    settings = bundle / "operator.env"
    cold = Deployment(
        instance=instance,
        environment_file=settings,
        environment_files=(DEFAULTS_FILE, settings, bundle / ".instance"),
        compose_file=bundle / "compose.yaml",
        bundle=bundle,
        destination=started.destination,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )
    try:
        launched = entry_point(bundle, "start")
        assert launched.returncode == 0, launched.stderr + launched.stdout[-3000:]
        assert "the deployment is READY" in launched.stdout
        assert verdict(entry_point(bundle, "status")) == "READY"

        state = probe_json(cold, DURABLE_STATE)
        assert state["runs"] == 0, state["runs"]
        assert state["objects"] == [], state["objects"]
        assert len(state["configuration_versions"]) == 1, state["configuration_versions"]
        assert state["configuration_versions"][0][1] == 1, state["configuration_versions"]
        assert volume_exists(foreign), "the cold start took a volume this instance does not own"
    finally:
        entry_point(bundle, "reset", instance)
        cold.down(volumes=True)
        docker(["volume", "rm", "--force", foreign])
