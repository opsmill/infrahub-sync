"""No credential value reaches command output, a container log, or a service that does not need it.

FR-035, SC-013. One deployment of the root file runs on a `.env` in which every
credential setting holds a value generated for this run: the 17 adapter
variables, the S3 pair, the three PostgreSQL passwords, the Prefect auth string,
the service bearer tokens and the CLI token. The one exception is the Infrahub
destination token, which is the fixture's own: the plan and apply below need a
destination that accepts it. It is still searched for.

The case runs every command the operator pages document that a test can run,
in the order an operator would, including a plan and an apply through the `cli`
service. Then it searches:

* the raw output of each of those commands;
* every container log of the project;
* the environment of each service that `contracts/environment-variables.md`
  does not list as a receiver of a credential.

The searches read raw text (`Captured.unredacted`), because searching redacted
text would pass by construction. A finding names the setting and where it was
found, never the value.
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from tasks.preview import SMOKE_BRANCH
from tests.compose.conftest import FIXTURE_INFRAHUB_PORT, PREFECT_UI_OVERRIDE
from tests.compose.lifecycle import (
    FIXTURE_OVERRIDE,
    Deployment,
    api_client,
    await_verification,
    canary,
    container_reachable_host,
    docker,
    operator_environment,
    plant_pending_update,
    smoke_package,
    wait_for,
    worker_state,
)
from tests.compose.redaction import SECRETS, Captured, capture
from tests.compose.test_source_credentials import ADAPTER_CREDENTIAL_VARIABLES

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

pytestmark = pytest.mark.compose

# Ports of this module alone, so the deployment can run beside a developer's own
# and beside the other modules' deployments.
API_PORT = 8077
PREFECT_PORT = 4277

DESTINATION_TOKEN = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"  # noqa: S105 -- a setting name, not a credential
BEARER_TOKENS = "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"
CLI_TOKEN = "INFRAHUB_SYNC_API_TOKEN"  # noqa: S105 -- a setting name, not a credential
SYNC_CLIENTS = frozenset({"sync-bootstrap", "sync-api", "sync-worker"})

# The receivers table of `contracts/environment-variables.md`, "Receivers of each
# credential (root file)". A value in the environment of any other service is a
# finding. `object-store` receives the S3 pair because the bundled store runs.
RECEIVERS: dict[str, frozenset[str]] = {
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": frozenset({"postgres", "db-bootstrap"}),
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": frozenset({"db-bootstrap", *SYNC_CLIENTS}),
    "INFRAHUB_SYNC_PREFECT_PASSWORD": frozenset({"db-bootstrap", "prefect-server"}),
    "INFRAHUB_SYNC_PREFECT_AUTH_STRING": frozenset({"prefect-server", *SYNC_CLIENTS}),
    "INFRAHUB_SYNC_S3_ACCESS_KEY": frozenset({*SYNC_CLIENTS, "object-store"}),
    "INFRAHUB_SYNC_S3_SECRET_KEY": frozenset({*SYNC_CLIENTS, "object-store"}),
    BEARER_TOKENS: frozenset({"sync-api"}),
    CLI_TOKEN: frozenset({"cli"}),
    DESTINATION_TOKEN: frozenset({"sync-api", "sync-worker"}),
    **{name: frozenset({"sync-worker"}) for name in ADAPTER_CREDENTIAL_VARIABLES if name != DESTINATION_TOKEN},
}

# Documented commands this case does not run, and why. Every other command in
# `compose-deployment.mdx`, `quickstart-compose.mdx` and `operations/*.mdx` runs below.
NOT_RUN = {
    "docker login registry.opsmill.io": "needs a registry account; the image under test is already local",
    "curl -fsSLO https://raw.githubusercontent.com/...": "downloads a released file; the case uses the checkout's file",
    "the `.env` generator": "the case writes its own `.env` with every credential setting, not only the required ones",
    "docker compose config (without --quiet)": "documented as printing every value; the page warns not to share it",
    "docker inspect": "documented as printing every value; the case inspects each container privately instead",
    "ssh -N -L 4200:127.0.0.1:4200 <user>@<docker-host>": "needs a second computer",
    "python3 -m venv .schema-tools; pip install 'infrahub-sdk[ctl]'": "needs a package index; the suite's "
    "own `infrahubctl` runs instead",
    "infrahubctl schema load schema.yml --branch BRANCH_NAME": "changes the shared fixture's schema; the "
    "fixture loads its schema with the same command",
    "infrahubctl branch create BRANCH_NAME": "changes the shared fixture; the fixture creates its branch itself",
    "docker build -t infrahub-sync:compose-test . and the compose suite": "is this suite",
    "uname -s; uname -m; mkdir; chmod": "print no deployment value",
}

PACKAGE_TARGET = "/input/package.yaml"
RUN_ID = re.compile(r"^run_id: (\S+)$", re.MULTILINE)
CHECKSUM = re.compile(r"^plan_checksum: (\S+)$", re.MULTILINE)
CONFIG_ID = re.compile(r"^config_id: (\S+)$", re.MULTILINE)
STATUS_PROBE = "import httpx; print(httpx.get('http://sync-api:8000/status', timeout=5).json()['worker']['state'])"


def generated(prefix: str) -> str:
    """A fresh value for one setting, registered so no rendered stream carries it."""
    value = f"leak-{prefix}-{secrets.token_hex(12)}"
    SECRETS.register(value)
    return value


def write_environment(path: Path, values: Mapping[str, str]) -> None:
    path.write_text("".join(f"{name}={value}\n" for name, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def found_in(text: str, named: Mapping[str, str]) -> list[str]:
    """The setting names whose value is in the text; the text itself is never rendered."""
    return SECRETS.leaked(text, named)


def environment_of(identifier: str) -> str:
    """One container's environment, raw, for a private search."""
    inspected = docker(["inspect", identifier])
    assert inspected.returncode == 0, inspected.stderr
    return "\n".join(json.loads(inspected.unredacted())[0]["Config"]["Env"] or [])


