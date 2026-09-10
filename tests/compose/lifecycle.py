"""Drivers for the Docker-backed Compose bundle suites.

Everything here talks to a real daemon. The helpers exist so the tests read as
statements about the deployment rather than as Compose invocations, and so one
session-scoped stack can be shared by every case that needs one.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND
from tests.compose.conftest import BUNDLE, COMPOSE_FILE, DEFAULTS_FILE, INSTANCE_LABEL, compose, start_command
from tests.compose.redaction import SECRETS, Captured, capture

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

FIXTURE_OVERRIDE = Path(__file__).resolve().parent / "fixture-override.yaml"

# Every published surface the suite drives is on loopback, and the two ports
# below are deliberately not the bundle's own defaults: a developer's stack must
# be able to keep running while this suite has one of its own.
API_PORT = 8021
PREFECT_PORT = 4221

# The bundle's own PostgreSQL image, reused as a probe so no fourth external
# image has to be pinned for a two-command question.
GATEWAY_PROBE_IMAGE = "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"

# The declared readiness bound. Compose is given it as `--wait-timeout`, so it is
# Compose that decides a deployment did not come up; the subprocess cushion below
# only lets that decision be reported and the process reaped.
READY_TIMEOUT_SECONDS = 420
PROCESS_CUSHION_SECONDS = 60
RUN_TIMEOUT_SECONDS = 300
POLL_SECONDS = 3


# One throwaway value per session, planted in every credential the deployment
# resolves. Nothing the operator can see may carry it.
def canary(kind: str) -> str:
    """Return a throwaway secret whose appearance anywhere is a leak."""
    value = f"canary-{kind}-{secrets.token_hex(12)}"
    SECRETS.register(value)
    return value


def docker(argv: Sequence[str], *, timeout: int = 300) -> Captured:
    """Run one fixed-argv Docker command and return its redacted result.

    `docker inspect` prints every environment entry a container was given, so
    this is as much a credential-bearing stream as Compose's own.
    """
    return capture(["docker", *argv], timeout=timeout)


def daemon_available() -> bool:
    """Report whether a Docker daemon answers at all."""
    return docker(["info", "--format", "{{.ServerVersion}}"], timeout=60).returncode == 0


def inspect(reference: str, kind: str = "container") -> dict[str, Any]:
    """Return one Docker object's inspection document."""
    result = docker([kind, "inspect", reference])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)[0]


