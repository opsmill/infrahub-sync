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

import pytest

from tests.compose.conftest import (
    COMPOSE_FILE,
    CONTRACT_ENVIRONMENT,
    DEFAULT_IMAGE_REPOSITORY,
    IMAGE_REPOSITORY_ENV,
    IMAGE_VERSION_ENV,
    SCRATCH_OPTIONS,
    SYNC_SCRATCH_ROOTS,
    SYNC_SERVICES,
    compose,
    mount_sources,
    resolve,
    resolve_privately,
    service,
)

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
MINIO_IMAGE = (
    "cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba"
)
DEVELOPMENT_COMPOSE = Path(__file__).resolve().parents[2] / "development"
DESTINATION_COMPOSE = DEVELOPMENT_COMPOSE / "docker-compose.infrahub.yml"
PREVIEW_COMPOSE = DEVELOPMENT_COMPOSE / "docker-compose.preview.yml"
PREVIEW_ENV = DEVELOPMENT_COMPOSE / "preview.env"

# The two durable volumes and the services allowed to mount them. The short-lived
# owner repair shares the object store's volume before the server starts.
PERSISTENT_VOLUMES = {"postgres-data", "object-store-data"}
PERSISTENT_SERVICES = {"postgres", "object-store-init", "object-store"}

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
SOURCE_TOKEN_SETTINGS = ("NETBOX_TOKEN", "NAUTOBOT_TOKEN")
SOURCE_TOKEN_RECEIVERS = {"sync-worker"}

# The destination credential, and the two services that resolve one. The API
# resolves it for the destination schema reads it serves; the worker resolves it
# for a run. Nothing else has a destination to reach.
DESTINATION_CREDENTIAL = "INFRAHUB_API_TOKEN"
DESTINATION_CREDENTIAL_RECEIVERS = {"sync-api", "sync-worker"}

# The opt-in client, and the profile that is the only way to resolve it.
CLI_SERVICE = "cli"
# The API client credential, and the only service allowed to hold one. It
# authenticates a caller *to* this deployment, so a worker or a job holding it
# would be a service carrying a credential for the service that dispatches it.
CLIENT_CREDENTIAL = "INFRAHUB_SYNC_API_TOKEN"
CLIENT_CREDENTIAL_RECEIVERS = {CLI_SERVICE}


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
    api = mount_sources(service(model, "sync-api"))
    worker = mount_sources(service(model, "sync-worker"))

    assert api & worker == set(), f"sync-api and sync-worker both mount {sorted(api & worker)}"


@pytest.mark.parametrize("name", SYNC_SERVICES)
def test_a_sync_service_mounts_nothing_at_all(model: dict[str, Any], name: str) -> None:
    """Not even a private mount: a writable path that survives its container is state.

    Disjoint mounts would still leave each side a place to keep a cache or a run
    directory across a restart, which is the second half of what the object-store
    transport exists to replace.
    """
    definition = service(model, name)

    assert definition.get("volumes") in (None, []), f"{name} mounts {definition['volumes']}"


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
        if any(mount.get("type") == "volume" for mount in definition.get("volumes") or [])
    }

    assert declared == PERSISTENT_VOLUMES, f"the file declares the volumes {sorted(declared)}"
    assert holders == PERSISTENT_SERVICES, f"named volumes are held by {sorted(holders)}"


def test_prefect_keeps_no_state_of_its_own(model: dict[str, Any]) -> None:
    """Its records live in PostgreSQL, so a replacement container resumes from them.

    Prefect defaults to a SQLite file inside its container. Left that way the
    server would hold deployment and work-pool state no other service could see,
    and a restart that replaced the container would silently lose it.
    """
    definition = service(model, "prefect-server")
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


def test_only_the_api_and_prefect_publish_a_host_port(model: dict[str, Any]) -> None:
    """PostgreSQL, the object store, the worker, and every job stay on the private network."""
    publishers = {name for name, definition in services(model).items() if published(definition)}

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


def test_every_image_other_than_sync_is_named_by_digest(cli_model: dict[str, Any]) -> None:
    """A tag can be re-pointed; a digest names one artifact for good.

    One assertion over every service, so a service added later is covered by
    having been added rather than by somebody remembering to extend a list. The
    Sync image is the one deliberate exception: it is selected by release tag.
    """
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in services(cli_model).items()
        if name not in ALL_SYNC_SERVICES and not IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
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
    all four together.
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
        if not TAGGED_INDEX_REFERENCE.fullmatch(str(definition["image"]))
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
    for name in ("task-manager", "infrahub-server", "task-worker", "sync-prefect"):
        assert shipped[name]["image"] == defaults[name]["image"], name


def test_tagged_index_reference_requires_a_tag_after_the_last_slash() -> None:
    """A registry port cannot masquerade as an image tag."""
    digest = "sha256:" + "a" * 64
    assert TAGGED_INDEX_REFERENCE.fullmatch(f"registry:5000/image:tag@{digest}")
    assert not TAGGED_INDEX_REFERENCE.fullmatch(f"registry:5000/image@{digest}")


def test_image_overrides_keep_their_matching_digests(compose_version: str) -> None:
    """Custom Infrahub and Prefect tags can be paired with their own index digests."""
    del compose_version
    infrahub_digest = "@sha256:" + "a" * 64
    prefect_digest = "@sha256:" + "b" * 64
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
            "PREVIEW_PREFECT_IMAGE_TAG": "custom",
            "PREVIEW_PREFECT_IMAGE_DIGEST": prefect_digest,
        },
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    assert configured["message-queue"]["image"] == "example.invalid/rabbitmq:custom"
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == f"example.invalid/infrahub:custom{infrahub_digest}"
    assert configured["sync-prefect"]["image"] == f"prefecthq/prefect:custom{prefect_digest}"


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
            "PREVIEW_PREFECT_IMAGE_TAG": "custom",
            "PREVIEW_PREFECT_IMAGE_DIGEST": "",
        },
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "local-infrahub:custom"
    assert configured["sync-prefect"]["image"] == "prefecthq/prefect:custom"


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
        environment={"VERSION": "custom", "PREVIEW_PREFECT_IMAGE_TAG": "custom"},
    )
    assert result.returncode == 0, result.stderr
    configured = services(json.loads(result.stdout))
    for name in ("task-manager", "infrahub-server", "task-worker"):
        assert configured[name]["image"] == "registry.opsmill.io/opsmill/infrahub:custom"
    assert configured["sync-prefect"]["image"] == "prefecthq/prefect:custom"


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
        name: definition.get("volumes") or []
        for name, definition in services(model).items()
        if name.startswith("sync-") and (definition.get("volumes") or [])
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
