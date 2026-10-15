"""Properties of the root `docker-compose.yml`, read from Compose's own model.

Almost every assertion below reads `docker compose config` output, so it
describes what Compose will run rather than what the file appears to say:
interpolation, extension merging, and defaulting have already happened. A
property that would survive an edit to the YAML but change what runs is not a
property this suite can be fooled by.

The exceptions read the file's text on purpose: the image reference form and the
`:?` guard on each credential are properties of how the file is written, which
interpolation erases before a model exists.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.credentials import ENV_CREDENTIAL_PREFIX
from tests.compose.conftest import (
    CLI_PROFILE,
    COMPOSE_FILE,
    CONTRACT_ENVIRONMENT,
    PREFECT_UI_OVERRIDE,
    REPO_ROOT,
    SCRATCH_OPTIONS,
    SYNC_SCRATCH_ROOTS,
    SYNC_SERVICES,
    compose,
    mount_sources,
    resolve,
    resolve_privately,
    service,
)
from tests.compose.redaction import capture
from tests.docker_image import DEFAULT_IMAGE_REPOSITORY, IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV

if TYPE_CHECKING:
    from collections.abc import Mapping

# An immutable reference is one of exactly two forms: the local Docker image ID,
# which is the OCI configuration digest a build recorded, or a published
# repository pinned to a manifest digest. A tag matches neither.
IMMUTABLE_REFERENCE = re.compile(r"^(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})$")
# Every service that runs the Sync image, the Prefect server and the opt-in CLI
# included, and the one image reference form they all use: the registry
# repository and the release version, each overridable.
ALL_SYNC_SERVICES = ("prefect-server", "sync-bootstrap", "sync-api", "sync-worker", "cli")
SYNC_IMAGE_FORM = re.compile(
    r'^\s*image:\s*"\$\{INFRAHUB_SYNC_DOCKER_IMAGE:-registry\.opsmill\.io/opsmill/infrahub-sync\}'
    r':\$\{VERSION:-(?P<version>[0-9][0-9A-Za-z.+-]*)\}"\s*$'
)
# The operator credentials: never defaulted, so each is guarded with `:?`.
CREDENTIALS = (
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD",
    "INFRAHUB_SYNC_PRODUCT_PASSWORD",
    "INFRAHUB_SYNC_PREFECT_PASSWORD",
    "INFRAHUB_SYNC_S3_ACCESS_KEY",
    "INFRAHUB_SYNC_S3_SECRET_KEY",
    "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS",
)
# The database bootstrap script: a top-level `configs` entry, mounted where the
# job's entrypoint runs it.
BOOTSTRAP_CONFIG = "db-bootstrap-script"
BOOTSTRAP_TARGET = "/usr/local/bin/databases.sh"
TAGGED_INDEX_REFERENCE = re.compile(r"^(?:[^\s/@]+/)*[^/\s@:]+:[^/\s@:]+@sha256:[0-9a-f]{64}$")
MINIO_IMAGE = (
    "cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba"
)
DEVELOPMENT_COMPOSE = Path(__file__).resolve().parents[2] / "development"
DESTINATION_COMPOSE = DEVELOPMENT_COMPOSE / "docker-compose.infrahub.yml"
PREVIEW_COMPOSE = DEVELOPMENT_COMPOSE / "docker-compose.preview.yml"
PREVIEW_ENV = DEVELOPMENT_COMPOSE / "preview.env"
DEVELOPMENT_FILE = DEVELOPMENT_COMPOSE / "docker-compose.dev.yml"

# The three Compose files of the repository, each resolved the way it runs: the
# operator file with its required inputs and the CLI profile, the development file
# alone, and the preview file over the Infrahub file it overrides, with its env file.
REPOSITORY_STACKS = ("operator", "development", "preview")
# The images the development and preview stacks build from this checkout. They are
# the Sync image of their stack, so no digest names them.
CHECKOUT_IMAGES = {"development": "infrahub-sync:dev", "preview": "infrahub-sync:preview"}
# The one image other than Sync that a repository file names by tag alone: the
# development PostgreSQL, which predates the digest rule. Recorded here so that a
# second exception fails the test instead of joining it unnoticed.
UNPINNED_IMAGES = {("development", "postgres"): "postgres:16-alpine"}
# The Prefect server of each stack that runs from the Sync image.
PREFECT_SERVERS = {"operator": "prefect-server", "development": "prefect-server", "preview": "sync-prefect"}
PREFECT_SERVER_COMMAND = ["python", "-m", "infrahub_sync.service.prefect_server"]
# The preview settings that used to select the stock Prefect image. Written in two
# parts, so that this file does not match its own search.
RETIRED_PREFECT_IMAGE_SETTING = "PREVIEW_PREFECT_" + "IMAGE_"

# The two durable volumes and the services allowed to mount them. The short-lived
# owner repair shares the object store's volume before the server starts.
PERSISTENT_VOLUMES = {"postgres-data", "object-store-data"}
PERSISTENT_SERVICES = {"postgres", "object-store-init", "object-store"}

# The one read-only mount a Sync service takes: the operator's CA certificates,
# trusted in addition to the public roots. With the setting unset its source is
# an empty named volume that nothing writes.
CA_SETTING = "INFRAHUB_SYNC_CA_CERTIFICATES"
CA_TARGET = "/etc/infrahub-sync/ca"
CA_VOLUME = "sync-ca-certificates"
CA_DIRECTORY_SETTING = "INFRAHUB_SYNC_CA_DIRECTORY"
CA_SERVICES = {"sync-bootstrap", "sync-api", "sync-worker"}

# The test-only override, and the host route it adds -- in the form Compose
# resolves it to, since the file writes `name:value` and the model renders
# `name=value`. One service reaches the declared destination: the worker that
# runs against it. The API resolves runs out of PostgreSQL and dispatches through
# Prefect, and the bootstrap job reaches no destination at all.
FIXTURE_OVERRIDE = Path(__file__).resolve().parent / "fixture-override.yaml"
HOST_ROUTE = "host.docker.internal=host-gateway"
ROUTED_SERVICES = {"sync-worker"}

# The destination credential, and the two services that resolve one. The API
# resolves it for the destination schema reads it serves; the worker resolves it
# for a run. Nothing else has a destination to reach.
DESTINATION_CREDENTIAL = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
DESTINATION_CREDENTIAL_RECEIVERS = {"sync-api", "sync-worker"}

# The credential variables of the bundled adapters, as
# `contracts/environment-variables.md` of spec 013 lists them. Written out, so a
# change to an adapter's declared credential settings fails here until the
# contract, the file and the documentation follow it.
CONTRACT_ADAPTER_CREDENTIALS = frozenset(
    {
        "INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME",
        "INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD",
        "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_TOKEN",
        "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_USERNAME",
        "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_PASSWORD",
        "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN",
        "INFRAHUB_SYNC_CREDENTIAL_IPFABRICSYNC_AUTH",
        "INFRAHUB_SYNC_CREDENTIAL_NAUTOBOT_TOKEN",
        "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN",
        "INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_TOKEN",
        "INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_USERNAME",
        "INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_PASSWORD",
        "INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_TOKEN",
        "INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_USERNAME",
        "INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_PASSWORD",
        "INFRAHUB_SYNC_CREDENTIAL_SLURPITSYNC_API_KEY",
        "INFRAHUB_SYNC_CREDENTIAL_SLURPITSYNC_TOKEN",
    }
)
# The one name that does not follow `<prefix><ADAPTER>_<SETTING>`: the Infrahub
# token kept the name it had before the rule existed.
CREDENTIAL_NAME_EXCEPTIONS = {("infrahub", "token"): DESTINATION_CREDENTIAL}


def credential_variable(adapter: str, setting: str) -> str:
    """Return the environment variable a package names for one adapter credential setting."""
    exception = CREDENTIAL_NAME_EXCEPTIONS.get((adapter, setting))
    if exception is not None:
        return exception
    return f"{ENV_CREDENTIAL_PREFIX}{adapter.upper()}_{setting.upper()}"


# Built from the capability registry, the declaration the configuration checks enforce.
ADAPTER_CREDENTIALS = frozenset(
    credential_variable(adapter, setting)
    for adapter, capabilities in BUILTIN_ADAPTER_CAPABILITIES.items()
    for setting in capabilities.credential_setting_paths
)

# The source credentials. Only a run consumes one, and only the worker runs one,
# so the worker is the only service that may be given any of them.
SOURCE_CREDENTIAL_SETTINGS = tuple(sorted(CONTRACT_ADAPTER_CREDENTIALS - {DESTINATION_CREDENTIAL}))
SOURCE_CREDENTIAL_RECEIVERS = {"sync-worker"}

# Every credential an operator's `.env` holds, and the exact services it reaches,
# as the contract's "Receivers of each credential" table lists them for the
# default deployment, which runs the bundled object store.
CREDENTIAL_RECEIVERS: dict[str, frozenset[str]] = {
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": frozenset({"postgres", "db-bootstrap"}),
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": frozenset({"db-bootstrap", "sync-bootstrap", "sync-api", "sync-worker"}),
    "INFRAHUB_SYNC_PREFECT_PASSWORD": frozenset({"db-bootstrap", "prefect-server"}),
    "INFRAHUB_SYNC_PREFECT_AUTH_STRING": frozenset({"prefect-server", "sync-bootstrap", "sync-api", "sync-worker"}),
    "INFRAHUB_SYNC_S3_ACCESS_KEY": frozenset({"sync-bootstrap", "sync-api", "sync-worker", "object-store"}),
    "INFRAHUB_SYNC_S3_SECRET_KEY": frozenset({"sync-bootstrap", "sync-api", "sync-worker", "object-store"}),
    "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": frozenset({"sync-api"}),
    "INFRAHUB_SYNC_API_TOKEN": frozenset({"cli"}),
    DESTINATION_CREDENTIAL: frozenset(DESTINATION_CREDENTIAL_RECEIVERS),
    **dict.fromkeys(SOURCE_CREDENTIAL_SETTINGS, frozenset(SOURCE_CREDENTIAL_RECEIVERS)),
}

# The opt-in client, and the profile that is the only way to resolve it.
CLI_SERVICE = "cli"
# The API client credential, and the only service allowed to hold one. It
# authenticates a caller *to* this deployment, so a worker or a job holding it
# would be a service carrying a credential for the service that dispatches it.
CLIENT_CREDENTIAL = "INFRAHUB_SYNC_API_TOKEN"
CLIENT_CREDENTIAL_RECEIVERS = {CLI_SERVICE}

# The Prefect API credential: the operator setting `init` generates, the server
# setting that turns authentication on, and the client setting every Prefect
# caller presents. The bootstrap job applies the deployment, the API submits and
# observes runs, and the worker polls for them; nothing else calls Prefect.
PREFECT_CREDENTIAL_SETTING = "INFRAHUB_SYNC_PREFECT_AUTH_STRING"
PREFECT_SERVER_CREDENTIAL = "PREFECT_SERVER_API_AUTH_STRING"
PREFECT_CLIENT_CREDENTIAL = "PREFECT_API_AUTH_STRING"
PREFECT_CREDENTIAL_RECEIVERS = {"sync-bootstrap", "sync-api", "sync-worker"}

# Where the object store is and how its buckets are addressed: the three Sync
# processes read both, and unset they name the bundled MinIO with path-style
# addressing. The setting that leaves the bundled store out, and the two
# services it scales. The S3 credential reaches the Sync processes and, as its
# root credential, the bundled server.
OBJECT_STORE_SETTINGS = ("INFRAHUB_SYNC_S3_ENDPOINT_URL", "INFRAHUB_SYNC_S3_ADDRESSING_STYLE")
OBJECT_STORE_RECEIVERS = ("sync-bootstrap", "sync-api", "sync-worker")
BUNDLED_OBJECT_STORE_DEFAULTS = {
    "INFRAHUB_SYNC_S3_ENDPOINT_URL": "http://object-store:9000",
    "INFRAHUB_SYNC_S3_ADDRESSING_STYLE": "path",
}
BUNDLED_OBJECT_STORE_SETTING = "INFRAHUB_SYNC_BUNDLED_OBJECT_STORE"
BUNDLED_OBJECT_STORE_SERVICES = ("object-store-init", "object-store")
OBJECT_STORE_CREDENTIALS = ("INFRAHUB_SYNC_S3_ACCESS_KEY", "INFRAHUB_SYNC_S3_SECRET_KEY")
OBJECT_STORE_CREDENTIAL_RECEIVERS = {"sync-bootstrap", "sync-api", "sync-worker", "object-store"}


def services(model: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(model["services"])


def published(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(entry) for entry in definition.get("ports") or []]


def is_ca_mount(mount: Mapping[str, Any]) -> bool:
    """Whether a resolved mount is the read-only CA directory."""
    return mount.get("target") == CA_TARGET and mount.get("read_only") is True


def writable_mounts(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the mounts of one resolved service that are not read-only."""
    return [dict(mount) for mount in definition.get("volumes") or [] if mount.get("read_only") is not True]


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------


