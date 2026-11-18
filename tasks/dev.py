"""The local development stack: build the image from the working tree, and run it.

This is "Run from source". Nothing here is pinned, released, or reproducible: the
operator deployment (the root `docker-compose.yml`) pulls a released image from the
OpsMill registry. These tasks drive `development/docker-compose.yml`, build whatever the
working tree holds, for the host's own architecture, start it, and remove it again.

The `dev` namespace (`dev.build`, `dev.start`, `dev.restart`, `dev.stop`, `dev.destroy`,
`dev.status`, `dev.logs`, `dev.seed`, `dev.smoke`) runs one of four presets:

- `sync`, the default: the core services and the Sync services (`sync` profile). The
  worker syncs into an Infrahub that runs elsewhere, by default one on this host at
  `http://host.docker.internal:8000` (`INFRAHUB_SYNC_DEV_INFRAHUB_URL` changes it).
- `infrahub`: `sync` plus a local Infrahub (`infrahub` profile).
- `preview`: `infrahub` plus the local NetBox from the `netbox` tasks, and the smoke
  dataset seeded into the local Infrahub: one command from a fresh clone to a stack to try.
- `demo`: `infrahub` plus the local NetBox, and nothing written into Infrahub. `dev.demo`
  starts it, then loads the NetBox demo's schema and registers its configuration with
  `infrahub-sync demo prepare`, run on the host.

`dev.start --preset NAME` and `dev.deps --preset NAME` select one, `dev.infrahub` and
`dev.preview` are `dev.start` with that preset, and `INFRAHUB_SYNC_DEV_PRESET` selects one
for every task. Without either, a task follows the stack's containers (`running_preset()`):
an `infrahub-server` container means `infrahub`, read from the running containers, or from
every container when none runs, so a bare `dev.start` after `dev.stop` starts the same
services again. The containers never select `preview` or `demo`. While the
local NetBox runs, the worker's NetBox credential defaults to its token whatever the preset.
With `--infrahub-network NAME`, or `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`, the preset is
`sync` and the worker joins that network.

`dev.deps` starts the core services, and the preset's Infrahub and NetBox, and returns; `dev.api` and
`dev.worker` then run the Sync API and the worker on the host, each in the foreground,
against those containers' loopback publications. No task writes a PID file or keeps a
process in the background. `dev.smoke` notices this mode from the running containers
(the Prefect server runs, no `sync-api` container does) and first checks both host
processes through the Sync API's `GET /api/status`.

`dev.frontend` runs the web interface's development server on the host, in the
foreground, against the Sync API of either mode.

The image tag is `infrahub-sync:dev` unless `SYNC_IMAGE` names another one.

Every setting the tasks print or use comes from `dev_settings()`, which asks Compose to
resolve the development file (`docker compose config --format json`), so a shell
variable that changes the stack changes the tasks too.

`build_inputs_hash()` and `rebuild_decision()` tell whether `infrahub-sync:dev` still
matches the checkout: the image carries the hash of the files that shape it as a label,
and a change to the Python source alone never changes that hash, because the stack mounts
the source over the installed copy. A change to the web interface's build inputs under
`frontend/` changes it, because the image carries the built web interface. After a change
there, `dev.start` or `dev.build` rebuilds the image; `dev.restart` does not.

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
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit, urlunsplit

from invoke import Collection, Context, task

from .netbox import (
    DEMO_DATASET,
    DEV_STACK_PROJECT,
    WORKER_INFRAHUB_URL,
    WORKER_NETBOX_URL,
    NetboxError,
    attach_dev_worker,
    connect_dev_worker,
    ensure_netbox,
    load_netbox_env,
    netbox_state,
    netbox_token,
    recorded_dataset,
)
from .smoke_dataset import load_smoke_dataset
from .utils import ESCAPED_REPO_PATH, REPO_BASE, escape_path

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

NAMESPACE = "INFRAHUB-SYNC-DEV"

API_URL = "http://127.0.0.1:8001"
# The development stack's Compose file, relative to the repository root.
DEV_COMPOSE_FILE = "development/docker-compose.yml"
# The Infrahub services' own file, which the development file includes.
INFRAHUB_COMPOSE_FILE = REPO_BASE / "development" / "docker-compose.infrahub.yml"
# The default profile of the Compose commands, which the benchmark runner's start and build
# use: the Sync services. The core services (PostgreSQL, the object store, the Prefect
# server) have no profile and always start.
START_PROFILES = ("sync",)
# Every profile of the development file, so `dev.destroy` removes every service it started.
ALL_PROFILES = ("sync", "infrahub")
# The services `dev.restart` recreates so they import the mounted source again.
SYNC_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker")
# The API token of the dev stack's `admin` account, the value docker-compose.yml defaults to.
API_TOKEN = "infrahub-sync-dev-token"  # noqa: S105 -- a local-only development credential
ADMIN_PASSWORD = "infrahub-sync-dev-password"  # noqa: S105 -- the public default of development/docker-compose.yml
WAIT_TIMEOUT_SECONDS = 420
INFRAHUB_NETWORK_VARIABLE = "INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK"
INFRAHUB_URL_VARIABLE = "INFRAHUB_SYNC_DEV_INFRAHUB_URL"
DEFAULT_INFRAHUB_NETWORK = "infrahub_default"
# Where the worker reaches Infrahub with the `sync` preset and no network: an Infrahub that
# publishes port 8000 on this host. The worker's `host.docker.internal` route is in the file.
HOST_INFRAHUB_URL = "http://host.docker.internal:8000"
PRESET_VARIABLE = "INFRAHUB_SYNC_DEV_PRESET"
# The credential a registered package's `infrahub-token` reference resolves.
INFRAHUB_CREDENTIAL_VARIABLE = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
# The credential a registered package's `netbox-token` reference resolves.
NETBOX_CREDENTIAL_VARIABLE = "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"
# The variable of the Sync API that holds the API token of its `admin` account.
ADMIN_TOKEN_VARIABLE = "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN"  # noqa: S105 -- the name of a variable
DEV_IMAGE = "infrahub-sync:dev"
BUILD_INPUTS_LABEL = "org.opsmill.infrahub-sync.build-inputs"
# The files whose bytes decide whether the image must be rebuilt, in hashing order.
BUILD_INPUT_FILES = ("Dockerfile", "pyproject.toml", "uv.lock")
# The web interface's build inputs: the paths the Dockerfile's `web` stage copies, in
# hashing order. A directory contributes every file under it, and a missing path, such
# as the submodule in a checkout that did not fetch it, contributes nothing.
BUILD_INPUT_FRONTEND_PATHS = (
    "frontend/.npmrc",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/pnpm-workspace.yaml",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/tsr.config.json",
    "frontend/src",
    "frontend/vendor/infrahub/frontend/packages/ui",
)
# Directories that a local install or build creates and the image build never copies.
BUILD_INPUT_SKIPPED_DIRECTORIES = frozenset({"node_modules", "dist", "__pycache__"})
# Files `.dockerignore` keeps out of the build context: hashing them would ask for a rebuild
# that changes nothing in the image, for example after Finder writes a `.DS_Store`.
BUILD_INPUT_SKIPPED_FILES = frozenset({".DS_Store"})
BUILD_INPUT_SKIPPED_SUFFIXES = (".pyc",)
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
SMOKE_COMMAND = "uv run pytest -m smoke tests/dev_stack/smoke -q"
# The value `dev.smoke` sets in dependencies mode, where the worker runs on the host and a
# package therefore names Infrahub by its host publication.
DEPS_SMOKE_STACK = "dev-deps"
# The profiles `dev.deps` selects: the core services, which have none, and Infrahub.
DEPS_PROFILES = ("infrahub",)
# Where `dev.api` listens: loopback only, like every publication of the stack.
HOST_PROCESS_ADDRESS = "127.0.0.1"
API_FACTORY = "infrahub_sync.service.serve:build_service"
DATABASE_URL_VARIABLE = "INFRAHUB_SYNC_DATABASE_URL"
S3_ENDPOINT_VARIABLE = "INFRAHUB_SYNC_S3_ENDPOINT_URL"
# The bundled store's service, the host name its endpoint names inside the stack.
BUNDLED_STORE_SERVICE = "object-store"
BUNDLED_STORE_VARIABLE = "INFRAHUB_SYNC_BUNDLED_OBJECT_STORE"
# Where each process reads CA certificates; in the containers, the CA mount's path.
CA_DIRECTORY_VARIABLE = "INFRAHUB_SYNC_CA_DIRECTORY"
# The web interface: its directory, the submodule that holds `@infrahub/ui`, the variables
# its development server reads (the same names as the CLI's) and the address it serves.
FRONTEND_DIRECTORY = "frontend"
FRONTEND_SUBMODULE = "frontend/vendor/infrahub"
FRONTEND_UI_PACKAGE = "frontend/vendor/infrahub/frontend/packages/ui"
FRONTEND_URL_VARIABLE = "INFRAHUB_SYNC_API_URL"
FRONTEND_URL = "http://localhost:3000"
# `dev.demo`: the command it runs on the host, the variables that command reads, and the
# branch its printed next commands create.
DEMO_PREPARE_COMMAND = "uv run infrahub-sync demo prepare"
INFRAHUB_TOKEN_VARIABLE = "INFRAHUB_API_TOKEN"  # noqa: S105 -- a variable name
SYNC_URL_VARIABLE = "INFRAHUB_SYNC_API_URL"
SYNC_TOKEN_VARIABLE = "INFRAHUB_SYNC_API_TOKEN"  # noqa: S105 -- a variable name
DEMO_BRANCH = "netbox-demo-1"
# The `/status` worker states that mean a worker takes runs (see `service._service_status`).
AVAILABLE_WORKER_STATES = frozenset({"ready", "busy"})


class DevStackError(RuntimeError):
    """Raised when the development stack's settings cannot be read or do not allow a task."""


