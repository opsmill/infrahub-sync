"""Fixtures for the Compose deployment suites.

Two suites live here and they need different things.

The contract suite reads the *resolved* Compose model — what `docker compose
config` produces after interpolation, extension merging, and defaulting — rather
than the file's own YAML. Compose itself is the parser, so a property proved
here is a property of what Compose will run, not of what the file appears to
say. It needs the Compose CLI and no daemon, so it runs in the ordinary unit
suite wherever Docker is installed.

The lifecycle suite drives a real stack through a Docker daemon. It is opt-in
under the `compose` marker, single-process, and shares one session-scoped stack.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # noqa: S404 -- for the failure types `capture` can raise
import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.compose.redaction import SECRETS, Captured, capture
from tests.docker_image import (
    IMAGE_REPOSITORY_ENV,
    IMAGE_VERSION_ENV,
    image_reference,
    missing_image_settings,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
# The operator deployment: one self-contained file at the repository root, the same
# file a release tag carries and an operator fetches.
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Turns a skipped Compose test into a failure, so a run that skipped everything
# cannot pass as green. The nightly `compose-suite` job passes it.
ZERO_SKIP_OPTION = "--compose-zero-skip"

# The project label Compose writes on everything it creates. It is what tells one
# deployment's containers and volumes from another's.
PROJECT_LABEL = "com.docker.compose.project"


@lru_cache(maxsize=1)
def interpolated_settings() -> frozenset[str]:
    """Every variable the Compose file interpolates.

    Removed from the caller's environment before each Compose call, so a value a
    developer exported cannot change what the suite resolves: the same reason the
    removed wrapper unset them.
    """
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    return frozenset(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", text))


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the flag the nightly `compose-suite` job runs this suite with."""
    parser.addoption(
        ZERO_SKIP_OPTION,
        action="store_true",
        default=False,
        help="Treat a skipped compose-marked case as a failure; the lifecycle matrix has no optional rows.",
    )