def test_the_api_and_the_worker_share_no_writable_mount_source(model: dict[str, Any]) -> None:
    """A shared writable source is a channel run state could cross without the object store.

    The deployment's whole isolation claim is that a submitter and the worker that
    serves it have no writable filesystem in common, so the intersection is the
    thing to assert — not that each one's list happens to look right today. The
    read-only CA directory they both mount carries public certificates in one
    direction only, from the operator, and neither side can write to it.
    """
    api = mount_sources({"volumes": writable_mounts(service(model, "sync-api"))})
    worker = mount_sources({"volumes": writable_mounts(service(model, "sync-worker"))})

    assert api & worker == set(), f"sync-api and sync-worker both mount {sorted(api & worker)}"


@pytest.mark.parametrize("name", ALL_SYNC_SERVICES)
def test_a_sync_service_mounts_only_the_read_only_ca_directory(cli_model: dict[str, Any], name: str) -> None:
    """Not even a private writable mount: a writable path that survives its container is state.

    Disjoint mounts would still leave each side a place to keep a cache or a run
    directory across a restart, which is the second half of what the object-store
    transport exists to replace. The one exception is the read-only CA directory,
    on the three services that build clients at start.
    """
    definition = service(cli_model, name)
    targets = [(mount.get("target"), mount.get("read_only") is True) for mount in definition.get("volumes") or []]
    expected = [(CA_TARGET, True)] if name in CA_SERVICES else []

    assert targets == expected, f"{name} mounts {targets}"


@pytest.mark.parametrize("name", ALL_SYNC_SERVICES)
def test_a_sync_service_runs_read_only_over_the_image_declared_scratch(cli_model: dict[str, Any], name: str) -> None:
    """The declared roots are tmpfs, handed to the runtime user, and nothing else is writable.

    A tmpfs mount takes its own ownership, so without the options a container
    running as 10001 finds a root-owned directory and fails on its first write.
    The Prefect server copies its UI files under the Prefect scratch root.
    """
    definition = service(cli_model, name)
    expected = [f"{root}:{SCRATCH_OPTIONS}" for root in SYNC_SCRATCH_ROOTS]

    assert definition.get("read_only") is True, f"{name} does not run on a read-only root filesystem"
    assert definition.get("tmpfs") == expected, f"{name} declares scratch {definition.get('tmpfs')}"