def forbidden_for(service: str, named: Mapping[str, str]) -> dict[str, str]:
    """The settings a service must not hold.

    A value that two settings share, such as the CLI token that is also a token
    in the bearer-token map, is allowed where either setting is received.
    """
    allowed = {value for name, value in named.items() if service in RECEIVERS[name]}
    return {name: value for name, value in named.items() if service not in RECEIVERS[name] and value not in allowed}


@pytest.fixture(name="started")
def started_deployment(sync_image: str, infrahub_fixture: dict[str, str], tmp_path: Path) -> Iterator[Deployment]:
    """One deployment whose `.env` holds a generated value for every credential setting."""
    kinds = ("administrator", "product", "prefect", "prefect_auth", "object_store", "principal")
    planted = {kind: canary(kind) for kind in kinds}
    environment_file = operator_environment(
        tmp_path / "deployment",
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=planted,
        api_port=API_PORT,
    )
    values = dict(line.split("=", 1) for line in environment_file.read_text(encoding="utf-8").splitlines() if line)
    values["INFRAHUB_SYNC_S3_ACCESS_KEY"] = generated("access-key")
    values["INFRAHUB_SYNC_PREFECT_PORT"] = str(PREFECT_PORT)
    for name in ADAPTER_CREDENTIAL_VARIABLES:
        if name != DESTINATION_TOKEN:
            values[name] = generated(name.removeprefix("INFRAHUB_SYNC_CREDENTIAL_").lower())
    write_environment(environment_file, values)

    deployment = Deployment(
        instance=f"leaks{uuid.uuid4().hex[:12]}",
        environment_file=environment_file,
        overrides=(FIXTURE_OVERRIDE,),
        destination=f"http://{container_reachable_host()}:{FIXTURE_INFRAHUB_PORT}",
        api_port=API_PORT,
    )
    try:
        yield deployment
    finally:
        deployment.down(volumes=True)


class Sweep:
    """Every documented command's output, kept for a search by name."""

    def __init__(self, deployment: Deployment, named: Mapping[str, str]) -> None:
        self.deployment = deployment
        self.named = named
        self.findings: list[str] = []

    def keep(self, label: str, result: Captured, *, must_pass: bool = True) -> Captured:
        self.findings.extend(f"output of `{label}`: {name}" for name in found_in(result.unredacted(), self.named))
        if must_pass:
            assert result.returncode == 0, f"`{label}` failed: {result.output[-3000:]}"
        return result

    def compose(self, label: str, *argv: str, deployment: Deployment | None = None) -> Captured:
        return self.keep(label, (deployment or self.deployment).compose(list(argv)))

    def cli(self, *arguments: str, volume: Path | None = None) -> Captured:
        mount = ["-v", f"{volume}:{PACKAGE_TARGET}:ro"] if volume is not None else []
        argv = ["run", "--rm", "--no-deps", "-T", *mount, "cli", *arguments]
        return self.compose("docker compose " + " ".join(argv), *argv)

    def curl(self, label: str, *arguments: str) -> Captured:
        return self.keep(label, capture(["curl", "-sS", *arguments], timeout=120))

    def up(self, label: str, deployment: Deployment | None = None) -> None:
        self.keep(label, (deployment or self.deployment).up())
        wait_for("the deployment reporting a live worker", lambda: worker_state(self.deployment) in {"ready", "busy"})