@dataclass(frozen=True)
class Preset:
    """What one preset of the development stack runs besides the core and Sync services."""

    name: str
    summary: str
    # The stack's own Infrahub, the `infrahub` profile.
    local_infrahub: bool
    # The local NetBox from the `netbox` tasks.
    netbox: bool = False
    # The smoke dataset, loaded into the local Infrahub once the stack runs.
    smoke_dataset: bool = False

    @property
    def profiles(self) -> tuple[str, ...]:
        """The profiles `dev.start` selects."""
        return ALL_PROFILES if self.local_infrahub else START_PROFILES

    @property
    def deps_profiles(self) -> tuple[str, ...]:
        """The profiles `dev.deps` selects: the core services, which have none, and the preset's Infrahub."""
        return DEPS_PROFILES if self.local_infrahub else ()


PRESETS: dict[str, Preset] = {
    preset.name: preset
    for preset in (
        Preset("sync", "PostgreSQL, the object store, Prefect and the Sync services", local_infrahub=False),
        Preset("infrahub", "the `sync` preset and a local Infrahub", local_infrahub=True),
        Preset(
            "preview",
            "the `infrahub` preset, the local NetBox and the smoke dataset in Infrahub",
            local_infrahub=True,
            netbox=True,
            smoke_dataset=True,
        ),
        Preset(
            "demo",
            "the `infrahub` preset and the local NetBox with the `demo` dataset, ready for the NetBox demo",
            local_infrahub=True,
            netbox=True,
        ),
    )
}
DEFAULT_PRESET = "sync"
PRESET_HELP = "The preset: " + ", ".join(f"{name} ({preset.summary})" for name, preset in PRESETS.items())


