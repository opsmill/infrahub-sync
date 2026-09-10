"""What a worker does with a declared source credential, at the two boundaries that carry one.

The bundled NetBox and Nautobot adapters are the first source adapters whose SDK
the image installs, so this suite proves the thing that installation is for: a
registered package's declared credential reference reaches the real adapter
client, and nothing ambient does.

Two boundaries, deliberately separate.

* In-process, here, against the real parser, the real resolver, the real
  installed SDK and the real bundled adapter. Nothing is stubbed except the one
  transport call Nautobot's constructor makes, and every socket is denied, so a
  pass cannot come from a network the test did not intend.
* In the container, under the `docker` marker, through the shipped image. That
  one proves the same property survives the image and the Compose model.

Neither is an extraction claim and neither qualifies a source version. No source
server is reached, imitated, or described: the only external response in this
file is the fixed version document Nautobot's constructor demands before it will
return an object at all.

Canary values never reach retained output. Comparisons happen privately and a
failure reports names.
"""

from __future__ import annotations

import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import requests

from infrahub_sync.configuration import collect_findings, parse_configuration_package
from infrahub_sync.configuration.credentials import CredentialConfigurationError, select_runtime_credential
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from tests.compose.conftest import CONTRACT_ENVIRONMENT, compose
from tests.compose.redaction import SECRETS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync import SyncInstance

# Long enough that a substring match means the value itself, and unmistakably a
# test value. These stand in for operator secrets; none is a real credential.
DECLARED_SOURCE_TOKEN = "pkg-r1-declared-source-token-9d41c7"  # noqa: S105 -- a test stand-in, not a credential
DECLARED_DESTINATION_TOKEN = "pkg-r1-declared-destination-token-4b82ae"  # noqa: S105 -- a test stand-in, not a credential
AMBIENT_SOURCE_TOKEN = "pkg-r1-ambient-source-token-must-lose-1f60d3"  # noqa: S105 -- a test stand-in, not a credential

# Reserved by RFC 6761: resolvable by nothing, so a leaked request cannot reach a
# real host even if a deny were somehow bypassed.
NETBOX_URL = "https://netbox.invalid"
NAUTOBOT_URL = "https://nautobot.invalid"
AMBIENT_URL = "https://ambient-source.invalid"

# The one external answer this file supplies. Nautobot's client asks for the API
# version during construction and refuses to return an object without it; every
# other request is rejected.
NAUTOBOT_VERSION_URL = f"{NAUTOBOT_URL}/api/"
NAUTOBOT_API_VERSION = "2.4"

# The built candidate the Docker-marked cases run against.
IMAGE_REFERENCE_ENV = "INFRAHUB_SYNC_IMAGE"


@dataclass(frozen=True)
class Source:
    """One bundled source adapter's declared shape, as a package names it."""

    name: str
    url: str
    reference: str
    identifier: str
    ambient_url_names: tuple[str, ...]
    mapping: str


SOURCES = {
    source.name: source
    for source in (
        Source(
            name="netbox",
            url=NETBOX_URL,
            reference="netbox-token",
            identifier="NETBOX_TOKEN",
            ambient_url_names=("NETBOX_ADDRESS", "NETBOX_URL"),
            mapping="extras.tags",
        ),
        Source(
            name="nautobot",
            url=NAUTOBOT_URL,
            reference="nautobot-token",
            identifier="NAUTOBOT_TOKEN",
            ambient_url_names=("NAUTOBOT_ADDRESS", "NAUTOBOT_URL"),
            mapping="extras.tags",
        ),
    )
}

# Every environment name any of this touches, cleared before each case so an
# ambient value on the developer's machine cannot decide an outcome.
MANAGED_ENVIRONMENT = (
    "NETBOX_TOKEN",
    "NETBOX_ADDRESS",
    "NETBOX_URL",
    "NAUTOBOT_TOKEN",
    "NAUTOBOT_ADDRESS",
    "NAUTOBOT_URL",
    "INFRAHUB_API_TOKEN",
    "INFRAHUB_ADDRESS",
)


def package_content(source: str) -> dict[str, Any]:
    """One minimal single-kind package declaring `source` against Infrahub.

    Sanitized and illustrative. It is the smallest package that carries a source
    credential reference, which is the only property this file reads from it.
    """
    profile = SOURCES[source]
    return {
        "format_version": 1,
        "configuration": {
            "name": f"pkg-r1-{source}",
            "source": {
                "name": source,
                "settings": {
                    "url": profile.url,
                    "token": {"$credential": profile.reference},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": "http://infrahub.example.net:8000",
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": "BuiltinTag",
                    "mapping": profile.mapping,
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}],
                }
            ],
        },
        "credentials": {
            profile.reference: {"provider": "env", "identifier": profile.identifier},
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"},
        },
    }


