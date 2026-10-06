"""The local development stack: build the image from the working tree, and run it.

This is "Run from source". Nothing here is pinned, qualified, or reproducible: a release
package (`deploy/compose`) runs one digest-pinned image that passed full qualification.
These tasks build whatever the working tree holds, for the host's own architecture,
start it, and remove it again.
"""

from __future__ import annotations

import shlex

from invoke import Context, task

from .netbox import DEV_STACK_PROJECT, attach_dev_worker, load_netbox_env
from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8030"
PREFECT_URL = "http://127.0.0.1:4230"
# The dev stack's constant principal, the same value compose.yaml inlines.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
WAIT_TIMEOUT_SECONDS = 420


def _compose_command(project: str, compose_file: str) -> str:
    """Return the Compose command, optionally pinned to a project and file (benchmark runner only)."""
    command = "docker compose"
    if project:
        command += f" --project-name {shlex.quote(project)}"
    if compose_file:
        command += f" -f {shlex.quote(compose_file)}"
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
    # was connected to afterwards. Reconnect it to the local NetBox, if that is running.
    attach_dev_worker(context, load_netbox_env(), project=project or DEV_STACK_PROJECT)


def _destroy(context: Context, *, project: str = "", compose_file: str = "") -> None:
    """Remove the stack and its volumes; the public task passes no project or file."""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"{_compose_command(project, compose_file)} down --volumes --remove-orphans", pty=True)


@task(name="build")
def build(context: Context, no_cache: bool = False) -> None:  # noqa: FBT001, FBT002 -- Invoke boolean flag idiom
    """Build the local development image from the current working tree."""
    _build(context, no_cache=no_cache)
    print(f" - [{NAMESPACE}] Built infrahub-sync:dev; start it with `uv run invoke start`")


@task(name="start")
def start(context: Context) -> None:
    """Start the local development stack, building the image first if it is absent.

    When the local NetBox (`invoke netbox.up` or `netbox.seed`) is running, the worker also
    joins NetBox's network, so a package can read NetBox at `http://netbox:8080`.
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