def preset_named(name: str) -> Preset:
    """Return the preset of that name.

    Raises:
        DevStackError: when no preset has that name.
    """
    try:
        return PRESETS[name]
    except KeyError:
        msg = f"unknown preset {name!r}; expected one of {', '.join(PRESETS)}"
        raise DevStackError(msg) from None


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
    # None when the stack runs no Infrahub of its own: the `sync` preset, with or without
    # an Infrahub network (`--infrahub-network`).
    infrahub: Publication | None
    infrahub_admin_token: str
    # The Infrahub URL the worker uses, the one a package names.
    worker_infrahub_url: str
    infrahub_network: str
    # The API token of the `admin` account, as the Sync API receives it.
    admin_token: str
    work_pool: str
    # The image every Sync-image service runs, `SYNC_IMAGE` or `infrahub-sync:dev`.
    image: str = DEV_IMAGE
    # The publications the host processes of `dev.api` and `dev.worker` use; None when
    # the file does not publish them. The object store's is also None when
    # INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0 leaves the bundled store out.
    postgres: Publication | None = None
    object_store: Publication | None = None
    # The resolved environments of the `sync-api` and `sync-worker` containers, which
    # `host_environment()` moves onto the loopback publications.
    api_environment: Mapping[str, str] = field(default_factory=dict)
    worker_environment: Mapping[str, str] = field(default_factory=dict)
    # The host directories bound into each container, by container path.
    api_binds: Mapping[str, str] = field(default_factory=dict)
    worker_binds: Mapping[str, str] = field(default_factory=dict)
    # The preset the settings were resolved for; `infrahub` is None unless it runs a local Infrahub.
    preset: Preset = PRESETS["infrahub"]
    # Whether the local NetBox runs, read by `dev_settings()`; the `preview` and `demo` presets imply it.
    local_netbox: bool = False

    @property
    def netbox_credential(self) -> bool:
        """Whether the worker's NetBox credential defaults to the local NetBox's token."""
        return self.preset.netbox or self.local_netbox

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
        """The profiles `dev.start` selects: `infrahub` only with a preset that runs a local Infrahub."""
        return self.preset.profiles


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


def _binds(services: Mapping[str, Any], name: str) -> dict[str, str]:
    """Return one service's bind mounts as container path to host path; Compose makes the host path absolute."""
    volumes = (services.get(name) or {}).get("volumes") or []
    return {
        str(volume["target"]): str(volume["source"])
        for volume in volumes
        if isinstance(volume, dict) and volume.get("type") == "bind" and volume.get("source") and volume.get("target")
    }


def _bundled_store_publication(services: Mapping[str, Any]) -> Publication | None:
    """Return the bundled store's publication, or None when the file or a `scale` of 0 leaves it out.

    With INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0, Compose keeps `object-store` and its port in
    the resolved model and creates no container, so its port names nothing.
    """
    if int((services.get(BUNDLED_STORE_SERVICE) or {}).get("scale", 1)) == 0:
        return None
    return _optional_publication(services, BUNDLED_STORE_SERVICE, 9000)


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


def parse_dev_settings(
    resolved: Mapping[str, Any], *, infrahub_network: str = "", preset: Preset | None = None
) -> DevSettings:
    """Build the settings from the output of `docker compose config --format json`.

    Without a preset, the resolved file decides: `infrahub-server` in it, and no Infrahub
    network, means the `infrahub` preset; anything else, `sync`.
    """
    services = resolved.get("services") or {}
    sync_api_environment = (services.get("sync-api") or {}).get("environment") or {}
    if preset is None:
        local = not infrahub_network and "infrahub-server" in services
        preset = PRESETS["infrahub"] if local else PRESETS[DEFAULT_PRESET]
    if preset.local_infrahub:
        infrahub, token = infrahub_server(services)
        worker_url = WORKER_INFRAHUB_URL
    else:
        infrahub = None
        token = os.environ.get(INFRAHUB_CREDENTIAL_VARIABLE, "")
        default_url = WORKER_INFRAHUB_URL if infrahub_network else HOST_INFRAHUB_URL
        worker_url = os.environ.get(INFRAHUB_URL_VARIABLE) or default_url
    return DevSettings(
        project=str(resolved.get("name") or DEV_STACK_PROJECT),
        sync_api=_publication(services, "sync-api", 8000),
        prefect=_publication(services, "prefect-server", 4200),
        infrahub=infrahub,
        infrahub_admin_token=token,
        worker_infrahub_url=worker_url,
        infrahub_network=infrahub_network,
        admin_token=str(sync_api_environment.get(ADMIN_TOKEN_VARIABLE, "")),
        work_pool=str(sync_api_environment.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "")),
        image=str((services.get(BUILD_SERVICE) or {}).get("image") or DEV_IMAGE),
        postgres=_optional_publication(services, "postgres", 5432),
        object_store=_bundled_store_publication(services),
        api_environment=_environment(services, "sync-api"),
        worker_environment=_environment(services, "sync-worker"),
        api_binds=_binds(services, "sync-api"),
        worker_binds=_binds(services, "sync-worker"),
        preset=preset,
    )


