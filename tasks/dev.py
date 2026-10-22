"""The local development stack: build the image from the working tree, and run it.

This is "Run from source". Nothing here is pinned, released, or reproducible: the
operator deployment (the root `docker-compose.yml`) pulls a released image from the
OpsMill registry. These tasks drive `development/docker-compose.yml`, build whatever the
working tree holds, for the host's own architecture, start it, and remove it again.

The `dev` namespace (`dev.build`, `dev.start`, `dev.restart`, `dev.stop`, `dev.destroy`,
`dev.status`, `dev.logs`, `dev.seed`, `dev.smoke`) runs the core services, the Sync
services (`sync` profile) and a local Infrahub (`infrahub` profile). With
`--infrahub-network NAME`, or `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`, the stack leaves the
`infrahub` profile off and connects the worker to that network instead.

Every setting the tasks print or use comes from `dev_settings()`, which asks Compose to
resolve the development file (`docker compose config --format json`), so a shell
variable that changes the stack changes the tasks too.

`build_inputs_hash()` and `rebuild_decision()` tell whether `infrahub-sync:dev` still
matches the checkout: the image carries the hash of the files that shape it as a label,
and a code change alone never changes that hash, because the stack mounts the source
over the installed copy.

The top-level `build`, `start` and `destroy` tasks are the earlier entry points. `start`
runs the Sync services only and connects the worker to the Compose networks of a local
NetBox and a local Infrahub, when they run, so a package can name them by service name.
Infrahub's network is `infrahub_default`, the network of Infrahub's own Compose file
started with `docker compose -p infrahub`; `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK` names
another one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from invoke import Collection, Context, task

from .netbox import DEV_STACK_PROJECT, WORKER_INFRAHUB_URL, attach_dev_worker, connect_dev_worker, load_netbox_env
from .smoke_dataset import load_smoke_dataset
from .utils import ESCAPED_REPO_PATH, REPO_BASE

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8030"
PREFECT_URL = "http://127.0.0.1:4230"
# The development stack's Compose file, relative to the repository root.
DEV_COMPOSE_FILE = "development/docker-compose.yml"
# The Infrahub services' own file, which the development file includes.
INFRAHUB_COMPOSE_FILE = REPO_BASE / "development" / "docker-compose.infrahub.yml"
# The profiles the top-level `start` and `build` select: the Sync services. The core
# services (PostgreSQL, the object store, the Prefect server) have no profile and always start.
START_PROFILES = ("sync",)
# Every profile of the development file, so `destroy` removes every service it started.
ALL_PROFILES = ("sync", "infrahub")
# The services `dev.restart` recreates so they import the mounted source again.
SYNC_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker")
# The dev stack's default principal, the value docker-compose.yml defaults to.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
WAIT_TIMEOUT_SECONDS = 420
INFRAHUB_NETWORK_VARIABLE = "INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK"
INFRAHUB_URL_VARIABLE = "INFRAHUB_SYNC_DEV_INFRAHUB_URL"
DEFAULT_INFRAHUB_NETWORK = "infrahub_default"
# The credential a registered package's `infrahub-token` reference resolves.
INFRAHUB_CREDENTIAL_VARIABLE = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
DEV_IMAGE = "infrahub-sync:dev"
BUILD_INPUTS_LABEL = "org.opsmill.infrahub-sync.build-inputs"
# The files whose bytes decide whether the image must be rebuilt, in hashing order.
BUILD_INPUT_FILES = ("Dockerfile", "pyproject.toml", "uv.lock")
# The service whose build produces `infrahub-sync:dev`; every Sync-image service uses it.
BUILD_SERVICE = "prefect-server"
# What `docker image inspect` prints for a label the image does not carry.
_NO_LABEL_VALUES = frozenset({"", "<no value>"})
_WILDCARD_OR_LOOPBACK = frozenset({"", "127.0.0.1", "localhost", "0.0.0.0", "::", "::1"})  # noqa: S104

# What `dev.smoke` sets so the smoke suite reads the development stack's settings.
SMOKE_STACK_VARIABLE = "INFRAHUB_SYNC_SMOKE_STACK"
SMOKE_COMMAND = "uv run pytest -m preview tests/preview -q"


class DevStackError(RuntimeError):
    """Raised when the development stack's settings cannot be read or do not allow a task."""