def test_only_postgresql_and_the_object_store_keep_a_named_volume(model: dict[str, Any]) -> None:
    """The one-shot owner repair shares only the object store's durable volume.

    The empty CA placeholder is the only other declared volume, and every service
    that mounts it does so read-only, so it never keeps data.
    """
    declared = set(model.get("volumes") or {})
    holders = {
        name
        for name, definition in services(model).items()
        if any(mount.get("type") == "volume" for mount in writable_mounts(definition))
    }
    placeholder_holders = {
        name
        for name, definition in services(model).items()
        for mount in definition.get("volumes") or []
        if mount.get("type") == "volume" and mount.get("source") == CA_VOLUME
    }

    assert declared == PERSISTENT_VOLUMES | {CA_VOLUME}, f"the file declares the volumes {sorted(declared)}"
    assert holders == PERSISTENT_SERVICES, f"writable named volumes are held by {sorted(holders)}"
    assert placeholder_holders == CA_SERVICES, f"{CA_VOLUME} is mounted by {sorted(placeholder_holders)}"


def test_prefect_keeps_no_state_of_its_own(model: dict[str, Any]) -> None:
    """Its records live in PostgreSQL, so a replacement container resumes from them.

    Prefect defaults to a SQLite file inside its container. Left that way the
    server would hold deployment and work-pool state no other service could see,
    and a restart that replaced the container would silently lose it. It mounts
    no volume and no bind, not even the CA directory: it calls no TLS endpoint.
    """
    definition = service(model, "prefect-server")
    connection = definition["environment"]["PREFECT_SERVER_DATABASE_CONNECTION_URL"]
    mounts = [(mount.get("type"), mount.get("target")) for mount in definition.get("volumes") or []]

    assert mounts == [], f"prefect-server mounts {mounts}"
    assert connection.startswith("postgresql"), f"prefect-server is configured on {connection.split(':', 1)[0]}"


def test_the_worker_is_given_no_configuration_directory(model: dict[str, Any]) -> None:
    """A registered run reads its declared configuration from PostgreSQL.

    Naming a directory here would be the start of a worker-side configuration
    surface: something an operator would have to keep in step with the registry,
    and something a run could come to depend on.
    """
    definition = service(model, "sync-worker")

    assert "INFRAHUB_SYNC_CONFIG_DIRECTORY" not in definition["environment"]


def test_no_service_mounts_a_host_path(cli_model: dict[str, Any]) -> None:
    """One self-contained file: with the CA setting unset, nothing it runs reads a host path.

    An equality over every service, the opt-in CLI included, so a bind mount
    added to any of them fails here. A named volume or a tmpfs is not a host path.
    The contract environment leaves `INFRAHUB_SYNC_CA_CERTIFICATES` unset, so the
    CA directory resolves to its empty named volume.
    """
    binds = sorted(
        f"{name}: {mount.get('source')}"
        for name, definition in services(cli_model).items()
        for mount in definition.get("volumes") or []
        if mount.get("type") == "bind"
    )

    assert binds == [], f"these services mount a host path: {binds}"


def test_the_ca_directory_is_mounted_read_only_on_exactly_the_three_sync_services(cli_model: dict[str, Any]) -> None:
    """Only the services that build clients at start trust the operator's CA."""
    holders = {
        name: [
            bool(mount.get("read_only"))
            for mount in definition.get("volumes") or []
            if mount.get("target") == CA_TARGET
        ]
        for name, definition in services(cli_model).items()
    }
    mounted = {name for name, flags in holders.items() if flags}

    assert mounted == CA_SERVICES, f"{CA_TARGET} is mounted by {sorted(mounted)}"
    assert all(holders[name] == [True] for name in CA_SERVICES), f"{CA_TARGET} mounts: {holders}"


def test_the_ca_directory_setting_reaches_exactly_the_three_sync_services(cli_model: dict[str, Any]) -> None:
    receivers = {
        name: (definition.get("environment") or {}).get(CA_DIRECTORY_SETTING)
        for name, definition in services(cli_model).items()
        if CA_DIRECTORY_SETTING in (definition.get("environment") or {})
    }

    assert receivers == dict.fromkeys(CA_SERVICES, CA_TARGET), f"{CA_DIRECTORY_SETTING} reaches {receivers}"


def _ca_sources(model: Mapping[str, Any]) -> dict[str, tuple[str | None, str | None]]:
    return {
        name: (mount.get("type"), mount.get("source"))
        for name, definition in services(model).items()
        for mount in definition.get("volumes") or []
        if is_ca_mount(mount)
    }


def test_without_the_ca_setting_the_source_is_the_empty_named_volume(model: dict[str, Any]) -> None:
    assert _ca_sources(model) == dict.fromkeys(CA_SERVICES, ("volume", CA_VOLUME))


def test_the_ca_setting_binds_the_operator_directory(contract_environment: dict[str, str]) -> None:
    """A `./` path resolves beside the file, the way an operator's `.env` writes it."""
    bound = resolve({**contract_environment, CA_SETTING: "./ca-certificates"})
    expected = str(COMPOSE_FILE.parent / "ca-certificates")

    assert _ca_sources(bound) == dict.fromkeys(CA_SERVICES, ("bind", expected))


def test_the_bootstrap_script_comes_from_an_inline_config(model: dict[str, Any]) -> None:
    """The database bootstrap is carried by the file itself, not by a script beside it."""
    bootstrap = service(model, "db-bootstrap")
    mounted = {(entry["source"], entry["target"]) for entry in bootstrap.get("configs") or []}
    declared = (model.get("configs") or {}).get(BOOTSTRAP_CONFIG) or {}

    assert mounted == {(BOOTSTRAP_CONFIG, BOOTSTRAP_TARGET)}, mounted
    assert "content" in declared, f"{BOOTSTRAP_CONFIG} is not inline content: {sorted(declared)}"
    assert "file" not in declared, f"{BOOTSTRAP_CONFIG} reads a file from the host"
    assert bootstrap["entrypoint"] == ["/bin/sh", BOOTSTRAP_TARGET], bootstrap["entrypoint"]


def test_the_bootstrap_script_binds_every_value_as_a_variable(model: dict[str, Any]) -> None:
    """Compose renders the content once, so a value interpolated into it would be baked in.

    Every shell `$` is escaped in the file, so the rendered script still reads
    each setting from the container's environment at run time, and each one
    crosses into SQL as a bound `psql` variable rather than as text.
    """
    content = model["configs"][BOOTSTRAP_CONFIG]["content"]

    for setting in ("ROLE", "PASSWORD", "DATABASE"):
        for owner in ("PRODUCT", "PREFECT"):
            name = f"INFRAHUB_SYNC_{owner}_{setting}"
            # `config` prints the content with its `$$` escapes kept; Compose
            # renders each as one `$` when it creates the config.
            reference = re.compile(r'"\$?\$\{' + name + r'\}"')
            assert reference.search(content), f"the bootstrap script does not read {name} at run time"
    assert CONTRACT_ENVIRONMENT["INFRAHUB_SYNC_PRODUCT_PASSWORD"] not in content, "a password was baked in"
    assert content.count("\\gexec") == 4, "every role and database creation is a guarded statement"


def test_the_file_declares_no_secret_file(cli_model: dict[str, Any]) -> None:
    """Credentials arrive through the environment, never as a file beside the deployment."""
    assert not cli_model.get("secrets"), f"the file declares secrets {sorted(cli_model.get('secrets') or {})}"
    users = sorted(name for name, definition in services(cli_model).items() if definition.get("secrets"))
    assert users == [], f"these services read a secret file: {users}"