def _stack_services(context: Context, *, all_states: bool) -> set[str]:
    """Return the stack's services with a running container, or with any container.

    Raises:
        DevStackError: when Compose does not answer, so an unreadable state never passes
            for a stack without Infrahub.
    """
    states = "--all" if all_states else "--status running"
    command = f"{_compose_command('', ALL_PROFILES)} ps --services {states}"
    with context.cd(ESCAPED_REPO_PATH):
        result = context.run(command, hide=True, warn=True, pty=False)
    if result is None or not result.ok:
        msg = (
            f"`{command}` failed, so the preset the stack runs is not known; "
            f"name one with --preset or {PRESET_VARIABLE}"
        )
        raise DevStackError(msg)
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def running_preset(context: Context) -> Preset:
    """The preset the stack's containers show: `infrahub` or the default.

    While a container of the stack runs, a running `infrahub-server` means `infrahub`.
    When none runs, an `infrahub-server` container that still exists means `infrahub`, so
    a bare `dev.start` after `dev.stop` starts the stopped Infrahub again with its data.
    The containers never select `preview`: it writes to Infrahub, so only `--preset` or
    INFRAHUB_SYNC_DEV_PRESET selects it.
    """
    services = _stack_services(context, all_states=False) or _stack_services(context, all_states=True)
    return PRESETS["infrahub"] if "infrahub-server" in services else PRESETS[DEFAULT_PRESET]


def select_preset(context: Context, option: str = "", *, infrahub_network: str = "") -> Preset:
    """Return the preset a task runs: the option, `INFRAHUB_SYNC_DEV_PRESET`, the running containers.

    An Infrahub network means the `sync` preset, whatever the containers show.

    Raises:
        DevStackError: when the name is not a preset, or names a preset with a local
            Infrahub together with an Infrahub network.
    """
    name = option or os.environ.get(PRESET_VARIABLE, "")
    if name:
        preset = preset_named(name)
        if infrahub_network and preset.local_infrahub:
            msg = (
                f"the {preset.name!r} preset runs a local Infrahub, and {INFRAHUB_NETWORK_VARIABLE} or "
                f"--infrahub-network names {infrahub_network}; use one or the other"
            )
            raise DevStackError(msg)
        return preset
    if infrahub_network:
        return PRESETS[DEFAULT_PRESET]
    return running_preset(context)


def dev_settings(context: Context | None = None, *, infrahub_network: str = "", preset: str = "") -> DevSettings:
    """Resolve the development file with Compose and return the settings the tasks use.

    The preset comes from `select_preset()`. With a local Infrahub, Compose resolves the
    `sync` and `infrahub` profiles; otherwise `sync` only, and the Infrahub URL comes from
    `INFRAHUB_SYNC_DEV_INFRAHUB_URL`: by default `http://infrahub-server:8000` on an
    Infrahub network (the argument, else `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK`), and
    `http://host.docker.internal:8000` without one.
    """
    runner = context or Context()
    network = selected_infrahub_network(infrahub_network)
    selected = select_preset(runner, preset, infrahub_network=network)
    resolved = resolved_compose(runner, ComposeTarget(profiles=selected.profiles))
    settings = parse_dev_settings(resolved, infrahub_network=network, preset=selected)
    if selected.netbox:
        return settings
    # The NetBox check only decides a credential default, so a failed check does not stop
    # tasks that never use NetBox; the worker then gets no NetBox credential by default.
    try:
        netbox_running = netbox_state(runner, load_netbox_env()) == "running"
    except NetboxError as exc:
        print(f" - [{NAMESPACE}] {exc}; the worker gets no NetBox credential by default")
        netbox_running = False
    return replace(settings, local_netbox=netbox_running)


def stack_project(context: Context) -> str:
    """The stack's Compose project name, read without asking which preset runs.

    For the tasks that act on every container whatever the preset (`dev.stop`,
    `dev.destroy`, `dev.logs`), so a Docker probe never stands in their way.
    """
    return _stack_identity(context)[0]


def _stack_identity(context: Context) -> tuple[str, str]:
    """The stack's Compose project name and image, from the file alone."""
    resolved = resolved_compose(context, ComposeTarget(profiles=START_PROFILES))
    services = resolved.get("services") or {}
    image = str((services.get(BUILD_SERVICE) or {}).get("image") or DEV_IMAGE)
    return str(resolved.get("name") or DEV_STACK_PROJECT), image


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


def _frontend_build_inputs(root: Path) -> list[Path]:
    """Return the files of `BUILD_INPUT_FRONTEND_PATHS` that exist, in hashing order.

    A directory's files are sorted by relative path, without what `.dockerignore` excludes:
    `node_modules`, `dist` and `__pycache__` directories, `.DS_Store` and `*.pyc` files.
    """
    files: list[Path] = []
    for name in BUILD_INPUT_FRONTEND_PATHS:
        path = root / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            found = (
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.name not in BUILD_INPUT_SKIPPED_FILES
                and not candidate.name.endswith(BUILD_INPUT_SKIPPED_SUFFIXES)
                and not BUILD_INPUT_SKIPPED_DIRECTORIES.intersection(candidate.relative_to(path).parts)
            )
            files.extend(sorted(found, key=lambda candidate: candidate.relative_to(root).as_posix()))
    return files


