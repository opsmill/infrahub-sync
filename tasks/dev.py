"""The local development stack: build the image from the working tree, and run it.

This is "Run from source". Nothing here is pinned, released, or reproducible: the
operator deployment (the root `docker-compose.yml`) pulls a released image from the
OpsMill registry. These tasks drive `development/docker-compose.dev.yml`, build whatever the working tree holds, for the host's own architecture,
start it, and remove it again.
"""

from __future__ import annotations

from invoke import Context, task

from .netbox import attach_dev_worker, load_netbox_env
from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8030"
# The stack's own Infrahub task manager (see docker-compose.dev.yml).
PREFECT_URL = "http://127.0.0.1:4230"
# The development stack's Compose file, relative to the repository root.
DEV_COMPOSE = "docker compose -f development/docker-compose.dev.yml"
# The dev stack's constant principal, the same value docker-compose.dev.yml inlines.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
WAIT_TIMEOUT_SECONDS = 420


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

    It runs its own Infrahub task manager, from the image the preview pins.
    When the local NetBox (`invoke netbox.up` or `netbox.seed`) is running, the worker also
    joins NetBox's network, so a package can read NetBox at `http://netbox:8080`.
    """
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{DEV_COMPOSE} up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", pty=True)
    # `up` recreates the worker whenever its settings change, which drops a network it
    # was connected to afterwards. Reconnect it to the local NetBox, if that is running.
    attach_dev_worker(context, load_netbox_env())
    print(f" - [{NAMESPACE}] Sync API    {API_URL}  (bearer {API_TOKEN})")
    print(f" - [{NAMESPACE}] Prefect UI  {PREFECT_URL}")
    print(f" - [{NAMESPACE}] After a code change: `uv run invoke build && uv run invoke start`")
    print(f" - [{NAMESPACE}] Stop and reset:      `uv run invoke destroy`")


@task(name="destroy")
def destroy(context: Context) -> None:
    """Remove the local development stack, along with its data volumes.

    Destructive by design: the task manager's PostgreSQL data, which holds Sync's
    product database, and the object store's buckets go with it, which is what makes
    the next `invoke start` a first start again. The image the working tree built is
    kept, so that start does not rebuild.
    """
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{DEV_COMPOSE} down --volumes --remove-orphans", pty=True)
    print(f" - [{NAMESPACE}] Removed the stack and its data; infrahub-sync:dev is kept")
    print(f" - [{NAMESPACE}] Start again with `uv run invoke start`")