# ---------------------------------------------------------------------------
# Required credentials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CREDENTIALS)
def test_every_reference_to_a_credential_is_guarded_with_a_required_marker(name: str) -> None:
    """Read from the text: each `${NAME...}` is `${NAME:?...}`, so no default can creep in.

    A model cannot answer this, because interpolation has already replaced every
    reference with its value by the time one exists.
    """
    # `$${NAME}` is an escaped, literal `$` for the shell inside the bootstrap
    # script, read at run time from the container: not an interpolation.
    pattern = r"(?<!\$)\$\{" + name + r"(?![A-Z0-9_])([^}]*)\}"
    references = re.findall(pattern, COMPOSE_FILE.read_text(encoding="utf-8"))
    unguarded = [reference for reference in references if not reference.startswith(":?")]

    assert references, f"the file never references {name}"
    assert unguarded == [], f"{name} is referenced without a :? guard: {unguarded}"


@pytest.mark.parametrize("name", CREDENTIALS)
def test_a_missing_credential_stops_compose_naming_it(compose_version: str, name: str) -> None:
    """Compose refuses before any container exists, and the refusal says which one."""
    del compose_version
    without = {key: value for key, value in CONTRACT_ENVIRONMENT.items() if key != name}

    refused = compose(["config", "--quiet"], environment=without)

    assert refused.returncode != 0, f"Compose resolved the file without {name}"
    assert f"{name} is required" in refused.unredacted(), "the refusal does not name the missing credential"


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def test_only_the_api_publishes_a_host_port(model: dict[str, Any]) -> None:
    """Prefect, PostgreSQL, the object store, the worker, and every job stay on the private network."""
    publishers = {name for name, definition in services(model).items() if published(definition)}

    assert publishers == {"sync-api"}, f"host ports are published by {sorted(publishers)}"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({}, "http://127.0.0.1:4200/api", id="default"),
        pytest.param({"INFRAHUB_SYNC_PREFECT_PORT": "4310"}, "http://127.0.0.1:4310/api", id="port-setting"),
        pytest.param({"INFRAHUB_SYNC_BIND_ADDRESS": "0.0.0.0"}, "http://127.0.0.1:4200/api", id="bind-address"),  # noqa: S104
    ],
)
def test_the_prefect_ui_calls_its_api_on_the_opt_in_loopback_port(
    compose_version: str, contract_environment: dict[str, str], overrides: dict[str, str], expected: str
) -> None:
    """The browser reaches the API where `prefect-ui.yml` publishes it, which is never the bind address."""
    del compose_version
    resolved = resolve({**contract_environment, **overrides})

    assert service(resolved, "prefect-server")["environment"]["PREFECT_SERVER_UI_API_URL"] == expected


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0"])  # noqa: S104
def test_the_prefect_ui_override_publishes_prefect_on_loopback_only(
    compose_version: str, contract_environment: dict[str, str], tmp_path: Path, bind_address: str | None
) -> None:
    """The documented `prefect-ui.yml` adds one loopback port, whatever the API's bind address is."""
    del compose_version
    override = tmp_path / "prefect-ui.yml"
    override.write_text(PREFECT_UI_OVERRIDE, encoding="utf-8")
    environment = dict(contract_environment)
    if bind_address is not None:
        environment["INFRAHUB_SYNC_BIND_ADDRESS"] = bind_address

    resolved = resolve(environment, files=(COMPOSE_FILE, override))
    prefect = [
        (entry.get("host_ip"), entry.get("published"), entry.get("target"))
        for entry in published(service(resolved, "prefect-server"))
    ]

    assert prefect == [("127.0.0.1", "4200", 4200)]
    publishers = {name for name, definition in services(resolved).items() if published(definition)}
    assert publishers == {"sync-api", "prefect-server"}, f"host ports are published by {sorted(publishers)}"


def test_every_published_address_is_loopback(model: dict[str, Any]) -> None:
    """The supported topology is one host; external exposure is a separate decision.

    Asserting over every publication rather than the two known ones is what makes
    a third surface added later fail here instead of reaching the network.
    """
    addresses = {
        (name, entry.get("host_ip")) for name, definition in services(model).items() for entry in published(definition)
    }
    external = sorted(name for name, host_ip in addresses if host_ip != "127.0.0.1")

    assert external == [], f"{external} publish to an address other than loopback"


def test_prefect_telemetry_is_off(model: dict[str, Any]) -> None:
    """An optional call home is not something a deployment should have to discover."""
    assert service(model, "prefect-server")["environment"]["PREFECT_SERVER_ANALYTICS_ENABLED"] == "false"


# ---------------------------------------------------------------------------
# The Prefect API credential
# ---------------------------------------------------------------------------


def test_the_prefect_server_requires_the_generated_credential(
    compose_version: str, contract_environment: dict[str, str]
) -> None:
    """A caller that can create a deployment in the pool decides what the worker runs.

    So the server is never started without authentication: the credential is the
    operator's generated setting, and nothing else can stand in for it. Read from
    raw output and compared privately, because the credential is redacted by name.
    """
    del compose_version
    environment = service(resolve_privately(contract_environment), "prefect-server")["environment"]

    requires_it = environment.get(PREFECT_SERVER_CREDENTIAL) == contract_environment[PREFECT_CREDENTIAL_SETTING]
    assert requires_it, f"prefect-server does not require {PREFECT_CREDENTIAL_SETTING}"


def test_every_prefect_client_presents_that_credential_and_nothing_else_holds_it(
    compose_version: str, contract_environment: dict[str, str]
) -> None:
    """The three Sync processes that call Prefect carry it; the CLI and the jobs do not."""
    del compose_version
    resolved = resolve_privately(contract_environment)
    holders = {
        name
        for name, definition in services(resolved).items()
        if PREFECT_CLIENT_CREDENTIAL in (definition.get("environment") or {})
    }

    assert holders == PREFECT_CREDENTIAL_RECEIVERS, f"{PREFECT_CLIENT_CREDENTIAL} is given to {sorted(holders)}"
    for name in sorted(holders):
        presented = service(resolved, name)["environment"][PREFECT_CLIENT_CREDENTIAL]
        same = presented == contract_environment[PREFECT_CREDENTIAL_SETTING]
        assert same, f"{name} presents another credential"


def test_the_cli_service_is_not_given_the_prefect_credential(cli_model: dict[str, Any]) -> None:
    """The operator's client reaches the Sync API alone, so it has no Prefect access to hold."""
    environment = service(cli_model, CLI_SERVICE).get("environment") or {}

    assert PREFECT_CLIENT_CREDENTIAL not in environment


def test_the_bundle_refuses_to_resolve_without_the_prefect_credential(
    compose_version: str, contract_environment: dict[str, str]
) -> None:
    """An empty credential would start an unauthenticated server, so it is not a default."""
    del compose_version
    without = {key: value for key, value in contract_environment.items() if key != PREFECT_CREDENTIAL_SETTING}
    result = compose(["config"], environment={**without, PREFECT_CREDENTIAL_SETTING: ""})

    assert result.returncode != 0
    assert "Prefect API credential" in result.stderr


