"""Fixtures for the Compose bundle suites.

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
import subprocess  # noqa: S404 -- fixed argv Compose and Docker probes for the bundle gate
import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "deploy" / "compose"
COMPOSE_FILE = BUNDLE / "compose.yaml"
DEFAULTS_FILE = BUNDLE / "defaults.conf"
BUNDLED_CONFIGURATION = BUNDLE / "configuration" / "qualification.yaml"

INSTANCE_LABEL = "io.infrahub-sync.instance"
BUNDLE_LABEL = "io.infrahub-sync.bundle"

# The Sync services, and the two roles whose isolation from each other is the
# property the bundle exists to hold.
SYNC_SERVICES = ("sync-api", "sync-worker")
# The image's declared writable roots, restated here rather than imported from
# the image suite so this suite states the contract it checks.
SYNC_SCRATCH_ROOTS = (
    "/var/lib/infrahub-sync",
    "/var/lib/infrahub-sync/prefect",
    "/tmp/infrahub-sync",  # noqa: S108 -- the image's own fixed scratch root, not a shared /tmp path
)
SCRATCH_OPTIONS = "uid=10001,gid=10001,mode=0700"

# Non-secret stand-ins for every operator input the bundle requires. They only
# have to be well formed: the contract suite never starts a container, so no
# value here reaches a process. A resolved model is what is under test.
CONTRACT_ENVIRONMENT: dict[str, str] = {
    "INFRAHUB_SYNC_INSTANCE": "contract-0000000000000000",
    "INFRAHUB_SYNC_IMAGE": "sha256:" + "0" * 64,
    "INFRAHUB_SYNC_DATABASE_URL": "postgresql://infrahub_sync:contract@postgres:5432/infrahub_sync",
    "INFRAHUB_SYNC_PREFECT_DATABASE_URL": "postgresql+asyncpg://prefect:contract@postgres:5432/prefect",
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": "contract-product-password",
    "INFRAHUB_SYNC_PREFECT_PASSWORD": "contract-prefect-password",
    "INFRAHUB_SYNC_S3_ACCESS_KEY": "contract-access-key",
    "INFRAHUB_SYNC_S3_SECRET_KEY": "contract-secret-key",
    "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": '{"contract": {"token": "contract-token-0123456789"}}',
    "INFRAHUB_API_TOKEN": "contract-destination-token",
}


def compose(  # noqa: PLR0913 -- the bundle, its overrides, and its inputs vary independently
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    project: str | None = None,
    files: Sequence[Path] = (COMPOSE_FILE,),
    env_files: Sequence[Path] = (DEFAULTS_FILE,),
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """Run one fixed-argv `docker compose` command against the bundle."""
    command = ["docker", "compose"]
    if project is not None:
        command += ["--project-name", project]
    for env_file in env_files:
        command += ["--env-file", str(env_file)]
    for path in files:
        command += ["--file", str(path)]
    return subprocess.run(  # noqa: S603 -- fixed argv
        [*command, *argv],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        cwd=BUNDLE,
        env={**os.environ, **(environment or {})},
    )


def resolve(environment: Mapping[str, str], *, files: Sequence[Path] = (COMPOSE_FILE,)) -> dict[str, Any]:
    """Return the model Compose resolves the bundle to, or fail naming its refusal."""
    result = compose(["config", "--format", "json"], environment=environment, files=files)
    if result.returncode != 0:
        pytest.fail(f"docker compose config refused the bundle: {result.stderr.strip()}")
    return json.loads(result.stdout)


@lru_cache(maxsize=1)
def _compose_available() -> str | None:
    """Return the installed Compose version, or None when the CLI is absent."""
    try:
        probe = subprocess.run(
            ["docker", "compose", "version", "--short"],  # noqa: S607 -- resolved from the gate's PATH
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
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
def contract_environment(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Every operator input the bundle requires, with the one file input placed.

    The administrator password is a file input, so Compose resolves the bundle
    only once one exists. Placing a throwaway file under the test's own root is
    what keeps this suite from depending on an operator's untracked secret.
    """
    secret = tmp_path_factory.mktemp("compose-contract") / "postgres-admin-password"
    secret.write_text("contract-administrator-password\n", encoding="utf-8")
    return {**CONTRACT_ENVIRONMENT, "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD_FILE": str(secret)}


@pytest.fixture(scope="session")
def model(compose_version: str, contract_environment: dict[str, str]) -> dict[str, Any]:
    """The resolved bundle every contract test reads."""
    del compose_version
    return resolve(contract_environment)


