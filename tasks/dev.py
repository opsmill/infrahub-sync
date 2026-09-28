"""The local development stack: build the image from the working tree, and run it.

Nothing here is pinned, qualified, or reproducible, and nothing here is the supported
deployment -- `deploy/compose` is, and it runs one digest-pinned image it was qualified
against. These two tasks build whatever the working tree holds, for the host's own
architecture, and start it.
"""

from __future__ import annotations

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8030"
PREFECT_URL = "http://127.0.0.1:4230"
# The dev stack's constant principal, the same value compose.yaml inlines.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
WAIT_TIMEOUT_SECONDS = 420


@task(name="build")
def build(context: Context, no_cache: bool = False) -> None:  # noqa: FBT001, FBT002 -- Invoke boolean flag idiom
    """Build the local development image from the current working tree."""
    arguments = " --no-cache" if no_cache else ""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"docker compose build{arguments}", pty=True)
    print(f" - [{NAMESPACE}] Built infrahub-sync:dev; start it with `uv run invoke start`")


@task(name="start")
def start(context: Context) -> None:
    """Start the local development stack, building the image first if it is absent."""
    with context.cd(ESCAPED_REPO_PATH):
        context.run(f"docker compose up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", pty=True)
    print(f" - [{NAMESPACE}] Sync API    {API_URL}  (bearer {API_TOKEN})")
    print(f" - [{NAMESPACE}] Prefect UI  {PREFECT_URL}")
    print(f" - [{NAMESPACE}] After a code change: `uv run invoke build && uv run invoke start`")
    print(f" - [{NAMESPACE}] Stop and reset:      `docker compose down --volumes`")