def test_the_prefect_server_receives_only_its_database_and_api_credentials(
    compose_version: str, contract_environment: dict[str, str]
) -> None:
    """US1 scenario 4: the Sync image holds S3 and source clients, the Prefect server none of their keys.

    Checked by name and by value: no credential variable of another service is
    declared on it, and no other operator credential's value reaches any of its
    variables.
    """
    del compose_version
    resolved = resolve_privately(contract_environment)
    environment = service(resolved, "prefect-server").get("environment") or {}
    credential_names = {
        "POSTGRES_PASSWORD",
        "PGPASSWORD",
        "INFRAHUB_SYNC_DATABASE_URL",
        "INFRAHUB_SYNC_PRODUCT_PASSWORD",
        "INFRAHUB_SYNC_PREFECT_PASSWORD",
        PREFECT_SERVER_CREDENTIAL,
        PREFECT_CLIENT_CREDENTIAL,
        "PREFECT_SERVER_DATABASE_CONNECTION_URL",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS",
        CLIENT_CREDENTIAL,
    }
    declared = {
        name for name in environment if name in credential_names or name.startswith("INFRAHUB_SYNC_CREDENTIAL_")
    }
    carried = {
        (variable, setting)
        for setting, value in contract_environment.items()
        for variable, resolved_value in environment.items()
        if value and value in str(resolved_value)
    }

    assert declared == {"PREFECT_SERVER_DATABASE_CONNECTION_URL", PREFECT_SERVER_CREDENTIAL}, sorted(declared)
    assert carried == {
        ("PREFECT_SERVER_DATABASE_CONNECTION_URL", "INFRAHUB_SYNC_PREFECT_PASSWORD"),
        (PREFECT_SERVER_CREDENTIAL, PREFECT_CREDENTIAL_SETTING),
    }, sorted(variable for variable, _setting in carried)


# ---------------------------------------------------------------------------
# The Prefect server runs from the Sync image
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stack_models(cli_model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The resolved services of each repository Compose file, keyed by `REPOSITORY_STACKS`."""
    resolved = {"operator": services(cli_model)}
    for stack, files, env_files in (
        ("development", (DEVELOPMENT_FILE,), ()),
        ("preview", (DESTINATION_COMPOSE, PREVIEW_COMPOSE), (PREVIEW_ENV,)),
    ):
        result = compose(["config", "--format", "json"], files=files, env_files=env_files, inherit_environment=False)
        assert result.returncode == 0, result.stderr
        resolved[stack] = services(json.loads(result.stdout))
    return resolved


def is_sync_image(stack: str, name: str, definition: Mapping[str, Any]) -> bool:
    """Whether a service of a stack runs that stack's Sync image."""
    if stack == "operator":
        return name in ALL_SYNC_SERVICES
    return definition["image"] == CHECKOUT_IMAGES[stack]


@pytest.mark.parametrize("stack", REPOSITORY_STACKS)
def test_no_service_runs_the_stock_prefect_image(stack_models: dict[str, dict[str, Any]], stack: str) -> None:
    """FR-001, FR-010: no repository Compose file pulls the `prefecthq/prefect` image."""
    stock = sorted(
        name
        for name, definition in stack_models[stack].items()
        if str(definition["image"]).split("@", 1)[0].rsplit(":", 1)[0] == "prefecthq/prefect"
    )

    assert stock == [], f"these {stack} services run the prefecthq/prefect image: {stock}"


@pytest.mark.parametrize("stack", REPOSITORY_STACKS)
def test_the_prefect_server_runs_the_sync_factory_module(stack_models: dict[str, dict[str, Any]], stack: str) -> None:
    """FR-003, FR-010: Prefect's own application, started through the Sync module that builds it."""
    prefect = stack_models[stack][PREFECT_SERVERS[stack]]

    assert prefect["command"] == PREFECT_SERVER_COMMAND
    assert is_sync_image(stack, PREFECT_SERVERS[stack], prefect)


@pytest.mark.parametrize("stack", ["development", "preview"])
def test_the_checkout_stacks_build_their_prefect_server_from_the_repository_dockerfile(
    stack_models: dict[str, dict[str, Any]], stack: str
) -> None:
    """FR-010: the development and preview Prefect servers run what this checkout builds."""
    build = stack_models[stack][PREFECT_SERVERS[stack]].get("build") or {}

    assert Path(build.get("context", "")) == REPO_ROOT, build
    assert build.get("dockerfile") == "Dockerfile", build


def test_no_repository_file_names_the_retired_prefect_image_settings() -> None:
    """FR-011: the preview Prefect image settings are gone; only spec history and the changelog keep them."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed; the search reads the tracked files")
    result = capture(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "grep",
            "--files-with-matches",
            "--fixed-strings",
            RETIRED_PREFECT_IMAGE_SETTING,
            "--",
            ".",
            ":(exclude)dev/",
            ":(exclude)changelog/",
        ],
        timeout=60,
    )
    if result.returncode not in {0, 1}:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")

    assert result.stdout.split() == [], f"these files name {RETIRED_PREFECT_IMAGE_SETTING}*: {result.stdout.split()}"


# ---------------------------------------------------------------------------
# Immutable image identity
# ---------------------------------------------------------------------------


def test_object_store_uses_the_pinned_minio_build_and_migrates_existing_data(model: dict[str, Any]) -> None:
    """Ownership repair completes before the server starts without root privileges."""
    init = service(model, "object-store-init")
    object_store = service(model, "object-store")

    assert init["image"] == MINIO_IMAGE
    assert init["user"] == "0"
    assert init["entrypoint"] == ["/bin/sh", "-ec"]
    assert 'if [ "$$(stat -c %u:%g /data)" != "65532:65532" ]; then' in init["command"][0]
    assert "chown -R 65532:65532 /data" in init["command"][0]
    assert init["restart"] == "no"
    assert init["network_mode"] == "none"
    assert init["read_only"] is True
    assert init["cap_drop"] == ["ALL"]
    assert init["cap_add"] == ["CHOWN"]
    assert {mount["source"] for mount in init["volumes"]} == {"object-store-data"}
    assert object_store["image"] == MINIO_IMAGE
    assert object_store["user"] == "65532:65532"
    assert object_store["command"] == ["server", "/data"]
    assert object_store["depends_on"]["object-store-init"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize("stack", REPOSITORY_STACKS)
def test_every_image_other_than_sync_is_named_by_digest(stack_models: dict[str, dict[str, Any]], stack: str) -> None:
    """FR-014: a tag can be re-pointed; a digest names one artifact for good.

    One assertion over every service of each repository file, so a service added
    later is covered by having been added rather than by somebody remembering to
    extend a list. The Sync image is the one deliberate exception: the operator
    file selects it by release tag, and the other two build it from the checkout.
    The development PostgreSQL is the one recorded exception, in `UNPINNED_IMAGES`.
    """
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in stack_models[stack].items()
        if not is_sync_image(stack, name, definition)
        and UNPINNED_IMAGES.get((stack, name)) != definition["image"]
        and not IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
    )

    assert mutable == [], f"these {stack} services are not pinned to a digest: {mutable}"


def sync_image_lines() -> dict[str, str]:
    """The raw `image:` line of each Sync service, read from the file's text."""
    found: dict[str, str] = {}
    current = ""
    for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines():
        heading = re.fullmatch(r"  ([a-z][a-z0-9-]*):\s*", line)
        if heading:
            current = heading.group(1)
        elif current in ALL_SYNC_SERVICES and line.lstrip().startswith("image:"):
            found[current] = line
    return found


def pinned_versions() -> dict[str, str]:
    """The default `VERSION` each Sync service's image line names, for lines of the right form."""
    return {
        name: match.group("version")
        for name, line in sync_image_lines().items()
        if (match := SYNC_IMAGE_FORM.match(line))
    }


def test_every_sync_service_names_the_registry_image_and_the_release_version() -> None:
    """`${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-X}`, verbatim.

    Read from the text, because the model only shows what the defaults resolved
    to. Every Sync service carries the same reference, so one `VERSION` moves
    all five together, the Prefect server included.
    """
    lines = sync_image_lines()
    versions = pinned_versions()

    assert set(lines) == set(ALL_SYNC_SERVICES), f"Sync services with an image line: {sorted(lines)}"
    assert set(versions) == set(ALL_SYNC_SERVICES), (
        f"services off the reference form: {sorted(set(lines) - set(versions))}"
    )
    assert len(set(versions.values())) == 1, f"the Sync services name different versions: {versions}"


def test_the_sync_image_defaults_to_the_registry_at_the_pinned_version(cli_model: dict[str, Any]) -> None:
    """With nothing set, every Sync service resolves to one registry image."""
    (pinned,) = set(pinned_versions().values())
    expected = f"{DEFAULT_IMAGE_REPOSITORY}:{pinned}"

    resolved = {name: service(cli_model, name)["image"] for name in ALL_SYNC_SERVICES}

    assert set(resolved.values()) == {expected}, resolved


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({IMAGE_VERSION_ENV: "9.9.9"}, f"{DEFAULT_IMAGE_REPOSITORY}:9.9.9", id="version"),
        pytest.param(
            {IMAGE_REPOSITORY_ENV: "infrahub-sync", IMAGE_VERSION_ENV: "compose-test"},
            "infrahub-sync:compose-test",
            id="repository-and-version",
        ),
    ],
)
def test_the_operator_selects_another_sync_image_with_two_settings(
    compose_version: str, contract_environment: dict[str, str], overrides: dict[str, str], expected: str
) -> None:
    """`VERSION` and `INFRAHUB_SYNC_DOCKER_IMAGE` move every Sync service, and nothing else."""
    del compose_version
    result = compose(
        ["--profile", "cli", "config", "--format", "json"], environment={**contract_environment, **overrides}
    )
    assert result.returncode == 0, result.stderr
    resolved = services(json.loads(result.stdout))

    assert {resolved[name]["image"] for name in ALL_SYNC_SERVICES} == {expected}
    assert all(
        IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
        for name, definition in resolved.items()
        if name not in ALL_SYNC_SERVICES
    )