def service(model: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return one resolved service, failing rather than skipping when it is gone."""
    services = model["services"]
    assert name in services, f"the bundle declares no {name} service; it declares {sorted(services)}"
    return dict(services[name])


def mount_sources(definition: Mapping[str, Any]) -> set[str]:
    """Return every filesystem source one resolved service mounts."""
    return {str(mount["source"]) for mount in definition.get("volumes") or [] if mount.get("source") is not None}


# ---------------------------------------------------------------------------
# The Docker-backed suites
# ---------------------------------------------------------------------------
# These need a daemon, an already-built Sync image, and — for the mandatory
# managed rows — a real Infrahub. They are opt-in under the `compose` marker and
# share one session-scoped stack, so they run single-process.

IMAGE_REFERENCE_ENV = "INFRAHUB_SYNC_IMAGE"
FIXTURE_PROJECT = "infrahub-sync-compose-fixture"
# Deliberately not the development stack's own port: a developer's preview keeps
# working while this suite runs its own copy of the same pinned release.
FIXTURE_INFRAHUB_PORT = "8081"
FIXTURE_READY_SECONDS = 420
FIXTURE_SCHEMA_CONVERGE_SECONDS = 120


@pytest.fixture(scope="session")
def docker_daemon() -> None:
    """Skip the Docker-backed suites where no daemon answers."""
    from tests.compose.lifecycle import daemon_available

    if not daemon_available():
        pytest.skip("no Docker daemon; run this suite through `uv run invoke compose.lifecycle`")


@pytest.fixture(scope="session")
def sync_image(docker_daemon: None) -> str:
    """The immutable reference of the already-built Sync image under test.

    Never built here. A suite that builds its own subject proves nothing about
    the artifact the gate ships, so the reference arrives from the task that
    built and loaded it.
    """
    del docker_daemon
    reference = os.environ.get(IMAGE_REFERENCE_ENV, "").strip()
    if not reference:
        pytest.skip(f"{IMAGE_REFERENCE_ENV} is unset; run this suite through `uv run invoke compose.lifecycle`")
    return reference


def _infrahub_environment() -> dict[str, str]:
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
    }


@pytest.fixture(scope="session")
def infrahub_fixture(docker_daemon: None) -> Iterator[dict[str, str]]:
    """One pinned Infrahub 1.10.6, seeded with the smoke schema, device, and branch.

    Test infrastructure, never a service of the release bundle: it is started
    from the development stack's own pinned files, published on its own port, and
    removed with its volumes afterwards. Only `infrahub-server`, `task-worker`
    and their dependency closure are started; the development stack's own
    `sync-*` services are not part of what this fixture provides.
    """
    del docker_daemon
    from tasks.preview import COMPOSE_FILES, ENV_FILE, SCHEMA_FILE, ensure_smoke_branch

    values = _infrahub_environment()
    address = f"http://127.0.0.1:{FIXTURE_INFRAHUB_PORT}"
    environment = {**os.environ, **values}
    base = ["docker", "compose", "--project-name", FIXTURE_PROJECT, "--env-file", str(ENV_FILE)]
    for path in COMPOSE_FILES:
        base += ["--file", str(path)]

    def run(argv: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- fixed argv
            [*base, *argv], capture_output=True, text=True, check=False, timeout=timeout, env=environment
        )

    started = run(
        ["up", "--detach", "--wait", "--quiet-pull", "infrahub-server", "task-worker"],
        timeout=FIXTURE_READY_SECONDS * 2,
    )
    if started.returncode != 0:
        run(["down", "--volumes", "--remove-orphans"], timeout=FIXTURE_READY_SECONDS)
        pytest.fail(f"the pinned Infrahub fixture did not start: {started.stderr[-2000:]}")
    try:
        seed_environment = {
            "INFRAHUB_ADDRESS": address,
            "INFRAHUB_API_TOKEN": values["INFRAHUB_INITIAL_ADMIN_TOKEN"],
        }
        loaded = subprocess.run(  # noqa: S603 -- fixed argv
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
            capture_output=True,
            text=True,
            check=False,
            timeout=FIXTURE_READY_SECONDS,
            env={**os.environ, **seed_environment},
        )
        assert loaded.returncode == 0, f"the fixture schema did not load: {loaded.stdout}{loaded.stderr}"
        ensure_smoke_branch(seed_environment)
        yield {"address": address, "token": values["INFRAHUB_INITIAL_ADMIN_TOKEN"]}
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

    return {kind: canary(kind) for kind in ("administrator", "product", "prefect", "object_store", "principal")}


@pytest.fixture(scope="session")
def deployment(
    sync_image: str,
    infrahub_fixture: dict[str, str],
    canaries: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Any]:
    """One started, converged, endpoint-ready deployment shared by the whole module.

    The destination is the pinned fixture, reached through the host gateway the
    test-only override adds. The bundle itself neither joins that stack's network
    nor carries the route.
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
        instance=instance,
        image=sync_image,
        destination_token=infrahub_fixture["token"],
        canaries=canaries,
    )
    started = Deployment(instance=instance, environment_file=environment_file, overrides=(FIXTURE_OVERRIDE,))
    result = started.up("sync-api", "sync-worker")
    if result.returncode != 0:
        # Bootstrap first: it is the step every other service waits on, and a
        # tail of the whole project is almost entirely PostgreSQL's own startup.
        detail = started.logs("sync-bootstrap", "sync-api", "sync-worker", tail=80)
        started.down(volumes=True)
        pytest.fail(f"the bundle did not start: {result.stderr[-1500:]}\n{detail[-4000:]}")
    try:
        wait_for(
            "the deployment reporting a live worker",
            lambda: worker_state(started) in {"ready", "busy"},
        )
        yield started
    finally:
        started.down(volumes=True)
