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
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest

from tests.compose.conftest import (
    COMPOSE_FILE,
    CONTRACT_ENVIRONMENT,
    SCRATCH_OPTIONS,
    SYNC_SCRATCH_ROOTS,
    SYNC_SERVICES,
    compose,
    resolve,
    resolve_privately,
    service,
)
from tests.compose.stacks import image_references
from tests.docker_image import DEFAULT_IMAGE_REPOSITORY, IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV

if TYPE_CHECKING:
    from collections.abc import Mapping

# An immutable reference is one of exactly two forms: the local Docker image ID,
# which is the OCI configuration digest a build recorded, or a published
# repository pinned to a manifest digest. A tag matches neither.
IMMUTABLE_REFERENCE = re.compile(r"^(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})$")
# Every Sync service, the opt-in CLI included, and the one image reference form
# they all use: the registry repository and the release version, each overridable.
ALL_SYNC_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker", "cli")
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
SILO_IMAGE = (
    "pgsty/silo:RELEASE.2026-10-18T00-00-00Z@sha256:2636fc4155d9a7e41ff58cdb65ddd13ba9304e67184e13c25bc5cb34016269d7"
)
DEVELOPMENT_COMPOSE = Path(__file__).resolve().parents[2] / "development"
DESTINATION_COMPOSE = DEVELOPMENT_COMPOSE / "docker-compose.infrahub.yml"
# Every service of the destination file is under this profile, so a resolution names it.
INFRAHUB_PROFILE = ("--profile", "infrahub")

# The two durable volumes and the services allowed to mount them. The short-lived
# owner repair shares the object store's volume before the server starts.
PERSISTENT_VOLUMES = {"postgres-data", "object-store-data"}
PERSISTENT_SERVICES = {"postgres", "object-store-init", "object-store"}

# Spec 021 FR-011: the one shared, read-only mount -- the operator's CA directory -- on the
# three services that make outgoing TLS connections. Unset, its source is an empty named
# volume that nothing writes.
CA_TARGET = "/etc/infrahub-sync/ca"
CA_VOLUME = "sync-ca-certificates"
CA_SERVICES = {"sync-bootstrap", "sync-api", "sync-worker"}


