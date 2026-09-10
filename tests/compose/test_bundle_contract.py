"""Properties of the resolved Compose bundle, read from Compose's own model.

Every assertion below reads `docker compose config` output, so it describes what
Compose will run rather than what the file appears to say: interpolation,
extension merging, and defaulting have already happened. A property that would
survive an edit to the YAML but change what runs is not a property this suite
can be fooled by.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration.models import parse_configuration_package
from infrahub_sync.product_store import configs
from tests.compose.conftest import (
    BUNDLE_LABEL,
    BUNDLED_CONFIGURATION,
    COMPOSE_FILE,
    DEFAULTS_FILE,
    INSTANCE_LABEL,
    SCRATCH_OPTIONS,
    SYNC_SCRATCH_ROOTS,
    SYNC_SERVICES,
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

# The two services that keep data, and therefore the only two allowed to hold a
# named volume.
PERSISTENT_VOLUMES = {"postgres-data", "object-store-data"}
PERSISTENT_SERVICES = {"postgres", "object-store"}

# The test-only override, and the host route it adds -- in the form Compose
# resolves it to, since the file writes `name:value` and the model renders
# `name=value`. Exactly two services reach the declared destination: the job that
# probes it before a start, and the worker that runs against it. The API resolves
# runs out of PostgreSQL and dispatches through Prefect, and opens no connection
# to the destination at all.
FIXTURE_OVERRIDE = Path(__file__).resolve().parent / "fixture-override.yaml"
HOST_ROUTE = "host.docker.internal=host-gateway"
ROUTED_SERVICES = {"sync-bootstrap", "sync-worker"}

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
CLI_SERVICE = "sync-cli"


def services(model: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(model["services"])


def published(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(entry) for entry in definition.get("ports") or []]


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------


def test_the_api_and_the_worker_share_no_mount_source(model: dict[str, Any]) -> None:
    """A shared source is a channel run state could cross without the object store.

    The bundle's whole isolation claim is that a submitter and the worker that
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
    """Every other writable path disappears with its container, by construction."""
    declared = set(model.get("volumes") or {})
    holders = {
        name
        for name, definition in services(model).items()
        if any(mount.get("type") == "volume" for mount in definition.get("volumes") or [])
    }

    assert declared == PERSISTENT_VOLUMES, f"the bundle declares the volumes {sorted(declared)}"
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


def test_every_image_the_bundle_runs_is_named_by_digest(model: dict[str, Any]) -> None:
    """A tag can be re-pointed; a digest names one artifact for good.

    One assertion over every service, so a service added later is covered by
    having been added rather than by somebody remembering to extend a list.
    """
    mutable = sorted(
        f"{name}: {definition['image']}"
        for name, definition in services(model).items()
        if not IMMUTABLE_REFERENCE.fullmatch(str(definition["image"]))
    )

    assert mutable == [], f"these services are not pinned to a digest: {mutable}"


def test_a_tag_only_sync_image_still_resolves_to_the_tag_it_was_given(
    contract_environment: dict[str, str],
) -> None:
    """Compose interpolates whatever it is handed, which is why the check above matters.

    Compose has no opinion about mutability: a tag passes through it unchanged.
    This records that fact, so the digest property is understood as one this
    bundle's own gates hold rather than one Compose enforces.
    """
    tagged = resolve({**contract_environment, "INFRAHUB_SYNC_IMAGE": "infrahub-sync:latest"})

    assert tagged["services"]["sync-api"]["image"] == "infrahub-sync:latest"


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_every_owned_resource_carries_the_instance_label(model: dict[str, Any]) -> None:
    """Teardown resolves targets by name and then verifies this label before it mutates.

    A resource created without it can never be proved to belong to this instance,
    so a later reset would have to either refuse it or take it on faith.
    """
    owned: dict[str, Mapping[str, Any]] = {
        **{f"service {name}": definition for name, definition in services(model).items()},
        **{f"volume {name}": definition for name, definition in (model.get("volumes") or {}).items()},
        **{f"network {name}": definition for name, definition in (model.get("networks") or {}).items()},
    }
    unlabelled = sorted(
        description
        for description, definition in owned.items()
        if (definition.get("labels") or {}).get(INSTANCE_LABEL) != "contract-0000000000000000"
        or (definition.get("labels") or {}).get(BUNDLE_LABEL) != "compose"
    )

    assert unlabelled == [], f"these resources carry no instance label: {unlabelled}"


