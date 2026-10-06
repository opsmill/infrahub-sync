"""The local development stack: build the image from the working tree, and run it.

This is "Run from source". Nothing here is pinned, released, or reproducible: the
operator deployment (the root `docker-compose.yml`) pulls a released image from the
OpsMill registry. These tasks drive `development/docker-compose.dev.yml`, build whatever the working tree holds, for the host's own architecture,
start it, and remove it again.

`start` also connects the worker to the Compose networks of a local NetBox and a local
Infrahub, when they run, so a package can name them by service name. Infrahub's network
is `infrahub_default`, the network of Infrahub's own Compose file started with
`docker compose -p infrahub`; `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` names another one.
"""

from __future__ import annotations

import os

from invoke import Context, task

from .netbox import WORKER_INFRAHUB_URL, attach_dev_worker, connect_dev_worker, load_netbox_env
from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8030"
PREFECT_URL = "http://127.0.0.1:4230"
# The development stack's Compose file, relative to the repository root.
DEV_COMPOSE = "docker compose -f development/docker-compose.dev.yml"
# The dev stack's constant principal, the same value docker-compose.dev.yml inlines.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
WAIT_TIMEOUT_SECONDS = 420
INFRAHUB_NETWORK_VARIABLE = "INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK"
DEFAULT_INFRAHUB_NETWORK = "infrahub_default"


def infrahub_network() -> str:
    """The Compose network of the local Infrahub the worker joins."""
    return os.environ.get(INFRAHUB_NETWORK_VARIABLE) or DEFAULT_INFRAHUB_NETWORK


def attach_dev_worker_to_infrahub(context: Context) -> bool:
    """Connect the dev stack's worker to the local Infrahub's network, when both exist."""
    network = infrahub_network()
    connected = connect_dev_worker(context, network)
    if connected:
        print(f" - [{NAMESPACE}] The worker is on {network}; it reaches Infrahub at {WORKER_INFRAHUB_URL}")
    return connected


@task(name="build")
def build(context: Context, no_cache: bool = False) -> None:  # noqa: FBT001, FBT002 -- Invoke boolean flag idiom
    """Build the local development image from the current working tree."""
    arguments = " --no-cache" if no_cache else ""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{DEV_COMPOSE} build{arguments}", pty=True)
    print(f" - [{NAMESPACE}] Built infrahub-sync:dev; start it with `uv run invoke start`")


@task(name="start")
def start(context: Context) -> None:
    """Start the local development stack, building the image first if it is absent.

    When the local NetBox (`invoke netbox.up` or `netbox.seed`) is running, the worker also
    joins NetBox's network, so a package can read NetBox at `http://netbox:8080`. When a
    local Infrahub started from Infrahub's own Compose file is running, the worker joins its
    network too, so a package can write to Infrahub at `http://infrahub-server:8000`.
    """
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{DEV_COMPOSE} up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", pty=True)
    # `up` recreates the worker whenever its settings change, which drops a network it
    # was connected to afterwards. Reconnect it to the local NetBox and the local
    # Infrahub, for each one that is running.
    attach_dev_worker(context, load_netbox_env())
    attach_dev_worker_to_infrahub(context)
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
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{DEV_COMPOSE} down --volumes --remove-orphans", pty=True)
    print(f" - [{NAMESPACE}] Removed the stack and its data; infrahub-sync:dev is kept")
    print(f" - [{NAMESPACE}] Start again with `uv run invoke start`")