@dataclass(frozen=True)
class Publication:
    """One host publication of a service: the bind address and the host port."""

    host_ip: str
    port: str

    @property
    def url(self) -> str:
        """The HTTP URL the host reaches the publication at.

        Loopback also answers a wildcard publication, so only another address, such as
        the Docker bridge gateway on Linux, replaces `127.0.0.1`.
        """
        address = self.host_ip.strip()
        if address in _WILDCARD_OR_LOOPBACK:
            host = "127.0.0.1"
        elif ":" in address:
            host = f"[{address}]"
        else:
            host = address
        return f"http://{host}:{self.port}"


@dataclass(frozen=True)
class DevSettings:
    """The development stack's resolved settings, as `docker compose config` reports them."""

    project: str
    sync_api: Publication
    prefect: Publication
    # None when the stack uses an Infrahub on another network (`--infrahub-network`).
    infrahub: Publication | None
    infrahub_admin_token: str
    # The Infrahub URL the worker uses, the one a package names.
    worker_infrahub_url: str
    infrahub_network: str
    bearer_tokens: str
    work_pool: str

    @property
    def sync_api_url(self) -> str:
        """The Sync API's URL on the host."""
        return self.sync_api.url

    @property
    def prefect_url(self) -> str:
        """The Prefect server's URL on the host."""
        return self.prefect.url

    @property
    def infrahub_url(self) -> str:
        """The Infrahub URL on the host; with an attached network, the worker's URL."""
        return self.infrahub.url if self.infrahub is not None else self.worker_infrahub_url

    @property
    def profiles(self) -> tuple[str, ...]:
        """The profiles `dev.start` selects: `infrahub` only without an attached network."""
        return ("sync",) if self.infrahub_network else ALL_PROFILES


def selected_infrahub_network(option: str = "") -> str:
    """Return the Infrahub network the stack attaches to: the option, the variable, or none."""
    return option or os.environ.get(INFRAHUB_NETWORK_VARIABLE, "")


def _publication(services: Mapping[str, Any], name: str, target: int) -> Publication:
    """Return the host publication of one service's container port."""
    try:
        ports = services[name].get("ports") or []
    except KeyError:
        msg = f"{DEV_COMPOSE_FILE} has no {name} service"
        raise DevStackError(msg) from None
    for port in ports:
        if int(port.get("target", 0)) == target and port.get("published"):
            return Publication(host_ip=str(port.get("host_ip", "")), port=str(port["published"]))
    msg = f"{name} does not publish port {target} in {DEV_COMPOSE_FILE}"
    raise DevStackError(msg)


def parse_dev_settings(resolved: Mapping[str, Any], *, infrahub_network: str = "") -> DevSettings:
    """Build the settings from the output of `docker compose config --format json`."""
    services = resolved.get("services") or {}
    sync_api_environment = (services.get("sync-api") or {}).get("environment") or {}
    if infrahub_network:
        infrahub = None
        token = os.environ.get(INFRAHUB_CREDENTIAL_VARIABLE, "")
        worker_url = os.environ.get(INFRAHUB_URL_VARIABLE) or WORKER_INFRAHUB_URL
    else:
        infrahub = _publication(services, "infrahub-server", 8000)
        token = str((services["infrahub-server"].get("environment") or {}).get("INFRAHUB_INITIAL_ADMIN_TOKEN", ""))
        worker_url = WORKER_INFRAHUB_URL
    return DevSettings(
        project=str(resolved.get("name") or DEV_STACK_PROJECT),
        sync_api=_publication(services, "sync-api", 8000),
        prefect=_publication(services, "prefect-server", 4200),
        infrahub=infrahub,
        infrahub_admin_token=token,
        worker_infrahub_url=worker_url,
        infrahub_network=infrahub_network,
        bearer_tokens=str(sync_api_environment.get("INFRAHUB_SYNC_SERVICE_BEARER_TOKENS", "")),
        work_pool=str(sync_api_environment.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "")),
    )