def test_the_bundle_refuses_to_resolve_without_an_instance_identity(
    contract_environment: dict[str, str],
) -> None:
    """The label is required, so an unlabelled stack cannot be started by accident."""
    from tests.compose.conftest import compose

    anonymous = {key: value for key, value in contract_environment.items() if key != "INFRAHUB_SYNC_INSTANCE"}
    result = compose(["config"], environment={**anonymous, "INFRAHUB_SYNC_INSTANCE": ""})

    assert result.returncode != 0
    assert "instance identity" in result.stderr


# ---------------------------------------------------------------------------
# The example configuration
# ---------------------------------------------------------------------------


def test_the_example_configuration_is_a_registerable_package() -> None:
    """Nothing loads it; an operator registers it, so it has to be one a register accepts."""
    package = parse_configuration_package(configs.load_package_content(BUNDLED_CONFIGURATION))

    assert package.configuration.name == "infrahub-sync-qualification"


def test_the_bundled_configuration_declares_no_secret_value() -> None:
    """Its credentials are references the worker resolves from its own environment.

    Declared content is what the registry checksums and stores, so a literal
    credential here would be a credential in PostgreSQL and in every API
    response that echoes a version.
    """
    package = parse_configuration_package(configs.load_package_content(BUNDLED_CONFIGURATION))
    declared = package.configuration.model_dump(mode="json", by_alias=True)

    for side in ("source", "destination"):
        assert declared[side]["settings"]["token"] == {"$credential": "infrahub-token"}


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


def test_the_bundle_interpolates_no_declared_configuration_setting(model: dict[str, Any]) -> None:
    """The setting is gone from the shipped startup, not merely unused by it."""
    interpolated = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", COMPOSE_FILE.read_text(encoding="utf-8")))
    shipped = {
        line.split("=", 1)[0]
        for line in DEFAULTS_FILE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    resolved = {name for definition in services(model).values() for name in (definition.get("environment") or {})}

    assert "INFRAHUB_SYNC_BOOTSTRAP_CONFIGURATION" not in interpolated
    assert "INFRAHUB_SYNC_BOOTSTRAP_CONFIGURATION" not in shipped
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


def test_the_host_route_is_test_only_and_reaches_exactly_the_two_destination_services(
    model: dict[str, Any], contract_environment: dict[str, str]
) -> None:
    """The shipped bundle grants it to nobody; the override grants it to exactly two.

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

    assert routed(model) == set(), f"the shipped bundle routes {sorted(routed(model))} to the host"
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


def test_the_cli_service_reaches_no_host_and_keeps_nothing(cli_model: dict[str, Any]) -> None:
    """No published port, no dependency startup, no volume, and no Docker socket."""
    definition = service(cli_model, CLI_SERVICE)

    assert published(definition) == [], f"the CLI service publishes {published(definition)}"
    assert definition.get("depends_on") in (None, {}), f"the CLI service waits for {definition.get('depends_on')}"
    assert definition.get("volumes") in (None, []), f"the CLI service mounts {definition.get('volumes')}"
    assert definition.get("restart") == "no"
    assert "/var/run/docker.sock" not in str(definition)


def test_the_cli_service_runs_the_cli_in_the_image_the_deployment_runs(cli_model: dict[str, Any]) -> None:
    """Same immutable reference, the CLI as its entrypoint, and the image's own user."""
    definition = service(cli_model, CLI_SERVICE)
    api = service(cli_model, "sync-api")
    expected = [f"{root}:{SCRATCH_OPTIONS}" for root in SYNC_SCRATCH_ROOTS]

    assert definition["image"] == api["image"]
    assert IMMUTABLE_REFERENCE.fullmatch(str(definition["image"])), definition["image"]
    assert definition["entrypoint"] == ["infrahub-sync"]
    assert definition.get("read_only") is True
    assert definition.get("tmpfs") == expected
    # Nothing overrides the user, so the container runs as the image's non-root one.
    assert "user" not in definition, f"the CLI service overrides the image user with {definition.get('user')}"


def test_the_cli_service_carries_the_instance_labels_like_every_other(cli_model: dict[str, Any]) -> None:
    """A container this bundle created has to be one teardown can prove it owns."""
    labels = service(cli_model, CLI_SERVICE)["labels"]

    assert labels.get(INSTANCE_LABEL) == "contract-0000000000000000"
    assert labels.get(BUNDLE_LABEL) == "compose"


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
