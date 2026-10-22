"""The local development stack: build the image from the working tree, and run it.

This is "Run from source". Nothing here is pinned, released, or reproducible: the
operator deployment (the root `docker-compose.yml`) pulls a released image from the
OpsMill registry. These tasks drive `development/docker-compose.yml`, build whatever the
working tree holds, for the host's own architecture, start it, and remove it again.

`build_inputs_hash()` and `rebuild_decision()` tell whether `infrahub-sync:dev` still
matches the checkout: the image carries the hash of the files that shape it as a label,
and a code change alone never changes that hash, because the stack mounts the source
over the installed copy.

`start` also connects the worker to the Compose networks of a local NetBox and a local
Infrahub, when they run, so a package can name them by service name. Infrahub's network
is `infrahub_default`, the network of Infrahub's own Compose file started with
`docker compose -p infrahub`; `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` names another one.
"""

from __future__ import annotations

import hashlib
import os
import shlex
from pathlib import Path
from typing import Literal

from invoke import Context, task

from .netbox import DEV_STACK_PROJECT, WORKER_INFRAHUB_URL, attach_dev_worker, connect_dev_worker, load_netbox_env
from .utils import ESCAPED_REPO_PATH, REPO_BASE

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8030"
PREFECT_URL = "http://127.0.0.1:4230"
# The development stack's Compose file, relative to the repository root.
DEV_COMPOSE_FILE = "development/docker-compose.yml"
# The profiles `start` and `build` select: the Sync services. The core services
# (PostgreSQL, the object store, the Prefect server) have no profile and always start.
START_PROFILES = ("sync",)
# Every profile of the development file, so `destroy` removes every service it started.
ALL_PROFILES = ("sync", "infrahub")
# The dev stack's default principal, the value docker-compose.yml defaults to.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
WAIT_TIMEOUT_SECONDS = 420
INFRAHUB_NETWORK_VARIABLE = "INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK"
DEFAULT_INFRAHUB_NETWORK = "infrahub_default"
DEV_IMAGE = "infrahub-sync:dev"
BUILD_INPUTS_LABEL = "org.opsmill.infrahub-sync.build-inputs"
# The files whose bytes decide whether the image must be rebuilt, in hashing order.
BUILD_INPUT_FILES = ("Dockerfile", "pyproject.toml", "uv.lock")
# What `docker image inspect` prints for a label the image does not carry.
_NO_LABEL_VALUES = frozenset({"", "<no value>"})


def build_inputs_hash(root: Path = REPO_BASE) -> str:
    """Return the SHA-256 over `Dockerfile`, `pyproject.toml` and `uv.lock`, in that order."""
    digest = hashlib.sha256()
    for name in BUILD_INPUT_FILES:
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def image_build_inputs(context: Context, image: str = DEV_IMAGE) -> str | None:
    """Return the image's build-inputs label: None when the image is missing, "" when unlabelled."""
    label_format = shlex.quote(f'{{{{ index .Config.Labels "{BUILD_INPUTS_LABEL}" }}}}')
    result = context.run(
        f"docker image inspect --format {label_format} {shlex.quote(image)}", hide=True, warn=True, pty=False
    )
    if result is None or not result.ok:
        return None
    value = result.stdout.strip()
    return "" if value in _NO_LABEL_VALUES else value


def rebuild_decision(context: Context, image: str = DEV_IMAGE, root: Path = REPO_BASE) -> Literal["build", "start"]:
    """Return "build" when the image is missing, unlabelled or built from other inputs, else "start"."""
    label = image_build_inputs(context, image)
    if not label or label != build_inputs_hash(root):
        return "build"
    return "start"


def infrahub_network() -> str:
    """The Compose network of the local Infrahub the worker joins."""
    return os.environ.get(INFRAHUB_NETWORK_VARIABLE) or DEFAULT_INFRAHUB_NETWORK