# Set for one test when its call phase failed, so a teardown can tell a failure
# from a pass without re-deriving it from the report.
FAILED = pytest.StashKey[bool]()
# Where a failed lifecycle run leaves what it saw. Git ignores it, and the gate
# uploads it only when a job has already failed.
DIAGNOSTIC_DIR = REPO_ROOT / ".diagnostics"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Under `--compose-zero-skip`, turn a skipped mandatory case into a failed one.

    Every row of the matrix is required, so a skip there is the claim quietly not
    being made. Changing the report itself is what makes the exit code follow,
    with nothing having to read what pytest printed. The marker decides which
    cases are mandatory, not `item.keywords`: this package is named `compose`, so
    everything under it carries that word either way.
    """
    del call
    report = yield
    if report.skipped and item.get_closest_marker("compose") is not None and item.config.getoption(ZERO_SKIP_OPTION):
        report.outcome = "failed"
        report.longrepr = f"{item.nodeid} skipped under {ZERO_SKIP_OPTION}, where every matrix case is required"
    if report.when == "call":
        item.stash[FAILED] = report.failed
    return report


@pytest.fixture(autouse=True)
def _diagnostic_on_failure(request: pytest.FixtureRequest) -> Iterator[None]:
    """Retain what a failed deployment's services said, for the run that failed.

    Only for a test that already built a deployment and already failed: nothing
    on the passing path changes, and no deployment is created to be diagnosed.
    """
    yield
    if not request.node.stash.get(FAILED, False):
        return
    # Imported here: lifecycle imports this module, so the cycle only closes at call time.
    from tests.compose.lifecycle import write_diagnostic

    for name in ("started", "deployment"):
        deployment = request.node.funcargs.get(name)
        if deployment is None:
            continue
        named = request.node.funcargs.get("canaries") or {}
        report = write_diagnostic(
            deployment,
            DIAGNOSTIC_DIR / f"{request.node.name}.log",
            named=dict(named),
        )
        # Attached to the teardown report rather than printed: pytest shows it
        # with the failure it belongs to, and nothing is emitted for a pass.
        request.node.add_report_section("teardown", "compose diagnostic", report)
        return


# The Sync services, and the two roles whose isolation from each other is the
# property the deployment exists to hold.
SYNC_SERVICES = ("sync-api", "sync-worker")
# The image's declared writable roots, restated here rather than imported from
# the image suite so this suite states the contract it checks.
SYNC_SCRATCH_ROOTS = (
    "/var/lib/infrahub-sync",
    "/var/lib/infrahub-sync/prefect",
    "/tmp/infrahub-sync",  # noqa: S108 -- the image's own fixed scratch root, not a shared /tmp path
)
SCRATCH_OPTIONS = "uid=10001,gid=10001,mode=0700"

# Non-secret stand-ins for every operator input the file requires. They only
# have to be well formed: the contract suite never starts a container, so no
# value here reaches a process. A resolved model is what is under test.
CONTRACT_ENVIRONMENT: dict[str, str] = {
    "INFRAHUB_TASKMANAGER_DB_PASSWORD": "contract-administrator-password",
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": "contract-product-password",
    "INFRAHUB_SYNC_PREFECT_AUTH_STRING": "contract-prefect-auth-string",
    "INFRAHUB_SYNC_S3_ACCESS_KEY": "contract-access-key",
    "INFRAHUB_SYNC_S3_SECRET_KEY": "contract-secret-key",
    "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": '{"contract": {"token": "contract-token-0123456789"}}',
    "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN": "contract-destination-token",
}


def compose(  # noqa: PLR0913 -- the file, its overrides, and its inputs vary independently
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    project: str | None = None,
    files: Sequence[Path] = (COMPOSE_FILE,),
    env_files: Sequence[Path] = (),
    timeout: int = 600,
    inherit_environment: bool = True,
) -> Captured:
    """Run one fixed-argv `docker compose` command against the operator file.

    Its output comes back through the redaction boundary, because Compose
    interpolates every credential the file names and prints them back in
    `config`, in an interpolation refusal, and in whatever a failing `up`
    quotes.

    With no `env_files`, an empty one is named instead: otherwise Compose would
    load a `.env` beside the file, and a developer's own would feed the suite.
    """
    command = ["docker", "compose"]
    if project is not None:
        command += ["--project-name", project]
    for env_file in env_files or (Path(os.devnull),):
        command += ["--env-file", str(env_file)]
    for path in files:
        command += ["--file", str(path)]
    if inherit_environment:
        excluded = interpolated_settings()
        base_environment = {name: value for name, value in os.environ.items() if name not in excluded}
    else:
        base_environment = {name: os.environ[name] for name in ("PATH", "HOME", "DOCKER_CONFIG") if name in os.environ}
    return capture(
        [*command, *argv],
        timeout=timeout,
        cwd=REPO_ROOT,
        env={**base_environment, **(environment or {})},
    )


def start_command(services: Sequence[str], *, bound: int) -> list[str]:
    """The waited `up` this suite runs, with the readiness bound given to Compose.

    `compose` prefixes it with `-f docker-compose.yml --env-file <the deployment's
    .env>`, so a deployment starts the way an operator's does:
    `docker compose -f … --env-file … up -d --wait`.

    `--wait-timeout` is the whole point. A subprocess timeout is a cushion for
    Compose's own exit, not a readiness rule: expiring first would kill Compose
    part-way through a start and leave the deployment in whatever state it had
    reached, which is a different answer than "it did not become ready".
    """
    return ["up", "--detach", "--wait", "--wait-timeout", str(bound), "--quiet-pull", *services]


def resolve(environment: Mapping[str, str], *, files: Sequence[Path] = (COMPOSE_FILE,)) -> dict[str, Any]:
    """Return the model Compose resolves the file to, or fail naming its refusal."""
    result = compose(["config", "--format", "json"], environment=environment, files=files)
    if result.returncode != 0:
        pytest.fail(f"docker compose config refused the file: {result.stderr.strip()}")
    return json.loads(result.stdout)


def resolve_privately(environment: Mapping[str, str], *, files: Sequence[Path] = (COMPOSE_FILE,)) -> dict[str, Any]:
    """Return the resolved model built from raw output, for value-level checks.

    `resolve` reads redacted output, which is right for every property about
    shape: a credential must not reach a value the suite can render. But a
    credential-named setting is redacted by name, so a caller asking *which*
    value the model carries reads `[redacted]` there and can prove nothing.

    This is the sanctioned exception, and it comes with an obligation: a caller
    compares privately and reports names. Nothing returned here may be rendered
    into an assertion message.
    """
    result = compose(["config", "--format", "json"], environment=environment, files=files)
    if result.returncode != 0:
        pytest.fail(f"docker compose config refused the file: {result.stderr.strip()}")
    return json.loads(result.unredacted())


@lru_cache(maxsize=1)
def _compose_available() -> str | None:
    """Return the installed Compose version, or None when the CLI is absent."""
    try:
        probe = capture(["docker", "compose", "version", "--short"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return probe.stdout.strip() or None if probe.returncode == 0 else None


@pytest.fixture(scope="session")
def compose_version() -> str:
    """Skip the contract suite where the Compose CLI is not installed."""
    version = _compose_available()
    if version is None:
        pytest.skip("docker compose is not installed; the Compose contract suite needs the CLI, not a daemon")
    return version


@pytest.fixture(scope="session")
def contract_environment() -> dict[str, str]:
    """Every operator input the file requires, and nothing else."""
    return dict(CONTRACT_ENVIRONMENT)


@pytest.fixture(scope="session")
def model(compose_version: str, contract_environment: dict[str, str]) -> dict[str, Any]:
    """The resolved file every contract test reads."""
    del compose_version
    return resolve(contract_environment)


# The one profile the file declares, and the service behind it.
CLI_PROFILE = "cli"


@pytest.fixture(scope="session")
def cli_model(compose_version: str, contract_environment: dict[str, str]) -> dict[str, Any]:
    """The resolved file with the CLI profile named, which is the only way to get it."""
    del compose_version
    result = compose(
        ["--profile", CLI_PROFILE, "config", "--format", "json"],
        environment=contract_environment,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config refused the file: {result.stderr.strip()}")
    return json.loads(result.stdout)


def service(model: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return one resolved service, failing rather than skipping when it is gone."""
    services = model["services"]
    assert name in services, f"the file declares no {name} service; it declares {sorted(services)}"
    return dict(services[name])


