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

`dev.deps` starts the core and Infrahub services only and returns; `dev.api` and
`dev.worker` then run the Sync API and the worker on the host, each in the foreground,
against those containers' loopback publications. No task writes a PID file or keeps a
process in the background. `dev.smoke` notices this mode from the running containers
(the Prefect server runs, no `sync-api` container does) and first checks both host
processes through the Sync API's `GET /status`.

The image tag is `infrahub-sync:dev` unless `SYNC_IMAGE` names another one.

Every setting the tasks print or use comes from `dev_settings()`, which asks Compose to
resolve the development file (`docker compose config --format json`), so a shell
variable that changes the stack changes the tasks too.

`build_inputs_hash()` and `rebuild_decision()` tell whether `infrahub-sync:dev` still
matches the checkout: the image carries the hash of the files that shape it as a label,
and a code change alone never changes that hash, because the stack mounts the source
over the installed copy.

To use an Infrahub started from Infrahub's own Compose file (`docker compose -p infrahub`),
run `dev.start --infrahub-network infrahub_default`: the stack runs the Sync services only and
the worker joins that network, and NetBox's when the local NetBox runs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shlex
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from invoke import Collection, Context, task

from .netbox import DEV_STACK_PROJECT, WORKER_INFRAHUB_URL, attach_dev_worker, connect_dev_worker, load_netbox_env
from .smoke_dataset import load_smoke_dataset
from .utils import ESCAPED_REPO_PATH, REPO_BASE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8001"
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
# The bind addresses whose publication the host reaches at 127.0.0.1: the wildcards and
# the IPv4 loopback names. Not `is_loopback()`: a publication bound to 127.0.0.2 is
# loopback but is not reached at 127.0.0.1, and one bound to `::1` listens on IPv6 only,
# so it is reached at `[::1]`.
_REACHED_AT_127_0_0_1 = frozenset({"", "127.0.0.1", "localhost", "0.0.0.0", "::"})  # noqa: S104
# The variable that moves Infrahub's publication off loopback, and the one that sets its
# initial admin token. `docker-compose.infrahub.yml` falls back to the token below, which
# is published in this repository and so protects nothing on an address other hosts reach.
INFRAHUB_BIND_VARIABLE = "INFRAHUB_BIND_ADDRESS"
INFRAHUB_ADMIN_TOKEN_VARIABLE = "INFRAHUB_INITIAL_ADMIN_TOKEN"  # noqa: S105 -- a variable name
PUBLIC_INFRAHUB_ADMIN_TOKEN = "06438eb2-8019-4776-878c-0941b1f1d1ec"  # noqa: S105 -- the public development default

# What `dev.smoke` sets so the smoke suite reads the development stack's settings.
SMOKE_STACK_VARIABLE = "INFRAHUB_SYNC_SMOKE_STACK"
SMOKE_COMMAND = "uv run pytest -m preview tests/preview -q"
# The value `dev.smoke` sets in dependencies mode, where the worker runs on the host and a
# package therefore names Infrahub by its host publication.
DEPS_SMOKE_STACK = "dev-deps"
# The profiles `dev.deps` selects: the core services, which have none, and Infrahub.
DEPS_PROFILES = ("infrahub",)
# Where `dev.api` listens: loopback only, like every publication of the stack.
HOST_PROCESS_ADDRESS = "127.0.0.1"
API_FACTORY = "infrahub_sync.service.serve:build_app"
DATABASE_URL_VARIABLE = "INFRAHUB_SYNC_DATABASE_URL"
S3_ENDPOINT_VARIABLE = "INFRAHUB_SYNC_S3_ENDPOINT_URL"
# The `/status` worker states that mean a worker takes runs (see `service._service_status`).
AVAILABLE_WORKER_STATES = frozenset({"ready", "busy"})


class DevStackError(RuntimeError):
    """Raised when the development stack's settings cannot be read or do not allow a task."""