class Deployment:
    """One labelled Compose deployment of the bundle, driven the way an operator would."""

    def __init__(  # noqa: PLR0913 -- a deployment is named by every one of these independently
        self,
        *,
        instance: str,
        environment_file: Path,
        overrides: Sequence[Path] = (),
        compose_file: Path = COMPOSE_FILE,
        environment_files: Sequence[Path] = (),
        bundle: Path = BUNDLE,
        destination: str = "",
        api_port: int = API_PORT,
        prefect_port: int = PREFECT_PORT,
    ) -> None:
        self.instance = instance
        self.project = f"infrahub-sync-{instance}"
        # Where the entry point that owns this deployment lives, and the address
        # its declared configuration names. Both are the deployment's own facts,
        # so a test that has one has the other.
        self.bundle = bundle
        self.destination = destination
        # Each deployment publishes on its own loopback ports, so more than one
        # can be up at a time and no probe can reach the wrong one.
        self.api_port = api_port
        self.prefect_port = prefect_port
        self._environment_file = environment_file
        self._files = (compose_file, *overrides)
        self._environment_files = environment_files

    def compose(self, argv: Sequence[str], *, timeout: int = 900) -> Captured:
        """Run one Compose command against this deployment."""
        return compose(
            argv,
            project=self.project,
            files=self._files,
            env_files=self._environment_files or (DEFAULTS_FILE, self._environment_file),
            timeout=timeout,
        )

    def up(self, *services: str) -> Captured:
        """Start the named services, or all of them, and wait for their health gates.

        Compose is given the readiness bound. A subprocess timeout that expired
        first would kill Compose mid-start and leave the deployment in whatever
        state it had reached, which is a different answer than "not ready".
        """
        return self.compose(
            start_command(services, bound=READY_TIMEOUT_SECONDS),
            timeout=READY_TIMEOUT_SECONDS + PROCESS_CUSHION_SECONDS,
        )

    def container(self, service: str) -> str:
        """Return the one container identifier of a service, failing when it has none."""
        result = self.compose(["ps", "--all", "--quiet", service])
        assert result.returncode == 0, result.stderr
        identifiers = result.stdout.split()
        assert len(identifiers) == 1, f"{service} resolves to {identifiers}"
        return identifiers[0]

    def containers(self) -> dict[str, str]:
        """Return every container this project holds, keyed by its service name."""
        result = self.compose(["ps", "--all", "--quiet"])
        assert result.returncode == 0, result.stderr
        found = {}
        for identifier in result.stdout.split():
            labels = inspect(identifier)["Config"]["Labels"]
            found[labels["com.docker.compose.service"]] = identifier
        return found

    def logs(self, *services: str, tail: int = 200) -> Captured:
        """Return bounded log output for the named services.

        A `Captured`, not a string: a caller that renders it gets the redacted
        streams, and a sweep that searches it has to ask for the raw text.
        """
        return self.compose(["logs", "--no-color", "--tail", str(tail), *services])

    def down(self, *, volumes: bool = False) -> None:
        """Remove this deployment, and its data when asked."""
        argv = ["down", "--remove-orphans"]
        if volumes:
            argv.append("--volumes")
        self.compose(argv)

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def prefect(self) -> str:
        return f"http://127.0.0.1:{self.prefect_port}"


# The bound one wrapper command is given. A `start` runs preflight, a waited
# `up`, and then polls for a live worker, so it is the longest of them.
ENTRY_POINT_TIMEOUT_SECONDS = 900


def entry_point(bundle: Path, *arguments: str, timeout: int = ENTRY_POINT_TIMEOUT_SECONDS) -> Captured:
    """Run one lifecycle command exactly as an operator would.

    The entry point prints Compose's own output, so what comes back is retained
    Compose output and goes through the same redaction boundary as the rest.
    """
    return capture([str(bundle / "infrahub-sync-compose"), *arguments], timeout=timeout, env=os.environ.copy())