def mount_sources(definition: Mapping[str, Any]) -> set[str]:
    """Return every filesystem source one resolved service mounts."""
    return {str(mount["source"]) for mount in definition.get("volumes") or [] if mount.get("source") is not None}


# ---------------------------------------------------------------------------
# The Docker-backed suites
# ---------------------------------------------------------------------------
# These need a daemon, an already-built Sync image named by
# INFRAHUB_SYNC_DOCKER_IMAGE and VERSION, and — for the mandatory
# managed rows — a real Infrahub. They are opt-in under the `compose` marker and
# share one session-scoped stack, so they run single-process.

FIXTURE_PROJECT = "infrahub-sync-suite-fixture"
# Sync deployments join the fixture's Compose network and use its task manager and
# that task manager's PostgreSQL server, as an operator deployment joins Infrahub's.
FIXTURE_NETWORK = f"{FIXTURE_PROJECT}_default"
FIXTURE_TASK_MANAGER_DB = f"{FIXTURE_PROJECT}-task-manager-db-1"
# The fixture's own host ports for the task manager and its database, so a
# developer's preview stack can keep running beside the suite.
FIXTURE_PREFECT_PORT = "4262"
FIXTURE_POSTGRES_PORT = "5462"
# Deliberately not the development stack's own port: a developer's preview keeps
# working while this suite runs its own copy of the same pinned release.
FIXTURE_INFRAHUB_PORT = "8081"
# The declared readiness bound, and it is Compose that is given it: `--wait-timeout`
# is what decides the fixture is not coming up. The subprocess timeout below is a
# cushion for Compose's own exit and cleanup, never a second readiness rule.
FIXTURE_READY_SECONDS = 420
FIXTURE_PROCESS_CUSHION_SECONDS = 60
FIXTURE_SCHEMA_CONVERGE_SECONDS = 120


@pytest.fixture(scope="session")
def docker_daemon() -> None:
    """Skip the Docker-backed suites where no daemon answers."""
    from tests.compose.lifecycle import daemon_available

    if not daemon_available():
        pytest.skip("no Docker daemon; the compose-marked suite drives a real one")