@pytest.mark.parametrize("files", [(DESTINATION_COMPOSE,), (DESTINATION_COMPOSE, PREVIEW_COMPOSE)])
@pytest.mark.parametrize("env_files", [(), (PREVIEW_ENV,)])
def test_destination_and_preview_default_images_are_tagged_indexes(
    compose_version: str, files: tuple[Path, ...], env_files: tuple[Path, ...]
) -> None:
    """Compose's resolved defaults must retain readable tags and immutable digests."""
    del compose_version
    result = compose(["config", "--format", "json"], files=files, env_files=env_files, inherit_environment=False)
    assert result.returncode == 0, result.stderr
    configured = json.loads(result.stdout)
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in services(configured).items()
        if definition["image"] != CHECKOUT_IMAGES["preview"]
        and not TAGGED_INDEX_REFERENCE.fullmatch(str(definition["image"]))
    )
    assert mutable == [], f"destination or preview images lack tag and digest: {mutable}"


def test_preview_env_tags_match_compose_pin_defaults(compose_version: str) -> None:
    """Shipped preview tags and digests stay aligned with the Compose defaults."""
    del compose_version
    models = []
    for env_files in ((), (PREVIEW_ENV,)):
        result = compose(
            ["config", "--format", "json"],
            files=(DESTINATION_COMPOSE, PREVIEW_COMPOSE),
            env_files=env_files,
            inherit_environment=False,
        )
        assert result.returncode == 0, result.stderr
        models.append(services(json.loads(result.stdout)))
    defaults, shipped = models
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert shipped[name]["image"] == defaults[name]["image"], name


def test_tagged_index_reference_requires_a_tag_after_the_last_slash() -> None:
    """A registry port cannot masquerade as an image tag."""
    digest = "sha256:" + "a" * 64
    assert TAGGED_INDEX_REFERENCE.fullmatch(f"registry:5000/image:tag@{digest}")
    assert not TAGGED_INDEX_REFERENCE.fullmatch(f"registry:5000/image@{digest}")


