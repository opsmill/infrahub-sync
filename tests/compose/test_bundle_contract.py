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
    INSTANCE_LABEL,
    SCRATCH_OPTIONS,
    SYNC_SCRATCH_ROOTS,
    SYNC_SERVICES,
    mount_sources,
    resolve,
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
# The bundled configuration
# ---------------------------------------------------------------------------


def test_the_bundled_configuration_is_a_registerable_package() -> None:
    """Bootstrap registers this file's declared content; an unparseable one fails at start."""
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
# The one filesystem input
# ---------------------------------------------------------------------------


def test_the_bundled_configuration_is_the_only_filesystem_input_any_sync_service_takes(
    model: dict[str, Any],
) -> None:
    """One read-only file, given to the job that registers it and to nothing else.

    Registered content lives in PostgreSQL afterwards, which is what lets the API
    and the worker take no configuration input at all.
    """
    readers = {
        name: definition.get("volumes") or []
        for name, definition in services(model).items()
        if name.startswith("sync-") and (definition.get("volumes") or [])
    }

    assert sorted(readers) == ["sync-bootstrap"], f"Sync services taking a filesystem input: {sorted(readers)}"
    mounts = readers["sync-bootstrap"]
    assert len(mounts) == 1, f"sync-bootstrap takes {len(mounts)} inputs"
    assert mounts[0]["read_only"] is True, "the bundled configuration is mounted writable"
    assert Path(mounts[0]["source"]).resolve() == BUNDLED_CONFIGURATION.resolve()
    assert mounts[0]["target"] == "/etc/infrahub-sync/configuration.yaml"


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