def dev_settings(context: Context | None = None, *, infrahub_network: str = "") -> DevSettings:
    """Resolve the development file with Compose and return the settings the tasks use.

    Without an Infrahub network, Compose resolves the `sync` and `infrahub` profiles. With
    one (the argument, else `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`), it resolves `sync`
    only, and the Infrahub URL comes from `INFRAHUB_SYNC_DEV_INFRAHUB_URL`, default
    `http://infrahub-server:8000`.
    """
    runner = context or Context()
    network = selected_infrahub_network(infrahub_network)
    profiles = ("sync",) if network else ALL_PROFILES
    command = f"{_compose_command('', profiles)} config --format json"
    with runner.cd(ESCAPED_REPO_PATH):
        result = runner.run(command, hide=True, warn=True, pty=False)
    if result is None or not result.ok:
        detail = (result.stderr or "").strip() if result is not None else "no result"
        msg = f"`{command}` failed: {detail}"
        raise DevStackError(msg)
    try:
        resolved = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"`{command}` printed no JSON: {exc}"
        raise DevStackError(msg) from None
    return parse_dev_settings(resolved, infrahub_network=network)


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
    """The Compose network of the local Infrahub the top-level `start` connects the worker to."""
    return os.environ.get(INFRAHUB_NETWORK_VARIABLE) or DEFAULT_INFRAHUB_NETWORK


def attach_dev_worker_to_infrahub(context: Context, project: str = DEV_STACK_PROJECT) -> bool:
    """Connect the selected project's worker to the local Infrahub's network, when both exist."""
    network = infrahub_network()
    connected = connect_dev_worker(context, network, project=project)
    if connected:
        print(f" - [{NAMESPACE}] The worker is on {network}; it reaches Infrahub at {WORKER_INFRAHUB_URL}")
    return connected


def _compose_command(project: str = "", profiles: Sequence[str] = START_PROFILES) -> str:
    """Return the Compose command for the development file, with its profiles and project."""
    command = "docker compose"
    for profile in profiles:
        command += f" --profile {shlex.quote(profile)}"
    if project:
        command += f" --project-name {shlex.quote(project)}"
    return f"{command} -f {shlex.quote(DEV_COMPOSE_FILE)}"


def compose(
    context: Context,
    arguments: str,
    *,
    project: str = "",
    profiles: Sequence[str] = START_PROFILES,
    env: Mapping[str, str] | None = None,
    pty: bool = False,
) -> None:
    """Run one Compose command against the development file from the repository root.

    `env` is passed only when it holds values, so a caller's `config.run.env` (the
    benchmark runner sets one) is not replaced by an empty mapping.
    """
    command = f"{_compose_command(project, profiles)} {arguments}"
    with context.cd(ESCAPED_REPO_PATH):
        if env:
            context.run(command, env=dict(env), pty=pty)
        else:
            context.run(command, pty=pty)


def _build(context: Context, *, no_cache: bool = False, project: str = "") -> None:
    """Build `infrahub-sync:dev` with the build-inputs label; the benchmark runner pins the project."""
    arguments = f"build --build-arg BUILD_INPUTS={build_inputs_hash()}"
    if no_cache:
        arguments += " --no-cache"
    compose(context, f"{arguments} {BUILD_SERVICE}", project=project, pty=True)


def _start(context: Context, *, project: str = "", attach_infrahub: bool = True) -> None:
    """Start the Sync services and reattach the worker; the benchmark runner pins the project."""
    compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", project=project, pty=True)
    # `up` recreates the worker whenever its settings change, which drops a network it
    # was connected to afterwards. Reconnect it to local NetBox and, unless the caller
    # connects its own destination network (the benchmark runner does), Infrahub.
    selected_project = project or DEV_STACK_PROJECT
    attach_dev_worker(context, load_netbox_env(), project=selected_project)
    if attach_infrahub:
        attach_dev_worker_to_infrahub(context, project=selected_project)