@dataclass(frozen=True)
class Publication:
    """One host publication of a service: the bind address and the host port."""

    host_ip: str
    port: str

    @property
    def address(self) -> str:
        """The `host:port` the host reaches the publication at.

        Loopback also answers a wildcard publication, so only another address, such as
        the Docker bridge gateway on Linux, replaces `127.0.0.1`.
        """
        address = self.host_ip.strip()
        if address in _REACHED_AT_127_0_0_1:
            host = "127.0.0.1"
        elif ":" in address:
            host = f"[{address}]"
        else:
            host = address
        return f"{host}:{self.port}"

    @property
    def url(self) -> str:
        """The HTTP URL the host reaches the publication at."""
        return f"http://{self.address}"


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
    # The image every Sync-image service runs, `SYNC_IMAGE` or `infrahub-sync:dev`.
    image: str = DEV_IMAGE
    # The publications the host processes of `dev.api` and `dev.worker` use; None when
    # the file does not publish them.
    postgres: Publication | None = None
    object_store: Publication | None = None
    # The resolved environments of the `sync-api` and `sync-worker` containers, which
    # `host_environment()` moves onto the loopback publications.
    api_environment: Mapping[str, str] = field(default_factory=dict)
    worker_environment: Mapping[str, str] = field(default_factory=dict)

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


def _optional_publication(services: Mapping[str, Any], name: str, target: int) -> Publication | None:
    """Return the host publication of one service's container port, or None without one."""
    try:
        return _publication(services, name, target)
    except DevStackError:
        return None


def _environment(services: Mapping[str, Any], name: str) -> dict[str, str]:
    """Return one service's resolved environment; a variable without a value is left out."""
    environment = (services.get(name) or {}).get("environment") or {}
    return {key: str(value) for key, value in environment.items() if value is not None}


def infrahub_server(services: Mapping[str, Any]) -> tuple[Publication, str]:
    """Return the host publication of `infrahub-server`'s port 8000 and its initial admin token."""
    publication = _publication(services, "infrahub-server", 8000)
    token = str((services["infrahub-server"].get("environment") or {}).get(INFRAHUB_ADMIN_TOKEN_VARIABLE, ""))
    return publication, token


def is_loopback(host_ip: str) -> bool:
    """Report whether a publication's bind address reaches this host only.

    `localhost`, `::1` and every 127.0.0.0/8 address are loopback. An empty address, which
    Docker publishes on every interface, and a wildcard such as `0.0.0.0` are not.
    """
    address = host_ip.strip().strip("[]")
    if address.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def require_private_infrahub(publication: Publication | None, token: str) -> None:
    """Refuse an Infrahub published beyond loopback that keeps the public development admin token.

    Compose cannot make `INFRAHUB_INITIAL_ADMIN_TOKEN` required only when
    `INFRAHUB_BIND_ADDRESS` names another address, so the tasks that start Infrahub check
    the resolved publication and token here. The message never names the token.

    Raises:
        DevStackError: when Infrahub is published on a non-loopback address and its initial
            admin token is empty or the public development default.
    """
    if publication is None or is_loopback(publication.host_ip):
        return
    if token.strip() and token != PUBLIC_INFRAHUB_ADMIN_TOKEN:
        return
    where = publication.host_ip.strip() or "every address"
    msg = (
        f"{INFRAHUB_BIND_VARIABLE} publishes Infrahub on {where}, but its initial admin token is the public "
        f"development default; export {INFRAHUB_ADMIN_TOKEN_VARIABLE} with a token of your own and use the same "
        f"value as INFRAHUB_API_TOKEN, or unset {INFRAHUB_BIND_VARIABLE} to publish Infrahub on 127.0.0.1 only. "
        "Infrahub applies the token only when it first initialises its database, so a stack that has already "
        "started keeps the public default: remove it with `uv run invoke dev.destroy` before you start it "
        "with a new token"
    )
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
        infrahub, token = infrahub_server(services)
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
        image=str((services.get(BUILD_SERVICE) or {}).get("image") or DEV_IMAGE),
        postgres=_optional_publication(services, "postgres", 5432),
        object_store=_optional_publication(services, "object-store", 9000),
        api_environment=_environment(services, "sync-api"),
        worker_environment=_environment(services, "sync-worker"),
    )


