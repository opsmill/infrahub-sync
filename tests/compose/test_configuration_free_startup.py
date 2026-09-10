"""A deployment that starts, repeats and resets with no configuration at all.

Sync-owned only. This module starts one real deployment of the candidate image
and drives it through the entry point an operator uses, and it depends on no
external system: no Infrahub, no source, no destination, no credential for one.
That is the property — a deployment reaches READY on its own dependencies, holds
an empty registry until an operator registers something, and comes back empty
after a reset.

The package registered here declares unreachable external addresses and
credential references nothing resolves. Registration is content admission and
reaches no network, so the package stays registered and is never planned,
applied or synced.

This is an empty-startup and lifecycle proof. It is not the write-bearing
lifecycle matrix, which runs elsewhere against a real destination.
"""

from __future__ import annotations

import json
import shutil
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.compose.conftest import BUNDLE, DEFAULTS_FILE
from tests.compose.lifecycle import (
    Deployment,
    api_client,
    entry_point,
    instance_identity,
    probe_json,
    register,
    run_bootstrap,
    wait_for,
    worker_state,
    write_candidate_binding,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.compose

# This module's own loopback ports, so it can run beside every other deployment
# this suite starts.
API_PORT = "8051"
PREFECT_PORT = "4251"

# Addresses that resolve to nothing and a credential nothing sets. A registration
# admits declared content, so this package is registrable and unusable — which is
# what keeps a startup proof from acquiring a destination.
UNREACHABLE_SOURCE = "http://sync-r2-source.invalid:8000"
UNREACHABLE_DESTINATION = "http://sync-r2-destination.invalid:8000"
DECLARED_NAME = "configuration-free-startup"

# What a registry holds, read from inside the deployment through the store that
# holds it rather than from the API that serves it.
REGISTRY = """
import json
from infrahub_sync.configuration.models import parse_configuration_package
from infrahub_sync.product_store import configs
from infrahub_sync.service.storage import service_product_projection
projection = service_product_projection()
registry = []
for summary in configs.list_configs(projection=projection):
    for version in projection.list_configuration_versions(summary.config_id):
        registry.append(
            [
                parse_configuration_package(version.declared_content).configuration.name,
                version.registry_version,
                version.package_checksum,
            ]
        )
print(json.dumps(sorted(registry)))
"""

# Every audit event the deployment holds, as actor and operation. A registration
# is a decision and leaves one; convergence is not, and leaves none.
AUDIT = """
import json
from infrahub_sync.service.storage import service_product_projection
events = service_product_projection().audit_events()
print(json.dumps(sorted(event.actor + "/" + event.operation for event in events)))
"""

# The actor a bootstrap used to register under. Nothing writes it now, and a
# census carrying it would mean a startup registered something.
RETIRED_BOOTSTRAP_ACTOR = "compose-bootstrap"

SCHEMA_TABLES = """
import json, os, psycopg
with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()"
        )
        print(json.dumps(cursor.fetchone()[0]))
"""


def declared_package() -> dict[str, Any]:
    """A valid declared package whose external inputs resolve to nothing."""
    return {
        "format_version": 1,
        "configuration": {
            "name": DECLARED_NAME,
            "source": {
                "name": "infrahub",
                "settings": {
                    "url": UNREACHABLE_SOURCE,
                    "branch": "main",
                    "token": {"$credential": "startup-token"},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": UNREACHABLE_DESTINATION,
                    "branch": "main",
                    "token": {"$credential": "startup-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": "InfraDevice",
                    "mapping": "InfraDevice",
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}],
                }
            ],
        },
        # Nothing sets this variable, so the reference is declared and unresolved.
        "credentials": {"startup-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_R2_ABSENT_TOKEN"}},
    }


def verdict(result: Any) -> str:  # noqa: ANN401 -- the captured result of one wrapper command
    """Return the state word `status` printed, which is its last line."""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def family(result: Any) -> str:  # noqa: ANN401 -- the captured result of one wrapper command
    """Return the refusal family a run reported, or '' when it did not refuse."""
    for line in result.stderr.splitlines():
        if line.startswith("infrahub-sync: "):
            return line.removeprefix("infrahub-sync: ").split(":", 1)[0]
    return ""


def setting(bundle: Path, name: str) -> str:
    """Read one operator setting the way the entry point does."""
    for line in (bundle / "operator.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    return ""


def prepare(bundle: Path, image: str) -> None:
    """Take a fresh copy of the bundle through `init`, as an extracted archive.

    The binding is written first, because that is what an operator extracts: a
    release derives it from the candidate's digests and puts it in the archive.
    Nothing here names an image afterwards — the record does, and `init` reads it.
    """
    write_candidate_binding(bundle, image)
    created = entry_point(bundle, "init")
    assert created.returncode == 0, created.stderr
    settings = bundle / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8")
        # Loaded, never pulled: this is the candidate the image gate built at
        # this head, which no registry holds. No destination or source credential
        # is added, which is the whole point of this module.
        + f"INFRAHUB_SYNC_IMAGE_PULL_POLICY=never\nINFRAHUB_SYNC_API_PORT={API_PORT}\n"
        f"INFRAHUB_SYNC_PREFECT_PORT={PREFECT_PORT}\n",
        encoding="utf-8",
    )


def deployment_of(bundle: Path) -> Deployment:
    """The deployment this bundle's current identity names."""
    instance = instance_identity(bundle)
    settings = bundle / "operator.env"
    return Deployment(
        instance=instance,
        environment_file=settings,
        environment_files=(DEFAULTS_FILE, settings, bundle / ".instance"),
        compose_file=bundle / "compose.yaml",
        bundle=bundle,
        api_port=int(API_PORT),
        prefect_port=int(PREFECT_PORT),
    )