def writable_mounts(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every mount of one resolved service that its container can write to."""
    return [mount for mount in definition.get("volumes") or [] if not mount.get("read_only")]


def is_ca_mount(mount: Mapping[str, Any]) -> bool:
    return mount.get("target") == CA_TARGET and mount.get("read_only") is True


# The test-only override, and the host route it adds -- in the form Compose
# resolves it to, since the file writes `name:value` and the model renders
# `name=value`. One service reaches the declared destination: the worker that
# runs against it. The API resolves runs out of PostgreSQL and dispatches through
# Prefect, and the bootstrap job reaches no destination at all.
FIXTURE_OVERRIDE = Path(__file__).resolve().parent / "fixture-override.yaml"
HOST_ROUTE = "host.docker.internal=host-gateway"
ROUTED_SERVICES = {"sync-worker"}

# The source credentials the bundled adapters resolve. Only a run consumes one,
# and only the worker runs one, so the worker is the only service that may be
# given either. Naming them here rather than deriving them from the file keeps
# this a statement of the contract instead of a restatement of the YAML.
# Spec 021 FR-008: one documented name for each credential setting of each bundled adapter,
# and every name the shipped examples and adapter pages use, so those packages run unedited.
# The table is dev/specs/021-compose-store-credentials-ca/contracts/environment-variables.md.
CREDENTIAL_PREFIX = "INFRAHUB_SYNC_CREDENTIAL_"
ADAPTER_CREDENTIAL_NAMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("aci", "username"): ("CISCO_APIC_USERNAME",),
    ("aci", "password"): ("CISCO_APIC_PASSWORD",),
    ("genericrestapi", "token"): ("REST_API_TOKEN", "LIBRENMS_TOKEN", "PEERINGDB_API_KEY"),
    ("genericrestapi", "username"): ("REST_API_USERNAME", "DEVICE42_USERNAME", "OBSERVIUM_USERNAME"),
    ("genericrestapi", "password"): ("REST_API_PASSWORD", "DEVICE42_PASSWORD", "OBSERVIUM_PASSWORD"),
    ("infrahub", "token"): ("INFRAHUB_API_TOKEN", "SOURCE_INFRAHUB_TOKEN"),
    ("ipfabricsync", "auth"): ("IPF_TOKEN",),
    ("nautobot", "token"): ("NAUTOBOT_TOKEN",),
    ("netbox", "token"): ("NETBOX_TOKEN",),
    ("peeringmanager", "token"): ("PEERING_MANAGER_TOKEN",),
    ("peeringmanager", "username"): ("PEERING_MANAGER_USERNAME",),
    ("peeringmanager", "password"): ("PEERING_MANAGER_PASSWORD",),
    ("prometheus", "token"): ("PROMETHEUS_TOKEN",),
    ("prometheus", "username"): ("PROMETHEUS_USERNAME",),
    ("prometheus", "password"): ("PROMETHEUS_PASSWORD",),
    ("slurpitsync", "api_key"): ("SLURPIT_API_KEY",),
    ("slurpitsync", "token"): ("SLURPIT_TOKEN",),
}
WORKER_CREDENTIAL_SETTINGS = tuple(
    sorted({f"{CREDENTIAL_PREFIX}{name}" for names in ADAPTER_CREDENTIAL_NAMES.values() for name in names})
)

# The source credentials the bundled adapters resolve. Only a run consumes one,
# and only the worker runs one, so the worker is the only service that may be
# given any of them.
SOURCE_TOKEN_SETTINGS = tuple(
    setting for setting in WORKER_CREDENTIAL_SETTINGS if setting != "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
)
SOURCE_TOKEN_RECEIVERS = {"sync-worker"}

# The destination credential, and the two services that resolve one. The API
# resolves it for the destination schema reads it serves; the worker resolves it
# for a run. Nothing else has a destination to reach.
DESTINATION_CREDENTIAL = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
DESTINATION_CREDENTIAL_RECEIVERS = {"sync-api", "sync-worker"}

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

# The Prefect server runs from the Sync image, so it is a Sync-image service:
# it shares the reference the other Sync services use and is exempt from the
# digest rule for third-party images.
PREFECT_SERVER = "prefect-server"
SYNC_IMAGE_SERVICES = (PREFECT_SERVER, *ALL_SYNC_SERVICES)
PREFECT_SERVER_COMMAND = [
    "uvicorn",
    "--host",
    "0.0.0.0",  # noqa: S104 -- the container's own interface; the root file publishes no Prefect port
    "--port",
    "4200",
    "--factory",
    "infrahub_sync.service.prefect_server:create_prefect_app",
]
PREFECT_HEALTH_URL = "http://127.0.0.1:4200/api/health"
# Every setting that reaches or opens the Prefect API: its address and the
# credential on both sides of it.
PREFECT_API_SETTINGS = (
    "PREFECT_API_URL",
    PREFECT_CLIENT_CREDENTIAL,
    PREFECT_SERVER_CREDENTIAL,
    PREFECT_CREDENTIAL_SETTING,
)
# The Prefect database password, and the two services allowed to hold it:
# the server that connects with it and the job that creates the role.
PREFECT_DATABASE_CREDENTIAL = "INFRAHUB_SYNC_PREFECT_PASSWORD"
PREFECT_DATABASE_URL = "PREFECT_SERVER_DATABASE_CONNECTION_URL"
PREFECT_DATABASE_PASSWORD_RECEIVERS = {PREFECT_SERVER, "db-bootstrap"}
# PostgreSQL, the object store, and the Sync image.
ROOT_IMAGE_COUNT = 3
# The documented override snippet that turns the Prefect UI on, and the Sync
# scratch tmpfs its UI files must go to.
PREFECT_UI_SNIPPET = Path(__file__).resolve().parents[2] / "docs" / "docs" / "snippets" / "prefect-ui.override.yml"
SYNC_SCRATCH_ROOT = "/tmp/infrahub-sync"  # noqa: S108 -- the image's own fixed scratch root, not a shared /tmp path


def services(model: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(model["services"])


def published(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(entry) for entry in definition.get("ports") or []]


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------


def test_the_api_and_the_worker_share_no_mount_source(model: dict[str, Any]) -> None:
    """A shared source is a channel run state could cross without the object store.

    The deployment's whole isolation claim is that a submitter and the worker that
    serves it have no filesystem in common, so the intersection is the thing to
    assert — not that each one's list happens to look right today.
    """
    api = {str(mount["source"]) for mount in writable_mounts(service(model, "sync-api"))}
    worker = {str(mount["source"]) for mount in writable_mounts(service(model, "sync-worker"))}

    assert api & worker == set(), f"sync-api and sync-worker both mount {sorted(api & worker)}"


@pytest.mark.parametrize("name", SYNC_SERVICES)
def test_a_sync_service_mounts_nothing_at_all(model: dict[str, Any], name: str) -> None:
    """Not even a private mount: a writable path that survives its container is state.

    Disjoint mounts would still leave each side a place to keep a cache or a run
    directory across a restart, which is the second half of what the object-store
    transport exists to replace.
    """
    definition = service(model, name)
    mounts = [mount for mount in definition.get("volumes") or [] if not is_ca_mount(mount)]

    assert mounts == [], f"{name} mounts {mounts}"


@pytest.mark.parametrize("name", SYNC_SERVICES)
def test_a_sync_service_runs_read_only_over_the_image_declared_scratch(model: dict[str, Any], name: str) -> None:
    """The declared roots are tmpfs, handed to the runtime user, and nothing else is writable.

    A tmpfs mount takes its own ownership, so without the options a container
    running as 10001 finds a root-owned directory and fails on its first write.
    """
    definition = service(model, name)
    expected = [f"{root}:{SCRATCH_OPTIONS}" for root in SYNC_SCRATCH_ROOTS]

    assert definition.get("read_only") is True, f"{name} does not run on a read-only root filesystem"
    assert definition.get("tmpfs") == expected, f"{name} declares scratch {definition.get('tmpfs')}"


def test_only_postgresql_and_the_object_store_keep_a_named_volume(model: dict[str, Any]) -> None:
    """The one-shot owner repair shares only the object store's durable volume."""
    declared = set(model.get("volumes") or {})
    holders = {
        name
        for name, definition in services(model).items()
        if any(mount.get("type") == "volume" for mount in writable_mounts(definition))
    }

    assert declared == PERSISTENT_VOLUMES | {CA_VOLUME}, f"the file declares the volumes {sorted(declared)}"
    assert holders == PERSISTENT_SERVICES, f"named volumes are held by {sorted(holders)}"


def test_prefect_keeps_no_state_of_its_own(model: dict[str, Any]) -> None:
    """Its records live in PostgreSQL, so a replacement container resumes from them.

    The server runs from the Sync image, whose Prefect home is a tmpfs scratch
    root. Prefect defaults to a SQLite file under that home: left that way the
    server would hold deployment and work-pool state no other service could see,
    and every restart would lose it. So it mounts nothing and connects to
    PostgreSQL, like the other Sync-image services keep no state of their own.
    """
    definition = service(model, PREFECT_SERVER)
    connection = definition["environment"]["PREFECT_SERVER_DATABASE_CONNECTION_URL"]

    assert definition.get("volumes") in (None, []), f"prefect-server mounts {definition.get('volumes')}"
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
    """One self-contained file: nothing it runs reads a path beside it on the host.

    An equality over every service, the opt-in CLI included, so a bind mount
    added to any of them fails here. A named volume or a tmpfs is not a host path.
    """
    binds = sorted(
        f"{name}: {mount.get('source')}"
        for name, definition in services(cli_model).items()
        for mount in definition.get("volumes") or []
        if mount.get("type") == "bind"
    )

    assert binds == [], f"these services mount a host path: {binds}"


def test_a_named_ca_directory_is_the_only_host_path_and_is_read_only(contract_environment: dict[str, str]) -> None:
    """Spec 021: with INFRAHUB_SYNC_CA_CERTIFICATES set, the CA directory is bound read-only, and nothing else."""
    resolved = resolve({**contract_environment, "INFRAHUB_SYNC_CA_CERTIFICATES": "/srv/infrahub-sync/ca"})
    binds = {
        name: (mount.get("source"), mount.get("target"), mount.get("read_only"))
        for name, definition in services(resolved).items()
        for mount in definition.get("volumes") or []
        if mount.get("type") == "bind"
    }

    assert binds == dict.fromkeys(CA_SERVICES, ("/srv/infrahub-sync/ca", CA_TARGET, True))


def test_the_ca_mount_and_setting_reach_only_the_tls_clients(model: dict[str, Any]) -> None:
    """FR-011: sync-bootstrap, sync-api and sync-worker, and no other service."""
    mounted = {
        name for name, definition in services(model).items() if any(map(is_ca_mount, definition.get("volumes") or []))
    }
    configured = {
        name
        for name, definition in services(model).items()
        if (definition.get("environment") or {}).get("INFRAHUB_SYNC_CA_DIRECTORY") == CA_TARGET
    }

    assert mounted == CA_SERVICES
    assert configured == CA_SERVICES


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
    """One host port: Prefect, PostgreSQL, the object store, the worker, and every job stay private.

    The Prefect port opens only through the documented override snippet, which
    this default resolve does not include.
    """
    publishers = {name for name, definition in services(model).items() if published(definition)}

    assert publishers == {"sync-api"}, f"host ports are published by {sorted(publishers)}"


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


def test_the_api_publishes_on_host_port_8001_by_default(model: dict[str, Any]) -> None:
    """Host port 8000 is Infrahub's, so the Sync API takes 8001 and both run on one machine.

    Inside the container the API still listens on 8000; only the host side moves.
    """
    ports = [(str(entry.get("published")), entry.get("target")) for entry in published(service(model, "sync-api"))]

    assert ports == [("8001", 8000)]


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


# ---------------------------------------------------------------------------
# The Prefect server runs from the Sync image
# ---------------------------------------------------------------------------


def sync_image_services(resolved: Mapping[str, Any]) -> set[str]:
    """The services that run the image `sync-api` runs."""
    reference = service(resolved, "sync-api")["image"]
    return {name for name, definition in services(resolved).items() if definition.get("image") == reference}


def test_the_prefect_server_runs_the_image_the_sync_services_run(model: dict[str, Any]) -> None:
    """One image reference, so the server and its clients always run the same Prefect version."""
    images = {name: service(model, name)["image"] for name in (PREFECT_SERVER, "sync-api", "sync-worker")}

    assert len(set(images.values())) == 1, f"the Prefect server and the Sync services run different images: {images}"


def test_nothing_outside_the_sync_image_reaches_the_prefect_api(
    cli_model: dict[str, Any], contract_environment: dict[str, str]
) -> None:
    """Only processes of the Sync image hold the Prefect API address or its credential.

    Two equalities over every service: by setting name, the opt-in CLI included,
    and by the credential's value, read from raw output and compared privately
    because the credential is redacted by name.
    """
    outside = set(services(cli_model)) - sync_image_services(cli_model)
    by_name = sorted(
        f"{name}: {setting}"
        for name in outside
        for setting in PREFECT_API_SETTINGS
        if setting in (service(cli_model, name).get("environment") or {})
    )
    resolved = resolve_privately(contract_environment)
    credential = contract_environment[PREFECT_CREDENTIAL_SETTING]
    by_value = sorted(
        name
        for name in set(services(resolved)) - sync_image_services(resolved)
        if any(credential in str(value) for value in (service(resolved, name).get("environment") or {}).values())
    )

    assert by_name == [], f"services outside the Sync image hold Prefect API settings: {by_name}"
    assert by_value == [], f"services outside the Sync image hold the Prefect API credential: {by_value}"


def test_the_prefect_server_starts_the_sync_package_factory(model: dict[str, Any]) -> None:
    """uvicorn serves the server application the Sync package builds, not the `prefect` CLI."""
    assert service(model, PREFECT_SERVER)["command"] == PREFECT_SERVER_COMMAND


def test_the_prefect_server_health_check_calls_the_api_with_httpx(model: dict[str, Any]) -> None:
    """The check uses the image's own Python and `httpx`, on the loopback address."""
    test = service(model, PREFECT_SERVER)["healthcheck"]["test"]

    assert test[:3] == ["CMD", "python", "-c"], test
    assert len(test) == 4, test
    assert "import httpx" in test[3], test
    assert PREFECT_HEALTH_URL in test[3], test


def test_the_prefect_database_password_reaches_only_the_server_and_the_role_bootstrap(
    cli_model: dict[str, Any], contract_environment: dict[str, str]
) -> None:
    """The server connects with it and `db-bootstrap` creates the role with it; nothing else holds it.

    An equality over every service, by setting name and by value, read from raw
    output and compared privately because the password is redacted by name. The
    CLI reaches the Sync API alone, so it holds no Prefect setting at all.
    """
    resolved = resolve_privately(contract_environment)
    password = contract_environment[PREFECT_DATABASE_CREDENTIAL]
    holders = {
        name
        for name, definition in services(resolved).items()
        if PREFECT_DATABASE_CREDENTIAL in (environment := definition.get("environment") or {})
        or PREFECT_DATABASE_URL in environment
        or any(password in str(value) for value in environment.values())
    }
    cli_settings = sorted(
        setting
        for setting in service(cli_model, CLI_SERVICE).get("environment") or {}
        if setting.startswith(("PREFECT_", "INFRAHUB_SYNC_PREFECT_"))
    )

    assert holders == PREFECT_DATABASE_PASSWORD_RECEIVERS, f"the Prefect database password reaches {sorted(holders)}"
    assert cli_settings == [], f"the CLI service holds Prefect settings: {cli_settings}"


def test_the_prefect_ui_is_off_by_default(model: dict[str, Any]) -> None:
    """The UI turns on only through the documented override snippet."""
    assert service(model, PREFECT_SERVER)["environment"].get("PREFECT_UI_ENABLED") == "false"


def test_the_prefect_ui_api_address_is_loopback(model: dict[str, Any]) -> None:
    """Set even with the UI off, so the override snippet only has to turn the UI on."""
    address = service(model, PREFECT_SERVER)["environment"].get("PREFECT_SERVER_UI_API_URL")

    assert address, "prefect-server does not set PREFECT_SERVER_UI_API_URL"
    assert urlsplit(address).hostname == "127.0.0.1", f"the Prefect UI calls the API on {address}"


def test_the_prefect_ui_api_address_stays_loopback_with_another_bind_address(
    contract_environment: dict[str, str],
) -> None:
    """The snippet publishes the Prefect port on 127.0.0.1 only, so the UI must call the API there."""
    environment = {**contract_environment, "INFRAHUB_SYNC_BIND_ADDRESS": "0.0.0.0"}  # noqa: S104
    address = service(resolve(environment), PREFECT_SERVER)["environment"]["PREFECT_SERVER_UI_API_URL"]

    assert urlsplit(address).hostname == "127.0.0.1", f"the Prefect UI calls the API on {address}"


def test_the_prefect_ui_snippet_turns_the_ui_on_and_publishes_it_on_loopback_only(
    contract_environment: dict[str, str],
) -> None:
    """The documented override snippet opens the UI on one loopback port, and changes nothing else.

    The UI files go to the Sync scratch tmpfs, because the server's root
    filesystem is read-only. The comparison with the default resolve proves the
    snippet adds no other service publication.
    """
    overridden = resolve(contract_environment, files=(COMPOSE_FILE, PREFECT_UI_SNIPPET))
    definition = service(overridden, PREFECT_SERVER)
    environment = definition["environment"]
    ports = published(definition)

    assert environment.get("PREFECT_UI_ENABLED") == "true"
    static = environment.get("PREFECT_UI_STATIC_DIRECTORY", "")
    assert static.startswith(f"{SYNC_SCRATCH_ROOT}/"), f"the Prefect UI files go to {static!r}"
    assert len(ports) == 1, f"prefect-server publishes {ports}"
    assert ports[0].get("host_ip") == "127.0.0.1", f"prefect-server publishes on {ports[0].get('host_ip')}"
    assert str(ports[0].get("target")) == "4200"
    assert str(ports[0].get("published")) == "4200"
    others = {name for name, entry in services(overridden).items() if published(entry)} - {PREFECT_SERVER}
    assert others == {"sync-api"}, f"with the snippet, host ports are also published by {sorted(others)}"


def test_the_prefect_ui_snippet_publishes_the_configured_port(contract_environment: dict[str, str]) -> None:
    """`INFRAHUB_SYNC_PREFECT_PORT` takes effect through the snippet, as the release notes say."""
    environment = {**contract_environment, "INFRAHUB_SYNC_PREFECT_PORT": "4299"}
    overridden = resolve(environment, files=(COMPOSE_FILE, PREFECT_UI_SNIPPET))
    ports = published(service(overridden, PREFECT_SERVER))

    assert [str(entry.get("published")) for entry in ports] == ["4299"]
    address = service(overridden, PREFECT_SERVER)["environment"]["PREFECT_SERVER_UI_API_URL"]
    assert urlsplit(address).port == 4299, f"the Prefect UI calls the API on {address}"


def test_the_prefect_server_runs_read_only_over_the_sync_scratch(model: dict[str, Any]) -> None:
    """The same tmpfs roots as every Sync-image service, and nothing else writable."""
    definition = service(model, PREFECT_SERVER)
    expected = [f"{root}:{SCRATCH_OPTIONS}" for root in SYNC_SCRATCH_ROOTS]

    assert definition.get("read_only") is True, "prefect-server does not run on a read-only root filesystem"
    assert definition.get("tmpfs") == expected, f"prefect-server declares scratch {definition.get('tmpfs')}"


def test_the_root_file_names_three_images(cli_model: dict[str, Any]) -> None:
    """PostgreSQL, Silo and Sync: a deployment pulls three images, the opt-in CLI included."""
    images = image_references(cli_model)

    assert len(images) == ROOT_IMAGE_COUNT, f"the root file names {len(images)} images: {images}"


# ---------------------------------------------------------------------------
# Immutable image identity
# ---------------------------------------------------------------------------


def test_object_store_uses_the_pinned_silo_release_and_migrates_existing_data(model: dict[str, Any]) -> None:
    """Ownership repair completes before the server starts without root privileges."""
    init = service(model, "object-store-init")
    object_store = service(model, "object-store")

    assert init["image"] == SILO_IMAGE
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
    assert object_store["image"] == SILO_IMAGE
    assert object_store["user"] == "65532:65532"
    assert object_store["command"] == ["server", "/data"]
    assert object_store["depends_on"]["object-store-init"]["condition"] == "service_completed_successfully"


def test_every_image_other_than_sync_is_named_by_digest(cli_model: dict[str, Any]) -> None:
    """A tag can be re-pointed; a digest names one artifact for good.

    One assertion over every service, so a service added later is covered by
    having been added rather than by somebody remembering to extend a list. The
    Sync image is the one deliberate exception: it is selected by release tag,
    and every service that runs it, `prefect-server` included, is exempt.
    """
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in services(cli_model).items()
        if name not in SYNC_IMAGE_SERVICES and not IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
    )

    assert mutable == [], f"these services are not pinned to a digest: {mutable}"