def dev_settings(context: Context | None = None, *, infrahub_network: str = "") -> DevSettings:
    """Resolve the development file with Compose and return the settings the tasks use.

    Without an Infrahub network, Compose resolves the `sync` and `infrahub` profiles. With
    one (the argument, else `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`), it resolves `sync`
    only, and the Infrahub URL comes from `INFRAHUB_SYNC_DEV_INFRAHUB_URL`, default
    `http://infrahub-server:8000`.
    """
    network = selected_infrahub_network(infrahub_network)
    profiles = ("sync",) if network else ALL_PROFILES
    resolved = resolved_compose(context, ComposeTarget(profiles=profiles))
    return parse_dev_settings(resolved, infrahub_network=network)


def resolved_compose(context: Context | None = None, target: ComposeTarget | None = None) -> dict[str, Any]:
    """Return `docker compose config --format json` for the development file, as one target resolves it."""
    runner = context or Context()
    target = target or ComposeTarget()
    command = f"{_compose_command(target.project, target.profiles)} config --format json"
    options: dict[str, Any] = {"hide": True, "warn": True, "pty": False}
    if target.env:
        options["env"] = dict(target.env)
    with runner.cd(ESCAPED_REPO_PATH):
        result = runner.run(command, **options)
    if result is None or not result.ok:
        detail = (result.stderr or "").strip() if result is not None else "no result"
        msg = f"`{command}` failed: {detail}"
        raise DevStackError(msg)
    try:
        resolved = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"`{command}` printed no JSON: {exc}"
        raise DevStackError(msg) from None
    return resolved


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


@dataclass(frozen=True)
class ComposeTarget:
    """The Compose project and profiles one command runs against, and the shell values it adds."""

    project: str = ""
    profiles: Sequence[str] = START_PROFILES
    env: Mapping[str, str] | None = None


def compose(context: Context, arguments: str, target: ComposeTarget | None = None, *, pty: bool = False) -> None:
    """Run one Compose command against the development file from the repository root.

    `target.env` is passed only when it holds values, so a caller's `config.run.env` (the
    benchmark runner sets one) is not replaced by an empty mapping.
    """
    target = target or ComposeTarget()
    command = f"{_compose_command(target.project, target.profiles)} {arguments}"
    with context.cd(ESCAPED_REPO_PATH):
        if target.env:
            context.run(command, env=dict(target.env), pty=pty)
        else:
            context.run(command, pty=pty)


def _build(context: Context, *, no_cache: bool = False, project: str = "") -> None:
    """Build `infrahub-sync:dev` with the build-inputs label; the benchmark runner pins the project."""
    arguments = f"build --build-arg BUILD_INPUTS={build_inputs_hash()}"
    if no_cache:
        arguments += " --no-cache"
    compose(context, f"{arguments} {BUILD_SERVICE}", ComposeTarget(project=project), pty=True)


def _start(context: Context, *, project: str = "", attach_infrahub: bool = True) -> None:
    """Start the Sync services and reattach the worker; the benchmark runner pins the project."""
    compose(
        context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", ComposeTarget(project=project), pty=True
    )
    # `up` recreates the worker whenever its settings change, which drops a network it
    # was connected to afterwards. Reconnect it to local NetBox and, unless the caller
    # connects its own destination network (the benchmark runner does), Infrahub.
    selected_project = project or DEV_STACK_PROJECT
    attach_dev_worker(context, load_netbox_env(), project=selected_project)
    if attach_infrahub:
        attach_dev_worker_to_infrahub(context, project=selected_project)


def _destroy(context: Context, *, project: str = "") -> None:
    """Remove the stack and its volumes; the benchmark runner pins the project."""
    compose(context, "down --volumes --remove-orphans", ComposeTarget(project=project, profiles=ALL_PROFILES), pty=True)


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


def bearer_label(bearer_tokens: str) -> str:
    """Return what the URLs print for the Sync API's bearer token.

    The default principal's token is a public development value, so it is printed when a
    principal's token equals it exactly. A token set through BEARER_TOKENS is the
    developer's own, and so is anything that is not a JSON object of principals: they are
    named by the variable, never printed.
    """
    try:
        principals = json.loads(bearer_tokens)
    except ValueError:
        principals = None
    if isinstance(principals, dict) and any(
        isinstance(principal, dict) and principal.get("token") == API_TOKEN for principal in principals.values()
    ):
        return API_TOKEN
    return "from BEARER_TOKENS"