@pytest.fixture(scope="module")
def started(sync_image: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Deployment]:
    """One never-initialized copy of the bundle, taken to READY with no configuration."""
    bundle = tmp_path_factory.mktemp(f"startup{uuid.uuid4().hex[:8]}") / "compose"
    shutil.copytree(BUNDLE, bundle)
    prepare(bundle, sync_image)

    launched = entry_point(bundle, "start")
    assert launched.returncode == 0, launched.stderr + launched.stdout[-3000:]
    deployment = deployment_of(bundle)
    try:
        yield deployment
    finally:
        instance = instance_identity(bundle)
        entry_point(bundle, "reset", instance)
        deployment.down(volumes=True)


@pytest.fixture(scope="module")
def principal(started: Deployment) -> str:
    """The bearer token `init` generated for this deployment's one principal."""
    tokens = json.loads(setting(started.bundle, "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"))
    return str(next(iter(tokens.values()))["token"])


def test_a_deployment_with_no_configuration_reaches_ready(started: Deployment) -> None:
    """No package, no credentials, no destination — and a live worker anyway."""
    reported = entry_point(started.bundle, "status")

    assert verdict(reported) == "READY", reported.stdout + reported.stderr
    assert worker_state(started) in {"ready", "busy"}
    assert not setting(started.bundle, "INFRAHUB_API_TOKEN"), "the fixture supplied a destination credential"
    assert probe_json(started, SCHEMA_TABLES) > 0, "the deployment came back with no product schema"
    assert probe_json(started, REGISTRY) == [], "a start registered something"
    assert probe_json(started, AUDIT) == [], "a start recorded an audit event"


def test_a_repeated_start_keeps_the_registry_empty(started: Deployment) -> None:
    """`start` is the command an operator repeats, and it registers nothing either time."""
    repeated = entry_point(started.bundle, "start")

    assert repeated.returncode == 0, repeated.stderr + repeated.stdout[-3000:]
    assert "the deployment is READY" in repeated.stdout
    assert probe_json(started, REGISTRY) == []
    assert probe_json(started, AUDIT) == [], "a repeated start recorded an audit event"


def test_a_repeated_bootstrap_leaves_registered_content_exactly_as_it_was(started: Deployment, principal: str) -> None:
    """The registry is the operator's, and convergence passes over it untouched.

    Registered through the API and never planned: the package declares addresses
    that resolve to nothing, so admitting it proves registration is content
    admission rather than a connection.
    """
    with api_client(started, principal) as client:
        config_id, registry_version = register(
            client, declared_package(), "startup gate: register a configuration bootstrap must preserve"
        )
    registered = probe_json(started, REGISTRY)
    audited = probe_json(started, AUDIT)
    assert [entry[0] for entry in registered] == [DECLARED_NAME], registered

    repeated = run_bootstrap(started)

    assert repeated.returncode == 0, repeated.output
    assert probe_json(started, REGISTRY) == registered
    assert [entry[1] for entry in registered] == [registry_version]
    assert config_id
    # The operator's registration is the one decision recorded, and a repeated
    # bootstrap adds nothing to the census under any actor. The operation name is
    # the API's to choose, so what is asserted is the count and the actor.
    census = probe_json(started, AUDIT)
    assert census == audited, census
    assert len(audited) == 1, audited
    assert [event for event in census if event.startswith(f"{RETIRED_BOOTSTRAP_ACTOR}/")] == [], census


def test_stop_and_start_preserve_the_registered_package(started: Deployment) -> None:
    """Stopping keeps data; starting again converges without touching the registry."""
    before = probe_json(started, REGISTRY)

    stopped = entry_point(started.bundle, "stop")
    assert stopped.returncode == 0, stopped.stderr
    restarted = entry_point(started.bundle, "start")
    assert restarted.returncode == 0, restarted.stderr + restarted.stdout[-3000:]

    wait_for("the deployment reporting a live worker again", lambda: worker_state(started) in {"ready", "busy"})
    assert probe_json(started, REGISTRY) == before
    assert before, "this case compared a registry that held nothing"
    assert [event for event in probe_json(started, AUDIT) if event.startswith(f"{RETIRED_BOOTSTRAP_ACTOR}/")] == []


def test_reset_needs_the_exact_identity_and_the_next_start_comes_back_empty(
    started: Deployment, sync_image: str
) -> None:
    """What reset is for, from the other side of a real second start.

    Runs last: it takes this module's deployment down and brings a new identity
    up in its place.
    """
    bundle = started.bundle
    instance = instance_identity(bundle)

    refused = entry_point(bundle, "reset", "not-this-instance")
    assert refused.returncode != 0
    assert family(refused) == "confirmation-required", refused.stderr

    removed = entry_point(bundle, "reset", instance)
    assert removed.returncode == 0, removed.stderr

    # `reset` removes the generated state and leaves the binding, so the second
    # preparation names the same candidate the first one did.
    prepare(bundle, sync_image)
    cold = deployment_of(bundle)
    assert cold.instance != instance, "reset left the identity its volumes were labelled with"
    launched = entry_point(bundle, "start")
    assert launched.returncode == 0, launched.stderr + launched.stdout[-3000:]
    assert "the deployment is READY" in launched.stdout
    assert probe_json(cold, SCHEMA_TABLES) > 0
    assert probe_json(cold, REGISTRY) == [], "a cold start came back with a registry"
    assert probe_json(cold, AUDIT) == [], "a cold start came back with an audit event"