# How to name the image under test, quoted wherever the suite refuses to guess one.
IMAGE_USAGE = (
    "build one with `docker build -t infrahub-sync:compose-test .` and run "
    f"`{IMAGE_REPOSITORY_ENV}=infrahub-sync {IMAGE_VERSION_ENV}=compose-test uv run pytest -m compose tests/compose`"
)


def image_under_test() -> str:
    """Return `<repository>:<tag>` from the two settings the Compose file reads, or fail.

    A failure, not a skip: a compose run with no image named has tested nothing,
    and reporting that as skipped would let it pass as green.
    """
    reference = image_reference()
    if reference is None:
        missing = missing_image_settings()
        pytest.fail(f"{' and '.join(missing)} unset: the compose suite needs the image under test; {IMAGE_USAGE}")
    return reference


@pytest.fixture(scope="session")
def sync_image(docker_daemon: None) -> str:
    """The reference of the already-built Sync image under test.

    Never built here, and never pulled: the deployment's default pull policy only
    pulls an image the engine does not hold, so an image that is absent here would
    be fetched from a registry and the suite would test something else. It is
    refused instead, naming the reference.
    """
    del docker_daemon
    reference = image_under_test()
    held = capture(["docker", "image", "inspect", "--format", "{{.Id}}", reference], timeout=60)
    if held.returncode != 0:
        pytest.fail(f"this Docker engine holds no image {reference}; {IMAGE_USAGE}")
    return reference


def _infrahub_environment(task_manager_db_password: str) -> dict[str, str]:
    """The settings the pinned fixture stack and its seed both read."""
    from tasks.preview import (
        ENV_FILE,
        load_preview_env,
    )

    del ENV_FILE
    values = load_preview_env()
    return {
        **values,
        "COMPOSE_PROJECT_NAME": FIXTURE_PROJECT,
        "PREVIEW_INFRAHUB_PORT": FIXTURE_INFRAHUB_PORT,
        "PREVIEW_PREFECT_PORT": FIXTURE_PREFECT_PORT,
        "PREVIEW_STORAGE_POSTGRES_PORT": FIXTURE_POSTGRES_PORT,
        # The administrator canary: the Sync deployments' database bootstrap presents
        # it, so it must never surface in their output.
        "INFRAHUB_TASKMANAGER_DB_PASSWORD": task_manager_db_password,
        # The development stack publishes on loopback, which the bundle's worker
        # cannot reach: on Linux its host route is the Docker bridge gateway. This
        # session-scoped fixture is removed afterwards, so it is widened here alone.
        "PREVIEW_INFRAHUB_BIND_ADDRESS": "0.0.0.0",  # noqa: S104 - test fixture, see above.
    }