def _print_urls(settings: DevSettings, *, host_worker: bool = False) -> None:
    bearer = bearer_label(settings.bearer_tokens)
    print(f" - [{NAMESPACE}] Project     {settings.project}")
    print(f" - [{NAMESPACE}] Sync API    {settings.sync_api_url}  (bearer {bearer})")
    print(f" - [{NAMESPACE}] Prefect UI  {settings.prefect_url}")
    if settings.infrahub is not None and host_worker:
        print(f" - [{NAMESPACE}] Infrahub    {settings.infrahub_url}  (also for a package the host worker runs)")
    elif settings.infrahub is not None:
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


def _start_settings(context: Context, network: str = "") -> DevSettings:
    """The settings of a task that brings the `infrahub` profile up, refused when it would expose Infrahub."""
    settings = dev_settings(context, infrahub_network=network)
    require_private_infrahub(settings.infrahub, settings.infrahub_admin_token)
    return settings


def _seed_settings(context: Context) -> DevSettings:
    settings = dev_settings(context)
    if settings.infrahub is None:
        msg = (
            f"dev.seed and dev.smoke write to the stack's own Infrahub; {INFRAHUB_NETWORK_VARIABLE} "
            f"names {settings.infrahub_network}, whose address on this host is not known"
        )
        raise DevStackError(msg)
    return settings


def _rebuild_when_stale(context: Context, settings: DevSettings) -> None:
    """Build the stack's image when it is missing or was built from other build inputs."""
    if rebuild_decision(context, settings.image) == "build":
        print(f" - [{NAMESPACE}] {settings.image} is missing or was built from other build inputs; building it")
        _build(context, project=settings.project)


def _on_loopback(url: str, publication: Publication) -> str:
    """Return `url` with its host and port replaced by the publication's, keeping its credentials."""
    parts = urlsplit(url)
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = f"{userinfo}@{publication.address}" if userinfo else publication.address
    return urlunsplit(parts._replace(netloc=netloc))


def host_environment(settings: DevSettings, service: Literal["sync-api", "sync-worker"]) -> dict[str, str]:
    """The environment of `dev.api` or `dev.worker`: the container's, moved onto loopback.

    The settings are the ones the `sync-api` or `sync-worker` container would get, so a
    shell variable that changes the containers changes the host processes too. Only the
    addresses differ: PostgreSQL, the object store and the Prefect server are reached on
    their loopback publications instead of their service names. A variable resolved to
    an empty value is left out, so the shell's own value, if any, applies.
    """
    if settings.postgres is None or settings.object_store is None:
        msg = f"{DEV_COMPOSE_FILE} must publish postgres and object-store for the host processes"
        raise DevStackError(msg)
    source = settings.api_environment if service == "sync-api" else settings.worker_environment
    environment = {key: value for key, value in source.items() if value}
    if DATABASE_URL_VARIABLE not in environment:
        msg = f"{service} has no {DATABASE_URL_VARIABLE} in {DEV_COMPOSE_FILE}"
        raise DevStackError(msg)
    environment[DATABASE_URL_VARIABLE] = _on_loopback(environment[DATABASE_URL_VARIABLE], settings.postgres)
    environment[S3_ENDPOINT_VARIABLE] = settings.object_store.url
    environment["PREFECT_API_URL"] = f"{settings.prefect_url}/api"
    if service == "sync-api":
        environment["INFRAHUB_SYNC_SERVICE_HOST"] = HOST_PROCESS_ADDRESS
    environment.update(_stack_env(settings))
    return environment


def api_command(settings: DevSettings) -> str:
    """The Sync API on the host, on loopback and on the port the `sync-api` container publishes."""
    return (
        f"{shlex.quote(sys.executable)} -m uvicorn --factory {API_FACTORY} "
        f"--host {HOST_PROCESS_ADDRESS} --port {shlex.quote(settings.sync_api.port)}"
    )


def worker_command(settings: DevSettings) -> str:
    """The Sync worker on the host, polling the stack's work pool."""
    return f"{shlex.quote(sys.executable)} -m infrahub_sync.service.worker --pool {shlex.quote(settings.work_pool)}"