def wait_for(description: str, probe: Callable[[], object], *, timeout: int = READY_TIMEOUT_SECONDS) -> object:
    """Poll one probe until it returns a truthy value, or fail naming what it last said."""
    deadline = time.monotonic() + timeout
    last: object = "no attempt made"
    while time.monotonic() < deadline:
        try:
            answer = probe()
        except (httpx.HTTPError, AssertionError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if answer:
                return answer
            last = answer
        time.sleep(POLL_SECONDS)
    pytest.fail(f"{description} did not happen within {timeout}s (last: {last})")
    return None


def api_client(deployment: Deployment, token: str) -> httpx.Client:
    """An authenticated client for the deployment's published Sync API."""
    return httpx.Client(
        base_url=deployment.api,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )


def idempotency(prefix: str) -> dict[str, str]:
    """A fresh mutation key, so a repeat never replays an earlier response."""
    return {"Idempotency-Key": f"{prefix}-{secrets.token_hex(8)}"}


def worker_state(deployment: Deployment) -> str:
    """Return the worker state the unauthenticated status endpoint reports."""
    response = httpx.get(f"{deployment.api}/status", timeout=30)
    response.raise_for_status()
    return str(response.json()["worker"]["state"])


def register(client: httpx.Client, package: Mapping[str, Any], reason: str) -> tuple[str, int]:
    """Register one declared package and return the version it created."""
    response = client.post(
        "/configs",
        headers=idempotency("compose-register"),
        json={"package": dict(package), "reason": reason},
    )
    assert response.status_code == 201, response.text
    version = response.json()["version"]
    return version["config_id"], version["registry_version"]


def await_phase(client: httpx.Client, run_id: str, phase: str, *, timeout: int = RUN_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Poll one durable run until it reaches a phase, failing loudly when it fails.

    The phase each poll read is kept. A run that never arrives is diagnosed by
    what it was instead, and `wait_for` reports the probe's own answer -- which
    for an unmatched phase is the `None` that means "not yet", carrying nothing.
    """
    observed: list[str] = []

    def probe() -> dict[str, Any] | None:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        current = payload["run"]["phase"]
        observed.append(current)
        if "failed" in current:
            pytest.fail(f"run {run_id} failed while waiting for {phase!r}: {payload['run']}")
        return payload if current == phase else None

    try:
        return cast("dict[str, Any]", wait_for(f"run {run_id} reaching {phase!r}", probe, timeout=timeout))
    except pytest.fail.Exception:
        # Both outcomes arrive here: `wait_for` catches transport and assertion
        # errors, not what `pytest.fail` raises, so the probe's own failure
        # passes straight through. That one already carries the run payload,
        # which is the best evidence a failure produces, so only a run that never
        # arrived is described by the phase it last held.
        if observed and "failed" in observed[-1]:
            raise
        last = observed[-1] if observed else "no phase read"
        pytest.fail(
            f"run {run_id} reaching {phase!r} did not happen within {timeout}s "
            f"(last observed phase: {last!r}, polls: {len(observed)})"
        )


# What a failed run needs answered, and nothing else. Every part comes back from
# `Deployment.logs`, which is `--tail`-bounded and redacted at capture, so the
# artifact is built from output that already passed the boundary.
DIAGNOSTIC_SERVICES = ("sync-api", "sync-worker")
DIAGNOSTIC_TAIL = 200


def diagnostic_report(deployment: Deployment) -> str:
    """Return a bounded, redacted account of what the deployment's services said."""
    sections = [f"instance {deployment.instance}", f"project {deployment.project}"]
    sections.extend(
        f"--- {service} (last {DIAGNOSTIC_TAIL} lines) ---\n{deployment.logs(service, tail=DIAGNOSTIC_TAIL).output}"
        for service in DIAGNOSTIC_SERVICES
    )
    return "\n\n".join(sections)


def write_diagnostic(deployment: Deployment, destination: Path, *, named: Mapping[str, str]) -> str:
    """Write the diagnostic report, or withhold it, and return what happened.

    The sweep reads the bytes that would be retained rather than the parts they
    were assembled from: redacting each part and trusting the whole is how a
    concatenation boundary leaks. A registered value in those bytes withholds the
    file entirely -- an artifact nobody has is a result, and one that has left is
    not recoverable.
    """
    report = diagnostic_report(deployment)
    names = SECRETS.leaked(report, named)
    registered = any(value in report for value in SECRETS.values())
    if names or registered:
        found = ", ".join(names) if names else "a registered value this sweep cannot name"
        return f"diagnostic withheld: its bytes carry {found}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding="utf-8")
    return f"diagnostic written to {destination}"


def owned_volumes(deployment: Deployment) -> dict[str, str]:
    """Return the volumes labelled for this exact instance, and the label each carries."""
    result = docker(["volume", "ls", "--quiet", "--filter", f"label={INSTANCE_LABEL}={deployment.instance}"])
    assert result.returncode == 0, result.stderr
    found = {}
    for name in result.stdout.split():
        labels = inspect(name, kind="volume").get("Labels") or {}
        found[name] = labels.get(INSTANCE_LABEL, "")
    return found


def bundle_relative(path: Path) -> str:
    """Render one path the way a Compose file inside the bundle would name it."""
    return str(path.relative_to(BUNDLE))


def write_candidate_binding(bundle: Path, image: str) -> bytes:
    """Write into a bundle copy the binding a release would generate for this candidate.

    Two rules meet here. The record is produced by the release's own helper from
    the whole digest record the image gate wrote — its index name and index
    digest included — because a record synthesised from the candidate reference
    alone would be a shape this repository never ships, and the suite would be
    asserting against its own invention.

    And the environment stays what it is: `INFRAHUB_SYNC_IMAGE` names the test
    input, and this refuses rather than quietly writing a record that names
    something else. A helper that accepted the drift would turn the environment
    into the operator image-selection channel the binding exists to remove.
    """
    from tasks.image import read_digests, recorded_identity
    from tasks.release import BINDING_CONFIG_KEY, BINDING_MEMBER, image_binding

    record = read_digests()
    binding = image_binding(record, recorded_identity(record))
    consumed = dict(line.split("=", 1) for line in binding.decode("utf-8").splitlines())
    assert consumed[BINDING_CONFIG_KEY] == image, (
        "the generated binding names a candidate other than the image under test"
    )
    (bundle / BINDING_MEMBER).write_bytes(binding)
    return binding


def instance_identity(bundle: Path) -> str:
    """Return the identity a bundle's generated state file names."""
    from tests.compose.conftest import instance_setting

    return instance_setting(bundle, "INFRAHUB_SYNC_INSTANCE")


def operator_environment(
    directory: Path,
    *,
    instance: str,
    image: str,
    destination_token: str,
    canaries: Mapping[str, str],
) -> Path:
    """Write the operator-owned inputs one deployment runs on, and return the file.

    This is the shape `init` produces for a real operator: every credential in
    one gitignored file, except the database administrator password, which the
    official PostgreSQL image reads from a file of its own.
    """
    administrator = directory / "postgres-admin-password"
    administrator.write_text(canaries["administrator"] + "\n", encoding="utf-8")
    administrator.chmod(0o600)
    principals = json.dumps({"compose-suite": {"token": canaries["principal"], "administrator": True}})
    values = {
        "INFRAHUB_SYNC_INSTANCE": instance,
        "INFRAHUB_SYNC_IMAGE": image,
        # The image is loaded, never pulled: this suite runs the candidate the
        # image gate built at this exact head, which no registry holds.
        "INFRAHUB_SYNC_IMAGE_PULL_POLICY": "never",
        "INFRAHUB_SYNC_API_PORT": str(API_PORT),
        "INFRAHUB_SYNC_PREFECT_PORT": str(PREFECT_PORT),
        "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD_FILE": str(administrator),
        "INFRAHUB_SYNC_PRODUCT_PASSWORD": canaries["product"],
        "INFRAHUB_SYNC_PREFECT_PASSWORD": canaries["prefect"],
        "INFRAHUB_SYNC_DATABASE_URL": (f"postgresql://infrahub_sync:{canaries['product']}@postgres:5432/infrahub_sync"),
        "INFRAHUB_SYNC_PREFECT_DATABASE_URL": (
            f"postgresql+asyncpg://prefect:{canaries['prefect']}@postgres:5432/prefect"
        ),
        "INFRAHUB_SYNC_S3_ACCESS_KEY": "compose-suite-access-key",
        "INFRAHUB_SYNC_S3_SECRET_KEY": canaries["object_store"],
        "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": principals,
        "INFRAHUB_API_TOKEN": destination_token,
    }
    path = directory / "operator.env"
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    path.chmod(0o600)
    return path


# The one-off command form every durable-state probe uses. It runs in the Sync
# image, on the deployment's own network, so a probe reaches PostgreSQL, the
# object store, Prefect, and the API exactly the way the deployment's own
# processes do -- and needs no published port to do it.
PROBE_SERVICE = "sync-bootstrap"


def probe_json(deployment: Deployment, script: str) -> Any:  # noqa: ANN401 -- it returns whatever the probe printed
    """Run one Python snippet inside the deployment and return the JSON it printed."""
    result = deployment.compose(
        ["run", "--rm", "--no-deps", "-T", "--quiet-pull", PROBE_SERVICE, "python", "-c", script]
    )
    assert result.returncode == 0, result.output
    payload = [line for line in result.stdout.splitlines() if line.strip()]
    assert payload, result.output
    return json.loads(payload[-1])


def run_bootstrap(deployment: Deployment, service: str = PROBE_SERVICE) -> Captured:
    """Run one bootstrap job again against the state a previous run left."""
    return deployment.compose(["run", "--rm", "-T", "--quiet-pull", service])


def container_reachable_host() -> str:
    """Return an address a container reaches this host by, without a shipped route.

    The bundle carries no host route on purpose: a real deployment's destination
    is an external system it reaches by ordinary DNS. A test host is external in
    the same sense, but how a container names it differs — Docker Desktop
    resolves `host.docker.internal` on its own, and on Linux the address is the
    default gateway of the container's own network.
    """
    resolved = docker(
        ["run", "--rm", "--entrypoint", "getent", GATEWAY_PROBE_IMAGE, "hosts", "host.docker.internal"],
        timeout=120,
    )
    if resolved.returncode == 0 and resolved.stdout.split():
        return "host.docker.internal"
    route = docker(["run", "--rm", "--entrypoint", "ip", GATEWAY_PROBE_IMAGE, "route"], timeout=120)
    assert route.returncode == 0, route.stderr
    for line in route.stdout.splitlines():
        fields = line.split()
        if fields[:1] == ["default"] and len(fields) >= 3:
            return fields[2]
    pytest.fail(f"no container-reachable host address could be determined: {route.stdout}")
    return ""


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


def plant_pending_update(infrahub_fixture: Mapping[str, str]) -> str:
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


# The durable state a restart must carry across, read from inside the deployment
# so each answer comes from the store that holds it rather than from a cache the
# lifecycle command happens to have.
DURABLE_STATE = """
import json, os, boto3, httpx, psycopg
from infrahub_sync.product_store import configs
from infrahub_sync.service.storage import service_product_projection
projection = service_product_projection()
versions = []
for summary in configs.list_configs(projection=projection):
    for version in projection.list_configuration_versions(summary.config_id):
        versions.append([summary.config_id, version.registry_version, version.package_checksum])
with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM product_runs")
        runs = cursor.fetchone()[0]
client = boto3.client(
    "s3",
    endpoint_url=os.environ["INFRAHUB_SYNC_S3_ENDPOINT_URL"],
    region_name=os.environ["INFRAHUB_SYNC_S3_REGION"],
)
listing = client.list_objects_v2(Bucket=os.environ["INFRAHUB_SYNC_S3_BUCKET"])
deployments = httpx.post("http://prefect-server:4200/api/deployments/filter", json={}, timeout=30).json()
print(json.dumps({
    "configuration_versions": sorted(versions),
    "runs": runs,
    "objects": sorted(item["Key"] for item in listing.get("Contents", [])),
    "deployments": sorted((entry["id"], entry["name"]) for entry in deployments),
}))
"""

WORKERS = """
import json, httpx
pool = "infrahub-sync"
records = httpx.post(
    f"http://prefect-server:4200/api/work_pools/{pool}/workers/filter", json={}, timeout=30
).json()
print(json.dumps(sorted((record["name"], record["status"]) for record in records)))
"""


def online_worker_names(deployment: Deployment) -> list[str]:
    """Return the names Prefect currently reports as online in the pool."""
    return [name for name, status in probe_json(deployment, WORKERS) if status == "ONLINE"]


def await_verification(client: httpx.Client, run_id: str, *, timeout: int = RUN_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Poll until the read-only verification has been recorded against a run.

    Verification writes nothing and advances no phase -- it is a second opinion
    on a retained plan, not a state change -- so what proves it happened is the
    result it merges into the run's own record.
    """

    def probe() -> dict[str, Any] | None:
        response = client.get(f"/runs/{run_id}/results")
        assert response.status_code == 200, response.text
        return response.json()["results"].get("verification")

    return cast("dict[str, Any]", wait_for(f"run {run_id} recording its verification", probe, timeout=timeout))