def package_file(directory: Path, destination: str, *, fields: tuple[str, ...] | None = None) -> Path:
    """The smoke package as the documented `package.yml`, readable by the image user."""
    package = smoke_package(destination)
    if fields is not None:
        package["configuration"]["schema_mapping"][0]["fields"] = [{"name": name, "mapping": name} for name in fields]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "package.yml"
    # JSON is YAML, and needs no YAML writer here.
    path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    path.chmod(0o644)
    return path


def prefect_page(path: str) -> str:
    """One page of the Prefect UI the override publishes on loopback."""
    response = httpx.get(f"http://127.0.0.1:{PREFECT_PORT}{path}", timeout=10)
    response.raise_for_status()
    return response.text


def one(pattern: re.Pattern[str], result: Captured, what: str) -> str:
    match = pattern.search(result.stdout)
    assert match is not None, f"no {what} in: {result.output[-2000:]}"
    return match.group(1)


def install_and_start(sweep: Sweep, infrahub_fixture: Mapping[str, str], home: Path) -> None:
    """Install and start (compose-deployment.mdx, quickstart-compose.mdx)."""
    api = sweep.deployment.api
    sweep.keep("docker compose version --short", capture(["docker", "compose", "version", "--short"], timeout=60))
    sweep.compose("docker compose config --quiet", "config", "--quiet")
    sweep.compose("docker compose config --no-interpolate", "config", "--no-interpolate")
    sweep.up("docker compose up -d --wait")
    sweep.curl("curl -s /status", f"{api}/status")
    sweep.curl("curl --fail --silent --show-error /version", "--fail", f"{api}/version")
    sweep.compose("docker compose ps", "ps")
    # The Infrahub branch check, with the token read from `.env` as the page does.
    sweep.keep(
        "infrahubctl branch list",
        capture(
            [str(Path(sys.executable).parent / "infrahubctl"), "branch", "list"],
            timeout=120,
            env={
                "PATH": str(Path(sys.executable).parent),
                "HOME": str(home),
                "INFRAHUB_ADDRESS": infrahub_fixture["address"],
                "INFRAHUB_API_TOKEN": sweep.deployment.setting(DESTINATION_TOKEN),
            },
        ),
    )


def verify_with_curl(sweep: Sweep, run_id: str, directory: Path) -> None:
    """The documented `curl` calls with a token, through a private config file."""
    api = sweep.deployment.api
    curl_config = directory / "curl-config"
    curl_config.write_text(
        f'header = "Authorization: Bearer {sweep.deployment.setting(CLI_TOKEN)}"\n', encoding="utf-8"
    )
    curl_config.chmod(0o600)
    try:
        sweep.curl("curl --config CURL_CONFIG /configs", "--config", str(curl_config), f"{api}/configs")
        sweep.curl(
            "curl -X POST --config CURL_CONFIG /runs/RUN_ID/verify",
            *("-X", "POST", "--config", str(curl_config)),
            *("-H", f"Idempotency-Key: {uuid.uuid4()}", "-H", "Content-Type: application/json"),
            *("-d", '{"reason": "second opinion before applying"}'),
            f"{api}/runs/{run_id}/verify",
        )
    finally:
        curl_config.unlink()
    # The API runs one execution of a run at a time: an apply sent while the
    # verification still runs is refused with `run-execution-conflict`.
    with api_client(sweep.deployment, sweep.deployment.setting(CLI_TOKEN)) as client:
        await_verification(client, run_id)


def register_plan_and_apply(sweep: Sweep, infrahub_fixture: Mapping[str, str], directory: Path) -> None:
    """Register, plan, review, verify and apply (compose-deployment.mdx, "Run a sync")."""
    destination = sweep.deployment.destination
    sweep.cli("--help")
    sweep.cli("configs", "list")
    registered = sweep.cli(
        *("configs", "register", PACKAGE_TARGET, "--reason", "register my configuration"),
        volume=package_file(directory / "package", destination),
    )
    config_id = one(CONFIG_ID, registered, "config_id")
    sweep.cli("configs", "list")
    sweep.cli("configs", "show", config_id)
    sweep.cli("configs", "versions", config_id)
    sweep.cli("configs", "validate", config_id, "1")
    sweep.cli("configs", "show", config_id, "--version", "1")
    plant_pending_update(infrahub_fixture)
    selected = ("--config-id", config_id, "--version", "1", "--branch", SMOKE_BRANCH)
    run_id = one(RUN_ID, sweep.cli("diff", *selected, "--reason", "review initial sync"), "run_id")
    checksum = one(CHECKSUM, sweep.cli("runs", "plan", run_id, "--detail"), "plan_checksum")
    verify_with_curl(sweep, run_id, directory)
    sweep.cli(
        *("apply", run_id, "--expected-checksum", checksum, "--branch", SMOKE_BRANCH),
        *("--reason", "apply reviewed initial sync"),
    )
    sweep.cli("runs", "show", run_id)
    sweep.cli("runs", "results", run_id)
    sweep.cli("diff", *selected, "--reason", "verify unchanged source")
    sweep.cli(
        *("configs", "version", config_id, PACKAGE_TARGET, "--reason", "register edited configuration"),
        volume=package_file(directory / "edited", destination, fields=("name",)),
    )
    sweep.cli("configs", "show", config_id, "--version", "2")


