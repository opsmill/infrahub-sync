"""Fixtures for the container-image smoke suite.

The Docker-backed tests run against an image that is already built and loaded,
named by `INFRAHUB_SYNC_DOCKER_IMAGE` and `VERSION` like the root
`docker-compose.yml` names it; the reusable image workflow loads each platform
under `infrahub-sync:smoke` and runs this suite before it pushes anything. They never build one themselves: a test that builds its own
subject cannot prove anything about the image that ships.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 -- fixed argv container probes for the image gate
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.docker_image import image_reference, missing_image_settings

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

# The identity, the writable roots, and the read-only roots the artifact contract
# fixes. They are written here rather than imported from the build task so the
# suite states the contract it checks instead of restating the builder's opinion.
RUNTIME_UID = 10001
RUNTIME_GID = 10001
WRITABLE_ROOTS = (
    "/var/lib/infrahub-sync",
    "/var/lib/infrahub-sync/prefect",
    "/tmp/infrahub-sync",  # noqa: S108 -- the image's own fixed scratch root, not a shared /tmp path
)
# A tmpfs mount root is owned by root at mode 755 under Docker's defaults, which
# would leave the declared writable paths unwritable to the runtime user however
# the image itself created them. Ownership has to be handed over at mount time, so
# these are the options every deployed shape — and the documented command — uses.
TMPFS_OPTIONS = f"uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700"
READ_ONLY_ROOTS = (
    "/",
    "/etc",
    "/tmp",  # noqa: S108 -- proving the parent of the scratch root stays read-only
    "/var/lib",
    "/opt/infrahub-sync",
    "/opt/infrahub-sync/venv",
    "/opt/infrahub-sync/venv/bin",
    "/usr/local/lib",
)

# What the image ships and what it must not. Ruff belongs in the first group even
# though it is also a development tool: `generate` shells out to
# `python -m ruff format` to keep regenerated code byte-stable, so the project
# declares it as a runtime dependency and the image has to carry a working one.
SERVICE_RUNTIMES = ("boto3", "fastapi", "prefect", "psycopg", "uvicorn")
# The bundled source adapters whose SDK the service profile installs. The
# adapter modules, not the SDKs: `import pynetbox` succeeding says the wheel is
# present, while importing the adapter is what a registered run actually does
# and is what fails when the adapter and the installed SDK disagree.
BUNDLED_SOURCE_ADAPTERS = (
    "infrahub_sync.adapters.netbox",
    "infrahub_sync.adapters.nautobot",
)
RUNTIME_TOOLS = ("ruff",)
EXCLUDED_MODULES = ("pytest", "invoke", "pylint")

CONTAINER_TIMEOUT_SECONDS = 180


def external_image_references(dockerfile: str) -> list[str]:
    """Return every image reference the Dockerfile resolves outside its own stages."""
    stages: set[str] = set()
    references: list[str] = []
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        if line.startswith("FROM "):
            words = line.split()
            references.append(words[1])
            if len(words) >= 4 and words[2].upper() == "AS":
                stages.add(words[3])
        elif line.startswith("COPY --from="):
            references.append(line.removeprefix("COPY --from=").split()[0])
    return [reference for reference in references if reference not in stages]


def require_image_ref() -> str:
    """Return the loaded image reference under test, skipping locally but failing on CI when unset.

    On GitHub Actions a missing image would turn every smoke test into a skip, and
    an all-skipped suite reports green without having checked anything.
    """
    reference = image_reference()
    if reference is None:
        message = (
            f"{' and '.join(missing_image_settings())} unset; build and load an image, "
            "then name it with INFRAHUB_SYNC_DOCKER_IMAGE and VERSION"
        )
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail(message)
        pytest.skip(message)
    return reference


@pytest.fixture(scope="session")
def image_ref() -> str:
    """Return the loaded image reference under test."""
    return require_image_ref()


def docker(argv: Sequence[str], *, timeout: int = CONTAINER_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    """Run one fixed-argv Docker command and return its completed result."""
    return subprocess.run(  # noqa: S603 -- fixed argv
        ["docker", *argv],  # noqa: S607 -- Docker is resolved from the gate's PATH
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def tmpfs_arguments(paths: Sequence[str]) -> list[str]:
    """Return the mount arguments that make the declared writable roots writable."""
    return [argument for path in paths for argument in ("--tmpfs", f"{path}:{TMPFS_OPTIONS}")]


def run_in_image(
    image_ref: str,
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    writable: Sequence[str] = WRITABLE_ROOTS,
) -> subprocess.CompletedProcess[str]:
    """Run one command in the image the way the contract says it is deployed.

    Always read-only root, always without a network, and always with exactly the
    declared writable roots mounted, so no test can pass because of a permission
    the deployed shape does not grant.
    """
    command = ["run", "--rm", "--read-only", "--network=none", *tmpfs_arguments(writable)]
    for key, value in (environment or {}).items():
        command += ["--env", f"{key}={value}"]
    command += [image_ref, *argv]
    return docker(command)


# --------------------------------------------------------------------------
# The storage the default command needs to start
# --------------------------------------------------------------------------
# The Sync API builds its durable providers at construction, and the PostgreSQL
# provider connects and creates its schema there, so the API cannot start without
# a real database. This runs one throwaway PostgreSQL beside the image on a
# private network for the duration of the suite. It is test scaffolding for that
# one boundary, not a deployment: no volume, no published port, no bundle. The
# reference is a multi-platform index digest and no `--platform` is passed, so
# Docker runs the variant native to the host, amd64 or arm64 alike.
POSTGRES_IMAGE = "postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
STORAGE_DATABASE = "smoke"
STORAGE_USER = "smoke"
STORAGE_PASSWORD = "smoke-db-password-5d1c9e"  # noqa: S105 -- throwaway credential on a private container network
STORAGE_READY_SECONDS = 120
# Distinct, greppable throwaway values, so a test can prove none of them reaches
# the container's logs (constitution Principle VI).
BEARER_TOKEN = "smoke-token-0123456789"  # noqa: S105 -- throwaway credential for the smoke container
S3_SECRET_KEY = "smoke-secret-key-8a4f2b"  # noqa: S105 -- throwaway credential for the smoke container
INJECTED_SECRETS = (BEARER_TOKEN, STORAGE_PASSWORD, S3_SECRET_KEY)
API_READY_SECONDS = 60


def _unique(prefix: str) -> str:
    return f"infrahub-sync-smoke-{prefix}-{uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def storage_network() -> Iterator[str]:
    """Create the private network the API and its database share."""
    name = _unique("net")
    created = docker(["network", "create", name])
    assert created.returncode == 0, created.stderr
    try:
        yield name
    finally:
        docker(["network", "rm", name])


@pytest.fixture(scope="session")
def storage(storage_network: str) -> Iterator[str]:
    """Run one throwaway PostgreSQL and yield the host name the API reaches it by."""
    name = _unique("db")
    started = docker(
        [
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            storage_network,
            "--env",
            f"POSTGRES_DB={STORAGE_DATABASE}",
            "--env",
            f"POSTGRES_USER={STORAGE_USER}",
            "--env",
            f"POSTGRES_PASSWORD={STORAGE_PASSWORD}",
            POSTGRES_IMAGE,
        ]
    )
    assert started.returncode == 0, started.stderr
    try:
        _wait_for_storage(name)
        yield name
    finally:
        docker(["rm", "--force", "--volumes", name])


def _wait_for_storage(container: str) -> None:
    """Block until PostgreSQL accepts connections, or fail naming what it last said."""
    deadline = time.monotonic() + STORAGE_READY_SECONDS
    last = "no attempt made"
    while time.monotonic() < deadline:
        probe = docker(
            ["exec", container, "pg_isready", "--username", STORAGE_USER, "--dbname", STORAGE_DATABASE],
            timeout=30,
        )
        if probe.returncode == 0:
            return
        last = (probe.stdout + probe.stderr).strip()[-400:]
        time.sleep(1)
    logs = docker(["logs", "--tail", "40", container]).stdout
    pytest.fail(f"the smoke database was not ready within {STORAGE_READY_SECONDS}s: {last}\n{logs}")


def api_environment(storage_host: str) -> dict[str, str]:
    """Return the settings the default command needs to build and serve.

    The object store is addressed but never called during startup, and Prefect is
    pointed at a port nothing listens on: the reconciler logs its failures and the
    API serves regardless. Only the database has to be real.
    """
    return {
        "INFRAHUB_SYNC_DATABASE_URL": (
            f"postgresql://{STORAGE_USER}:{STORAGE_PASSWORD}@{storage_host}:5432/{STORAGE_DATABASE}"
        ),
        "INFRAHUB_SYNC_S3_BUCKET": "smoke",
        "INFRAHUB_SYNC_S3_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "smoke-access-key",
        "AWS_SECRET_ACCESS_KEY": S3_SECRET_KEY,
        "PREFECT_API_URL": "http://127.0.0.1:4200/api",
        "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": json.dumps({"smoke": {"token": BEARER_TOKEN, "administrator": False}}),
    }


@pytest.fixture
def started_container(image_ref: str, storage_network: str, storage: str) -> Iterator[str]:
    """Start the image's default command against real storage and yield its identifier."""
    command = [
        "run",
        "--detach",
        "--read-only",
        "--network",
        storage_network,
        *tmpfs_arguments(WRITABLE_ROOTS),
    ]
    for key, value in api_environment(storage).items():
        command += ["--env", f"{key}={value}"]
    command.append(image_ref)
    started = docker(command)
    assert started.returncode == 0, started.stderr
    container = started.stdout.strip()
    try:
        yield container
    finally:
        docker(["rm", "--force", container])


def wait_for_api(container: str, *, timeout: int = API_READY_SECONDS) -> dict[str, object]:
    """Return the served `GET /version` document once the container's API answers it."""
    probe = "import json,httpx;print(json.dumps(httpx.get('http://127.0.0.1:8000/version', timeout=5).raise_for_status().json()))"
    deadline = time.monotonic() + timeout
    last = "no attempt made"
    while time.monotonic() < deadline:
        result = docker(["exec", container, "python", "-c", probe], timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout.strip().splitlines()[-1])
        last = result.stderr.strip()[-400:]
        time.sleep(2)
    logs = docker(["logs", "--tail", "40", container]).stdout
    pytest.fail(f"the image's default command did not answer GET /version within {timeout}s: {last}\n{logs}")