def _destroy(context: Context, *, project: str = "") -> None:
    """Remove the stack and its volumes; the benchmark runner pins the project."""
    compose(context, "down --volumes --remove-orphans", project=project, profiles=ALL_PROFILES, pty=True)


def _stack_env(settings: DevSettings) -> dict[str, str]:
    """The shell values the Sync services read, beyond what the shell already sets.

    With the stack's own Infrahub, the worker's Infrahub credential defaults to that
    Infrahub's initial admin token, so the smoke package can write to it. A value the
    shell exports wins.
    """
    if settings.infrahub_network or os.environ.get(INFRAHUB_CREDENTIAL_VARIABLE):
        return {}
    return {INFRAHUB_CREDENTIAL_VARIABLE: settings.infrahub_admin_token}


def _attach_worker(context: Context, settings: DevSettings) -> None:
    """Connect the worker to NetBox's network and, in network mode, to the named Infrahub network."""
    attach_dev_worker(context, load_netbox_env(), project=settings.project)
    if not settings.infrahub_network:
        return
    if connect_dev_worker(context, settings.infrahub_network, project=settings.project):
        print(
            f" - [{NAMESPACE}] The worker is on {settings.infrahub_network}; "
            f"it reaches Infrahub at {settings.worker_infrahub_url}"
        )
    else:
        print(
            f" - [{NAMESPACE}] Network {settings.infrahub_network} does not exist or the worker is missing; "
            "the worker cannot reach that Infrahub"
        )


def _print_urls(settings: DevSettings) -> None:
    print(f" - [{NAMESPACE}] Project     {settings.project}")
    print(f" - [{NAMESPACE}] Sync API    {settings.sync_api_url}  (bearer {API_TOKEN})")
    print(f" - [{NAMESPACE}] Prefect UI  {settings.prefect_url}")
    if settings.infrahub is not None:
        print(f" - [{NAMESPACE}] Infrahub    {settings.infrahub_url}  (worker: {settings.worker_infrahub_url})")
    else:
        print(f" - [{NAMESPACE}] Infrahub    {settings.worker_infrahub_url} on {settings.infrahub_network}")


def _infrahub_reachable(address: str) -> bool:
    """Report whether Infrahub answers at all; a stopped stack lets the suite report itself."""
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires the service extras

    try:
        httpx.get(f"{address}/api/config", timeout=5)
    except httpx.HTTPError:
        return False
    return True


def _seed_settings(context: Context) -> DevSettings:
    settings = dev_settings(context)
    if settings.infrahub is None:
        msg = (
            f"dev.seed and dev.smoke write to the stack's own Infrahub; {INFRAHUB_NETWORK_VARIABLE} "
            f"names {settings.infrahub_network}, whose address on this host is not known"
        )
        raise DevStackError(msg)
    return settings


# ----- The `dev` namespace -----------------------------------------------------------------


@task(name="build")
def dev_build(context: Context, no_cache: bool = False) -> None:  # noqa: FBT001, FBT002 -- Invoke boolean flag idiom
    """Build infrahub-sync:dev from the working tree, labelled with its build inputs."""
    _build(context, no_cache=no_cache, project=dev_settings(context).project)
    print(f" - [{NAMESPACE}] Built {DEV_IMAGE}; start it with `uv run invoke dev.start`")