@pytest.fixture(autouse=True)
def _closed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case from an environment that names no source credential."""
    for name in MANAGED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every socket for the rest of the case.

    Installed after imports, so it constrains what the code under test does
    rather than what importing it did. A construction that reaches the network
    fails here instead of depending on what a host happens to resolve.
    """

    def refuse(*_args: object, **_kwargs: object) -> object:
        message = "this case denies every network call"
        raise AssertionError(message)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def runtime_instance(source: str, *, resolve_source_credentials: bool = True) -> SyncInstance:
    """Parse and resolve one package exactly as a registered worker run does."""
    package = parse_configuration_package(package_content(source))
    return resolve_runtime_instance(
        package,
        directory="/nonexistent/pkg-r1",
        resolve_source_credentials=resolve_source_credentials,
    )


# ---------------------------------------------------------------------------
# The resolver's refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_an_absent_selected_source_token_is_refused(source: str) -> None:
    """A run that needs a source credential does not start without one."""
    with pytest.raises(CredentialConfigurationError) as raised:
        runtime_instance(source)

    assert SOURCES[source].identifier in str(raised.value)


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_an_empty_selected_source_token_is_refused(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty value is an unset one; a blank operator entry must not start a run."""
    monkeypatch.setenv(SOURCES[source].identifier, "")

    with pytest.raises(CredentialConfigurationError) as raised:
        runtime_instance(source)

    assert SOURCES[source].identifier in str(raised.value)


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_a_refusal_names_the_environment_identifier_and_never_the_value(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diagnosis an operator needs is the name; the value must not be in it."""
    profile = SOURCES[source]
    monkeypatch.setenv("INFRAHUB_API_TOKEN", DECLARED_DESTINATION_TOKEN)

    with pytest.raises(CredentialConfigurationError) as raised:
        runtime_instance(source)

    rendered = str(raised.value)
    assert profile.identifier in rendered
    assert DECLARED_DESTINATION_TOKEN not in rendered


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_source_a_package_does_not_select_may_be_absent(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """One deployment serves packages of both kinds; only the selected token is needed."""
    monkeypatch.setenv(SOURCES[source].identifier, DECLARED_SOURCE_TOKEN)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", DECLARED_DESTINATION_TOKEN)

    instance = runtime_instance(source)

    other = next(name for name in SOURCES if name != source)
    assert SOURCES[other].identifier not in dict(instance.source.settings or {})
    assert (instance.source.settings or {})["token"] == DECLARED_SOURCE_TOKEN


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_declared_validation_resolves_no_source_credential(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration judges declared content, so it must not reach for the secret.

    Finding nothing wrong would also be true of a validation that resolved the
    credential and happened to succeed, so the resolver itself is made to fail
    the case if it is called at all.
    """

    def refuse(*_args: object, **_kwargs: object) -> str:
        message = "declared validation resolved a credential reference"
        raise AssertionError(message)

    monkeypatch.setattr("infrahub_sync.configuration.validation.resolve_reference", refuse, raising=False)
    monkeypatch.setattr("infrahub_sync.configuration.credentials.resolve_reference", refuse)
    package = parse_configuration_package(package_content(source))

    assert collect_findings(package) == ()


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_a_saved_plan_apply_needs_no_source_token(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An apply host constructs the destination only, so it holds no source secret."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", DECLARED_DESTINATION_TOKEN)

    instance = runtime_instance(source, resolve_source_credentials=False)

    assert (instance.source.settings or {})["token"] == {"$credential": SOURCES[source].reference}
    assert (instance.destination.settings or {})["token"] == DECLARED_DESTINATION_TOKEN


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_registered_url_and_token_beat_a_conflicting_ambient_pair(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered run reads its declared settings; the shell has no vote.

    Both halves matter. An ambient URL that won would send a declared credential
    somewhere the package never named, and an ambient token that won would send
    a credential the operator did not register.
    """
    profile = SOURCES[source]
    monkeypatch.setenv(profile.identifier, DECLARED_SOURCE_TOKEN)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", DECLARED_DESTINATION_TOKEN)
    instance = runtime_instance(source)
    settings = instance.source.settings or {}

    for name in profile.ambient_url_names:
        monkeypatch.setenv(name, AMBIENT_URL)
    monkeypatch.setenv(profile.identifier, AMBIENT_SOURCE_TOKEN)

    assert select_runtime_credential(settings, "url", profile.ambient_url_names) == profile.url
    assert select_runtime_credential(settings, "token", (profile.identifier,)) == DECLARED_SOURCE_TOKEN


# ---------------------------------------------------------------------------
# The real adapter clients
# ---------------------------------------------------------------------------
# The point of installing the two SDKs. A resolver that produced the right value
# proves nothing if the adapter then goes and reads the environment itself.


def test_the_netbox_adapter_client_carries_the_registered_credential(
    monkeypatch: pytest.MonkeyPatch, deny_network: None
) -> None:
    """The real NetBox client, built by the real adapter, offline.

    `pynetbox.api()` issues no request, so the whole construction happens under
    the network deny and any read of the environment would have to come from the
    adapter itself.
    """
    del deny_network
    from infrahub_sync.adapters.netbox import NetboxAdapter

    monkeypatch.setenv("NETBOX_TOKEN", DECLARED_SOURCE_TOKEN)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", DECLARED_DESTINATION_TOKEN)
    instance = runtime_instance("netbox")
    for name in SOURCES["netbox"].ambient_url_names:
        monkeypatch.setenv(name, AMBIENT_URL)
    monkeypatch.setenv("NETBOX_TOKEN", AMBIENT_SOURCE_TOKEN)

    adapter = NetboxAdapter(target="netbox", adapter=instance.source, config=instance)

    assert adapter.client.base_url == f"{NETBOX_URL}/api"
    assert adapter.client.token == DECLARED_SOURCE_TOKEN


def test_the_nautobot_adapter_client_carries_the_registered_credential(
    monkeypatch: pytest.MonkeyPatch, deny_network: None
) -> None:
    """The real Nautobot client, built by the real adapter, with one stubbed response.

    Nautobot's constructor performs a version-discovery GET and returns no object
    without it, so exactly that one request is answered with a fixed document.
    The SDK constructor, its version validation, the resolver and the adapter are
    all the real ones; any second request, or any request to another URL, fails
    the case. Answering it proves what the adapter sends, not what a server is.
    """
    del deny_network
    from infrahub_sync.adapters.nautobot import NautobotAdapter

    seen: list[tuple[str, str | None]] = []

    def version_only_get(
        session: requests.Session,
        url: str,
        *args: object,
        headers: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> requests.Response:
        del args, kwargs
        sent = {**dict(session.headers), **dict(headers or {})}
        seen.append((url, sent.get("Authorization")))
        assert url == NAUTOBOT_VERSION_URL, "the adapter requested something other than the version document"
        answer = requests.Response()
        answer.status_code = 200
        answer.url = url
        answer.headers["API-Version"] = NAUTOBOT_API_VERSION
        answer._content = b"{}"
        return answer

    monkeypatch.setattr(requests.Session, "get", version_only_get)
    monkeypatch.setenv("NAUTOBOT_TOKEN", DECLARED_SOURCE_TOKEN)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", DECLARED_DESTINATION_TOKEN)
    instance = runtime_instance("nautobot")
    for name in SOURCES["nautobot"].ambient_url_names:
        monkeypatch.setenv(name, AMBIENT_URL)
    monkeypatch.setenv("NAUTOBOT_TOKEN", AMBIENT_SOURCE_TOKEN)

    adapter = NautobotAdapter(target="nautobot", adapter=instance.source, config=instance)

    assert adapter.client.base_url == f"{NAUTOBOT_URL}/api"
    assert adapter.client.token == DECLARED_SOURCE_TOKEN
    assert len(seen) == 1, f"the constructor made {len(seen)} requests"
    assert seen[0] == (NAUTOBOT_VERSION_URL, f"Token {DECLARED_SOURCE_TOKEN}")


# ---------------------------------------------------------------------------
# The container boundary
# ---------------------------------------------------------------------------
# The same property, proved where it has to hold: inside the shipped image, on
# the Compose model an operator actually runs. Opt-in under `docker`, and driven
# through the suite's capture boundary so a canary cannot reach retained output.

CONTAINER_PROBE = """
import json, os, socket, sys

from infrahub_sync.configuration import parse_configuration_package
from infrahub_sync.configuration.runtime import resolve_runtime_instance

declared_url = sys.argv[1]
source = sys.argv[2]
reference = f"{source}-token"
identifier = f"{source.upper()}_TOKEN"

package = parse_configuration_package({
    "format_version": 1,
    "configuration": {
        "name": f"pkg-r1-container-{source}",
        "source": {"name": source, "settings": {
            "url": declared_url, "token": {"$credential": reference}}},
        "destination": {"name": "infrahub", "settings": {
            "url": "http://infrahub.invalid:8000", "token": {"$credential": "infrahub-token"}}},
        "schema_mapping": [{"name": "BuiltinTag", "mapping": "extras.tags",
                            "identifiers": ["name"],
                            "fields": [{"name": "name", "mapping": "name"}]}],
    },
    "credentials": {
        reference: {"provider": "env", "identifier": identifier},
        "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"},
    },
})
instance = resolve_runtime_instance(package, directory="/tmp/infrahub-sync/pkg-r1")
expected_token = os.environ[identifier]

if source == "nautobot":
    import requests

    def version_only_get(session, url, *args, headers=None, **kwargs):
        answer = requests.Response()
        answer.status_code = 200
        answer.url = url
        answer.headers["API-Version"] = "2.4"
        answer._content = b"{}"
        return answer

    requests.Session.get = version_only_get

def refuse(*args, **kwargs):
    raise AssertionError("the container probe denies every network call")

socket.socket.connect = refuse
socket.create_connection = refuse

if source == "netbox":
    from infrahub_sync.adapters.netbox import NetboxAdapter as Adapter
else:
    from infrahub_sync.adapters.nautobot import NautobotAdapter as Adapter

adapter = Adapter(target=source, adapter=instance.source, config=instance)

# Compared here, inside the container. Only the verdicts cross the boundary:
# a rendered value would put the credential into whatever retained the output.
checks = {
    "url_is_declared": adapter.client.base_url == declared_url.rstrip("/") + "/api",
    "token_is_declared": adapter.client.token == expected_token,
    "ambient_url_ignored": "ambient" not in adapter.client.base_url,
}
print("PKG-R1-CONTAINER " + json.dumps(checks, sort_keys=True))
print("PKG-R1-CONTAINER PASS" if all(checks.values()) else "PKG-R1-CONTAINER FAIL")
"""


def container_environment() -> dict[str, str]:
    """Every operator input the bundle needs, with the image under test named.

    The image reference is the built candidate the gate loaded, which is what
    makes this a claim about the artifact rather than about a source checkout.
    """
    image = os.environ.get(IMAGE_REFERENCE_ENV, "").strip()
    if not image:
        pytest.skip(f"{IMAGE_REFERENCE_ENV} names no built image; this gate runs against the candidate artifact")
    secret = Path(tempfile.mkdtemp()) / "postgres-admin-password"
    secret.write_text("container-administrator-password\n", encoding="utf-8")
    return {
        **CONTRACT_ENVIRONMENT,
        IMAGE_REFERENCE_ENV: image,
        "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD_FILE": str(secret),
    }


CONTAINER_DECLARED_URL = {"netbox": "http://netbox.invalid:8080", "nautobot": "http://nautobot.invalid:8080"}
CONTAINER_AMBIENT_URL = "http://ambient.invalid:9999"
# Distinct per source, so a leak sweep can name which one escaped.
CONTAINER_TOKENS = {
    "netbox": "pkg-r1-container-netbox-canary-a71f3c",
    "nautobot": "pkg-r1-container-nautobot-canary-c93e05",
}


@pytest.mark.docker
@pytest.mark.parametrize("source", sorted(SOURCES))
def test_the_worker_container_gives_the_declared_credential_to_the_real_adapter(
    docker_daemon: None, source: str
) -> None:
    """One `compose run` in the shipped image, resolving and constructing for real.

    The operator file supplies the token through the Compose model; a hostile
    ambient URL and token are exported into the same container. What the adapter
    ends up holding is compared inside the container, and only verdicts are
    printed, so the value never reaches anything this suite retains.
    """
    del docker_daemon
    profile = SOURCES[source]
    token = CONTAINER_TOKENS[source]
    SECRETS.register(token)

    result = compose(
        [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--env",
            f"{profile.ambient_url_names[0]}={CONTAINER_AMBIENT_URL}",
            "--env",
            f"{profile.ambient_url_names[1]}={CONTAINER_AMBIENT_URL}",
            "sync-worker",
            "python",
            "-c",
            CONTAINER_PROBE,
            CONTAINER_DECLARED_URL[source],
            source,
        ],
        environment={**container_environment(), profile.identifier: token},
    )

    assert result.returncode == 0, result.output
    assert "PKG-R1-CONTAINER PASS" in result.stdout, result.output
    assert SECRETS.leaked(result.unredacted(), {profile.identifier: token}) == []


@pytest.mark.docker
def test_a_missing_source_token_fails_the_run_inside_the_container(docker_daemon: None) -> None:
    """The refusal an operator sees names the variable, and the container still started."""
    del docker_daemon

    result = compose(
        [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "sync-worker",
            "python",
            "-c",
            CONTAINER_PROBE,
            CONTAINER_DECLARED_URL["netbox"],
            "netbox",
        ],
        environment=container_environment(),
    )

    assert result.returncode != 0
    assert "NETBOX_TOKEN" in result.output
    assert "PKG-R1-CONTAINER PASS" not in result.stdout