@pytest.fixture(scope="session")
def infrahub_fixture(sync_image: str, canaries: dict[str, str]) -> Iterator[dict[str, str]]:
    """One pinned Infrahub 1.11.4, seeded with the smoke schema, device, and branch.

    Test infrastructure, never a service of the operator deployment: it is started
    from the development stack's own pinned files, published on its own port, and
    removed with its volumes afterwards. Only `infrahub-server`, `task-worker`
    and their dependency closure, which includes the task manager and its
    PostgreSQL server every Sync deployment of the suite runs on, are started; the development stack's own
    `sync-*` services are not part of what this fixture provides.

    Depends on `sync_image`, which carries the daemon prerequisite with it: the
    image prerequisite has to pass before anything here is started.
    """
    del sync_image
    from tasks.preview import COMPOSE_FILES, ENV_FILE, SCHEMA_FILE, ensure_smoke_branch

    values = _infrahub_environment(canaries["administrator"])
    # The fixture's administrator token is the destination credential the deployment
    # resolves, so it is a secret of this session like any generated canary.
    SECRETS.register(values["INFRAHUB_INITIAL_ADMIN_TOKEN"])
    address = f"http://127.0.0.1:{FIXTURE_INFRAHUB_PORT}"
    environment = {**os.environ, **values}
    base = ["docker", "compose", "--project-name", FIXTURE_PROJECT, "--env-file", str(ENV_FILE)]
    for path in COMPOSE_FILES:
        base += ["--file", str(path)]

    def run(argv: Sequence[str], *, timeout: int) -> Captured:
        return capture([*base, *argv], timeout=timeout, env=environment)

    started = run(
        start_command(("infrahub-server", "task-worker"), bound=FIXTURE_READY_SECONDS),
        timeout=FIXTURE_READY_SECONDS + FIXTURE_PROCESS_CUSHION_SECONDS,
    )
    if started.returncode != 0:
        run(["down", "--volumes", "--remove-orphans"], timeout=FIXTURE_READY_SECONDS)
        pytest.fail(f"the pinned Infrahub fixture did not start: {started.stderr[-2000:]}")
    try:
        seed_environment = {
            "INFRAHUB_ADDRESS": address,
            "INFRAHUB_API_TOKEN": values["INFRAHUB_INITIAL_ADMIN_TOKEN"],
        }
        loaded = capture(
            [
                # The console script sits beside this interpreter, and the suite
                # is not started from an activated environment.
                str(Path(sys.executable).parent / "infrahubctl"),
                "schema",
                "load",
                "--wait",
                str(FIXTURE_SCHEMA_CONVERGE_SECONDS),
                str(SCHEMA_FILE),
            ],
            timeout=FIXTURE_READY_SECONDS,
            env={**os.environ, **seed_environment},
        )
        assert loaded.returncode == 0, f"the fixture schema did not load: {loaded.output}"
        ensure_smoke_branch(seed_environment)
        yield {
            "address": address,
            "token": values["INFRAHUB_INITIAL_ADMIN_TOKEN"],
            "network": FIXTURE_NETWORK,
            "task_manager_db": FIXTURE_TASK_MANAGER_DB,
        }
    finally:
        run(["down", "--volumes", "--remove-orphans"], timeout=FIXTURE_READY_SECONDS)


@pytest.fixture(scope="session")
def canaries() -> dict[str, str]:
    """One throwaway value per credential the deployment resolves.

    Every one of these reaches a real process. Their appearance in a log, an
    error, a rendered file, or retained Compose output is the leak the secret
    rows look for, and a deployment whose credentials were constants could not
    prove anything about that.
    """
    from tests.compose.lifecycle import canary

    planted = {
        kind: canary(kind)
        for kind in ("administrator", "product", "prefect", "prefect_auth", "object_store", "principal")
    }
    # Registered at the boundary, so no retained Compose or Docker stream can
    # render one. The sweeps still search the raw streams for these values.
    SECRETS.register(*planted.values())
    return planted


@pytest.fixture(scope="module")
def deployment(
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Any]:
    """One started, converged, endpoint-ready deployment shared by the whole module.

    Module-scoped: one task manager holds one Sync deployment, because the service's
    Prefect deployment name is fixed. Modules run one after another, so at most one
    Sync deployment exists on the fixture's task manager at a time.

    The destination is the pinned fixture, reached through the host gateway the
    test-only override adds. The operator file itself neither joins that stack's
    network nor carries the route.
    """
    from tests.compose.lifecycle import (
        FIXTURE_OVERRIDE,
        Deployment,
        operator_environment,
        wait_for,
        worker_state,
    )

    instance = f"suite{uuid4().hex[:16]}"
    directory = tmp_path_factory.mktemp("compose-deployment")
    environment_file = operator_environment(
        directory,
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
        instance=instance,
        infrahub_network=infrahub_fixture["network"],
    )
    started = Deployment(
        instance=instance,
        environment_file=environment_file,
        overrides=(FIXTURE_OVERRIDE,),
        task_manager_database_container=infrahub_fixture["task_manager_db"],
    )
    result = started.up("sync-api", "sync-worker")
    if result.returncode != 0:
        # Bootstrap first: it is the step every other service waits on, and a
        # tail of the whole project is almost entirely PostgreSQL's own startup.
        detail = started.logs("sync-bootstrap", "sync-api", "sync-worker", tail=80)
        started.down(data=True)
        pytest.fail(f"the deployment did not start: {result.stderr[-1500:]}\n{detail.output[-4000:]}")
    try:
        wait_for(
            "the deployment reporting a live worker",
            lambda: worker_state(started) in {"ready", "busy"},
        )
        yield started
    finally:
        started.down(data=True)