@task(name="start", help={"infrahub-network": "Use the Infrahub on this Docker network instead of the stack's own"})
def dev_start(context: Context, infrahub_network: str = "") -> None:
    """Start the core, Sync and Infrahub services, rebuilding the image when its inputs changed.

    With --infrahub-network NAME (or INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK), the stack's own
    Infrahub stays off and the worker joins NAME. When the local NetBox runs, the worker
    also joins NetBox's network.
    """
    settings = dev_settings(context, infrahub_network=infrahub_network)
    if rebuild_decision(context) == "build":
        print(f" - [{NAMESPACE}] {DEV_IMAGE} is missing or was built from other build inputs; building it")
        _build(context, project=settings.project)
    compose(
        context,
        f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}",
        project=settings.project,
        profiles=settings.profiles,
        env=_stack_env(settings),
        pty=True,
    )
    _attach_worker(context, settings)
    _print_urls(settings)
    print(f" - [{NAMESPACE}] After a code change: `uv run invoke dev.restart`")


@task(name="restart")
def dev_restart(context: Context) -> None:
    """Recreate the Sync containers so they import the mounted source again; no rebuild."""
    settings = dev_settings(context)
    services = " ".join(SYNC_SERVICES)
    compose(
        context,
        f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS} --force-recreate --no-deps {services}",
        project=settings.project,
        profiles=settings.profiles,
        env=_stack_env(settings),
        pty=True,
    )
    _attach_worker(context, settings)
    print(f" - [{NAMESPACE}] Recreated {services}")


@task(name="stop")
def dev_stop(context: Context) -> None:
    """Stop the containers and keep their volumes."""
    compose(context, "stop", project=dev_settings(context).project, profiles=ALL_PROFILES, pty=True)
    print(f" - [{NAMESPACE}] Stopped; `uv run invoke dev.start` starts it again with its data")


@task(name="destroy")
def dev_destroy(context: Context) -> None:
    """Remove the containers and their volumes; keep the image.

    Destructive by design: the Postgres databases, the object store's buckets and the
    Infrahub data go with it, which is what makes the next `dev.start` a first start again.
    """
    _destroy(context, project=dev_settings(context).project)
    print(f" - [{NAMESPACE}] Removed the stack and its data; {DEV_IMAGE} is kept")


@task(name="status")
def dev_status(context: Context) -> None:
    """Show the containers, their health and the URLs."""
    settings = dev_settings(context)
    compose(context, "ps --all", project=settings.project, profiles=ALL_PROFILES)
    _print_urls(settings)


@task(name="logs", help={"name": "The Compose service", "lines": "How many lines from the end"})
def dev_logs(context: Context, name: str = "sync-api", lines: int = 50) -> None:
    """Show the last lines of one service's logs."""
    compose(
        context,
        f"logs --no-color --tail {int(lines)} {shlex.quote(name)}",
        project=dev_settings(context).project,
        profiles=ALL_PROFILES,
    )


@task(name="seed")
def dev_seed(context: Context) -> None:
    """Load the smoke dataset into the stack's Infrahub: schema, shared device, smoke branch."""
    settings = _seed_settings(context)
    load_smoke_dataset(context, settings.infrahub_url, settings.infrahub_admin_token)


@task(name="smoke")
def dev_smoke(context: Context) -> None:
    """Seed the smoke dataset, then run the smoke suite against the running stack."""
    settings = _seed_settings(context)
    if _infrahub_reachable(settings.infrahub_url):
        print(f" - [{NAMESPACE}] The smoke suite writes, so it seeds first")
        load_smoke_dataset(context, settings.infrahub_url, settings.infrahub_admin_token)
    else:
        print(f" - [{NAMESPACE}] Infrahub is not reachable; the smoke suite will report the stack's state")
    print(f" - [{NAMESPACE}] Running the smoke suite against {settings.project}")
    with context.cd(ESCAPED_REPO_PATH):
        # Single-process deliberately: the suite's modules share one Infrahub branch and
        # one Prefect deployment, and its collection hook orders them against each other.
        context.run(SMOKE_COMMAND, env={SMOKE_STACK_VARIABLE: "dev"})


ns = Collection(
    "dev",
    dev_build,
    dev_start,
    dev_restart,
    dev_stop,
    dev_destroy,
    dev_status,
    dev_logs,
    dev_seed,
    dev_smoke,
)


# ----- The earlier top-level tasks, kept until the `dev` namespace replaces them ------------


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