def build_inputs_hash(root: Path = REPO_BASE) -> str:
    """Return the SHA-256 of the image's build inputs.

    The hash covers the bytes of `Dockerfile`, `pyproject.toml` and `uv.lock`, in that
    order, then, for every file of the web interface's build inputs, its relative path
    and its bytes. A checkout without the frontend submodule still yields a hash.
    """
    # The demo assets are not hashed on purpose: the dev stack mounts the source tree, and
    # `dev.demo` runs `demo prepare` on the host, so the image never serves them.
    digest = hashlib.sha256()
    for name in BUILD_INPUT_FILES:
        digest.update((root / name).read_bytes())
    for path in _frontend_build_inputs(root):
        content = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(str(len(content)).encode())
        digest.update(b"\0")
        digest.update(content)
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
    """The Compose network of the local Infrahub that `_start` connects the worker to."""
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
    Infrahub's initial admin token, so the smoke package can write to it. With the
    `preview` and `demo` presets, or while the local NetBox runs, the NetBox credential defaults to
    the local NetBox's development token. A value the shell exports wins.
    """
    environment: dict[str, str] = {}
    if settings.infrahub is not None and not os.environ.get(INFRAHUB_CREDENTIAL_VARIABLE):
        environment[INFRAHUB_CREDENTIAL_VARIABLE] = settings.infrahub_admin_token
    if settings.netbox_credential and not os.environ.get(NETBOX_CREDENTIAL_VARIABLE):
        environment[NETBOX_CREDENTIAL_VARIABLE] = netbox_token(load_netbox_env())
    return environment


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


def bearer_label(admin_token: str) -> str:
    """Return what the URLs print for the Sync API's bearer token.

    The default token of `admin` is a public development value, so it is printed when the
    token equals it exactly. A token set through ADMIN_ACCOUNT_TOKEN is the developer's own:
    it is named by the variable, never printed.
    """
    if admin_token == API_TOKEN:
        return API_TOKEN
    return "from ADMIN_ACCOUNT_TOKEN"


def _print_urls(settings: DevSettings, *, host_worker: bool = False) -> None:
    bearer = bearer_label(settings.admin_token)
    print(f" - [{NAMESPACE}] Project     {settings.project}")
    print(f" - [{NAMESPACE}] Preset      {settings.preset.name}: {settings.preset.summary}")
    print(f" - [{NAMESPACE}] Sync API    {settings.sync_api_url}  (bearer {bearer})")
    print(f" - [{NAMESPACE}] Prefect UI  {settings.prefect_url}")
    if settings.infrahub is not None and host_worker:
        print(f" - [{NAMESPACE}] Infrahub    {settings.infrahub_url}  (also for a package the host worker runs)")
    elif settings.infrahub is not None:
        print(f" - [{NAMESPACE}] Infrahub    {settings.infrahub_url}  (worker: {settings.worker_infrahub_url})")
    elif settings.infrahub_network:
        print(f" - [{NAMESPACE}] Infrahub    {settings.worker_infrahub_url} on {settings.infrahub_network}")
    elif host_worker:
        print(f" - [{NAMESPACE}] Infrahub    not part of this stack; the host worker uses the URL a package names")
    else:
        print(
            f" - [{NAMESPACE}] Infrahub    {settings.worker_infrahub_url}  "
            f"(not part of this stack; {INFRAHUB_URL_VARIABLE} changes it)"
        )


def _infrahub_reachable(address: str) -> bool:
    """Report whether Infrahub answers at all; a stopped stack lets the suite report itself."""
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires the service extras

    try:
        httpx.get(f"{address}/api/config", timeout=5)
    except httpx.HTTPError:
        return False
    return True


def _start_settings(context: Context, network: str = "", preset: str = "") -> DevSettings:
    """The settings of a task that starts a preset, refused when it would expose Infrahub."""
    settings = dev_settings(context, infrahub_network=network, preset=preset)
    require_private_infrahub(settings.infrahub, settings.infrahub_admin_token)
    return settings


def _seed_settings(context: Context) -> DevSettings:
    settings = dev_settings(context)
    if settings.infrahub is None and settings.infrahub_network:
        msg = (
            f"dev.seed and dev.smoke write to the stack's own Infrahub; {INFRAHUB_NETWORK_VARIABLE} "
            f"names {settings.infrahub_network}, whose address on this host is not known"
        )
        raise DevStackError(msg)
    if settings.infrahub is None:
        msg = (
            "dev.seed and dev.smoke write to the stack's own Infrahub, and no local Infrahub runs "
            f"(preset {settings.preset.name!r}); start one with `uv run invoke dev.start --preset infrahub`"
        )
        raise DevStackError(msg)
    return settings


def _start_netbox(context: Context, settings: DevSettings) -> None:
    """Make the local NetBox run when the preset includes it, before the worker is created."""
    if settings.preset.netbox:
        ensure_netbox(context, load_netbox_env())


def _seed_preview(context: Context, settings: DevSettings) -> None:
    """Load the smoke dataset into the local Infrahub when the preset includes it: `preview`, never `demo`."""
    if settings.preset.smoke_dataset and settings.infrahub is not None:
        load_smoke_dataset(context, settings.infrahub_url, settings.infrahub_admin_token)


def _report_left_running(context: Context, settings: DevSettings) -> None:
    """Name the local Infrahub when it still runs from an earlier, larger preset.

    `up` starts the preset's services and leaves every other container as it is, so a
    move to a lighter preset does not stop Infrahub.
    """
    if settings.infrahub is not None or "infrahub-server" not in _running_services(context, settings.project):
        return
    print(
        f" - [{NAMESPACE}] The local Infrahub from an earlier preset still runs; the {settings.preset.name!r} "
        "preset does not use it. `uv run invoke dev.stop` stops every container of the stack"
    )


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


def host_object_store_url(settings: DevSettings, endpoint: str) -> str:
    """The object store endpoint a host process uses, from the endpoint its container gets.

    An endpoint that names the bundled store's service moves onto the store's loopback
    publication; any other endpoint is an external store and is kept as it is. An endpoint
    the URL parser refuses is kept too, so `sync-bootstrap` reports it by name.

    Raises:
        DevStackError: when the endpoint names the bundled store but the file leaves the
            store out, for example with INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0. The message
            never prints the endpoint, which can carry a user name and password.
    """
    try:
        host = urlsplit(endpoint).hostname
    except ValueError:
        return endpoint
    if host != BUNDLED_STORE_SERVICE:
        return endpoint
    if settings.object_store is None:
        msg = (
            f"{S3_ENDPOINT_VARIABLE} names the bundled store, but {DEV_COMPOSE_FILE} leaves the "
            f"{BUNDLED_STORE_SERVICE} service out or does not publish it; set {S3_ENDPOINT_VARIABLE} to the "
            f"external store, or unset {BUNDLED_STORE_VARIABLE} to run the bundled store"
        )
        raise DevStackError(msg)
    return _on_loopback(endpoint, settings.object_store)


def host_environment(settings: DevSettings, service: Literal["sync-api", "sync-worker"]) -> dict[str, str]:
    """The environment of `dev.api` or `dev.worker`: the container's, moved onto the host.

    The settings are the ones the `sync-api` or `sync-worker` container would get, so a
    shell variable that changes the containers changes the host processes too. Only the
    addresses and paths differ: PostgreSQL, the bundled object store and the Prefect
    server are reached on their loopback publications instead of their service names, an
    external object store is reached at its own endpoint, and the CA directory is the
    host directory INFRAHUB_SYNC_CA_CERTIFICATES names. A variable resolved to an empty
    value is left out, so the shell's own value, if any, applies.
    """
    if settings.postgres is None:
        msg = f"{DEV_COMPOSE_FILE} must publish postgres for the host processes"
        raise DevStackError(msg)
    source = settings.api_environment if service == "sync-api" else settings.worker_environment
    binds = settings.api_binds if service == "sync-api" else settings.worker_binds
    environment = {key: value for key, value in source.items() if value}
    if DATABASE_URL_VARIABLE not in environment:
        msg = f"{service} has no {DATABASE_URL_VARIABLE} in {DEV_COMPOSE_FILE}"
        raise DevStackError(msg)
    environment[DATABASE_URL_VARIABLE] = _on_loopback(environment[DATABASE_URL_VARIABLE], settings.postgres)
    if S3_ENDPOINT_VARIABLE in environment:
        environment[S3_ENDPOINT_VARIABLE] = host_object_store_url(settings, environment[S3_ENDPOINT_VARIABLE])
    # The container path exists only in the container. A bind mount there carries the
    # host directory of INFRAHUB_SYNC_CA_CERTIFICATES; without one, the mount is the
    # empty named volume and the host processes trust no extra certificate.
    ca_directory = environment.pop(CA_DIRECTORY_VARIABLE, "")
    if ca_directory in binds:
        environment[CA_DIRECTORY_VARIABLE] = binds[ca_directory]
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


def _running_services(context: Context, project: str = "") -> set[str]:
    """Return the stack's services that have a running container; no project means the file's own."""
    command = f"{_compose_command(project, ALL_PROFILES)} ps --services --status running"
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
    running = _running_services(context, settings.project)
    return BUILD_SERVICE in running and "sync-api" not in running


def check_host_processes(settings: DevSettings) -> None:
    """Fail naming the missing host process, from the Sync API's `GET /api/status` alone.

    No answer names `dev.api`. An answer whose worker is not available names
    `dev.worker`. The Sync API reads worker availability from Prefect itself, so this
    check never calls the Prefect API.
    """
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires the service extras

    status_url = f"{settings.sync_api_url}/api/status"
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


_START_HELP = {
    "preset": PRESET_HELP + f". Default: {PRESET_VARIABLE}, else what runs, else {DEFAULT_PRESET}",
    "infrahub-network": "Use the Infrahub on this Docker network; the preset is then `sync`",
}


def _start_stack(context: Context, *, preset: str = "", infrahub_network: str = "") -> None:
    """Start one preset in containers: what `dev.start`, `dev.infrahub` and `dev.preview` run."""
    _run_stack(context, _start_settings(context, infrahub_network, preset))


def _run_stack(context: Context, settings: DevSettings) -> None:
    """Start the containers of resolved settings, seed the smoke dataset when the preset has it, print the URLs."""
    _rebuild_when_stale(context, settings)
    _start_netbox(context, settings)
    compose(
        context,
        f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}",
        ComposeTarget(project=settings.project, profiles=settings.profiles, env=_stack_env(settings)),
        pty=True,
    )
    _attach_worker(context, settings)
    _report_left_running(context, settings)
    _seed_preview(context, settings)
    _print_urls(settings)
    print(f" - [{NAMESPACE}] After a Python change: `uv run invoke dev.restart`")
    print(f" - [{NAMESPACE}] After a web interface change: `uv run invoke dev.start`")


@task(name="start", help=_START_HELP)
def dev_start(context: Context, preset: str = "", infrahub_network: str = "") -> None:
    """Start the core and Sync services, and the preset's Infrahub and NetBox; rebuild the image when needed.

    The preset is --preset, else INFRAHUB_SYNC_DEV_PRESET, else the one the running
    containers show, else `sync`. With --infrahub-network NAME (or
    INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK), the preset is `sync` and the worker joins NAME.
    When the local NetBox runs, the worker also joins NetBox's network. Refuses to start
    when INFRAHUB_BIND_ADDRESS publishes Infrahub beyond loopback and
    INFRAHUB_INITIAL_ADMIN_TOKEN keeps the public default.
    """
    _start_stack(context, preset=preset, infrahub_network=infrahub_network)


@task(name="infrahub")
def dev_infrahub(context: Context) -> None:
    """Start the `infrahub` preset: the Sync services and a local Infrahub (`dev.start --preset infrahub`)."""
    _start_stack(context, preset="infrahub")


@task(name="preview")
def dev_preview(context: Context) -> None:
    """Start the `preview` preset: a local Infrahub, the local NetBox and the smoke dataset (`dev.start --preset preview`).

    A NetBox that does not exist yet gets the `demo` dataset; an existing NetBox keeps its
    data. The smoke dataset is written to the local Infrahub, as `dev.seed` does.
    """
    _start_stack(context, preset="preview")


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
    compose(context, "stop", ComposeTarget(project=stack_project(context), profiles=ALL_PROFILES), pty=True)
    print(f" - [{NAMESPACE}] Stopped; `uv run invoke dev.start` starts it again with its data")


@task(name="destroy")
def dev_destroy(context: Context) -> None:
    """Remove the containers and their volumes; keep the image.

    Destructive by design: the Postgres databases, the object store's buckets and the
    Infrahub data go with it, which is what makes the next `dev.start` a first start again.
    """
    project, image = _stack_identity(context)
    _destroy(context, project=project)
    print(f" - [{NAMESPACE}] Removed the stack and its data; {image} is kept")


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
        ComposeTarget(project=stack_project(context), profiles=ALL_PROFILES),
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
            f" - [{NAMESPACE}] No Sync containers run; checking dev.api and dev.worker through {settings.sync_api_url}/api/status"
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


@task(name="deps", help=_START_HELP)
def dev_deps(context: Context, preset: str = "", infrahub_network: str = "") -> None:
    """Start the core services, and the preset's Infrahub and NetBox, for a Sync API and worker run on the host.

    Rebuilds the image when its inputs changed, starts the containers, converges the
    database, the bucket, the work pool and the deployment with a one-off `sync-bootstrap`
    container, prints the URLs and returns. Run `dev.api` and `dev.worker` next, each in
    its own terminal. The preset is chosen as `dev.start` chooses it. Without a local
    Infrahub (the `sync` preset, or --infrahub-network NAME), the host worker reaches the
    Infrahub at INFRAHUB_SYNC_DEV_INFRAHUB_URL, which must be an address the host can
    resolve. Refuses to start when INFRAHUB_BIND_ADDRESS publishes Infrahub beyond
    loopback and INFRAHUB_INITIAL_ADMIN_TOKEN keeps the public default.
    """
    settings = _start_settings(context, infrahub_network, preset)
    # Refuses the bundled store's endpoint without the bundled store before anything starts.
    object_store = host_object_store_url(settings, settings.worker_environment.get(S3_ENDPOINT_VARIABLE, ""))
    _rebuild_when_stale(context, settings)
    _start_netbox(context, settings)
    profiles = settings.preset.deps_profiles
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
    _report_left_running(context, settings)
    _seed_preview(context, settings)
    _print_urls(settings, host_worker=True)
    print(f" - [{NAMESPACE}] PostgreSQL  {_publication_address(settings.postgres)}")
    print(f" - [{NAMESPACE}] Object store {object_store or 'not set'}")
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


def _frontend_submodule_present() -> bool:
    return (REPO_BASE / FRONTEND_UI_PACKAGE / "package.json").is_file()


def _fetch_frontend_submodule(context: Context) -> None:
    """Fetch the `frontend/vendor/infrahub` submodule when it is missing; the image build needs it too."""
    if not _frontend_submodule_present():
        with context.cd(ESCAPED_REPO_PATH):
            context.run(f"git submodule update --init {FRONTEND_SUBMODULE}", pty=True)


@task(name="frontend")
def dev_frontend(context: Context) -> None:
    """Run the web interface's development server on the host, against the stack's Sync API.

    Fetches the `frontend/vendor/infrahub` submodule when it is missing (a checkout it
    already holds is left as it is), installs the locked dependencies, then runs `pnpm dev`
    in the foreground. The server forwards `/api` to the Sync API unchanged and adds no
    token: the person signs in on the page with an account name and a password. The task prints
    how to sign in as `admin`: the variable that holds the password and its public default, never
    a password or a token the developer set.
    """
    settings = dev_settings(context)
    environment = {FRONTEND_URL_VARIABLE: settings.sync_api_url}
    _fetch_frontend_submodule(context)
    with context.cd(escape_path(REPO_BASE / FRONTEND_DIRECTORY)):
        context.run("pnpm install --frozen-lockfile", pty=True)
        print(f" - [{NAMESPACE}] Web interface on {FRONTEND_URL}, Sync API {settings.sync_api_url}; Ctrl-C stops it")
        print(
            f" - [{NAMESPACE}] Sign in on the page as admin, with the password in ADMIN_ACCOUNT_PASSWORD"
            f" (by default {ADMIN_PASSWORD})"
        )
        context.run("pnpm dev", env=environment, pty=True)


def _demo_environment(settings: DevSettings) -> dict[str, str]:
    """The variables `infrahub-sync demo prepare` reads, for the stack's Infrahub and Sync API.

    The Sync token is the development token when the Sync API accepts it. With tokens set
    through BEARER_TOKENS, the command inherits INFRAHUB_SYNC_API_TOKEN from the shell, so
    the token never passes through this task.

    Raises:
        DevStackError: when the Sync API does not accept the development token and the
            shell does not set INFRAHUB_SYNC_API_TOKEN.
    """
    environment = {
        INFRAHUB_TOKEN_VARIABLE: settings.infrahub_admin_token,
        SYNC_URL_VARIABLE: settings.sync_api_url,
    }
    if bearer_label(settings.bearer_tokens) == API_TOKEN:
        environment[SYNC_TOKEN_VARIABLE] = API_TOKEN
    elif not os.environ.get(SYNC_TOKEN_VARIABLE):
        msg = (
            f"the Sync API takes its tokens from BEARER_TOKENS; set {SYNC_TOKEN_VARIABLE} to one of them "
            "before `uv run invoke dev.demo`"
        )
        raise DevStackError(msg)
    return environment


def _admin_token_label(token: str) -> str:
    """The Infrahub admin token as `dev.demo` prints it: the public development default, or the variable."""
    return token if token == PUBLIC_INFRAHUB_ADMIN_TOKEN else f"<from {INFRAHUB_ADMIN_TOKEN_VARIABLE}>"


def _report_demo_netbox(found: str) -> None:
    """Say which data the local NetBox holds: the `demo` dataset when it was new, else what is known."""
    if found == "absent":
        print(f" - [{NAMESPACE}] NetBox was new: loaded the {DEMO_DATASET!r} dataset")
        return
    dataset = recorded_dataset()
    held = f"it holds {dataset}, loaded from this checkout" if dataset else "the dataset it holds is not known"
    print(f" - [{NAMESPACE}] The local NetBox already existed and keeps its data; {held}")
    print(
        f" - [{NAMESPACE}] To load the {DEMO_DATASET!r} dataset: `uv run invoke netbox.seed --dataset {DEMO_DATASET}`"
    )


# How many lines of `demo prepare` output a failure to read its result shows.
DEMO_OUTPUT_LINES = 4


def _prepare_demo(context: Context, settings: DevSettings, environment: Mapping[str, str]) -> dict[str, Any]:
    """Run `infrahub-sync demo prepare --json` on the host and return the object it prints.

    Raises:
        DevStackError: when the command fails or prints no configuration ID and version.
    """
    command = " ".join(
        [
            DEMO_PREPARE_COMMAND,
            f"--netbox-url {shlex.quote(WORKER_NETBOX_URL)}",
            f"--infrahub-url {shlex.quote(WORKER_INFRAHUB_URL)}",
            f"--infrahub-address {shlex.quote(settings.infrahub_url)}",
            "--json",
        ]
    )
    print(f" - [{NAMESPACE}] Loading the demo schema and registering the NetBox configuration")
    with context.cd(ESCAPED_REPO_PATH):
        result = context.run(command, env=dict(environment), hide="out", warn=True, pty=False)
    if result is None or not result.ok:
        msg = "`infrahub-sync demo prepare` failed; its error is printed above"
        raise DevStackError(msg)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        prepared = json.loads(lines[-1]) if lines else None
    except ValueError:
        prepared = None
    if not isinstance(prepared, dict) or "config_id" not in prepared or "version" not in prepared:
        msg = "`infrahub-sync demo prepare --json` printed no configuration ID and version"
        if lines:
            # Standard output holds the result and any notes, never a token: tokens reach the
            # command through the environment, and its errors go to standard error.
            last_lines = "\n".join(f"   {line}" for line in lines[-DEMO_OUTPUT_LINES:])
            msg = f"{msg}; its last lines:\n{last_lines}"
        raise DevStackError(msg)
    return prepared


def _print_demo_next_steps(settings: DevSettings, prepared: Mapping[str, Any]) -> None:
    sync_token = bearer_label(settings.bearer_tokens)
    if sync_token != API_TOKEN:
        sync_token = f"<{sync_token}>"
    config_id = shlex.quote(str(prepared["config_id"]))
    version = shlex.quote(str(prepared["version"]))
    print(f" - [{NAMESPACE}] schema: {prepared.get('schema', 'unknown')}")
    print(f" - [{NAMESPACE}] configuration: {prepared.get('configuration', 'unknown')}")
    print(f" - [{NAMESPACE}] Configuration  {prepared['config_id']}, version {prepared['version']}")
    print(f" - [{NAMESPACE}] Web interface  {settings.sync_api_url}  (token to enter: {sync_token})")
    print(f" - [{NAMESPACE}] Sync API       {settings.sync_api_url}")
    print(f" - [{NAMESPACE}] Next, a first plan on a new branch:")
    print(
        f"     export INFRAHUB_ADDRESS={settings.infrahub_url} "
        f"{INFRAHUB_TOKEN_VARIABLE}={_admin_token_label(settings.infrahub_admin_token)}"
    )
    print(f"     export {SYNC_URL_VARIABLE}={settings.sync_api_url} {SYNC_TOKEN_VARIABLE}={sync_token}")
    print(f"     uv run infrahubctl branch create {DEMO_BRANCH}")
    print(
        f"     uv run infrahub-sync plan --config-id {config_id} --version {version} "
        f'--branch {DEMO_BRANCH} --reason "demo"'
    )


@task(name="demo")
def dev_demo(context: Context) -> None:
    """Start the `demo` preset and prepare it for the NetBox demo; then print the first plan's commands.

    Starts the core and Sync services, a local Infrahub and the local NetBox. A NetBox that
    does not exist yet gets the `demo` dataset; an existing NetBox keeps its data, and the
    task prints the command that reloads the `demo` dataset. Then runs `infrahub-sync demo
    prepare` on the host, from this checkout: it loads the demo schema into the local
    Infrahub and registers the NetBox configuration with the addresses the worker uses. The
    tokens pass in that command's environment, never as arguments. Running it again changes
    nothing and says so. It never plans, applies or approves.
    """
    settings = _start_settings(context, "", "demo")
    environment = _demo_environment(settings)
    found = netbox_state(context, load_netbox_env())
    _fetch_frontend_submodule(context)
    _run_stack(context, settings)
    _report_demo_netbox(found)
    prepared = _prepare_demo(context, settings, environment)
    _print_demo_next_steps(settings, prepared)


def _publication_address(publication: Publication | None) -> str:
    return publication.address if publication is not None else "not published"


ns = Collection(
    "dev",
    dev_build,
    dev_start,
    dev_infrahub,
    dev_preview,
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
    dev_frontend,
    dev_demo,
)
