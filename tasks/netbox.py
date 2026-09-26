"""Local, disposable NetBox: the fixed dataset the saved-plan apply integration test needs.

`invoke netbox.up` starts a disposable local NetBox instance and prints its URL and
development token. `invoke netbox.seed` resets the database, loads a named dataset --
today only `seed`, the deterministic dataset
`tests/integration/test_saved_plan_apply_integration.py` requires -- and prints the same
URL and token banner. `invoke netbox.down` removes the containers and their volumes.

Configuration ships in `development/netbox/netbox.env` (no secrets -- local-only
defaults); personal overrides belong in the gitignored
`development/netbox/netbox.local.env`.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

if TYPE_CHECKING:
    import httpx

NAMESPACE = "INFRAHUB-SYNC-NETBOX"
REPO_ROOT = Path(__file__).parent.parent.resolve()
DEV_DIR = REPO_ROOT / "development" / "netbox"
ENV_FILE = DEV_DIR / "netbox.env"
LOCAL_ENV_FILE = DEV_DIR / "netbox.local.env"
COMPOSE_FILE = DEV_DIR / "docker-compose.netbox.yml"
DATASETS_DIR = DEV_DIR / "datasets"

# One seeder script per dataset name. The `demo` dataset (SYNC-131 part 2, the official
# NetBox demo data) adds a second entry here; nothing else about the task structure changes.
DATASETS: dict[str, Path] = {
    "seed": DATASETS_DIR / "seed_netbox.py",
}
DEFAULT_DATASET = "seed"
# NetBox's first migration run took roughly 16 minutes on the reference machine, right at
# the previous 960s ceiling -- this leaves real headroom instead of racing it.
WAIT_TIMEOUT_SECONDS = 1800

_REQUIRED_ENV_KEYS = (
    "COMPOSE_PROJECT_NAME",
    "NETBOX_PORT",
    "NETBOX_DB_PASSWORD",
    "NETBOX_SECRET_KEY",
    "NETBOX_TOKEN_PEPPER",
    "NETBOX_ADMIN_PASSWORD",
    "NETBOX_TOKEN_KEY",
    "NETBOX_TOKEN",
)


class NetboxError(RuntimeError):
    """Raised when the local NetBox environment cannot reach a required state."""


def load_netbox_env() -> dict[str, str]:
    """Read settings from `netbox.env`, then `netbox.local.env` overrides."""
    values: dict[str, str] = {}
    for env_file in (ENV_FILE, LOCAL_ENV_FILE):
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = set(_REQUIRED_ENV_KEYS) - values.keys()
    if missing:
        msg = f"{ENV_FILE} is missing required keys: {sorted(missing)}"
        raise NetboxError(msg)
    return values


def netbox_url(values: dict[str, str]) -> str:
    """The local NetBox base URL for the configured host port."""
    return f"http://localhost:{values['NETBOX_PORT']}"


def netbox_token(values: dict[str, str]) -> str:
    """The client-facing v2 API token, NetBox's `nbt_<key>.<token>` shape.

    Built from `NETBOX_TOKEN_KEY` (the token's public key component) and `NETBOX_TOKEN`
    (its plaintext secret), the two halves the container's superuser bootstrap accepts
    (`SUPERUSER_API_KEY` / `SUPERUSER_API_TOKEN`).
    """
    return f"nbt_{values['NETBOX_TOKEN_KEY']}.{values['NETBOX_TOKEN']}"


def dataset_script(dataset: str) -> Path:
    """The seeder script for a dataset name, or a clear error for an unknown one."""
    try:
        return DATASETS[dataset]
    except KeyError:
        msg = f"unknown dataset {dataset!r}; expected one of {sorted(DATASETS)}"
        raise NetboxError(msg) from None


def _compose(context: Context, arguments: str, values: dict[str, str]) -> None:
    command = (
        f"docker compose --project-name {shlex.quote(values['COMPOSE_PROJECT_NAME'])} "
        f"--env-file {shlex.quote(str(ENV_FILE))} -f {shlex.quote(str(COMPOSE_FILE))} {arguments}"
    )
    with context.cd(ESCAPED_REPO_PATH):
        context.run(command, env=values, pty=False)


_SERVER_ERROR_FLOOR = 500


def _wait_for_http(url: str, description: str, timeout: int = WAIT_TIMEOUT_SECONDS) -> None:
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires httpx

    print(f" - [{NAMESPACE}] Waiting for {description} at {url}")
    deadline = time.monotonic() + timeout
    last_error = "no attempt made"
    while time.monotonic() < deadline:
        try:
            response: httpx.Response = httpx.get(url, timeout=5)
        except httpx.HTTPError as exc:
            last_error = str(exc)
        else:
            if response.status_code < _SERVER_ERROR_FLOOR:
                return
            last_error = f"HTTP {response.status_code}"
        time.sleep(3)
    msg = f"{description} did not become ready within {timeout}s (last: {last_error})"
    raise NetboxError(msg)


def reset_database(context: Context, values: dict[str, str]) -> None:
    """Empty NetBox by recreating its containers from a fresh volume.

    `docker compose down --volumes` drops the Postgres data volume; the following `up`
    recreates NetBox against an empty database. Every seeder script under `DATASETS`
    asserts emptiness before writing, so a dataset load always starts from nothing —
    this is what makes loading a dataset safe to repeat.
    """
    print(f" - [{NAMESPACE}] Resetting the local NetBox database")
    _compose(context, "down --volumes", values)
    _compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", values)
    _wait_for_http(f"{netbox_url(values)}/api/", "NetBox")


def _print_ready_banner(values: dict[str, str]) -> None:
    print(f" - [{NAMESPACE}] NetBox ready")
    print(f"     URL:   {netbox_url(values)}")
    print(f"     Token: {netbox_token(values)}")


@task
def up(context: Context) -> None:
    """Bring up the disposable local NetBox, then print its URL and development token."""
    values = load_netbox_env()
    _compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", values)
    _wait_for_http(f"{netbox_url(values)}/api/", "NetBox")
    _print_ready_banner(values)


@task
def seed(context: Context, dataset: str = DEFAULT_DATASET) -> None:
    """Reset the database, then load the named dataset (default: `seed`)."""
    script = dataset_script(dataset)
    values = load_netbox_env()
    reset_database(context, values)
    print(f" - [{NAMESPACE}] Loading the {dataset!r} dataset from {script}")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(
            f"uv run python {shlex.quote(str(script))} "
            f"--url {shlex.quote(netbox_url(values))} --token {shlex.quote(netbox_token(values))}",
            pty=False,
        )
    _print_ready_banner(values)


@task
def down(context: Context) -> None:
    """Stop the local NetBox and remove its containers and data volumes."""
    values = load_netbox_env()
    _compose(context, "down --volumes", values)
    print(f" - [{NAMESPACE}] Local NetBox stopped and data volumes removed")