def test_image_overrides_keep_their_matching_digests(compose_version: str) -> None:
    """A custom Infrahub tag can be paired with its own index digest."""
    del compose_version
    infrahub_digest = "@sha256:" + "a" * 64
    result = compose(
        ["config", "--format", "json"],
        files=(DESTINATION_COMPOSE, PREVIEW_COMPOSE),
        env_files=(PREVIEW_ENV,),
        inherit_environment=False,
        environment={
            "MESSAGE_QUEUE_DOCKER_IMAGE": "example.invalid/rabbitmq:custom",
            "INFRAHUB_DOCKER_IMAGE": "example.invalid/infrahub",
            "VERSION": "custom",
            "INFRAHUB_DOCKER_IMAGE_DIGEST": infrahub_digest,
        },
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    assert configured["message-queue"]["image"] == "example.invalid/rabbitmq:custom"
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == f"example.invalid/infrahub:custom{infrahub_digest}"


def test_local_image_override_can_drop_the_digest(compose_version: str) -> None:
    """An empty digest keeps the preexisting local image override usable."""
    del compose_version
    result = compose(
        ["config", "--format", "json"],
        files=(DESTINATION_COMPOSE, PREVIEW_COMPOSE),
        env_files=(PREVIEW_ENV,),
        inherit_environment=False,
        environment={
            "INFRAHUB_DOCKER_IMAGE": "local-infrahub",
            "VERSION": "custom",
            "INFRAHUB_DOCKER_IMAGE_DIGEST": "",
        },
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "local-infrahub:custom"


@pytest.mark.parametrize("digest_override", [{}, {"INFRAHUB_DOCKER_IMAGE_DIGEST": ""}])
def test_direct_compose_local_image_override_drops_the_digest(
    compose_version: str, digest_override: dict[str, str]
) -> None:
    """An image-name override cannot retain the shipped digest without an env file."""
    del compose_version
    result = compose(
        ["config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
        inherit_environment=False,
        environment={"INFRAHUB_DOCKER_IMAGE": "local-infrahub", **digest_override},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "local-infrahub:1.10.6"


def test_direct_compose_digest_override_replaces_the_shipped_digest(compose_version: str) -> None:
    """A digest-only override must resolve to one valid image reference."""
    del compose_version
    digest = "@sha256:" + "a" * 64
    result = compose(
        ["config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
        inherit_environment=False,
        environment={"INFRAHUB_DOCKER_IMAGE_DIGEST": digest},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    expected = f"registry.opsmill.io/opsmill/infrahub:1.10.6{digest}"
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == expected
        assert TAGGED_INDEX_REFERENCE.fullmatch(configured[name]["image"])


def test_standalone_version_override_drops_the_shipped_digest(compose_version: str) -> None:
    """Direct Compose use must not pair a new tag with the shipped digest."""
    del compose_version
    result = compose(
        ["config", "--format", "json"],
        files=(DESTINATION_COMPOSE, PREVIEW_COMPOSE),
        inherit_environment=False,
        environment={"VERSION": "custom"},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "registry.opsmill.io/opsmill/infrahub:custom"
    # `VERSION` is Infrahub's version here; the Prefect server keeps the checkout's build.
    assert configured["sync-prefect"]["image"] == CHECKOUT_IMAGES["preview"]


# ---------------------------------------------------------------------------
# No filesystem input at all
# ---------------------------------------------------------------------------


def test_no_sync_service_takes_a_filesystem_input(model: dict[str, Any]) -> None:
    """A start needs no configuration file, so no service is given one.

    An equality over every Sync service, not the absence of one known mount: a
    configuration handed back to any of them would restore a startup that decides
    what the deployment runs before the operator has registered anything. The
    read-only CA directory holds certificates, not configuration, and is the one
    mount allowed.
    """
    readers = {
        name: other
        for name, definition in services(model).items()
        if name in ALL_SYNC_SERVICES
        and (other := [mount for mount in definition.get("volumes") or [] if not is_ca_mount(mount)])
    }

    assert readers == {}, f"Sync services taking a filesystem input: {sorted(readers)}"


def test_the_file_interpolates_no_declared_configuration_setting(model: dict[str, Any]) -> None:
    """The setting is gone from the shipped startup, not merely unused by it."""
    interpolated = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", COMPOSE_FILE.read_text(encoding="utf-8")))
    resolved = {name for definition in services(model).values() for name in (definition.get("environment") or {})}

    assert "INFRAHUB_SYNC_BOOTSTRAP_CONFIGURATION" not in interpolated
    assert "INFRAHUB_SYNC_BOOTSTRAP_CONFIGURATION" not in resolved


# ---------------------------------------------------------------------------
# The destination credential
# ---------------------------------------------------------------------------


def test_the_destination_credential_reaches_the_api_and_the_worker_and_no_job(model: dict[str, Any]) -> None:
    """Only the two services that resolve a destination are given it."""
    holders = {
        name
        for name, definition in services(model).items()
        if DESTINATION_CREDENTIAL in (definition.get("environment") or {})
    }

    assert holders == DESTINATION_CREDENTIAL_RECEIVERS, f"{DESTINATION_CREDENTIAL} is given to {sorted(holders)}"


def test_the_destination_credential_is_optional_and_resolves_empty_when_unset(
    compose_version: str, contract_environment: dict[str, str]
) -> None:
    """Required interpolation would refuse the resolve before anything is registered."""
    del compose_version
    without = {key: value for key, value in contract_environment.items() if key != DESTINATION_CREDENTIAL}

    resolved = resolve_privately(without)

    for name in sorted(DESTINATION_CREDENTIAL_RECEIVERS):
        environment = service(resolved, name)["environment"]
        assert DESTINATION_CREDENTIAL in environment, f"{DESTINATION_CREDENTIAL} is absent from {name}"
        assert not environment[DESTINATION_CREDENTIAL], f"{DESTINATION_CREDENTIAL} resolved to a value nobody supplied"


def test_the_long_running_sync_services_wait_for_that_convergence(model: dict[str, Any]) -> None:
    """Neither can do its job before the pool, the deployment, and the registry exist.

    The worker in particular refuses to create its own pool, so starting it first
    would leave it restarting until something else made one.
    """
    for name in SYNC_SERVICES:
        depends = service(model, name)["depends_on"]
        assert depends.get("sync-bootstrap", {}).get("condition") == "service_completed_successfully", (
            f"{name} does not wait for sync-bootstrap to complete: {depends}"
        )


def test_the_host_route_is_test_only_and_reaches_exactly_the_destination_consumer(
    model: dict[str, Any], contract_environment: dict[str, str]
) -> None:
    """The shipped file grants it to nobody; the override grants it to the worker alone.

    Both halves are equalities over every service, so a route added to the
    shipped file fails, and adding a service to the override or dropping one
    from it fails too.
    """

    def routed(resolved: Mapping[str, Any]) -> set[str]:
        return {
            name
            for name, definition in services(resolved).items()
            if HOST_ROUTE in (definition.get("extra_hosts") or [])
        }

    assert routed(model) == set(), f"the shipped file routes {sorted(routed(model))} to the host"
    overridden = resolve(contract_environment, files=(COMPOSE_FILE, FIXTURE_OVERRIDE))
    assert routed(overridden) == ROUTED_SERVICES, f"the override routes {sorted(routed(overridden))}"


# ---------------------------------------------------------------------------
# The containerized CLI
# ---------------------------------------------------------------------------


def test_the_cli_service_exists_only_when_its_profile_is_named(
    model: dict[str, Any], cli_model: dict[str, Any]
) -> None:
    """An ordinary start creates no CLI container; naming the profile is what does."""
    assert CLI_SERVICE not in services(model), "the CLI service is part of an ordinary start"
    assert CLI_SERVICE in services(cli_model), "the CLI profile resolves no CLI service"


def test_the_cli_service_is_given_exactly_the_two_settings_it_needs(cli_model: dict[str, Any]) -> None:
    """It talks to the Sync API, so an equality here is what keeps every other credential out.

    A storage, Prefect, source or destination value added to this service would
    fail here even though the CLI kept working.
    """
    environment = service(cli_model, CLI_SERVICE)["environment"]

    assert set(environment) == {"INFRAHUB_SYNC_API_URL", "INFRAHUB_SYNC_API_TOKEN"}, sorted(environment)
    assert environment["INFRAHUB_SYNC_API_URL"] == "http://sync-api:8000"


def test_the_client_credential_is_held_by_the_cli_service_alone(cli_model: dict[str, Any]) -> None:
    """An equality over every service, so a token added to the worker or a job fails here.

    Closing the CLI service's own environment says what it holds; this says that
    nothing else holds the same credential.
    """
    holders = {
        name
        for name, definition in services(cli_model).items()
        if CLIENT_CREDENTIAL in (definition.get("environment") or {})
    }

    assert holders == CLIENT_CREDENTIAL_RECEIVERS, f"{CLIENT_CREDENTIAL} is given to {sorted(holders)}"


def test_the_cli_service_reaches_no_host_and_keeps_nothing(cli_model: dict[str, Any]) -> None:
    """No published port, no dependency startup, no volume, and no Docker socket."""
    definition = service(cli_model, CLI_SERVICE)

    assert published(definition) == [], f"the CLI service publishes {published(definition)}"
    assert definition.get("depends_on") in (None, {}), f"the CLI service waits for {definition.get('depends_on')}"
    assert definition.get("volumes") in (None, []), f"the CLI service mounts {definition.get('volumes')}"
    assert definition.get("restart") == "no"
    assert "/var/run/docker.sock" not in str(definition)


def test_the_cli_service_runs_the_cli_in_the_image_the_deployment_runs(cli_model: dict[str, Any]) -> None:
    """Same image reference, the CLI as its entrypoint, and the image's own user."""
    definition = service(cli_model, CLI_SERVICE)
    api = service(cli_model, "sync-api")
    expected = [f"{root}:{SCRATCH_OPTIONS}" for root in SYNC_SCRATCH_ROOTS]

    assert definition["image"] == api["image"]
    assert definition["entrypoint"] == ["infrahub-sync"]
    assert definition.get("read_only") is True
    assert definition.get("tmpfs") == expected
    # Nothing overrides the user, so the container runs as the image's non-root one.
    assert "user" not in definition, f"the CLI service overrides the image user with {definition.get('user')}"


# ---------------------------------------------------------------------------
# Source credentials
# ---------------------------------------------------------------------------


def test_the_adapter_credentials_follow_the_naming_rule_and_match_the_contract() -> None:
    """The registry's credential settings name exactly the contract's 17 variables.

    A new credential setting on a bundled adapter fails here, so it cannot ship
    without a variable the worker receives.
    """
    assert len(CONTRACT_ADAPTER_CREDENTIALS) == 17
    assert sorted(ADAPTER_CREDENTIALS - CONTRACT_ADAPTER_CREDENTIALS) == [], (
        "registry names a variable the contract lacks"
    )
    assert sorted(CONTRACT_ADAPTER_CREDENTIALS - ADAPTER_CREDENTIALS) == [], (
        "contract names a variable no adapter reads"
    )


def _privately_resolved_with_profile(environment: Mapping[str, str]) -> dict[str, Any]:
    """The resolved file with the CLI profile named, from raw output, for value-level checks.

    The same obligation as `resolve_privately`: compare privately, report names.
    """
    result = compose(["--profile", CLI_PROFILE, "config", "--format", "json"], environment=environment)
    if result.returncode != 0:
        pytest.fail(f"docker compose config refused the file: {result.stderr.strip()}")
    return json.loads(result.unredacted())


@pytest.mark.parametrize("setting", sorted(CREDENTIAL_RECEIVERS))
def test_each_credential_reaches_exactly_its_listed_receivers(
    compose_version: str, contract_environment: dict[str, str], setting: str
) -> None:
    """A planted value is found in the listed services' definitions and in no other.

    The whole resolved definition is searched, not one variable name, because a
    credential also arrives inside another value, such as the product password
    inside `INFRAHUB_SYNC_DATABASE_URL`. The bearer-token setting is JSON, so its
    planted value sits inside a well-formed document.
    """
    del compose_version
    planted = f"receiver-{setting.lower().replace('_', '-')}-8c31d5"
    value = (
        json.dumps({"receiver": {"token": planted}}) if setting == "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS" else planted
    )

    resolved = _privately_resolved_with_profile({**contract_environment, setting: value})

    carriers = {name for name, definition in services(resolved).items() if planted in json.dumps(definition)}
    assert carriers == CREDENTIAL_RECEIVERS[setting], f"{setting} value reached {sorted(carriers)}"


@pytest.mark.parametrize("setting", SOURCE_CREDENTIAL_SETTINGS)
def test_only_the_worker_is_given_a_source_credential(model: dict[str, Any], setting: str) -> None:
    """A secret with no receiver is a secret that cannot leak from one.

    An equality over every service, not a check that the worker has it: a source
    credential added to the API, the bootstrap job, or Prefect would fail here
    even though the worker still worked.
    """
    holders = {name for name, definition in services(model).items() if setting in (definition.get("environment") or {})}

    assert holders == SOURCE_CREDENTIAL_RECEIVERS, f"{setting} is given to {sorted(holders)}"


@pytest.mark.parametrize("setting", SOURCE_CREDENTIAL_SETTINGS)
def test_a_source_credential_is_optional_and_resolves_empty_when_unset(
    contract_environment: dict[str, str], compose_version: str, setting: str
) -> None:
    """A deployment that syncs neither source must still start.

    The contract environment supplies no source token, so an interpolation that
    made one required would have failed the whole resolve, and one that carried
    a default would show it here.
    """
    del compose_version
    resolved = resolve_privately(contract_environment)
    environment = service(resolved, "sync-worker")["environment"]

    assert setting in environment, f"{setting} is absent from the worker environment"
    assert not environment[setting], f"{setting} resolved to a value with no operator input"


@pytest.mark.parametrize("setting", SOURCE_CREDENTIAL_SETTINGS)
def test_a_declared_source_credential_reaches_only_the_worker(
    contract_environment: dict[str, str], compose_version: str, setting: str
) -> None:
    """The operator's value is what the worker is given, and the only thing given it.

    Read from raw output, because a credential-named setting is redacted by name
    and a redacted model cannot answer which value it carries. Compared
    privately: the assertions below report service names, never the value.
    """
    del compose_version
    planted = f"contract-{setting.lower().replace('_', '-')}-4f7ab2"

    resolved = resolve_privately({**contract_environment, setting: planted})

    carriers = {
        name
        for name, definition in services(resolved).items()
        if planted in (definition.get("environment") or {}).values()
    }
    assert carriers == SOURCE_CREDENTIAL_RECEIVERS, f"{setting} value reached {sorted(carriers)}"
    assert service(resolved, "sync-worker")["environment"][setting] == planted


# ---------------------------------------------------------------------------
# The object store: bundled or the team's own
# ---------------------------------------------------------------------------


def _env_file(directory: Path, settings: Mapping[str, str]) -> Path:
    """Write a `.env` the way an operator does, one `NAME=value` line per setting."""
    path = directory / ".env"
    path.write_text("".join(f"{name}={value}\n" for name, value in settings.items()), encoding="utf-8")
    return path


def _storage_settings(model: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    """The endpoint and the addressing style each service receives, by service."""
    return {
        name: {setting: (definition.get("environment") or {}).get(setting) for setting in OBJECT_STORE_SETTINGS}
        for name, definition in services(model).items()
        if any(setting in (definition.get("environment") or {}) for setting in OBJECT_STORE_SETTINGS)
    }


def test_the_object_store_settings_reach_the_three_sync_services_with_the_bundled_defaults(
    model: dict[str, Any],
) -> None:
    """FR-028: unset, every Sync process reaches the bundled MinIO with path-style addressing."""
    assert _storage_settings(model) == dict.fromkeys(OBJECT_STORE_RECEIVERS, BUNDLED_OBJECT_STORE_DEFAULTS)


def test_the_object_store_settings_follow_the_env_file(
    compose_version: str, contract_environment: dict[str, str], tmp_path: Path
) -> None:
    """A team's own store is named in `.env`, and all three Sync processes are given it."""
    del compose_version
    external = {
        "INFRAHUB_SYNC_S3_ENDPOINT_URL": "https://s3.example.test:9443",
        "INFRAHUB_SYNC_S3_ADDRESSING_STYLE": "virtual",
    }
    result = compose(
        ["config", "--format", "json"],
        environment=contract_environment,
        env_files=(_env_file(tmp_path, external),),
    )

    assert result.returncode == 0, result.stderr
    assert _storage_settings(json.loads(result.stdout)) == dict.fromkeys(OBJECT_STORE_RECEIVERS, external)


@pytest.mark.parametrize(
    ("overrides", "scale"),
    [
        pytest.param({}, 1, id="unset"),
        pytest.param({BUNDLED_OBJECT_STORE_SETTING: "1"}, 1, id="bundled"),
        pytest.param({BUNDLED_OBJECT_STORE_SETTING: "0"}, 0, id="left-out"),
    ],
)
def test_the_bundled_object_store_setting_scales_both_object_store_services(
    compose_version: str, contract_environment: dict[str, str], overrides: dict[str, str], scale: int
) -> None:
    """FR-029: `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` starts neither MinIO service."""
    del compose_version
    resolved = resolve({**contract_environment, **overrides})

    scales = {name: service(resolved, name).get("scale") for name in BUNDLED_OBJECT_STORE_SERVICES}
    assert scales == dict.fromkeys(BUNDLED_OBJECT_STORE_SERVICES, scale), scales


@pytest.mark.parametrize("bundled", [None, "0"])
def test_the_bootstrap_job_does_not_require_the_bundled_object_store(
    compose_version: str, contract_environment: dict[str, str], bundled: str | None
) -> None:
    """Left out, the bundled store is a dependency `sync-bootstrap` may start without.

    Still declared, so a deployment that runs it waits for it to be healthy first.
    """
    del compose_version
    environment = dict(contract_environment)
    if bundled is not None:
        environment[BUNDLED_OBJECT_STORE_SETTING] = bundled
    dependency = service(resolve(environment), "sync-bootstrap")["depends_on"]["object-store"]

    assert dependency["condition"] == "service_healthy"
    assert dependency["required"] is False


@pytest.mark.parametrize("setting", OBJECT_STORE_CREDENTIALS)
def test_the_object_store_credential_reaches_exactly_the_sync_services_and_the_bundled_store(
    compose_version: str, contract_environment: dict[str, str], setting: str
) -> None:
    """The S3 credential is given to the three Sync processes and to the MinIO server, and nothing else.

    Compared privately on raw output, because the credential is redacted by name:
    the assertion reports service names, never the value.
    """
    del compose_version
    planted = f"contract-{setting.lower().replace('_', '-')}-9c31e0"

    resolved = resolve_privately({**contract_environment, setting: planted})

    carriers = {
        name
        for name, definition in services(resolved).items()
        if any(planted in str(value) for value in (definition.get("environment") or {}).values())
    }
    assert carriers == OBJECT_STORE_CREDENTIAL_RECEIVERS, f"{setting} value reached {sorted(carriers)}"