def _running_services(context: Context, settings: DevSettings) -> set[str]:
    """Return the stack's services that have a running container."""
    command = f"{_compose_command(settings.project, ALL_PROFILES)} ps --services --status running"
    with context.cd(ESCAPED_REPO_PATH):
        result = context.run(command, hide=True, warn=True, pty=False)
    if result is None or not result.ok:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def in_dependencies_mode(context: Context, settings: DevSettings) -> bool:
    """Report whether the stack runs as `dev.deps` left it: core services up, no Sync API container.

    Read from Docker, not from a file the tasks write, so it holds however the stack
    was started. A stack that is not running at all is not in dependencies mode; the
    smoke suite then reports the stack's state itself.
    """
    running = _running_services(context, settings)
    return BUILD_SERVICE in running and "sync-api" not in running


def check_host_processes(settings: DevSettings) -> None:
    """Fail naming the missing host process, from the Sync API's `GET /status` alone.

    No answer names `dev.api`. An answer whose worker is not available names
    `dev.worker`. The Sync API reads worker availability from Prefect itself, so this
    check never calls the Prefect API.
    """
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires the service extras

    status_url = f"{settings.sync_api_url}/status"
    try:
        response = httpx.get(status_url, timeout=10)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        msg = f"The Sync API does not answer at {status_url}; start it with `uv run invoke dev.api`"
        raise DevStackError(msg) from None
    worker = body.get("worker") if isinstance(body, dict) else None
    state = str(worker.get("state", "")) if isinstance(worker, dict) else ""
    if state not in AVAILABLE_WORKER_STATES:
        msg = (
            f"The Sync API reports no available worker (state {state or 'missing'}); "
            "start it with `uv run invoke dev.worker`"
        )
        raise DevStackError(msg)


# ----- The `dev` namespace -----------------------------------------------------------------


@task(name="build")
def dev_build(context: Context, no_cache: bool = False) -> None:  # noqa: FBT001, FBT002 -- Invoke boolean flag idiom
    """Build infrahub-sync:dev from the working tree, labelled with its build inputs."""
    settings = dev_settings(context)
    _build(context, no_cache=no_cache, project=settings.project)
    print(f" - [{NAMESPACE}] Built {settings.image}; start it with `uv run invoke dev.start`")


@task(name="start", help={"infrahub-network": "Use the Infrahub on this Docker network instead of the stack's own"})
def dev_start(context: Context, infrahub_network: str = "") -> None:
    """Start the core, Sync and Infrahub services, rebuilding the image when its inputs changed.

    With --infrahub-network NAME (or INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK), the stack's own
    Infrahub stays off and the worker joins NAME. When the local NetBox runs, the worker
    also joins NetBox's network. Refuses to start when INFRAHUB_BIND_ADDRESS publishes
    Infrahub beyond loopback and INFRAHUB_INITIAL_ADMIN_TOKEN keeps the public default.
    """
    settings = _start_settings(context, infrahub_network)
    _rebuild_when_stale(context, settings)
    compose(
        context,
        f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}",
        ComposeTarget(project=settings.project, profiles=settings.profiles, env=_stack_env(settings)),
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
        ComposeTarget(project=settings.project, profiles=settings.profiles, env=_stack_env(settings)),
        pty=True,
    )
    _attach_worker(context, settings)
    print(f" - [{NAMESPACE}] Recreated {services}")


@task(name="stop")
def dev_stop(context: Context) -> None:
    """Stop the containers and keep their volumes."""
    compose(context, "stop", ComposeTarget(project=dev_settings(context).project, profiles=ALL_PROFILES), pty=True)
    print(f" - [{NAMESPACE}] Stopped; `uv run invoke dev.start` starts it again with its data")


@task(name="destroy")
def dev_destroy(context: Context) -> None:
    """Remove the containers and their volumes; keep the image.

    Destructive by design: the Postgres databases, the object store's buckets and the
    Infrahub data go with it, which is what makes the next `dev.start` a first start again.
    """
    settings = dev_settings(context)
    _destroy(context, project=settings.project)
    print(f" - [{NAMESPACE}] Removed the stack and its data; {settings.image} is kept")


@task(name="status")
def dev_status(context: Context) -> None:
    """Show the containers, their health and the URLs."""
    settings = dev_settings(context)
    compose(context, "ps --all", ComposeTarget(project=settings.project, profiles=ALL_PROFILES))
    _print_urls(settings)