def operate(sweep: Sweep, directory: Path) -> None:
    """Check, read logs, restart, stop, start, and open the Prefect UI (compose-deployment.mdx, operations/*.mdx)."""
    deployment = sweep.deployment
    sweep.compose(
        "docker compose run sync-bootstrap python -c STATUS",
        *("run", "--rm", "--no-deps", "-T", "sync-bootstrap", "python", "-c", STATUS_PROBE),
    )
    sweep.compose("docker compose logs --no-color --tail 200", "logs", "--no-color", "--tail", "200")
    sweep.compose(
        "docker compose logs --no-color --tail 200 sync-worker", "logs", "--no-color", "--tail", "200", "sync-worker"
    )
    sweep.compose("docker compose logs sync-bootstrap", "logs", "sync-bootstrap")
    sweep.compose("docker compose restart sync-api sync-worker", "restart", "sync-api", "sync-worker")
    wait_for("the worker live after a restart", lambda: worker_state(deployment) in {"ready", "busy"})
    sweep.compose("docker compose stop", "stop")
    sweep.compose("docker compose ps", "ps")
    sweep.up("docker compose up -d --wait")

    override = directory / "prefect-ui.yml"
    override.write_text(PREFECT_UI_OVERRIDE, encoding="utf-8")
    with_ui = Deployment(
        instance=deployment.instance,
        environment_file=deployment.environment_file,
        overrides=(FIXTURE_OVERRIDE, override),
        destination=deployment.destination,
        api_port=API_PORT,
    )
    sweep.up("docker compose -f docker-compose.yml -f prefect-ui.yml up -d --wait", with_ui)
    for path in ("/", "/api/health"):
        page = wait_for(f"the Prefect UI answering {path}", lambda path=path: prefect_page(path))
        sweep.findings.extend(f"Prefect UI page {path}: {name}" for name in found_in(str(page), sweep.named))
    sweep.up("docker compose up -d --wait")


def search_logs_and_environments(sweep: Sweep) -> None:
    """Every container log of the project, and each service's environment against the receivers table."""
    deployment, named = sweep.deployment, sweep.named
    sweep.compose("docker compose logs --no-color", "logs", "--no-color")
    for service, identifier in deployment.containers().items():
        held = found_in(environment_of(identifier), forbidden_for(service, named))
        sweep.findings.extend(f"environment of {service}: {name}" for name in held)
    cli_environment = deployment.compose(["run", "--rm", "--no-deps", "-T", "--entrypoint", "env", "cli"])
    assert cli_environment.returncode == 0, cli_environment.output[-2000:]
    held = found_in(cli_environment.unredacted(), forbidden_for("cli", named))
    sweep.findings.extend(f"environment of cli: {name}" for name in held)


def test_no_credential_reaches_output_logs_or_a_service_that_does_not_receive_it(
    started: Deployment,
    infrahub_fixture: dict[str, str],
    tmp_path: Path,
) -> None:
    deployment = started
    named = {name: deployment.setting(name) for name in RECEIVERS if name != BEARER_TOKENS}
    # The bearer-token map holds the CLI's token; that token is what would leak.
    named[BEARER_TOKENS] = deployment.setting(CLI_TOKEN)
    sweep = Sweep(deployment, named)

    install_and_start(sweep, infrahub_fixture, tmp_path)
    register_plan_and_apply(sweep, infrahub_fixture, tmp_path)
    operate(sweep, tmp_path)
    search_logs_and_environments(sweep)
    # The documented reset, last.
    sweep.compose("docker compose down --volumes --remove-orphans", "down", "--volumes", "--remove-orphans")

    assert not sweep.findings, f"credential values found (setting names only): {sweep.findings}"