def attach_dev_worker_to_infrahub(context: Context, project: str = DEV_STACK_PROJECT) -> bool:
    """Connect the selected project's worker to the local Infrahub's network, when both exist."""
    network = infrahub_network()
    connected = connect_dev_worker(context, network, project=project)
    if connected:
        print(f" - [{NAMESPACE}] The worker is on {network}; it reaches Infrahub at {WORKER_INFRAHUB_URL}")
    return connected


def _compose_command(project: str, compose_file: str, profiles: tuple[str, ...] = START_PROFILES) -> str:
    """Return the Compose command, optionally pinned to a project and file (benchmark runner only).

    The profiles apply to the development file only; a pinned file has its own services.
    """
    command = "docker compose"
    if not compose_file:
        for profile in profiles:
            command += f" --profile {shlex.quote(profile)}"
    if project:
        command += f" --project-name {shlex.quote(project)}"
    command += f" -f {shlex.quote(compose_file or DEV_COMPOSE_FILE)}"
    return command


def _build(context: Context, *, no_cache: bool = False, project: str = "", compose_file: str = "") -> None:
    """Build the image; the public task passes no project or file, the benchmark runner pins both."""
    arguments = " --no-cache" if no_cache else ""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{_compose_command(project, compose_file)} build{arguments}", pty=True)


def _start(context: Context, *, project: str = "", compose_file: str = "") -> None:
    """Start the stack and reattach the worker; the public task passes no project or file."""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(
            f"{_compose_command(project, compose_file)} up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}",
            pty=True,
        )
    # `up` recreates the worker whenever its settings change, which drops a network it
    # was connected to afterwards. Reconnect it to local NetBox and, for the
    # development stack, Infrahub. The benchmark runner connects its own preview
    # destination network after starting its pinned Compose file.
    selected_project = project or DEV_STACK_PROJECT
    attach_dev_worker(context, load_netbox_env(), project=selected_project)
    if not compose_file:
        attach_dev_worker_to_infrahub(context, project=selected_project)


def _destroy(context: Context, *, project: str = "", compose_file: str = "") -> None:
    """Remove the stack and its volumes; the public task passes no project or file."""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(
            f"{_compose_command(project, compose_file, ALL_PROFILES)} down --volumes --remove-orphans", pty=True
        )


@task(name="build")
def build(context: Context, no_cache: bool = False) -> None:  # noqa: FBT001, FBT002 -- Invoke boolean flag idiom
    """Build the local development image from the current working tree."""
    _build(context, no_cache=no_cache)
    print(f" - [{NAMESPACE}] Built infrahub-sync:dev; start it with `uv run invoke start`")


@task(name="start")
def start(context: Context) -> None:
    """Start the local development stack, building the image first if it is absent.

    When the local NetBox (`invoke netbox.up` or `netbox.seed`) is running, the worker also
    joins NetBox's network, so a package can read NetBox at `http://netbox:8080`. When a
    local Infrahub started from Infrahub's own Compose file is running, the worker joins its
    network too, so a package can write to Infrahub at `http://infrahub-server:8000`.
    """
    _start(context)
    print(f" - [{NAMESPACE}] Sync API    {API_URL}  (bearer {API_TOKEN})")
    print(f" - [{NAMESPACE}] Prefect UI  {PREFECT_URL}")
    print(f" - [{NAMESPACE}] After a code change: `uv run invoke build && uv run invoke start`")
    print(f" - [{NAMESPACE}] Stop and reset:      `uv run invoke destroy`")


@task(name="destroy")
def destroy(context: Context) -> None:
    """Remove the local development stack, along with its data volumes.

    Destructive by design: the Postgres databases and the object store's buckets go with
    it, which is what makes the next `invoke start` a first start again. The image the
    working tree built is kept, so that start does not rebuild.
    """
    _destroy(context)
    print(f" - [{NAMESPACE}] Removed the stack and its data; infrahub-sync:dev is kept")
    print(f" - [{NAMESPACE}] Start again with `uv run invoke start`")