@task(name="logs", help={"name": "The Compose service", "lines": "How many lines from the end"})
def dev_logs(context: Context, name: str = "sync-api", lines: int = 50) -> None:
    """Show the last lines of one service's logs."""
    compose(
        context,
        f"logs --no-color --tail {int(lines)} {shlex.quote(name)}",
        ComposeTarget(project=dev_settings(context).project, profiles=ALL_PROFILES),
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
    stack = "dev"
    if in_dependencies_mode(context, settings):
        print(
            f" - [{NAMESPACE}] No Sync containers run; checking dev.api and dev.worker through {settings.sync_api_url}/status"
        )
        check_host_processes(settings)
        stack = DEPS_SMOKE_STACK
    if _infrahub_reachable(settings.infrahub_url):
        print(f" - [{NAMESPACE}] The smoke suite writes, so it seeds first")
        load_smoke_dataset(context, settings.infrahub_url, settings.infrahub_admin_token)
    else:
        print(f" - [{NAMESPACE}] Infrahub is not reachable; the smoke suite will report the stack's state")
    print(f" - [{NAMESPACE}] Running the smoke suite against {settings.project}")
    with context.cd(ESCAPED_REPO_PATH):
        # Single-process deliberately: the suite's modules share one Infrahub branch and
        # one Prefect deployment, and its collection hook orders them against each other.
        context.run(SMOKE_COMMAND, env={SMOKE_STACK_VARIABLE: stack})


@task(name="deps", help={"infrahub-network": "Use the Infrahub on this Docker network instead of the stack's own"})
def dev_deps(context: Context, infrahub_network: str = "") -> None:
    """Start the core and Infrahub services only, for a Sync API and worker run on the host.

    Rebuilds the image when its inputs changed, starts the containers, converges the
    database, the bucket, the work pool and the deployment with a one-off `sync-bootstrap`
    container, prints the URLs and returns. Run `dev.api` and `dev.worker` next, each in
    its own terminal. With --infrahub-network NAME (or INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK),
    the stack's own Infrahub stays off; the host worker then reaches the Infrahub at
    INFRAHUB_SYNC_DEV_INFRAHUB_URL, which must be an address the host can resolve.
    Refuses to start when INFRAHUB_BIND_ADDRESS publishes Infrahub beyond loopback and
    INFRAHUB_INITIAL_ADMIN_TOKEN keeps the public default.
    """
    settings = _start_settings(context, infrahub_network)
    _rebuild_when_stale(context, settings)
    profiles = DEPS_PROFILES if not settings.infrahub_network else ()
    compose(
        context,
        f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}",
        ComposeTarget(project=settings.project, profiles=profiles),
        pty=True,
    )
    compose(
        context,
        "run --rm --no-deps sync-bootstrap",
        ComposeTarget(project=settings.project, profiles=profiles),
        pty=True,
    )
    _print_urls(settings, host_worker=True)
    print(f" - [{NAMESPACE}] PostgreSQL  {_publication_address(settings.postgres)}")
    print(f" - [{NAMESPACE}] Object store {_publication_url(settings.object_store)}")
    print(f" - [{NAMESPACE}] Next: `uv run invoke dev.api` and `uv run invoke dev.worker`, each in its own terminal")


@task(name="api")
def dev_api(context: Context) -> None:
    """Run the Sync API on the host, in the foreground, against the `dev.deps` containers."""
    settings = dev_settings(context)
    environment = host_environment(settings, "sync-api")
    print(f" - [{NAMESPACE}] Sync API on {settings.sync_api_url}; Ctrl-C stops it")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(api_command(settings), env=environment, pty=True)


@task(name="worker")
def dev_worker(context: Context) -> None:
    """Run the Sync worker on the host, in the foreground, against the `dev.deps` containers."""
    settings = dev_settings(context)
    environment = host_environment(settings, "sync-worker")
    print(f" - [{NAMESPACE}] Worker on work pool {settings.work_pool}; Ctrl-C stops it")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(worker_command(settings), env=environment, pty=True)


def _publication_address(publication: Publication | None) -> str:
    return publication.address if publication is not None else "not published"


def _publication_url(publication: Publication | None) -> str:
    return publication.url if publication is not None else "not published"


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
    dev_deps,
    dev_api,
    dev_worker,
)