def sync_image_lines() -> dict[str, str]:
    """The raw `image:` line of each Sync service, read from the file's text."""
    found: dict[str, str] = {}
    current = ""
    for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines():
        heading = re.fullmatch(r"  ([a-z][a-z0-9-]*):\s*", line)
        if heading:
            current = heading.group(1)
        elif current in SYNC_IMAGE_SERVICES and line.lstrip().startswith("image:"):
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
    to. Every Sync-image service carries the same reference, so one `VERSION`
    moves all five together.
    """
    lines = sync_image_lines()
    versions = pinned_versions()

    assert set(lines) == set(SYNC_IMAGE_SERVICES), f"Sync services with an image line: {sorted(lines)}"
    assert set(versions) == set(SYNC_IMAGE_SERVICES), (
        f"services off the reference form: {sorted(set(lines) - set(versions))}"
    )
    assert len(set(versions.values())) == 1, f"the Sync services name different versions: {versions}"


def test_the_sync_image_defaults_to_the_registry_at_the_pinned_version(cli_model: dict[str, Any]) -> None:
    """With nothing set, every Sync service resolves to one registry image."""
    (pinned,) = set(pinned_versions().values())
    expected = f"{DEFAULT_IMAGE_REPOSITORY}:{pinned}"

    resolved = {name: service(cli_model, name)["image"] for name in SYNC_IMAGE_SERVICES}

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

    assert {resolved[name]["image"] for name in SYNC_IMAGE_SERVICES} == {expected}
    assert all(
        IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
        for name, definition in resolved.items()
        if name not in SYNC_IMAGE_SERVICES
    )


def test_destination_default_images_are_tagged_indexes(compose_version: str) -> None:
    """Compose's resolved defaults must retain readable tags and immutable digests."""
    del compose_version
    result = compose(
        [*INFRAHUB_PROFILE, "config", "--format", "json"], files=(DESTINATION_COMPOSE,), inherit_environment=False
    )
    assert result.returncode == 0, result.stderr
    configured = json.loads(result.stdout)
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in services(configured).items()
        if not TAGGED_INDEX_REFERENCE.fullmatch(str(definition["image"]))
    )
    assert mutable == [], f"destination images lack tag and digest: {mutable}"


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
        [*INFRAHUB_PROFILE, "config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
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
        [*INFRAHUB_PROFILE, "config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
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
        [*INFRAHUB_PROFILE, "config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
        inherit_environment=False,
        environment={"INFRAHUB_DOCKER_IMAGE": "local-infrahub", **digest_override},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "local-infrahub:1.11.9"


def test_direct_compose_digest_override_replaces_the_shipped_digest(compose_version: str) -> None:
    """A digest-only override must resolve to one valid image reference."""
    del compose_version
    digest = "@sha256:" + "a" * 64
    result = compose(
        [*INFRAHUB_PROFILE, "config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
        inherit_environment=False,
        environment={"INFRAHUB_DOCKER_IMAGE_DIGEST": digest},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    expected = f"registry.opsmill.io/opsmill/infrahub:1.11.9{digest}"
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == expected
        assert TAGGED_INDEX_REFERENCE.fullmatch(configured[name]["image"])


def test_standalone_version_override_drops_the_shipped_digest(compose_version: str) -> None:
    """Direct Compose use must not pair a new tag with the shipped digest."""
    del compose_version
    result = compose(
        [*INFRAHUB_PROFILE, "config", "--format", "json"],
        files=(DESTINATION_COMPOSE,),
        inherit_environment=False,
        environment={"VERSION": "custom"},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "registry.opsmill.io/opsmill/infrahub:custom"


# ---------------------------------------------------------------------------
# No filesystem input at all
# ---------------------------------------------------------------------------


def test_no_sync_service_takes_a_filesystem_input(model: dict[str, Any]) -> None:
    """A start needs no configuration file, so no service is given one.

    An equality over every Sync service, not the absence of one known mount: a
    configuration handed back to any of them would restore a startup that decides
    what the deployment runs before the operator has registered anything.
    """
    readers = {
        name: mounts
        for name, definition in services(model).items()
        if name.startswith("sync-")
        and (mounts := [mount for mount in definition.get("volumes") or [] if not is_ca_mount(mount)])
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


@pytest.mark.parametrize("setting", SOURCE_TOKEN_SETTINGS)
def test_only_the_worker_is_given_a_source_credential(model: dict[str, Any], setting: str) -> None:
    """A secret with no receiver is a secret that cannot leak from one.

    An equality over every service, not a check that the worker has it: a source
    token added to the API, the bootstrap job, or Prefect would fail here even
    though the worker still worked.
    """
    holders = {name for name, definition in services(model).items() if setting in (definition.get("environment") or {})}

    assert holders == SOURCE_TOKEN_RECEIVERS, f"{setting} is given to {sorted(holders)}"


@pytest.mark.parametrize("setting", SOURCE_TOKEN_SETTINGS)
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


@pytest.mark.parametrize("setting", SOURCE_TOKEN_SETTINGS)
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
    assert carriers == SOURCE_TOKEN_RECEIVERS, f"{setting} value reached {sorted(carriers)}"
    assert service(resolved, "sync-worker")["environment"][setting] == planted


# ---------------------------------------------------------------------------
# The team's own S3-compatible store (spec 021, FR-001, FR-002)
# ---------------------------------------------------------------------------

STORE_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker")
BUNDLED_STORE_SERVICES = ("object-store-init", "object-store")
LEAVE_OUT_SETTING = "INFRAHUB_SYNC_BUNDLED_OBJECT_STORE"
EXTERNAL_STORE = {
    "INFRAHUB_SYNC_S3_ENDPOINT_URL": "https://s3.example.test",
    "INFRAHUB_SYNC_S3_ADDRESSING_STYLE": "virtual",
    LEAVE_OUT_SETTING: "0",
}


@pytest.mark.parametrize("name", STORE_SERVICES)
def test_the_store_settings_default_to_the_bundled_store(model: dict[str, Any], name: str) -> None:
    environment = services(model)[name]["environment"]

    assert environment["INFRAHUB_SYNC_S3_ENDPOINT_URL"] == "http://object-store:9000"
    assert environment["INFRAHUB_SYNC_S3_ADDRESSING_STYLE"] == "path"


@pytest.mark.parametrize("name", STORE_SERVICES)
def test_the_store_settings_come_from_the_environment(contract_environment: dict[str, str], name: str) -> None:
    environment = services(resolve({**contract_environment, **EXTERNAL_STORE}))[name]["environment"]

    assert environment["INFRAHUB_SYNC_S3_ENDPOINT_URL"] == "https://s3.example.test"
    assert environment["INFRAHUB_SYNC_S3_ADDRESSING_STYLE"] == "virtual"


@pytest.mark.parametrize("name", BUNDLED_STORE_SERVICES)
def test_the_bundled_store_runs_by_default(model: dict[str, Any], name: str) -> None:
    assert services(model)[name].get("scale", 1) == 1


@pytest.mark.parametrize("name", BUNDLED_STORE_SERVICES)
def test_the_bundled_store_can_be_left_out(contract_environment: dict[str, str], name: str) -> None:
    resolved = services(resolve({**contract_environment, **EXTERNAL_STORE}))

    assert resolved[name].get("scale") == 0


def test_bootstrap_does_not_require_a_left_out_bundled_store(contract_environment: dict[str, str]) -> None:
    """Every Sync service still resolves, and bootstrap waits for the store only when it runs."""
    resolved = services(resolve({**contract_environment, **EXTERNAL_STORE}))
    dependency = resolved["sync-bootstrap"]["depends_on"]["object-store"]

    assert dependency["condition"] == "service_healthy"
    assert dependency["required"] is False
    assert set(STORE_SERVICES) <= set(resolved)


# ---------------------------------------------------------------------------
# The credential variables of every bundled adapter (spec 021, FR-008)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL_NAME = re.compile(rf"{CREDENTIAL_PREFIX}[A-Z0-9_]+")


def test_the_worker_holds_exactly_the_documented_credential_variables(model: dict[str, Any]) -> None:
    environment = services(model)["sync-worker"]["environment"]

    held = sorted(name for name in environment if name.startswith(CREDENTIAL_PREFIX))
    assert held == list(WORKER_CREDENTIAL_SETTINGS)
    assert len(held) == 24


def test_the_list_names_every_credential_setting_of_every_bundled_adapter() -> None:
    from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES

    settings = {
        (name, path)
        for name, capabilities in BUILTIN_ADAPTER_CAPABILITIES.items()
        for path in capabilities.credential_setting_paths
    }

    assert settings == set(ADAPTER_CREDENTIAL_NAMES)


def test_the_list_holds_every_name_the_examples_and_adapter_pages_use() -> None:
    """A shipped package or a documented one runs on the Compose deployment unedited."""
    sources = [*REPO_ROOT.glob("examples/*/package.yml"), *REPO_ROOT.glob("docs/docs/adapters/*.mdx")]
    used = {name for path in sources for name in CREDENTIAL_NAME.findall(path.read_text(encoding="utf-8"))}

    assert sources
    assert used <= set(WORKER_CREDENTIAL_SETTINGS), sorted(used - set(WORKER_CREDENTIAL_SETTINGS))


@pytest.mark.parametrize("setting", WORKER_CREDENTIAL_SETTINGS)
def test_each_credential_variable_is_optional_in_the_file(setting: str) -> None:
    """Written `${NAME:-}`: an unset variable resolves empty and the deployment still starts."""
    text = COMPOSE_FILE.read_text(encoding="utf-8")

    assert f'{setting}: "${{{setting}:-}}"' in text


# ---------------------------------------------------------------------------
# Image pins of the development stack (spec 021 FR-023, FR-024)
# ---------------------------------------------------------------------------

DEVELOPMENT_FILE = DEVELOPMENT_COMPOSE / "docker-compose.yml"
DEVELOPMENT_SYNC_IMAGE = "infrahub-sync:dev"


def test_every_development_image_other_than_the_checkout_build_is_named_by_digest(compose_version: str) -> None:
    """The development stack builds the Sync image from the checkout; every other image is pinned.

    The root file's own case covers the packaged copy, which is byte-identical to it.
    """
    del compose_version
    result = compose(
        ["--profile", "sync", "--profile", "infrahub", "config", "--format", "json"],
        files=(DEVELOPMENT_FILE,),
        inherit_environment=False,
    )
    assert result.returncode == 0, result.stderr
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in services(json.loads(result.stdout)).items()
        if definition["image"] != DEVELOPMENT_SYNC_IMAGE and not IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
    )

    assert mutable == [], f"these development services are not pinned to a digest: {mutable}"
